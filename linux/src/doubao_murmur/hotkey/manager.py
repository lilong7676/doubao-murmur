"""Hotkey manager: coordinates input methods for triggering recording.

Backends, in order of preference:
1. X11/XRecord (x11_listener) — preferred, and the only one that sees
   Steam Input's XTEST-injected controller keys.
2. evdev /dev/input (evdev_listener) — needs the `input` group, plus
   --device=input when sandboxed. The only one that works on a Wayland
   session, where keys sent to native Wayland windows never reach
   XWayland and are therefore invisible to XRecord.

evdev is started on Wayland and as the fallback when XRecord fails to
start. On a pure X11 session with XRecord active it would only re-read
the same keys, so it is skipped.

`overlay_button` is vestigial: the on-screen push-to-talk button was
removed in 6555ffa and app.py passes None. Reviving it would give Wayland
users a trigger that needs no /dev/input access at all — see
hotkey/overlay_button.py.
"""

from __future__ import annotations

import logging
import time

from gi.repository import GLib

from doubao_murmur.config import DEBOUNCE_INTERVAL
from doubao_murmur.session import is_pure_x11_session

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Coordinates input methods for triggering recording."""

    def __init__(self) -> None:
        self.on_toggle = None  # () -> None
        self.on_cancel = None  # () -> None
        self.on_keyboard = None  # () -> None (toggle on-screen keyboard)
        self._overlay_button = None
        self._evdev_listener = None
        self._evdev_factory = None
        self._x11_listener = None
        self._cancel_enabled = False
        self._last_toggle_time = 0.0
        self._last_keyboard_time = 0.0

    def start(
        self,
        overlay_button=None,
        x11_listener=None,
        evdev_factory=None,
    ) -> None:
        """Initialize input backends."""
        self._overlay_button = overlay_button
        self._x11_listener = x11_listener
        self._evdev_factory = evdev_factory

        if self._x11_listener:
            if self._x11_listener.start():
                logger.info("X11 key listener active")
            else:
                # is_available() only probes for the RECORD extension;
                # record_create_context() can still fail here.
                logger.warning("X11 key listener failed to start")
                self._x11_listener = None

        # XRecord already sees every key on a pure X11 session, so evdev
        # would only re-read them. It remains the backend on Wayland and
        # the fallback when XRecord could not start — which only now,
        # after start(), is known.
        if self._x11_listener is None or not is_pure_x11_session():
            self._start_evdev()

    def _start_evdev(self) -> None:
        """Start evdev via the factory, when one is configured."""
        listener = self._evdev_factory() if self._evdev_factory else None
        if listener is None:
            logger.warning("evdev listener unavailable; global hotkey disabled")
            return
        if listener.start():
            self._evdev_listener = listener
            logger.info("evdev listener active")
        else:
            logger.warning("evdev not available (need input group?)")

    def stop(self) -> None:
        """Stop all input backends."""
        if self._x11_listener:
            self._x11_listener.stop()
        if self._evdev_listener:
            self._evdev_listener.stop()

    @property
    def has_global_hotkey(self) -> bool:
        """True when a global hotkey backend is active."""
        return (
            self._evdev_listener is not None
            or self._x11_listener is not None
        )

    def trigger_toggle(self) -> None:
        """Called by input backends, possibly from non-GTK threads.

        Debounced, then marshalled to the GTK main thread — GTK4 is not
        thread-safe and the evdev listener runs on its own thread.
        """
        now = time.monotonic()
        if now - self._last_toggle_time < DEBOUNCE_INTERVAL:
            return
        self._last_toggle_time = now
        GLib.idle_add(self._dispatch_toggle)

    def trigger_cancel(self) -> None:
        """Called by input backends for cancel (ESC)."""
        if self._cancel_enabled:
            GLib.idle_add(self._dispatch_cancel)

    def trigger_keyboard(self) -> None:
        """Called by input backends for the on-screen-keyboard hotkey.

        Debounced so key auto-repeat (holding the chord) toggles once.
        """
        now = time.monotonic()
        if now - self._last_keyboard_time < DEBOUNCE_INTERVAL:
            return
        self._last_keyboard_time = now
        GLib.idle_add(self._dispatch_keyboard)

    def _dispatch_keyboard(self) -> bool:
        if self.on_keyboard:
            self.on_keyboard()
        return GLib.SOURCE_REMOVE

    def _dispatch_toggle(self) -> bool:
        if self.on_toggle:
            self.on_toggle()
        return GLib.SOURCE_REMOVE

    def _dispatch_cancel(self) -> bool:
        if self._cancel_enabled and self.on_cancel:
            self.on_cancel()
        return GLib.SOURCE_REMOVE

    def set_cancel_enabled(self, enabled: bool) -> None:
        self._cancel_enabled = enabled
