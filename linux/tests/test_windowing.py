"""Tests for compositor-aware overlay monitor selection."""

from unittest.mock import MagicMock, patch

from doubao_murmur.ui.windowing import _monitor_for_overlay


def _display_with_monitors(*connectors):
    monitors = []
    for connector in connectors:
        monitor = MagicMock()
        monitor.get_connector.return_value = connector
        monitors.append(monitor)
    model = MagicMock()
    model.get_n_items.return_value = len(monitors)
    model.get_item.side_effect = monitors.__getitem__
    display = MagicMock()
    display.get_monitors.return_value = model
    return display, monitors


def test_monitor_for_overlay_matches_hyprland_connector():
    display, monitors = _display_with_monitors("DP-1", "DP-2", "DP-3")
    with patch(
        "doubao_murmur.ui.windowing.hyprland_active_monitor_name",
        return_value="DP-3",
    ):
        assert _monitor_for_overlay(display) is monitors[2]


def test_monitor_for_overlay_falls_back_to_first_monitor():
    display, monitors = _display_with_monitors("DP-1", "DP-2")
    with patch(
        "doubao_murmur.ui.windowing.hyprland_active_monitor_name",
        return_value="",
    ):
        assert _monitor_for_overlay(display) is monitors[0]
