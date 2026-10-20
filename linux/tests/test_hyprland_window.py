"""Tests for Hyprland active-window detection."""

from unittest.mock import MagicMock, patch

from doubao_murmur.paste.hyprland_window import (
    active_monitor_name,
    active_window_class,
)


def test_active_window_class_returns_lowercase_class():
    result = MagicMock()
    result.stdout = b'{"class": "KiTtY"}'

    with patch(
        "doubao_murmur.paste.hyprland_window.command_candidates",
        return_value=[["hyprctl"]],
    ), patch(
        "doubao_murmur.paste.hyprland_window.subprocess.run",
        return_value=result,
    ) as run:
        assert active_window_class() == "kitty"

    run.assert_called_once_with(
        ["hyprctl", "activewindow", "-j"],
        capture_output=True,
        check=True,
        timeout=3,
    )


def test_active_window_class_returns_empty_for_invalid_json():
    result = MagicMock()
    result.stdout = b"not json"

    with patch(
        "doubao_murmur.paste.hyprland_window.command_candidates",
        return_value=[["hyprctl"]],
    ), patch(
        "doubao_murmur.paste.hyprland_window.subprocess.run",
        return_value=result,
    ):
        assert active_window_class() == ""


def test_active_window_class_returns_empty_without_hyprctl():
    with patch(
        "doubao_murmur.paste.hyprland_window.command_candidates",
        return_value=[],
    ):
        assert active_window_class() == ""


def test_active_monitor_name_uses_active_window_monitor_id():
    active = MagicMock(stdout=b'{"monitor": 2}')
    monitors = MagicMock(
        stdout=b'[{"id": 0, "name": "DP-1"}, {"id": 2, "name": "DP-3"}]'
    )
    with patch(
        "doubao_murmur.paste.hyprland_window.command_candidates",
        return_value=[["hyprctl"]],
    ), patch(
        "doubao_murmur.paste.hyprland_window.subprocess.run",
        side_effect=[active, monitors],
    ):
        assert active_monitor_name() == "DP-3"


def test_active_monitor_name_falls_back_to_focused_monitor():
    active = MagicMock(stdout=b"{}")
    monitors = MagicMock(
        stdout=b'[{"id": 0, "name": "DP-1", "focused": false}, '
        b'{"id": 1, "name": "DP-2", "focused": true}]'
    )
    with patch(
        "doubao_murmur.paste.hyprland_window.command_candidates",
        return_value=[["hyprctl"]],
    ), patch(
        "doubao_murmur.paste.hyprland_window.subprocess.run",
        side_effect=[active, monitors],
    ):
        assert active_monitor_name() == "DP-2"
