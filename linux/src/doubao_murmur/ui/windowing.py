"""GTK window helpers for SteamOS/Linux.

GTK4 removed several GTK3 window-manager hint APIs. On Wayland, KDE/SteamOS
is best served by gtk4-layer-shell when available; otherwise we fall back to
plain GTK windows and only call X11-style hints when the binding exposes them.
"""

from __future__ import annotations

import logging
from enum import Enum

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Gdk

from doubao_murmur.config import OVERLAY_WIDTH
from doubao_murmur.paste.hyprland_window import (
    active_monitor_name as hyprland_active_monitor_name,
)

logger = logging.getLogger(__name__)

_HAS_LAYER_SHELL = False
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell

    _HAS_LAYER_SHELL = True
except (ImportError, ValueError):
    LayerShell = None

# GTK4 dropped keep-above/move APIs; on X11 we fall back to setting the
# window-manager state out-of-band via GdkX11 + xdotool.
try:
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11
except (ImportError, ValueError):
    GdkX11 = None


class OverlayRole(Enum):
    """Screen role used to anchor an overlay-style window."""

    STATUS = "status"
    PTT = "ptt"
    # The on-screen keyboard manages its own position/size (restored from
    # config, draggable by the user), so it is pinned above + non-focusing
    # like the others but never auto-positioned.
    KEYBOARD = "keyboard"


def _monitor_for_overlay(display):
    """Return the GDK monitor matching Hyprland's focused connector."""
    if display is None:
        return None
    monitors = display.get_monitors()
    connector = hyprland_active_monitor_name()
    if connector:
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            if monitor is not None and monitor.get_connector() == connector:
                return monitor
    return monitors.get_item(0) if monitors.get_n_items() else None


def apply_overlay_window_hints(window: Gtk.Window, role: OverlayRole) -> None:
    """Make an overlay/PTT window as stable as the platform permits."""
    if _HAS_LAYER_SHELL and LayerShell is not None:
        _apply_layer_shell(window, role)
        return

    _apply_legacy_hints(window)
    logger.info(
        "gtk4-layer-shell not available; %s window will use GTK defaults",
        role.value,
    )


def _apply_layer_shell(window: Gtk.Window, role: OverlayRole) -> None:
    LayerShell.init_for_window(window)
    LayerShell.set_namespace(window, f"doubao-murmur-{role.value}")

    layer = getattr(LayerShell.Layer, "OVERLAY", None)
    if layer is None:
        layer = getattr(LayerShell.Layer, "TOP", None)
    if layer is not None:
        LayerShell.set_layer(window, layer)

    for edge_name in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        edge = getattr(LayerShell.Edge, edge_name)
        LayerShell.set_anchor(window, edge, False)

    if role == OverlayRole.PTT:
        LayerShell.set_anchor(window, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(window, LayerShell.Edge.BOTTOM, 24)
    elif role == OverlayRole.STATUS:
        LayerShell.set_anchor(window, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(window, LayerShell.Edge.LEFT, True)
        _position_overlay_center_bottom(window)
    else:
        LayerShell.set_anchor(window, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(window, LayerShell.Edge.TOP, 16)

    keyboard_mode = getattr(LayerShell, "KeyboardMode", None)
    set_keyboard_mode = getattr(LayerShell, "set_keyboard_mode", None)
    if keyboard_mode is not None and set_keyboard_mode is not None:
        mode = getattr(keyboard_mode, "ON_DEMAND", None)
        if mode is not None:
            set_keyboard_mode(window, mode)


def _position_overlay_center_bottom(window: Gtk.Window) -> None:
    """Position the overlay at the middle-lower area of the screen."""
    display = Gdk.Display.get_default()
    if display:
        monitor = display.get_monitors().get_item(0) if display else None
        if monitor is not None:
            geo = monitor.get_geometry()
            top_margin = geo.height * 3 // 5
            left_margin = geo.x + (geo.width - OVERLAY_WIDTH) // 2
            LayerShell.set_margin(window, LayerShell.Edge.TOP, top_margin)
            LayerShell.set_margin(window, LayerShell.Edge.LEFT, left_margin)


def _apply_legacy_hints(window: Gtk.Window) -> None:
    for method_name, value in (
        ("set_keep_above", True),
        ("set_skip_taskbar_hint", True),
        ("set_skip_pager_hint", True),
    ):
        method = getattr(window, method_name, None)
        if method is not None:
            method(value)


def present_overlay(window: Gtk.Window, role: OverlayRole,
                    x: int | None = None, y: int | None = None) -> None:
    """Present an overlay window and make sure it is actually visible.

    With layer-shell the compositor handles stacking/anchoring. On plain
    X11, KWin's focus-stealing prevention can leave a freshly mapped
    window buried below the active one, so after mapping we pin it above
    and position it via the X11 connection.

    If x, y are provided they override the default position (e.g. saved
    user position).
    """
    if _HAS_LAYER_SHELL and LayerShell is not None:
        window.present()
        return
    _x11_set_no_focus_pre_map(window)
    window.set_visible(True)
    state = {"tries": 0, "x": x, "y": y}
    GLib.timeout_add(50, _x11_pin_above, window, role, state)


def _x11_set_no_focus_pre_map(window: Gtk.Window) -> None:
    if GdkX11 is None:
        return
    window.realize()
    surface = window.get_surface()
    if surface is None or not isinstance(surface, GdkX11.X11Surface):
        return
    _x11_set_input_hint(surface.get_xid())


def _x11_pin_above(window: Gtk.Window, role: OverlayRole, state: dict) -> bool:
    surface = window.get_surface()
    if surface is None or GdkX11 is None or not isinstance(
        surface, GdkX11.X11Surface
    ):
        return GLib.SOURCE_REMOVE

    width = window.get_width()
    height = window.get_height()
    if not width or not height:
        state["tries"] += 1
        # Keep polling until mapped (max ~1s)
        return (
            GLib.SOURCE_CONTINUE
            if state["tries"] < 20
            else GLib.SOURCE_REMOVE
        )

    surface.set_skip_taskbar_hint(True)
    surface.set_skip_pager_hint(True)

    xid = surface.get_xid()

    x = state.get("x")
    y = state.get("y")
    if x is not None and y is not None:
        _x11_apply_wm_state(xid, x, y)
        return GLib.SOURCE_REMOVE

    x = y = None
    if role != OverlayRole.KEYBOARD:
        display = window.get_display()
        monitor = _monitor_for_overlay(display)
        if monitor is not None:
            geo = monitor.get_geometry()
            x = geo.x + (geo.width - width) // 2
            if role == OverlayRole.PTT:
                y = geo.y + geo.height - height - 24
            elif role == OverlayRole.STATUS:
                y = geo.y + geo.height * 3 // 5
            else:
                y = geo.y + 16

    _x11_apply_wm_state(xid, x, y)
    return GLib.SOURCE_REMOVE


_x11_conn = None


def _x11_set_input_hint(xid: int) -> None:
    """Mark an X11 window as never accepting keyboard focus.

    Dictation must not pull focus away from the window the user is
    typing into (the final Ctrl+V goes to whichever window has focus).
    Three layers, because GDK rewrites WM_HINTS on map and KWin honors
    several focus paths:
    - WM_HINTS input=0 (ICCCM passive focus off)
    - strip WM_TAKE_FOCUS from WM_PROTOCOLS ("globally active" off)
    - _NET_WM_WINDOW_TYPE_NOTIFICATION: KWin never focuses notification
      windows and keeps them above normal windows by design
    """
    global _x11_conn
    try:
        from Xlib import Xatom, Xutil
        from Xlib.display import Display
    except ImportError:
        return
    try:
        if _x11_conn is None:
            _x11_conn = Display()
        d = _x11_conn
        win = d.create_resource_object("window", xid)
        win.set_wm_hints(flags=Xutil.InputHint, input=0)

        take_focus = d.intern_atom("WM_TAKE_FOCUS")
        protocols = win.get_wm_protocols()
        if take_focus in protocols:
            win.set_wm_protocols(
                [p for p in protocols if p != take_focus]
            )

        window_type = d.intern_atom("_NET_WM_WINDOW_TYPE")
        utility = d.intern_atom("_NET_WM_WINDOW_TYPE_UTILITY")
        win.change_property(window_type, Xatom.ATOM, 32, [utility])
        d.flush()
    except Exception as e:
        logger.warning("X11 input hint failed: %s", e)
        _x11_conn = None


def _x11_apply_wm_state(xid: int, x: int | None, y: int | None) -> None:
    """Set _NET_WM_STATE_ABOVE, raise, and optionally move via Xlib.

    KWin ignores xdotool's windowstate client message, so send the EWMH
    message ourselves with python-xlib.
    """
    global _x11_conn
    try:
        from Xlib import X, protocol
        from Xlib.display import Display
    except ImportError:
        logger.warning("python-xlib not available; overlay may stay below")
        return

    # Re-assert no-focus in case GDK rewrote WM_HINTS/WM_PROTOCOLS on map.
    _x11_set_input_hint(xid)

    try:
        if _x11_conn is None:
            _x11_conn = Display()
        d = _x11_conn
        win = d.create_resource_object("window", xid)
        root = d.screen().root

        wm_state = d.intern_atom("_NET_WM_STATE")
        above = d.intern_atom("_NET_WM_STATE_ABOVE")
        # data: [action(1=add), prop1, prop2, source(1=application), 0]
        event = protocol.event.ClientMessage(
            window=win,
            client_type=wm_state,
            data=(32, [1, above, 0, 1, 0]),
        )
        root.send_event(
            event,
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )

        if x is not None and y is not None:
            win.configure(x=x, y=y)
        win.configure(stack_mode=X.Above)
        d.flush()
    except Exception as e:
        logger.warning("X11 keep-above failed: %s", e)
        _x11_conn = None


def _x11_surface(window: Gtk.Window):
    """Return the live X11Surface for a window, or None off-X11."""
    surface = window.get_surface()
    if surface is None or GdkX11 is None or not isinstance(
        surface, GdkX11.X11Surface
    ):
        return None
    return surface


def x11_move(window: Gtk.Window, x: int, y: int) -> None:
    """Move a mapped window to absolute screen coordinates (X11 only).

    GTK4 dropped ``Gtk.Window.move``; the keyboard's drag handle and saved
    position are applied straight through Xlib instead.
    """
    surface = _x11_surface(window)
    if surface is None:
        return
    global _x11_conn
    try:
        from Xlib.display import Display

        if _x11_conn is None:
            _x11_conn = Display()
        win = _x11_conn.create_resource_object("window", surface.get_xid())
        win.configure(x=int(x), y=int(y))
        _x11_conn.flush()
    except Exception as e:
        logger.warning("X11 move failed: %s", e)
        _x11_conn = None


def x11_move_resize(
    window: Gtk.Window, x: int, y: int, width: int, height: int
) -> None:
    """Move and resize a mapped window via Xlib (X11 only).

    GTK4 sizes top-levels to their content and offers no ``resize()``;
    forcing the X11 geometry resizes the surface and GTK reallocates its
    children to fill it.
    """
    surface = _x11_surface(window)
    if surface is None:
        return
    global _x11_conn
    try:
        from Xlib.display import Display

        if _x11_conn is None:
            _x11_conn = Display()
        win = _x11_conn.create_resource_object("window", surface.get_xid())
        win.configure(
            x=int(x), y=int(y), width=int(width), height=int(height)
        )
        _x11_conn.flush()
    except Exception as e:
        logger.warning("X11 move/resize failed: %s", e)
        _x11_conn = None


def x11_pointer() -> tuple[int, int] | None:
    """Return the absolute pointer position in root coords (X11 only).

    Used to drive drag-to-move/resize without the feedback jitter you get
    from widget-relative gesture offsets when the window moves under the
    finger.
    """
    global _x11_conn
    try:
        from Xlib.display import Display

        if _x11_conn is None:
            _x11_conn = Display()
        data = _x11_conn.screen().root.query_pointer()
        return (data.root_x, data.root_y)
    except Exception as e:
        logger.warning("X11 query pointer failed: %s", e)
        _x11_conn = None
        return None


def x11_button1_down() -> bool:
    """Whether the primary pointer button is currently pressed (X11 only).

    Used as a leak-proof guard for key auto-repeat: a touch press shows up
    as Button1 under KDE's mouse-emulation, so each repeat tick can verify
    the finger is still down and stop the moment it lifts — even if the
    GestureClick release/cancel event was lost. Returns False on any error,
    which safely stops repeating.
    """
    global _x11_conn
    try:
        from Xlib import X
        from Xlib.display import Display

        if _x11_conn is None:
            _x11_conn = Display()
        data = _x11_conn.screen().root.query_pointer()
        return bool(data.mask & X.Button1Mask)
    except Exception as e:
        logger.warning("X11 query button failed: %s", e)
        _x11_conn = None
        return False


def x11_get_geometry(window: Gtk.Window) -> tuple[int, int] | None:
    """Return the current (x, y) position of an X11 top-level window."""
    surface = _x11_surface(window)
    if surface is None:
        return None
    global _x11_conn
    try:
        from Xlib.display import Display

        if _x11_conn is None:
            _x11_conn = Display()
        win = _x11_conn.create_resource_object("window", surface.get_xid())
        geo = win.get_geometry()
        return (geo.x, geo.y)
    except Exception as e:
        logger.warning("X11 get geometry failed: %s", e)
        _x11_conn = None
        return None


def using_layer_shell() -> bool:
    """Return True if gtk4-layer-shell is active."""
    return _HAS_LAYER_SHELL


def layer_shell_get_margins(window: Gtk.Window) -> tuple[int, int]:
    """Get current (top_margin, left_margin) for a layer-shell window."""
    if not _HAS_LAYER_SHELL or LayerShell is None:
        return (0, 0)
    top = LayerShell.get_margin(window, LayerShell.Edge.TOP)
    left = LayerShell.get_margin(window, LayerShell.Edge.LEFT)
    return (top, left)


def layer_shell_set_margins(window: Gtk.Window, top: int, left: int) -> None:
    """Set margins for a layer-shell window (used for drag on Wayland)."""
    if not _HAS_LAYER_SHELL or LayerShell is None:
        return
    LayerShell.set_margin(window, LayerShell.Edge.TOP, top)
    LayerShell.set_margin(window, LayerShell.Edge.LEFT, left)
