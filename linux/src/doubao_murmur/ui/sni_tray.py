"""StatusNotifierItem system tray icon, implemented directly over DBus.

GTK4 removed GtkStatusIcon, and AyatanaAppIndicator3 requires GTK3
``Gtk.Menu`` widgets, so neither works in this process. KDE Plasma's tray
actually speaks the StatusNotifierItem DBus protocol, which needs no GTK at
all — this module implements just enough of it (plus ``com.canonical.dbusmenu``
for the context menu) using Gio.

Spec: https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/

Registration passes our *object path* to the watcher (KDE resolves it
against the caller's unique bus name), so no extra well-known bus name is
needed — important inside Flatpak, where owning arbitrary names requires
extra permissions. The only sandbox permission required is
``--talk-name=org.kde.StatusNotifierWatcher``.
"""

from __future__ import annotations

import logging
from typing import Callable

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
_ITEM_PATH = "/StatusNotifierItem"
_ITEM_IFACE = "org.kde.StatusNotifierItem"
_MENU_PATH = "/MenuBar"
_MENU_IFACE = "com.canonical.dbusmenu"

_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>
"""


class SniTray:
    """A StatusNotifierItem tray icon with a dbusmenu context menu.

    Menu items are plain dicts: ``{"label": str, "callback": callable|None,
    "enabled": bool, "checked": bool}``; ``None`` entries render as
    separators. Items with a ``checked`` key render as checkboxes.
    """

    def __init__(
        self,
        *,
        item_id: str,
        title: str,
        icon_name: str,
        tooltip: str = "",
        on_activate: Callable[[], None] | None = None,
    ) -> None:
        self._item_id = item_id
        self._title = title
        self._icon_name = icon_name
        self._tooltip = tooltip
        self._on_activate = on_activate
        self._conn: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._watch_id: int = 0
        self._revision: int = 1
        # id -> menu item dict; ids start at 1 (0 is the dbusmenu root)
        self._items: dict[int, dict | None] = {}

    # -- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        """Export the DBus objects and wait for a watcher to register with.

        Returns False if the session bus is unreachable. The icon appears
        once a StatusNotifierWatcher (e.g. Plasma's tray) is running; it
        re-registers automatically if the watcher restarts.
        """
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            sni_node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
            menu_node = Gio.DBusNodeInfo.new_for_xml(_MENU_XML)
            self._reg_ids = [
                self._conn.register_object(
                    _ITEM_PATH,
                    sni_node.interfaces[0],
                    self._sni_method_call,
                    self._sni_get_property,
                    None,
                ),
                self._conn.register_object(
                    _MENU_PATH,
                    menu_node.interfaces[0],
                    self._menu_method_call,
                    self._menu_get_property,
                    None,
                ),
            ]
        except GLib.Error:
            logger.exception("Could not export StatusNotifierItem")
            return False

        self._watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            _WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            None,
        )
        return True

    def stop(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._conn:
            for reg_id in self._reg_ids:
                self._conn.unregister_object(reg_id)
            self._reg_ids = []

    def _on_watcher_appeared(self, _conn, _name, owner: str) -> None:
        logger.info("StatusNotifierWatcher appeared (%s); registering", owner)
        self._conn.call(
            _WATCHER_NAME,
            _WATCHER_PATH,
            _WATCHER_IFACE,
            "RegisterStatusNotifierItem",
            # Passing an object path makes the watcher resolve it against
            # our unique bus name — no well-known name needed.
            GLib.Variant("(s)", (_ITEM_PATH,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_register_reply,
        )

    def _on_register_reply(self, conn, result) -> None:
        try:
            conn.call_finish(result)
            logger.info("Tray icon registered")
        except GLib.Error:
            logger.exception("RegisterStatusNotifierItem failed")

    # -- public updates ----------------------------------------------------

    def set_menu(self, items: list[dict | None]) -> None:
        self._items = {i + 1: item for i, item in enumerate(items)}
        self._revision += 1
        self._emit(_MENU_PATH, _MENU_IFACE, "LayoutUpdated",
                   GLib.Variant("(ui)", (self._revision, 0)))

    def set_icon(self, icon_name: str) -> None:
        if icon_name != self._icon_name:
            self._icon_name = icon_name
            self._emit(_ITEM_PATH, _ITEM_IFACE, "NewIcon", None)

    def set_tooltip(self, tooltip: str) -> None:
        if tooltip != self._tooltip:
            self._tooltip = tooltip
            self._emit(_ITEM_PATH, _ITEM_IFACE, "NewToolTip", None)

    def _emit(self, path: str, iface: str, signal: str, args) -> None:
        if not self._conn:
            return
        try:
            self._conn.emit_signal(None, path, iface, signal, args)
        except GLib.Error:
            logger.exception("Failed to emit %s", signal)

    # -- org.kde.StatusNotifierItem ----------------------------------------

    def _sni_method_call(
        self, _conn, _sender, _path, _iface, method, _params, invocation
    ) -> None:
        if method == "Activate" and self._on_activate:
            GLib.idle_add(self._on_activate)
        # SecondaryActivate / ContextMenu / Scroll: nothing to do —
        # Plasma renders the context menu itself from the Menu property.
        invocation.return_value(None)

    def _sni_get_property(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self._item_id),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)", ("", [], self._title, self._tooltip)
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", _MENU_PATH),
        }
        return values.get(prop)

    # -- com.canonical.dbusmenu ----------------------------------------------

    def _menu_get_property(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Version": GLib.Variant("u", 3),
            "Status": GLib.Variant("s", "normal"),
            "TextDirection": GLib.Variant("s", "ltr"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(prop)

    def _item_props(self, item_id: int) -> dict:
        item = self._items.get(item_id)
        if item is None:  # separator
            return {"type": GLib.Variant("s", "separator")}
        props = {
            "type": GLib.Variant("s", "standard"),
            "label": GLib.Variant("s", item.get("label", "")),
            "enabled": GLib.Variant("b", item.get("enabled", True)),
            "visible": GLib.Variant("b", True),
        }
        if "checked" in item:
            props["toggle-type"] = GLib.Variant("s", "checkmark")
            props["toggle-state"] = GLib.Variant("i", 1 if item["checked"] else 0)
        return props

    def _layout_root(self):
        children = [
            GLib.Variant("(ia{sv}av)", (item_id, self._item_props(item_id), []))
            for item_id in sorted(self._items)
        ]
        root_props = {"children-display": GLib.Variant("s", "submenu")}
        return (0, root_props, children)

    def _activate_item(self, item_id: int) -> None:
        item = self._items.get(item_id)
        if item and item.get("callback") and item.get("enabled", True):
            # idle_add: let the menu close before the action runs
            GLib.idle_add(item["callback"])

    def _menu_method_call(
        self, _conn, _sender, _path, _iface, method, params, invocation
    ) -> None:
        if method == "GetLayout":
            parent_id, _depth, _names = params.unpack()
            if parent_id == 0:
                layout = self._layout_root()
            else:
                layout = (parent_id, self._item_props(parent_id), [])
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (self._revision, layout))
            )
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            if not ids:
                ids = [0, *self._items.keys()]
            props = []
            for item_id in ids:
                if item_id == 0:
                    props.append((0, self._layout_root()[1]))
                elif item_id in self._items:
                    props.append((item_id, self._item_props(item_id)))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            value = self._item_props(item_id).get(name)
            if value is None:
                value = GLib.Variant("s", "")
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._activate_item(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self._activate_item(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown method {method}",
            )
