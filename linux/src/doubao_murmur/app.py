"""Main GTK Application.

Orchestrates all components and manages the application lifecycle.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from doubao_murmur.app_state import AppState, LoginStatus
from doubao_murmur.hotkey.evdev_listener import EvdevListener
from doubao_murmur.hotkey.manager import HotkeyManager
from doubao_murmur.hotkey.x11_listener import X11KeyListener
from doubao_murmur.keyboard.keyboard_window import KeyboardWindow
from doubao_murmur.params_store import ParamsStore
from doubao_murmur.paste.paste_helper import PasteHelper
from doubao_murmur.transcription import TranscriptionManager
from doubao_murmur.ui.login_window import LoginWindow
from doubao_murmur.ui.overlay import Overlay
from doubao_murmur.ui.tray_icon import TrayIcon

logger = logging.getLogger(__name__)


class DoubaoMurmurApp(Gtk.Application):
    """Main GTK Application."""

    def __init__(self) -> None:
        super().__init__(
            application_id="com.doubao.Murmur",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.app_state = AppState()
        self.login_window: LoginWindow | None = None
        self.overlay: Overlay | None = None
        self.tray_icon: TrayIcon | None = None
        self.hotkey_manager: HotkeyManager | None = None
        self.transcription_manager: TranscriptionManager | None = None
        self.keyboard: KeyboardWindow | None = None
        self._setup_done = False

    def do_activate(self):
        if self._setup_done:
            # Second launch of the single-instance app: surface the
            # control window (it starts hidden when logged in).
            if self.tray_icon:
                self.tray_icon.show_window()
            return
        self._setup_done = True
        # None of our windows are Gtk.ApplicationWindows, so hold the
        # application alive explicitly; released in _quit().
        self.hold()
        self._setup_components()

    def _setup_components(self) -> None:
        # 1. Check for cached params
        if ParamsStore.has_saved():
            self.app_state.login_status = LoginStatus.LOGGED_IN
            logger.info("Cached params found, skipping WebView")
        else:
            self.app_state.login_status = LoginStatus.NOT_LOGGED_IN

        # 2. Create overlay
        self.overlay = Overlay(self.app_state)

        # 3. Create transcription manager
        self.transcription_manager = TranscriptionManager(self.app_state)
        self.transcription_manager.on_auth_expired = self._on_auth_expired
        self.transcription_manager.on_show_login = self._show_login
        self.transcription_manager.on_paste = self._do_paste
        self.transcription_manager.on_overlay_show = self._show_overlay
        self.transcription_manager.on_overlay_hide = self._hide_overlay
        self.transcription_manager.on_overlay_update = self._update_overlay
        self.transcription_manager.on_params_needed = self._extract_params
        self.transcription_manager.on_cancel_enabled_changed = (
            self._on_cancel_enabled_changed
        )
        self.overlay.on_cancel = self.transcription_manager.handle_cancel

        # 4. Create hotkey manager
        self.hotkey_manager = HotkeyManager()
        self.hotkey_manager.on_toggle = self.transcription_manager.handle_toggle
        self.hotkey_manager.on_cancel = self.transcription_manager.handle_cancel
        self.hotkey_manager.on_keyboard = self._toggle_keyboard

        # Prefer the X11 listener: it sees both physical keys and
        # XTEST-injected ones (Steam Input desktop layouts inject
        # controller-mapped keys via XTEST, invisible to evdev).
        # evdev runs as a fallback in case X11 listener fails to start.
        x11 = None
        evdev = None
        if X11KeyListener.is_available():
            x11 = X11KeyListener(
                on_toggle=self.hotkey_manager.trigger_toggle,
                on_escape=self.hotkey_manager.trigger_cancel,
                on_keyboard=self.hotkey_manager.trigger_keyboard,
            )
        if EvdevListener.is_available():
            evdev = EvdevListener(
                on_toggle=self.hotkey_manager.trigger_toggle,
                on_escape=self.hotkey_manager.trigger_cancel,
            )

        self.hotkey_manager.start(
            overlay_button=None,
            evdev_listener=evdev,
            x11_listener=x11,
        )

        # 5. Create on-screen keyboard (lazily shown via tray)
        self.keyboard = KeyboardWindow()

        # 6. Create tray icon
        self.tray_icon = TrayIcon(
            app_state=self.app_state,
            on_login_clicked=self._show_login,
            on_logout_clicked=self._do_logout,
            on_quit_clicked=self._quit,
            on_help_clicked=self._show_help,
            on_keyboard_clicked=self._toggle_keyboard,
        )
        self.tray_icon.start()

        logger.info("All components initialized")

    def _toggle_keyboard(self) -> None:
        if not self.keyboard:
            return
        if not self.keyboard.available():
            dialog = Gtk.MessageDialog(
                transient_for=None,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="软键盘不可用",
            )
            dialog.set_property(
                "secondary-text",
                "需要 xdotool 才能把按键输入到其他窗口。\n"
                "sudo pacman -S xdotool",
            )
            dialog.connect("response", lambda d, _: d.destroy())
            dialog.present()
            return
        self.keyboard.toggle()

    def _show_login(self) -> None:
        if not LoginWindow.is_available():
            dialog = Gtk.MessageDialog(
                transient_for=None,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="WebKitGTK 不可用",
            )
            dialog.set_property(
                "secondary-text",
                "请安装 webkitgtk-6.0 以使用登录功能。\n"
                "sudo pacman -S webkitgtk-6.0",
            )
            dialog.connect("response", lambda d, _: d.destroy())
            dialog.present()
            return

        if not self.login_window:
            self.login_window = LoginWindow(self.app_state)
            self.login_window._on_login_status_change = (
                self._on_login_detected
            )
            self.login_window.load()
        self.login_window.show()

    def _on_login_detected(self, status: str, nickname: str | None) -> None:
        if status == "loggedIn":
            self.app_state.login_status = LoginStatus.LOGGED_IN
            logger.info("Logged in as: %s", nickname)
            self._extract_save_and_destroy_webview()
        else:
            self.app_state.login_status = LoginStatus.NOT_LOGGED_IN

    def _extract_save_and_destroy_webview(self) -> None:
        """Extract params, save, destroy WebView."""

        def on_params(params):
            if params:
                ParamsStore.save(params)
            if self.login_window:
                self.login_window.hide()
                self.login_window.destroy()
                self.login_window = None

        # Delay 1s for cookies to settle
        GLib.timeout_add(
            1000,
            lambda: (
                self.login_window.extract_params_async(on_params)
                if self.login_window
                else None,
                GLib.SOURCE_REMOVE,
            )[1],
        )

    def _extract_params(self, callback) -> None:
        """Called by TranscriptionManager when params are needed."""
        if self.login_window and self.login_window.is_active:
            self.login_window.extract_params_async(callback)
        else:
            callback(None)

    def _do_paste(self, text: str) -> None:
        """Copy and paste transcription."""
        PasteHelper.copy_and_paste(text)

    def _show_overlay(self) -> None:
        PasteHelper.remember_focused_window()
        if self.overlay:
            self.overlay.show()

    def _hide_overlay(self) -> None:
        if self.overlay:
            self.overlay.hide()
        PasteHelper.forget_focused_window()

    def _update_overlay(self, text: str) -> None:
        if self.overlay:
            self.overlay.update_text(text)

    def _on_cancel_enabled_changed(self, enabled: bool) -> None:
        if self.hotkey_manager:
            self.hotkey_manager.set_cancel_enabled(enabled)

    def _on_auth_expired(self) -> None:
        """Show re-login dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="认证已过期",
        )
        dialog.set_property(
            "secondary-text", "豆包登录凭证已失效，是否重新登录？"
        )
        dialog.connect("response", self._on_relogin_response)
        dialog.present()

    def _on_relogin_response(self, dialog, response) -> None:
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self._show_login()

    def _do_logout(self) -> None:
        ParamsStore.clear()
        self.app_state.login_status = LoginStatus.NOT_LOGGED_IN
        if self.login_window:
            self.login_window.logout()
        elif LoginWindow.is_available():
            self.login_window = LoginWindow(self.app_state)
            self.login_window._on_login_status_change = (
                self._on_login_detected
            )
            self.login_window.load()
            self.login_window.logout()

    def _show_help(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="使用帮助",
        )
        dialog.set_property(
            "secondary-text",
            "1. 点击 🎤 按钮开始录音\n"
            "2. 说话时文字会实时显示\n"
            "3. 再次点击 🎤 按钮停止录音\n"
            "4. 识别结果会自动粘贴到当前输入框\n\n"
            "按 ESC 键可取消当前录音\n\n"
            "快捷键：\n"
            "  右 Alt 键：切换录音\n"
            "  ESC 键：取消录音\n"
            "  Ctrl + Super + Shift：显示 / 隐藏软键盘",
        )
        dialog.connect("response", lambda d, _: d.destroy())
        dialog.present()

    def _quit(self) -> None:
        """Clean shutdown."""
        if self.transcription_manager:
            self.transcription_manager.handle_cancel()
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        self.release()
        self.quit()
