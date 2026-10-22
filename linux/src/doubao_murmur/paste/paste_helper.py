"""Copy text to clipboard and simulate Ctrl+V paste.

Mirrors PasteHelper.swift.

Clipboard backends, in priority order:
1. wl-copy (Wayland)
2. xclip, then xsel (X11)
3. GTK clipboard API (last resort)

Paste-simulation backends, first one that works wins:
1. xdotool on a pure X11 session (honors the user's XKB mapping)
2. uinput (kernel-level; needs write access to /dev/uinput)
3. ydotool
4. wtype
5. xdotool, as the last resort for X11 targets in a mixed session
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

from doubao_murmur.config import (
    PASTE_DELAY,
    PASTE_FOCUS_DELAY,
    PASTE_KEY_DELAY_MS,
    X11_SYNC_TIMEOUT,
)
from doubao_murmur.host_tools import command_candidates
from doubao_murmur.paste.hyprland_window import (
    active_window_class as hyprland_active_window_class,
)
from doubao_murmur.paste.kwin_window import active_window_class as kwin_active_window_class
from doubao_murmur.paste.uinput_injector import (
    KEY_LEFTCTRL,
    KEY_LEFTSHIFT,
    LETTER_KEYCODES,
    UinputPaster,
)
from doubao_murmur.session import is_pure_x11_session

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

    _x11_target_window: str | None = None

    @staticmethod
    def remember_focused_window() -> None:
        """Remember the X11 window that should receive the next paste.

        The recording overlay is mapped after this call. Some window managers
        (notably XFCE) may focus that GTK window despite its no-focus hints, so
        relying on whichever window is active at completion can paste back into
        Doubao Murmur itself.
        """
        PasteHelper._x11_target_window = None
        if not is_pure_x11_session():
            return
        for command in command_candidates("xdotool"):
            try:
                result = subprocess.run(
                    command + ["getactivewindow"],
                    capture_output=True,
                    check=True,
                    timeout=3,
                )
                window_id = result.stdout.decode().strip()
                if window_id.isdigit():
                    PasteHelper._x11_target_window = window_id
                    logger.info("Remembered X11 paste target: %s", window_id)
                    return
            except Exception as e:
                logger.warning("Could not remember X11 paste target: %s", e)

    @staticmethod
    def forget_focused_window() -> None:
        PasteHelper._x11_target_window = None

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
        # Try Wayland first, except in a known pure-X11 session where wl-copy
        # can only emit connection errors and delay the real xclip path.
        if not is_pure_x11_session():
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
        """Simulate the paste keystroke for the focused window."""

        # Give slow-to-focus applications (notably Electron) a beat to route
        # input focus to the editor widget before the synthetic chord lands.
        # Only do that after re-activating the remembered target: when one of
        # our own windows did not take the focus away, the user has already
        # chosen the target by clicking it and the chord must land there.
        if (PasteHelper._focus_is_our_window()
                and PasteHelper._restore_focused_window()):
            time.sleep(PASTE_FOCUS_DELAY)

        letter, use_shift = PasteHelper._paste_chord()
        # On a pure X11 session, xdotool resolves the logical Control_L
        # keysym through the active XKB map. This matters when users swap
        # CapsLock and Ctrl (ctrl:swapcaps): the Linux keycode 29 used by
        # uinput/ydotool is the physical left-Ctrl position, which XKB then
        # interprets as CapsLock. Do not let that lower-level fallback win on
        # X11 when xdotool can honor the user's logical key mapping.
        if is_pure_x11_session():
            if PasteHelper._simulate_paste_with_xdotool(letter, use_shift):
                return

        # Try uinput first: kernel-level injection that works on both
        # Wayland and X11 with no external tools. Gated on /dev/uinput
        # write access (SteamOS grants it; most distros do not by default).
        if UinputPaster.is_available():
            if UinputPaster.paste(letter, use_shift=use_shift):
                logger.info("Paste simulated via uinput")
                return
            logger.warning("uinput paste failed, falling back")

        # Try ydotool (works on both Wayland and X11)
        # Keycodes: 29=LEFTCTRL, 42=LEFTSHIFT; letter code from LETTER_KEYCODES.
        ctrl, shift = KEY_LEFTCTRL, KEY_LEFTSHIFT
        letter_code = LETTER_KEYCODES[letter]
        if use_shift:
            ydotool_keys = [f"{ctrl}:1", f"{shift}:1", f"{letter_code}:1",
                            f"{letter_code}:0", f"{shift}:0", f"{ctrl}:0"]
        else:
            ydotool_keys = [f"{ctrl}:1", f"{letter_code}:1",
                            f"{letter_code}:0", f"{ctrl}:0"]
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
            wtype_args = ["-M", "ctrl", "-M", "shift", "-P", letter,
                          "-m", "shift", "-m", "ctrl"]
        else:
            wtype_args = ["-M", "ctrl", "-P", letter, "-m", "ctrl"]
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

        # Try xdotool (X11 only). This is also the final fallback for mixed
        # sessions where the native target may still be an X11 window.
        if PasteHelper._simulate_paste_with_xdotool(letter, use_shift):
            return

        logger.error("No paste simulation method available")
        logger.info(
            "Text was copied to clipboard but could not auto-paste. "
            "Install ydotool or wtype for auto-paste."
        )

    @staticmethod
    def _simulate_paste_with_xdotool(letter: str, use_shift: bool) -> bool:
        """Try to send the paste shortcut through the active X11 keymap.

        The chord is sent with an explicit inter-key delay: Electron/Chromium
        applications can drop an XTEST chord whose Ctrl and letter arrive in
        the same instant, so a short pause lets them register the modifier.
        """
        xdotool_key = "ctrl+shift+" + letter if use_shift else "ctrl+" + letter
        for command in command_candidates("xdotool"):
            try:
                subprocess.run(
                    command + ["key", "--delay", str(PASTE_KEY_DELAY_MS), xdotool_key],
                    check=True,
                    timeout=3,
                )
                logger.info("Paste simulated via xdotool (%s)", xdotool_key)
                return True
            except Exception as e:
                logger.warning("xdotool failed: %s", e)
        return False

    @staticmethod
    def _restore_focused_window() -> bool:
        """Reactivate the X11 window captured before the overlay appeared."""
        window_id = PasteHelper._x11_target_window
        if not window_id or not is_pure_x11_session():
            return False
        for command in command_candidates("xdotool"):
            # xdotool sends the activation request before it starts polling the
            # --sync wait, so a timeout means we stopped waiting, not that the
            # request never went out. Keep going either way: the explicit focus
            # below is exactly what the stalled case needs.
            try:
                subprocess.run(
                    command + ["windowactivate", "--sync", window_id],
                    check=True,
                    timeout=X11_SYNC_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning("windowactivate was not confirmed: %s", e)
            # windowactivate raises and focuses the frame, but Electron apps
            # can leave input focus on the overlay; an explicit focus call
            # targets the editor widget. Best-effort: some windows reject
            # windowfocus, which must not abort the paste.
            try:
                subprocess.run(
                    command + ["windowfocus", "--sync", window_id],
                    check=False,
                    timeout=X11_SYNC_TIMEOUT,
                )
                logger.info("Restored X11 paste target: %s", window_id)
                return True
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning("Could not restore X11 paste target: %s", e)
        return False

    @staticmethod
    def _focus_is_our_window() -> bool:
        """Whether the active X11 window belongs to this process.

        `_restore_focused_window()` re-activates the remembered target, which
        is only the right thing when one of our own windows took the focus
        away: the recording overlay is mapped without input hints, but XFCE
        and some other window managers still focus it. When the user presses
        the hotkey first and then clicks the window to paste into, the active
        window is somebody else's, and stealing the focus back would undo
        that choice.

        `_NET_WM_PID` is used instead of the window class because a plain
        Gtk.Window derives its class from `g_get_prgname()`: "doubao-murmur"
        when installed, but "python3" when run from a source checkout.
        Unreadable values count as "ours" so a broken probe falls back to the
        unconditional restore instead of silently disabling auto-paste.
        """
        if not is_pure_x11_session():
            return False
        for command in command_candidates("xdotool"):
            try:
                result = subprocess.run(
                    command + ["getactivewindow", "getwindowpid"],
                    capture_output=True,
                    check=True,
                    timeout=3,
                )
                pid = result.stdout.decode().strip()
                # xdotool prints 0 for windows without _NET_WM_PID.
                if pid.isdigit() and int(pid) > 0:
                    return int(pid) == os.getpid()
            except Exception as e:
                logger.debug("Active window pid lookup failed: %s", e)
        return True

    @staticmethod
    def _paste_chord() -> tuple[str, bool]:
        """Return the paste chord for the focused window as ``(letter, use_shift)``.

        Terminals swallow Ctrl+V (their paste is Ctrl+Shift+V); everything
        else uses Ctrl+V.
        """
        wm_classes = PasteHelper._focused_window_classes()
        if not wm_classes:
            return "v", False
        # Wayland resourceClass may be a reverse-DNS app id like
        # "org.kde.konsole"; also match on the last dot-segment.
        candidates = set(wm_classes)
        for c in wm_classes:
            candidates.add(c.rsplit(".", 1)[-1])
        if candidates & _TERMINAL_WM_CLASSES:
            chord = "ctrl+shift+v"
            result = "v", True
        else:
            chord = "ctrl+v"
            result = "v", False
        logger.info(
            "Focused window class: %s (chord=%s)",
            "/".join(wm_classes),
            chord,
        )
        return result

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
