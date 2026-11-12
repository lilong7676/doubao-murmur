"""Tests for hotkey backend selection."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from doubao_murmur.app import DoubaoMurmurApp
from doubao_murmur.hotkey.manager import HotkeyManager


class FakeListener:
    def __init__(self, start_ok=True):
        self.start_ok = start_ok
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return self.start_ok

    def stop(self):
        self.stopped = True


def _start_manager(monkeypatch, *, pure_x11, x11=None, factory=None):
    monkeypatch.setattr(
        "doubao_murmur.hotkey.manager.is_pure_x11_session",
        lambda: pure_x11,
    )
    manager = HotkeyManager()
    manager.start(x11_listener=x11, evdev_factory=factory)
    return manager


class TestBackendSelection:
    def test_pure_x11_with_working_xrecord_skips_evdev(self, monkeypatch):
        x11 = FakeListener()
        factory = Mock()
        manager = _start_manager(
            monkeypatch, pure_x11=True, x11=x11, factory=factory
        )

        assert x11.started is True
        factory.assert_not_called()
        assert manager._evdev_listener is None
        assert manager.has_global_hotkey is True

    def test_pure_x11_falls_back_when_xrecord_fails(self, monkeypatch):
        x11 = FakeListener(start_ok=False)
        evdev = FakeListener()
        factory = Mock(return_value=evdev)
        manager = _start_manager(
            monkeypatch, pure_x11=True, x11=x11, factory=factory
        )

        factory.assert_called_once_with()
        assert evdev.started is True
        assert manager._x11_listener is None
        assert manager._evdev_listener is evdev
        assert manager.has_global_hotkey is True

    def test_wayland_starts_evdev_even_when_xrecord_works(self, monkeypatch):
        x11 = FakeListener()
        evdev = FakeListener()
        factory = Mock(return_value=evdev)
        manager = _start_manager(
            monkeypatch, pure_x11=False, x11=x11, factory=factory
        )

        assert x11.started is True
        factory.assert_called_once_with()
        assert evdev.started is True
        assert manager._x11_listener is x11
        assert manager._evdev_listener is evdev

    def test_no_x11_listener_starts_evdev(self, monkeypatch):
        evdev = FakeListener()
        factory = Mock(return_value=evdev)
        manager = _start_manager(
            monkeypatch, pure_x11=True, x11=None, factory=factory
        )

        factory.assert_called_once_with()
        assert evdev.started is True
        assert manager._evdev_listener is evdev

    def test_factory_returning_none_leaves_no_backend(self, monkeypatch):
        x11 = FakeListener(start_ok=False)
        factory = Mock(return_value=None)
        manager = _start_manager(
            monkeypatch, pure_x11=True, x11=x11, factory=factory
        )

        assert manager._evdev_listener is None
        assert manager.has_global_hotkey is False

    def test_failed_evdev_start_is_not_kept(self, monkeypatch):
        x11 = FakeListener(start_ok=False)
        evdev = FakeListener(start_ok=False)
        factory = Mock(return_value=evdev)
        manager = _start_manager(
            monkeypatch, pure_x11=True, x11=x11, factory=factory
        )

        assert evdev.started is True
        assert manager._evdev_listener is None
        assert manager.has_global_hotkey is False

    def test_stop_stops_started_backends(self, monkeypatch):
        x11 = FakeListener()
        evdev = FakeListener()
        manager = _start_manager(
            monkeypatch,
            pure_x11=False,
            x11=x11,
            factory=Mock(return_value=evdev),
        )

        manager.stop()

        assert x11.stopped is True
        assert evdev.stopped is True


class TestAppEvdevFactory:
    def test_returns_none_when_evdev_is_unavailable(self):
        app = SimpleNamespace(hotkey_manager=Mock())

        with patch("doubao_murmur.app.EvdevListener") as listener_cls:
            listener_cls.is_available.return_value = False
            assert DoubaoMurmurApp._create_evdev_listener(app) is None
            listener_cls.assert_not_called()

    def test_wires_the_manager_callbacks(self):
        manager = Mock()
        app = SimpleNamespace(hotkey_manager=manager)

        with patch("doubao_murmur.app.EvdevListener") as listener_cls:
            listener_cls.is_available.return_value = True
            listener = DoubaoMurmurApp._create_evdev_listener(app)

        listener_cls.assert_called_once_with(
            on_toggle=manager.trigger_toggle,
            on_escape=manager.trigger_cancel,
        )
        assert listener is listener_cls.return_value


class TestKeyboardHotkeySwitch:
    @staticmethod
    def _manager(monkeypatch):
        # trigger_keyboard() hands off through GLib.idle_add; run it inline.
        monkeypatch.setattr(
            "doubao_murmur.hotkey.manager.GLib.idle_add",
            lambda fn, *args: fn(*args),
        )
        manager = HotkeyManager()
        manager.calls = []
        manager.on_keyboard = lambda: manager.calls.append("keyboard")
        return manager

    def test_keyboard_hotkey_fires_by_default(self, monkeypatch):
        manager = self._manager(monkeypatch)
        manager.trigger_keyboard()
        assert manager.calls == ["keyboard"]

    def test_disabled_keyboard_hotkey_is_ignored(self, monkeypatch):
        manager = self._manager(monkeypatch)
        manager.keyboard_hotkey_enabled = False
        manager.trigger_keyboard()
        assert manager.calls == []

    def test_reenabling_takes_effect_immediately(self, monkeypatch):
        manager = self._manager(monkeypatch)
        manager.keyboard_hotkey_enabled = False
        manager.trigger_keyboard()
        manager.keyboard_hotkey_enabled = True
        manager.trigger_keyboard()
        assert manager.calls == ["keyboard"]
