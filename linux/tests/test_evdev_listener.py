"""Tests for evdev device filtering."""

from unittest.mock import patch

import pytest

from doubao_murmur.hotkey import evdev_listener
from doubao_murmur.hotkey.evdev_listener import (
    EV_KEY,
    KEY_ESC,
    KEY_RIGHTALT,
    EvdevListener,
)

EV_ABS = 0x03
KEY_LEFTCTRL = 29
BTN_LEFT = 0x110
BTN_SOUTH = 0x130


def _bitmap(length, *codes):
    bits = bytearray(length)
    for code in codes:
        bits[code // 8] |= 1 << (code % 8)
    return bits


def _is_keyboard(event_types, keys, error=None):
    event_bits = _bitmap(evdev_listener._EV_BITS_LEN, *event_types)
    key_bits = _bitmap(evdev_listener._KEY_BITS_LEN, *keys)

    def fake_ioctl(_fd, request, buffer, _mutate):
        if error is not None:
            raise error
        source = (
            event_bits
            if request == evdev_listener._EVIOCGBIT_EV
            else key_bits
        )
        buffer[:] = source
        return 0

    with patch(
        "doubao_murmur.hotkey.evdev_listener.fcntl.ioctl",
        side_effect=fake_ioctl,
    ):
        return EvdevListener._device_is_keyboard(3)


@pytest.mark.parametrize("key", [KEY_ESC, KEY_RIGHTALT])
def test_keyboard_keys_are_kept(key):
    assert _is_keyboard([EV_KEY], [key]) is True


def test_other_keyboard_keys_are_not_enough():
    assert _is_keyboard([EV_KEY], [KEY_LEFTCTRL]) is False


@pytest.mark.parametrize("button", [BTN_LEFT, BTN_SOUTH])
def test_pointer_and_gamepad_buttons_are_skipped(button):
    assert _is_keyboard([EV_KEY], [button]) is False


def test_device_without_ev_key_is_skipped():
    assert _is_keyboard([EV_ABS], [KEY_ESC]) is False


def test_ioctl_failure_is_skipped():
    assert _is_keyboard([EV_KEY], [KEY_ESC], error=OSError("unsupported")) is False


def test_find_keyboard_devices_skips_unopenable_and_non_keyboards(monkeypatch):
    paths = [
        "/dev/input/event0",
        "/dev/input/event1",
        "/dev/input/event2",
        "/dev/input/event3",
    ]
    opened = []
    closed = []

    def fake_open(path, _flags):
        opened.append(path)
        if path.endswith("event1"):
            raise PermissionError(path)
        if path.endswith("event3"):
            raise FileNotFoundError(path)
        return 10 if path.endswith("event0") else 12

    monkeypatch.setattr(
        evdev_listener.glob, "glob", lambda _pattern: list(reversed(paths))
    )
    monkeypatch.setattr(evdev_listener.os, "open", fake_open)
    monkeypatch.setattr(evdev_listener.os, "close", closed.append)
    monkeypatch.setattr(
        EvdevListener,
        "_device_is_keyboard",
        staticmethod(lambda fd: fd == 10),
    )

    assert EvdevListener._find_keyboard_devices() == ["/dev/input/event0"]
    assert opened == paths
    assert closed == [10, 12]


@pytest.mark.parametrize(
    ("devices", "expected"),
    [([], False), (["/dev/input/event0"], True)],
)
def test_is_available_uses_keyboard_devices(monkeypatch, devices, expected):
    monkeypatch.setattr(
        EvdevListener,
        "_find_keyboard_devices",
        staticmethod(lambda: devices),
    )

    assert EvdevListener.is_available() is expected
