"""Simulate paste keystrokes via /dev/uinput (kernel-level injection).

Works on both Wayland and X11 because the events enter the input stack
below the display server, exactly like a real keyboard. This is the only
reliable path on SteamOS's Plasma Wayland desktop, where xdotool cannot
reach native Wayland windows and ydotool/wtype are not shipped.

Requires write access to /dev/uinput. On SteamOS the deck user gets it
via an ACL; inside Flatpak it additionally needs ``--device=all``
(``--device=input`` only exposes /dev/input/event*).

Only ctypes and the stdlib are used -- no python-evdev dependency.
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
import struct
import time

logger = logging.getLogger(__name__)

_UINPUT_PATH = "/dev/uinput"

# input event constants (linux/input-event-codes.h)
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_V = 47
KEY_Y = 21

# Map a paste chord's letter to its Linux input keycode. Shared with the
# ydotool backend in paste_helper, which uses the same keycodes.
LETTER_KEYCODES = {"v": KEY_V, "y": KEY_Y}

# uinput ioctls (linux/uinput.h)
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503

# struct uinput_setup { struct input_id id; char name[80]; __u32 ff_effects_max; }
# struct input_id { __u16 bustype, vendor, product, version; }
_BUS_VIRTUAL = 0x06


class _InputEvent(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_long),
        ("tv_usec", ctypes.c_long),
        ("type", ctypes.c_uint16),
        ("code", ctypes.c_uint16),
        ("value", ctypes.c_int32),
    ]


class UinputPaster:
    """Create a transient virtual keyboard and tap Ctrl(+Shift)+V."""

    @staticmethod
    def is_available() -> bool:
        try:
            fd = os.open(_UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            return True
        except OSError:
            return False

    @staticmethod
    def paste(letter: str = "v", use_shift: bool = False) -> bool:
        """Inject the paste chord ``Ctrl(+Shift)+letter``. Returns True on success."""
        key = LETTER_KEYCODES[letter]
        try:
            fd = os.open(_UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            logger.warning("uinput open failed: %s", e)
            return False
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for k in (KEY_LEFTCTRL, KEY_LEFTSHIFT, key):
                fcntl.ioctl(fd, UI_SET_KEYBIT, k)

            setup = struct.pack(
                "HHHH80sI",
                _BUS_VIRTUAL, 0x1, 0x1, 1,
                b"doubao-murmur paste",
                0,
            )
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)

            # Give the compositor a moment to pick up the new device;
            # without this the first events can be dropped.
            time.sleep(0.2)

            keys_down = [KEY_LEFTCTRL]
            if use_shift:
                keys_down.append(KEY_LEFTSHIFT)
            keys_down.append(key)

            for key in keys_down:
                UinputPaster._emit(fd, EV_KEY, key, 1)
                UinputPaster._emit(fd, EV_SYN, SYN_REPORT, 0)
                time.sleep(0.02)
            for key in reversed(keys_down):
                UinputPaster._emit(fd, EV_KEY, key, 0)
                UinputPaster._emit(fd, EV_SYN, SYN_REPORT, 0)
                time.sleep(0.02)

            # Let the events flush before the device disappears.
            time.sleep(0.1)
            fcntl.ioctl(fd, UI_DEV_DESTROY)
            return True
        except OSError as e:
            logger.warning("uinput paste failed: %s", e)
            return False
        finally:
            os.close(fd)

    @staticmethod
    def _emit(fd: int, ev_type: int, code: int, value: int) -> None:
        event = _InputEvent(0, 0, ev_type, code, value)
        os.write(fd, bytes(event))
