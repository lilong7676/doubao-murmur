"""Tests for when the overlay is unmapped before the paste chord."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import GLib

from doubao_murmur.app import DoubaoMurmurApp


def _paste(*, layer_shell, pure_x11):
    app = SimpleNamespace(
        overlay=Mock(),
        _paste_after_overlay_hidden=DoubaoMurmurApp._paste_after_overlay_hidden,
    )
    with (
        patch("doubao_murmur.app.using_layer_shell", return_value=layer_shell),
        patch("doubao_murmur.app.is_pure_x11_session", return_value=pure_x11),
        patch("doubao_murmur.app.PasteHelper") as helper,
        patch("doubao_murmur.app.GLib.timeout_add") as timeout_add,
    ):
        DoubaoMurmurApp._do_paste(app, "hello")
    return app.overlay, helper, timeout_add


def test_wayland_without_layer_shell_hides_overlay_before_pasting():
    overlay, helper, timeout_add = _paste(layer_shell=False, pure_x11=False)

    overlay.hide.assert_called_once_with()
    helper.copy_and_paste.assert_not_called()
    delay, callback, text = timeout_add.call_args.args
    assert delay == 120
    with patch("doubao_murmur.app.PasteHelper") as helper:
        assert callback(text) == GLib.SOURCE_REMOVE
    helper.copy_and_paste.assert_called_once_with("hello")


def test_pure_x11_pastes_immediately():
    # The X11 path restores the remembered target window itself; hiding the
    # overlay first would clear that target before the paste runs.
    overlay, helper, timeout_add = _paste(layer_shell=False, pure_x11=True)

    overlay.hide.assert_not_called()
    timeout_add.assert_not_called()
    helper.copy_and_paste.assert_called_once_with("hello")


def test_layer_shell_pastes_immediately():
    overlay, helper, timeout_add = _paste(layer_shell=True, pure_x11=False)

    overlay.hide.assert_not_called()
    timeout_add.assert_not_called()
    helper.copy_and_paste.assert_called_once_with("hello")
