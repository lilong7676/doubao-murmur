"""Tests for PasteHelper."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from doubao_murmur.paste.paste_helper import PasteHelper
from doubao_murmur.paste.uinput_injector import UinputPaster


class TestCopyToClipboard:
    def test_wl_copy_preferred(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}), \
             patch("shutil.which", return_value="/usr/bin/wl-copy") as mock_which, \
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

    def test_x11_skips_wl_copy(self):
        def which_side_effect(cmd):
            if cmd in {"wl-copy", "xclip"}:
                return f"/usr/bin/{cmd}"
            return None

        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run") as mock_run:
            PasteHelper._copy_to_clipboard("hello")
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == [
                "xclip", "-selection", "clipboard"
            ]

    def test_empty_text_ignored(self):
        with patch("subprocess.run") as mock_run:
            PasteHelper.copy_and_paste("")
            mock_run.assert_not_called()


class TestSimulatePaste:
    @pytest.fixture(autouse=True)
    def wayland_session(self, monkeypatch):
        # Keep the existing backend-priority tests deterministic: default to a
        # Wayland session and no /dev/uinput, so the ydotool/wtype/xdotool
        # priorities are exercised regardless of the host's uinput access. The
        # X11 regression below explicitly switches back to a pure X11 session,
        # and the remapped-modifier test re-enables uinput.
        PasteHelper.forget_focused_window()
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr(UinputPaster, "is_available", lambda: False)
        yield
        PasteHelper.forget_focused_window()

    def test_remembers_and_restores_x11_target_before_paste(self):
        remembered = MagicMock(stdout=b"4242\n")
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}), \
             patch("shutil.which", return_value="/usr/bin/xdotool"), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", True)), \
             patch("subprocess.run", side_effect=[remembered, MagicMock(),
                                                   MagicMock(), MagicMock()]) as mock_run:
            PasteHelper.remember_focused_window()
            PasteHelper._simulate_paste()

            assert PasteHelper._x11_target_window == "4242"
            assert mock_run.call_args_list[1].args[0] == [
                "xdotool", "windowactivate", "--sync", "4242"
            ]
            assert mock_run.call_args_list[2].args[0] == [
                "xdotool", "windowfocus", "--sync", "4242"
            ]
            assert mock_run.call_args_list[3].args[0] == [
                "xdotool", "key", "--delay", "50", "ctrl+shift+v"
            ]

    def test_x11_prefers_xdotool_for_remapped_modifier(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}), \
             patch("shutil.which", return_value="/usr/bin/xdotool"), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", False)), \
             patch.object(PasteHelper, "_simulate_paste_with_xdotool",
                          wraps=PasteHelper._simulate_paste_with_xdotool) as mock_xdotool, \
             patch("doubao_murmur.paste.paste_helper.UinputPaster.is_available",
                   return_value=True), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()

            mock_xdotool.assert_called_once_with("v", False)
            mock_run.assert_called_once_with(
                ["xdotool", "key", "--delay", "50", "ctrl+v"],
                check=True,
                timeout=3,
            )

    def test_ydotool_preferred(self):
        with patch("shutil.which", return_value="/usr/bin/ydotool"), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", False)), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == [
                "ydotool", "key", "29:1", "47:1", "47:0", "29:0"
            ]

    def test_ydotool_terminal_uses_ctrl_shift_v(self):
        with patch("shutil.which", return_value="/usr/bin/ydotool"), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", True)), \
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
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", False)), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()

    def test_xdotool_fallback(self):
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", False)), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["xdotool", "key", "--delay", "50", "ctrl+v"]

    def test_xdotool_terminal_uses_ctrl_shift_v(self):
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        with patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", True)), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == [
                "xdotool", "key", "--delay", "50", "ctrl+shift+v"
            ]

    def test_flatpak_spawn_host_fallback(self):
        def which_side_effect(cmd):
            if cmd == "flatpak-spawn":
                return "/usr/bin/flatpak-spawn"
            return None

        with patch("os.path.exists", return_value=True), \
             patch("shutil.which", side_effect=which_side_effect), \
             patch.object(PasteHelper, "_paste_chord",
                          return_value=("v", False)), \
             patch("subprocess.run") as mock_run:
            PasteHelper._simulate_paste()
            mock_run.assert_called()
            args = mock_run.call_args
            assert args[0][0] == [
                "flatpak-spawn", "--host", "ydotool", "key",
                "29:1", "47:1", "47:0", "29:0"
            ]


class TestPasteChord:
    def _run_chord(self, classname: bytes) -> tuple[str, bool]:
        def which_side_effect(cmd):
            if cmd == "xdotool":
                return "/usr/bin/xdotool"
            return None

        mock_result = MagicMock()
        mock_result.stdout = classname
        with patch("shutil.which", side_effect=which_side_effect), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            result = PasteHelper._paste_chord()
            args = mock_run.call_args
            assert args[0][0] == [
                "xdotool", "getactivewindow", "getwindowclassname"
            ]
            return result

    def test_konsole_is_terminal(self):
        assert self._run_chord(b"konsole\n") == ("v", True)

    def test_emacs_uses_ctrl_y(self):
        assert self._run_chord(b"emacs\n") == ("y", False)

    def test_browser_is_not_terminal(self):
        assert self._run_chord(b"google-chrome\n") == ("v", False)

    def test_case_insensitive(self):
        assert self._run_chord(b"Alacritty\n") == ("v", True)

    def test_warp_is_terminal(self):
        # Warp's WM class is "dev.warp.Warp" (xdotool getwindowclassname).
        assert self._run_chord(b"dev.warp.Warp\n") == ("v", True)

    def test_no_xdotool_returns_default(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.exists", return_value=False):
            assert PasteHelper._paste_chord() == ("v", False)


class TestCopyOnly:
    def test_copy_only_does_not_paste(self):
        with patch("shutil.which", return_value="/usr/bin/wl-copy"), \
             patch("subprocess.run") as mock_run:
            PasteHelper.copy_only("text")
            # Should only be called once (for copy, not paste)
            assert mock_run.call_count == 1
