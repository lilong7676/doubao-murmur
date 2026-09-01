"""Floating overlay window showing recording status and transcription text.

Mirrors OverlayPanel.swift + OverlayView.swift.
"""

from __future__ import annotations

import json
import logging
import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from doubao_murmur.app_state import RecordingState
from doubao_murmur.config import (
    OVERLAY_HEIGHT,
    OVERLAY_MAX_LINES,
    OVERLAY_TEXT_CHARS,
    OVERLAY_WIDTH,
    get_overlay_config_path,
)
from doubao_murmur.ui.windowing import (
    OverlayRole,
    apply_overlay_window_hints,
    layer_shell_get_margins,
    layer_shell_set_margins,
    present_overlay,
    using_layer_shell,
    x11_get_geometry,
    x11_move,
    x11_pointer,
)

logger = logging.getLogger(__name__)

_OVERLAY_CSS = b"""
.overlay-window {
    background: rgba(30, 30, 30, 0.95);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.15);
}
.overlay-label {
    color: white;
    font-size: 14px;
    font-weight: 500;
}
.overlay-label.error {
    color: #ff6666;
}
"""


class Overlay:
    """Floating overlay window showing recording status and transcription text."""

    def __init__(self, app_state) -> None:
        self.app_state = app_state
        self.on_cancel = None  # () -> None
        self._window: Gtk.Window | None = None
        self._label: Gtk.Label | None = None
        self._indicator: Gtk.DrawingArea | None = None
        self._saved_x: int | None = None
        self._saved_y: int | None = None
        self._drag_anchor_pointer: tuple[int, int] | None = None
        self._drag_anchor_pos: tuple[int, int] | None = None
        self._drag_new_pos: tuple[int, int] | None = None
        self._anim_timer: int = 0
        self._load_position()

    def _create_window(self) -> None:
        self._window = Gtk.Window()
        self._window.set_title("Doubao Murmur")
        self._window.set_decorated(False)
        self._window.set_default_size(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self._window.set_resizable(False)
        self._window.set_can_focus(False)
        self._window.set_focusable(False)
        self._window.add_css_class("overlay-window")
        apply_overlay_window_hints(self._window, OverlayRole.STATUS)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self._indicator = Gtk.DrawingArea()
        self._indicator.set_size_request(14, 14)
        self._indicator.set_valign(Gtk.Align.START)
        self._indicator.set_margin_top(2)
        self._indicator.set_draw_func(self._draw_indicator)
        box.append(self._indicator)

        self._label = Gtk.Label()
        self._label.set_xalign(0)
        self._label.set_yalign(0)
        self._label.set_wrap(True)
        self._label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._label.set_width_chars(OVERLAY_TEXT_CHARS)
        self._label.set_max_width_chars(OVERLAY_TEXT_CHARS)
        self._label.set_lines(OVERLAY_MAX_LINES)
        self._label.set_ellipsize(Pango.EllipsizeMode.START)
        self._label.add_css_class("overlay-label")
        box.append(self._label)

        self._window.set_child(box)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self._window.add_controller(key_controller)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._window.add_controller(drag)

        display = Gdk.Display.get_default()
        if display:
            provider = Gtk.CssProvider()
            provider.load_from_data(_OVERLAY_CSS)
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            if self.on_cancel:
                self.on_cancel()
            return True
        return False

    def _draw_indicator(self, area, cr, width, height) -> None:
        state = self.app_state.recording_state
        if state == RecordingState.STARTING:
            angle = (time.time() * 4 * math.pi) % (2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.8)
            cr.set_line_width(2)
            cr.arc(width / 2, height / 2, 5, angle, angle + math.pi * 1.6)
            cr.stroke()
        elif state == RecordingState.RECORDING:
            pulse = 0.5 + 0.5 * math.sin(time.time() * 5)
            cr.set_source_rgba(1, 0.2, 0.2, pulse)
            cr.arc(width / 2, height / 2, 5, 0, 2 * math.pi)
            cr.fill()
        elif state == RecordingState.STOPPING:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            cr.arc(width / 2, height / 2, 5, 0, 2 * math.pi)
            cr.fill()

    def _on_drag_begin(self, _gesture, _x, _y) -> None:
        if using_layer_shell() and self._window:
            self._drag_anchor_pos = layer_shell_get_margins(self._window)
            return
        self._drag_anchor_pointer = x11_pointer()
        self._drag_anchor_pos = x11_get_geometry(self._window) if self._window else None

    def _on_drag_update(self, _gesture, offset_x, offset_y) -> None:
        if not self._window:
            return
        if using_layer_shell():
            if self._drag_anchor_pos is not None:
                base_top, base_left = self._drag_anchor_pos
                new_top = max(0, base_top + int(offset_y))
                new_left = max(0, base_left + int(offset_x))
                layer_shell_set_margins(self._window, new_top, new_left)
            return
        if self._drag_anchor_pointer is None or self._drag_anchor_pos is None:
            return
        cur = x11_pointer()
        if cur is None:
            return
        dx = cur[0] - self._drag_anchor_pointer[0]
        dy = cur[1] - self._drag_anchor_pointer[1]
        gx, gy = self._drag_anchor_pos
        self._drag_new_pos = (gx + dx, gy + dy)
        x11_move(self._window, self._drag_new_pos[0], self._drag_new_pos[1])

    def _on_drag_end(self, _gesture, _offset_x, _offset_y) -> None:
        if self._drag_new_pos is not None:
            self._saved_x, self._saved_y = self._drag_new_pos
            self._drag_new_pos = None
            self._write_position_file(self._saved_x, self._saved_y)
        else:
            self._save_position()
        self._drag_anchor_pointer = None
        self._drag_anchor_pos = None

    def show(self) -> None:
        if not self._window:
            self._create_window()
        self._update_content()
        if self._window:
            if using_layer_shell():
                present_overlay(self._window, OverlayRole.STATUS)
                self._apply_saved_position()
            elif self._saved_x is not None and self._saved_y is not None:
                present_overlay(self._window, OverlayRole.STATUS,
                                x=self._saved_x, y=self._saved_y)
            else:
                present_overlay(self._window, OverlayRole.STATUS)
        if not self._anim_timer:
            self._anim_timer = GLib.timeout_add(33, self._tick_indicator)

    def _tick_indicator(self) -> bool:
        if self._indicator:
            self._indicator.queue_draw()
            return GLib.SOURCE_CONTINUE
        self._anim_timer = 0
        return GLib.SOURCE_REMOVE

    def _apply_saved_position(self) -> None:
        if self._saved_x is None or self._saved_y is None or not self._window:
            return
        if using_layer_shell():
            layer_shell_set_margins(self._window, self._saved_y, self._saved_x)

    def hide(self) -> None:
        if self._anim_timer:
            GLib.source_remove(self._anim_timer)
            self._anim_timer = 0
        if self._window:
            self._window.set_visible(False)

    def update_text(self, text: str) -> None:
        if not self._label:
            return
        if self.app_state.error_message:
            self._label.set_text(self.app_state.error_message)
            self._label.add_css_class("error")
        elif text:
            self._label.set_text(text)
            self._label.remove_css_class("error")
        else:
            self._label.set_text(self._status_text())
            self._label.remove_css_class("error")

    def _update_content(self) -> None:
        self.update_text(self.app_state.transcription_text)

    def _status_text(self) -> str:
        state = self.app_state.recording_state
        return {
            RecordingState.STARTING: "正在启动语音识别...",
            RecordingState.RECORDING: "正在聆听...",
            RecordingState.STOPPING: "正在处理...",
        }.get(state, "")

    def _load_position(self) -> None:
        path = get_overlay_config_path()
        try:
            data = json.loads(path.read_text())
            self._saved_x = int(data["x"])
            self._saved_y = int(data["y"])
            logger.info("Loaded overlay position: %s", data)
        except Exception:
            self._saved_x = None
            self._saved_y = None

    def _save_position(self) -> None:
        if not self._window:
            return
        if using_layer_shell():
            top, left = layer_shell_get_margins(self._window)
            x, y = left, top
        else:
            geo = x11_get_geometry(self._window)
            if geo is None:
                return
            x, y = geo
        self._saved_x, self._saved_y = x, y
        self._write_position_file(x, y)

    def _write_position_file(self, x: int, y: int) -> None:
        path = get_overlay_config_path()
        data = {"x": x, "y": y}
        try:
            path.write_text(json.dumps(data))
        except Exception as e:
            logger.warning("Could not save overlay position: %s", e)
