"""evdev listener for global hotkeys via /dev/input.

OPTIONAL input method. Requires user to be in the 'input' group.
Listens for:
- Right Alt (KEY_RIGHTALT=100) press-and-release -> toggle
- ESC (KEY_ESC=1) -> cancel
"""

from __future__ import annotations

import fcntl
import glob
import logging
import os
import select
import struct
import threading

logger = logging.getLogger(__name__)

# evdev constants
EV_KEY = 0x01
KEY_ESC = 1
KEY_RIGHTALT = 100
EV_MAX = 0x1F
KEY_MAX = 0x2FF

# sizeof(struct input_event) on 64-bit Linux
EVENT_SIZE = 24
EVENT_FORMAT = "llHHi"

# EVIOCGBIT(ev, len) packs an ioctl direction, type, number and byte length
# into the request; see linux/input.h.
_IOC_READ = 2
_EV_BITS_LEN = (EV_MAX // 8) + 1
_KEY_BITS_LEN = (KEY_MAX // 8) + 1


def _eviocgbit(ev: int, length: int) -> int:
    return (
        (_IOC_READ << 30)
        | (length << 16)
        | (ord("E") << 8)
        | (0x20 + ev)
    )


_EVIOCGBIT_EV = _eviocgbit(0, _EV_BITS_LEN)
_EVIOCGBIT_KEY = _eviocgbit(EV_KEY, _KEY_BITS_LEN)


def _bit_is_set(bits: bytearray, bit: int) -> bool:
    return bool(bits[bit // 8] & (1 << (bit % 8)))


class EvdevListener:
    """Reads /dev/input/event* devices for global hotkeys."""

    def __init__(self, on_toggle, on_escape) -> None:
        self.on_toggle = on_toggle
        self.on_escape = on_escape
        self._thread: threading.Thread | None = None
        self._running = False
        self._right_alt_down = False
        self._other_key_pressed = False

    @staticmethod
    def is_available() -> bool:
        """Check if any keyboard evdev device is accessible."""
        return bool(EvdevListener._find_keyboard_devices())

    def start(self) -> bool:
        """Start listening. Returns False if no keyboard devices are found."""
        devices = self._find_keyboard_devices()
        if not devices:
            logger.warning("No accessible keyboard evdev devices found")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop, args=(devices,), daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    @staticmethod
    def _device_is_keyboard(fd: int) -> bool:
        """Whether the device declares a key this listener reacts to.

        `_find_keyboard_devices()` used to accept every readable
        /dev/input/event*, including mice, touchpads, gamepads and audio
        jacks. A continuously-emitting device then kept the Python event
        parser busy and could pin a core. Requiring one of the keys this
        listener actually handles keeps keyboards and drops pointers.
        """
        try:
            ev_bits = bytearray(_EV_BITS_LEN)
            fcntl.ioctl(fd, _EVIOCGBIT_EV, ev_bits, True)
            if not _bit_is_set(ev_bits, EV_KEY):
                return False
            key_bits = bytearray(_KEY_BITS_LEN)
            fcntl.ioctl(fd, _EVIOCGBIT_KEY, key_bits, True)
        except OSError:
            return False
        return (
            _bit_is_set(key_bits, KEY_ESC)
            or _bit_is_set(key_bits, KEY_RIGHTALT)
        )

    @staticmethod
    def _find_keyboard_devices() -> list[str]:
        """Find readable /dev/input/event* devices that are keyboards."""
        devices: list[str] = []
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                if EvdevListener._device_is_keyboard(fd):
                    devices.append(path)
            finally:
                os.close(fd)
        return devices

    def _listen_loop(self, devices: list[str]) -> None:
        """Main read loop using select() for multiplexing."""
        fds: dict[int, str] = {}
        for path in devices:
            try:
                fd = os.open(path, os.O_RDONLY)
                fds[fd] = path
            except Exception as e:
                logger.warning("Cannot open %s: %s", path, e)

        if not fds:
            return

        buf_size = EVENT_SIZE * 16

        try:
            while self._running:
                readable, _, _ = select.select(list(fds.keys()), [], [], 0.5)
                for fd in readable:
                    try:
                        data = os.read(fd, buf_size)
                    except OSError:
                        continue
                    for i in range(0, len(data), EVENT_SIZE):
                        if i + EVENT_SIZE > len(data):
                            break
                        event = struct.unpack(
                            EVENT_FORMAT, data[i : i + EVENT_SIZE]
                        )
                        ev_type = event[2]
                        ev_code = event[3]
                        ev_value = event[4]

                        if ev_type != EV_KEY:
                            continue

                        if ev_code == KEY_RIGHTALT:
                            if ev_value == 1:  # press
                                self._right_alt_down = True
                                self._other_key_pressed = False
                            elif ev_value == 0:  # release
                                if (
                                    self._right_alt_down
                                    and not self._other_key_pressed
                                ):
                                    self.on_toggle()
                                self._right_alt_down = False
                        elif ev_code != KEY_RIGHTALT and ev_value == 1:
                            if self._right_alt_down:
                                self._other_key_pressed = True
                            if ev_code == KEY_ESC:
                                self.on_escape()
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
