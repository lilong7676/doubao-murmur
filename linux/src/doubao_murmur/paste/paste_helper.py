"""Copy text to clipboard and simulate Ctrl+V paste.

Mirrors PasteHelper.swift.

Methods (in priority order):
1. wl-copy (Wayland clipboard) + uinput (kernel-level paste simulation)
2. ydotool/wtype (paste simulation)
3. xclip/xsel (X11 clipboard) + xdotool (X11 paste simulation)
4. GTK clipboard API as last resort
"""

from __future__ import annotations

import logging
import subprocess
import time

from doubao_murmur.config import PASTE_DELAY
from doubao_murmur.host_tools import command_candidates
from doubao_murmur.paste.hyprland_window import (
    active_window_class as hyprland_active_window_class,
)
from doubao_murmur.paste.kwin_window import active_window_class as kwin_active_window_class
from doubao_murmur.paste.uinput_injector import UinputPaster

logger = logging.getLogger(__name__)

# Terminal emulators interpret Ctrl+V as a control sequence; their paste
# shortcut is Ctrl+Shift+V instead. Matched against the focused window's
# WM class (lowercased).
_TERMINAL_WM_CLASSES = {
    "konsole",
    "yakuake",
    "alacritty",
    "kitty",
    "foot",
    "wezterm",
    "org.wezfurlong.wezterm",
    "gnome-terminal-server",
    "xterm",
    "urxvt",
    "st",
    "terminator",
    "tilix",
    "xfce4-terminal",
    "lxterminal",
    "deepin-terminal",
    "qterminal",
    "io.elementary.terminal",
    "ghostty",
    "com.mitchellh.ghostty",
    "warp",
    "warp-terminal",
    "dev.warp.warp",
}


class PasteHelper:
    """Copy text to clipboard and simulate paste keystroke."""

    @staticmethod
    def copy_and_paste(text: str) -> None:
        if not text:
            return
        PasteHelper._copy_to_clipboard(text)
        time.sleep(PASTE_DELAY)
        PasteHelper._simulate_paste()

    @staticmethod
    def copy_only(text: str) -> None:
        if not text:
            return
        PasteHelper._copy_to_clipboard(text)

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        """Copy text to system clipboard."""
        # Try Wayland first
        for command in command_candidates("wl-copy"):
            try:
                subprocess.run(
                    command,
                    input=text.encode(),
                    check=True,
                    timeout=3,
                )
                logger.info("Copied to clipboard via wl-copy")
                return
            except Exception as e:
                logger.warning("wl-copy failed: %s", e)

        # Try X11
        for command in command_candidates("xclip"):
            try:
                subprocess.run(
                    command + ["-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                    timeout=3,
                )
                logger.info("Copied to clipboard via xclip")
                return
            except Exception as e:
                logger.warning("xclip failed: %s", e)

        # Try xsel
        for command in command_candidates("xsel"):
            try:
                subprocess.run(
                    command + ["--clipboard", "--input"],
                    input=text.encode(),
                    check=True,
                    timeout=3,
                )
                logger.info("Copied to clipboard via xsel")
                return
            except Exception as e:
                logger.warning("xsel failed: %s", e)

        # GTK clipboard as last resort
        try:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                clipboard.set(text)
                logger.info("Copied to clipboard via GTK")
        except Exception as e:
            logger.error("All clipboard methods failed: %s", e)

    @staticmethod
    def _simulate_paste() -> None:
        """Simulate the paste keystroke for the focused window.

        Terminals use Ctrl+Shift+V; everything else uses Ctrl+V.
        """
        use_shift = PasteHelper._focused_window_is_terminal()

        # Try uinput first: kernel-level injection that works on both
        # Wayland and X11 with no external tools. Gated on /dev/uinput
        # write access (SteamOS grants it; most distros do not by default).
        if UinputPaster.is_available():
            if UinputPaster.paste(use_shift=use_shift):
                logger.info("Paste simulated via uinput")
                return
            logger.warning("uinput paste failed, falling back")

        # Try ydotool (works on both Wayland and X11)
        # Keycodes: 29=LEFTCTRL, 42=LEFTSHIFT, 47=V
        if use_shift:
            ydotool_keys = ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
        else:
            ydotool_keys = ["29:1", "47:1", "47:0", "29:0"]
        for command in command_candidates("ydotool"):
            try:
                subprocess.run(
                    command + ["key"] + ydotool_keys,
                    check=True,
                    timeout=3,
                )
                logger.info("Paste simulated via ydotool")
                return
            except Exception as e:
                logger.warning("ydotool failed: %s", e)

        # Try wtype (Wayland virtual keyboard)
        if use_shift:
            wtype_args = ["-M", "ctrl", "-M", "shift", "-P", "v",
                          "-m", "shift", "-m", "ctrl"]
        else:
            wtype_args = ["-M", "ctrl", "-P", "v", "-m", "ctrl"]
        for command in command_candidates("wtype"):
            try:
                subprocess.run(
                    command + wtype_args,
                    check=True,
                    timeout=3,
                )
                logger.info("Paste simulated via wtype")
                return
            except Exception as e:
                logger.warning("wtype failed: %s", e)

        # Try xdotool (X11 only)
        xdotool_key = "ctrl+shift+v" if use_shift else "ctrl+v"
        for command in command_candidates("xdotool"):
            try:
                subprocess.run(
                    command + ["key", xdotool_key],
                    check=True,
                    timeout=3,
                )
                logger.info("Paste simulated via xdotool (%s)", xdotool_key)
                return
            except Exception as e:
                logger.warning("xdotool failed: %s", e)

        logger.error("No paste simulation method available")
        logger.info(
            "Text was copied to clipboard but could not auto-paste. "
            "Install ydotool or wtype for auto-paste."
        )

    @staticmethod
    def _focused_window_is_terminal() -> bool:
        """Check whether the focused window is a terminal emulator."""
        wm_classes = PasteHelper._focused_window_classes()
        if not wm_classes:
            return False
        # Wayland resourceClass may be a reverse-DNS app id like
        # "org.kde.konsole"; also match on the last dot-segment.
        candidates = set(wm_classes)
        for c in wm_classes:
            candidates.add(c.rsplit(".", 1)[-1])
        is_terminal = bool(candidates & _TERMINAL_WM_CLASSES)
        logger.info(
            "Focused window class: %s (terminal=%s)",
            "/".join(wm_classes),
            is_terminal,
        )
        return is_terminal

    @staticmethod
    def _focused_window_classes() -> list[str]:
        """Lowercased WM_CLASS entries of the focused window.

        On Hyprland or KDE Plasma Wayland, X11 tools cannot see the active
        window, so ask the compositor first via its native control surface.

        `getwindowclassname` only exists in recent xdotool releases;
        Debian/Ubuntu still ship 3.20160805, where it exits with
        "Unknown command". Detection then always failed, so every paste
        used Ctrl+V -- which terminals swallow instead of pasting. Fall
        back to xprop, which lives in x11-utils and is present on any
        desktop that has xdotool.
        """
        hyprland_class = hyprland_active_window_class()
        if hyprland_class:
            return [hyprland_class]

        kwin_class = kwin_active_window_class()
        if kwin_class:
            return [kwin_class]

        for command in command_candidates("xdotool"):
            try:
                result = subprocess.run(
                    command + ["getactivewindow", "getwindowclassname"],
                    capture_output=True,
                    check=True,
                    timeout=3,
                )
                wm_class = result.stdout.decode().strip().lower()
                if wm_class:
                    return [wm_class]
            except Exception as e:
                logger.debug("xdotool getwindowclassname failed: %s", e)

        window_id = ""
        for command in command_candidates("xdotool"):
            try:
                result = subprocess.run(
                    command + ["getactivewindow"],
                    capture_output=True,
                    check=True,
                    timeout=3,
                )
                window_id = result.stdout.decode().strip()
                break
            except Exception as e:
                logger.warning("Active window detection failed: %s", e)
        if not window_id:
            return []

        for command in command_candidates("xprop"):
            try:
                result = subprocess.run(
                    command + ["-id", window_id, "WM_CLASS"],
                    capture_output=True,
                    check=True,
                    timeout=3,
                )
                # WM_CLASS(STRING) = "terminator", "Terminator"
                values = result.stdout.decode().partition("=")[2]
                return [
                    part.strip().strip('"').lower()
                    for part in values.split(",")
                    if part.strip()
                ]
            except Exception as e:
                logger.warning("xprop WM_CLASS lookup failed: %s", e)
        return []
