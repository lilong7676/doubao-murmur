"""System tray / control surface.

GTK4 removed GtkStatusIcon and AyatanaAppIndicator3 needs GTK3 menus, so
the tray icon is implemented over raw DBus (StatusNotifierItem — see
sni_tray.py), which KDE Plasma and most other trays speak natively.

A small control window backs it up: it is the click target of the tray
icon, the fallback UI when no StatusNotifierWatcher is running (e.g. stock
GNOME), and what a second app launch brings up.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from doubao_murmur.app_state import AppState, LoginStatus
from doubao_murmur.ui.sni_tray import SniTray

logger = logging.getLogger(__name__)

_APP_ICON = "com.doubao.Murmur"
_FALLBACK_ICON = "audio-input-microphone"
_KEYBOARD_HOTKEY_LABEL = "Ctrl+Super+Shift 呼出软键盘"


class TrayIcon:
    """System tray icon (SNI) plus control window."""

    def __init__(
        self,
        app_state: AppState,
        on_login_clicked,
        on_logout_clicked,
        on_quit_clicked,
        on_help_clicked=None,
        on_keyboard_clicked=None,
        keyboard_hotkey_enabled=None,
        on_keyboard_hotkey_toggled=None,
    ) -> None:
        self.app_state = app_state
        self._on_login_clicked = on_login_clicked
        self._on_logout_clicked = on_logout_clicked
        self._on_quit_clicked = on_quit_clicked
        self._on_help_clicked = on_help_clicked
        self._on_keyboard_clicked = on_keyboard_clicked
        self._keyboard_hotkey_enabled = keyboard_hotkey_enabled
        self._on_keyboard_hotkey_toggled = on_keyboard_hotkey_toggled
        self._keyboard_hotkey_check: Gtk.CheckButton | None = None
        self._sni: SniTray | None = None
        self._window: Gtk.Window | None = None
        self._status_label: Gtk.Label | None = None
        self._primary_button: Gtk.Button | None = None

    def start(self) -> None:
        """Create the control window and (if possible) the tray icon."""
        self._build_control_window()
        self._start_sni()
        self.app_state.connect(
            "login-status-changed", lambda *_: self._refresh()
        )
        self._refresh()
        # Start minimized: only pop up when user action is required
        # (not logged in). Re-activating the app (launching it again)
        # or clicking the tray icon presents the window.
        if self.app_state.login_status != LoginStatus.LOGGED_IN:
            self._window.present()

    # -- tray icon -----------------------------------------------------------

    def _start_sni(self) -> None:
        try:
            tray = SniTray(
                item_id="doubao-murmur",
                title="Doubao Murmur",
                tooltip="豆包语音输入",
                icon_name=self._pick_icon(),
                on_activate=self.show_window,
            )
            if tray.start():
                self._sni = tray
        except Exception:
            logger.exception("Tray icon unavailable; control window only")

    @staticmethod
    def _pick_icon() -> str:
        """App icon if installed (Flatpak/system), generic mic otherwise."""
        try:
            display = Gdk.Display.get_default()
            if display:
                theme = Gtk.IconTheme.get_for_display(display)
                if theme.has_icon(_APP_ICON):
                    return _APP_ICON
        except Exception:
            pass
        return _FALLBACK_ICON

    def _menu_items(self) -> list[dict | None]:
        logged_in = self.app_state.login_status == LoginStatus.LOGGED_IN
        status_map = {
            LoginStatus.CHECKING: "⏳ 检查中...",
            LoginStatus.LOGGED_IN: "✅ 已登录",
            LoginStatus.NOT_LOGGED_IN: "❌ 未登录",
        }
        items: list[dict | None] = [
            {
                "label": status_map.get(self.app_state.login_status, "⏳"),
                "enabled": False,
            },
            None,
            {
                "label": "退出登录" if logged_in else "登录豆包",
                "callback": (
                    self._on_logout_clicked
                    if logged_in
                    else self._on_login_clicked
                ),
            },
            {"label": "控制面板", "callback": self.show_window},
        ]
        if self._on_keyboard_clicked:
            items.append(
                {"label": "⌨ 软键盘", "callback": self._on_keyboard_clicked}
            )
        if self._has_keyboard_hotkey_toggle():
            enabled = self._keyboard_hotkey_enabled()
            items.append(
                {
                    "label": _KEYBOARD_HOTKEY_LABEL,
                    "checked": enabled,
                    "callback": lambda: self._set_keyboard_hotkey(not enabled),
                }
            )
        if self._on_help_clicked:
            items.append({"label": "使用帮助", "callback": self._on_help_clicked})
        items.append(None)
        items.append({"label": "退出", "callback": self._on_quit_clicked})
        return items

    # -- control window --------------------------------------------------------

    def _build_control_window(self) -> None:
        self._window = Gtk.Window()
        self._window.set_title("Doubao Murmur")
        self._window.set_default_size(320, 180)
        self._window.set_resizable(False)
        self._window.connect("close-request", self._on_control_close)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        self._status_label = Gtk.Label()
        self._status_label.set_xalign(0)
        box.append(self._status_label)

        self._primary_button = Gtk.Button()
        self._primary_button.connect("clicked", self._on_primary_clicked)
        box.append(self._primary_button)

        if self._on_keyboard_clicked:
            keyboard_button = Gtk.Button(label="⌨ 软键盘")
            keyboard_button.connect(
                "clicked", lambda _: self._on_keyboard_clicked()
            )
            box.append(keyboard_button)

        if self._has_keyboard_hotkey_toggle():
            self._keyboard_hotkey_check = Gtk.CheckButton(
                label=_KEYBOARD_HOTKEY_LABEL
            )
            self._keyboard_hotkey_check.set_active(
                self._keyboard_hotkey_enabled()
            )
            self._keyboard_hotkey_check.connect(
                "toggled", lambda b: self._set_keyboard_hotkey(b.get_active())
            )
            box.append(self._keyboard_hotkey_check)

        if self._on_help_clicked:
            help_button = Gtk.Button(label="使用帮助")
            help_button.connect("clicked", lambda _: self._on_help_clicked())
            box.append(help_button)

        quit_button = Gtk.Button(label="退出")
        quit_button.connect("clicked", lambda _: self._on_quit_clicked())
        box.append(quit_button)

        self._window.set_child(box)

    def _refresh(self) -> None:
        """Sync tray menu and control window with the current state."""
        if self._sni:
            self._sni.set_menu(self._menu_items())

        status_map = {
            LoginStatus.CHECKING: "状态：检查中...",
            LoginStatus.LOGGED_IN: "状态：已登录",
            LoginStatus.NOT_LOGGED_IN: "状态：未登录",
        }
        if self._status_label:
            self._status_label.set_text(
                status_map.get(self.app_state.login_status, "状态：检查中...")
            )
        if self._primary_button:
            if self.app_state.login_status == LoginStatus.LOGGED_IN:
                self._primary_button.set_label("退出登录")
            else:
                self._primary_button.set_label("登录豆包")
        if self._keyboard_hotkey_check:
            # set_active only emits "toggled" on an actual change, so this
            # can't loop back through _set_keyboard_hotkey.
            self._keyboard_hotkey_check.set_active(
                self._keyboard_hotkey_enabled()
            )

    def _has_keyboard_hotkey_toggle(self) -> bool:
        return bool(
            self._keyboard_hotkey_enabled and self._on_keyboard_hotkey_toggled
        )

    def _set_keyboard_hotkey(self, enabled: bool) -> None:
        """Shared by the tray checkbox and the control window checkbox."""
        if enabled != self._keyboard_hotkey_enabled():
            self._on_keyboard_hotkey_toggled(enabled)
        self._refresh()

    def _on_primary_clicked(self, _button) -> None:
        if self.app_state.login_status == LoginStatus.LOGGED_IN:
            self._on_logout_clicked()
        else:
            self._on_login_clicked()

    def show_window(self) -> None:
        """Present the control window (tray click / app re-activation)."""
        if self._window:
            self._window.present()

    def _on_control_close(self, _window) -> bool:
        # Hide instead of quit — the app keeps running for the hotkey.
        # Quit via the tray menu or the window's 退出 button.
        self._window.set_visible(False)
        return True
