"""Tests for PasteHelper."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from doubao_murmur.paste.paste_helper import PasteHelper


@pytest.fixture(autouse=True)
def isolate_paste_backend_tests():
    """Keep fallback tests independent of live compositor/uinput state."""
    with patch(
        "doubao_murmur.paste.paste_helper.hyprland_active_window_class",
        return_value="",
    ), patch(
        "doubao_murmur.paste.paste_helper.kwin_active_window_class",
        return_value="",
    ), patch(
        "doubao_murmur.paste.paste_helper.UinputPaster.is_available",
        return_value=False,
    ):
        yield


class TestCopyToClipboard:
    def test_wl_copy_preferred(self):
        with patch("shutil.which", return_value="/usr/bin/wl-copy") as mock_which, \
             patch("subprocess.run") as mock_run:
            PasteHelper._copy_to_clipboard("hello")
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["wl-copy"]
            assert args.kwargs["input"] == b"hello"

    def test_xclip_fallback(self):
        def which_side_effect(cmd):
            if cmd == "xclip":
                return "/usr/bin/xclip"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run") as mock_run:
            PasteHelper._copy_to_clipboard("hello")
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["xclip", "-selection", "clipboard"]

    def test_empty_text_ignored(self):
        with patch("subprocess.run") as mock_run:
            PasteHelper.copy_and_paste("")
            mock_run.assert_not_called()


class TestSimulatePaste:
    def test_ydotool_preferred(self):
        with patch("shutil.which", return_value="/usr/bin/ydotool"), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=False), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == [
                "ydotool", "key", "29:1", "47:1", "47:0", "29:0"
            ]

    def test_ydotool_terminal_uses_ctrl_shift_v(self):
        with patch("shutil.which", return_value="/usr/bin/ydotool"), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=True), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == [
                "ydotool", "key",
                "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"
            ]

    def test_wtype_fallback(self):
        def which_side_effect(cmd):
            if cmd == "wtype":
                return "/usr/bin/wtype"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=False), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()

    def test_xdotool_fallback(self):
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=False), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["xdotool", "key", "ctrl+v"]

    def test_xdotool_terminal_uses_ctrl_shift_v(self):
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=True), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["xdotool", "key", "ctrl+shift+v"]

    def test_flatpak_spawn_host_fallback(self):
        def which_side_effect(cmd):
            if cmd == "flatpak-spawn":
                return "/usr/bin/flatpak-spawn"
            return None

        with patch("os.path.exists", return_value=True), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_focused_window_is_terminal",
                          return_value=False), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called()
            args = mock_run.call_args
            assert args[0][0] == [
                "flatpak-spawn", "--host", "ydotool", "key",
                "29:1", "47:1", "47:0", "29:0"
            ]


class TestTerminalDetection:
    def test_hyprland_kitty_uses_terminal_paste_chord(self):
        with patch(
            "doubao_murmur.paste.paste_helper.hyprland_active_window_class",
            return_value="kitty",
        ), patch(
            "doubao_murmur.paste.paste_helper.kwin_active_window_class"
        ) as kwin_probe, patch(
            "doubao_murmur.paste.paste_helper.UinputPaster.is_available",
            return_value=True,
        ), patch(
            "doubao_murmur.paste.paste_helper.UinputPaster.paste",
            return_value=True,
        ) as paste:
            PasteHelper._simulate_paste()

        kwin_probe.assert_not_called()
        paste.assert_called_once_with(use_shift=True)

    def _run_detection(self, classname: bytes) -> bool:
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        mock_result = MagicMock()
        mock_result.stdout = classname
        with patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            result = PasteHelper._focused_window_is_terminal()
            args = mock_run.call_args
            assert args[0][0] == [
                "xdotool", "getactivewindow", "getwindowclassname"
            ]
            return result

    def test_konsole_is_terminal(self):
        assert self._run_detection(b"konsole\n") is True

    def test_browser_is_not_terminal(self):
        assert self._run_detection(b"google-chrome\n") is False

    def test_case_insensitive(self):
        assert self._run_detection(b"Alacritty\n") is True

    def test_warp_is_terminal(self):
        # Warp's WM class is "dev.warp.Warp" (xdotool getwindowclassname).
        assert self._run_detection(b"dev.warp.Warp\n") is True

    def test_no_xdotool_returns_false(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.exists", return_value=False):
            assert PasteHelper._focused_window_is_terminal() is False


class TestCopyOnly:
    def test_copy_only_does_not_paste(self):
        with patch("shutil.which", return_value="/usr/bin/wl-copy"), \
             patch("subprocess.run") as mock_run:
            PasteHelper.copy_only("text")
            # Should only be called once (for copy, not paste)
            assert mock_run.call_count == 1
