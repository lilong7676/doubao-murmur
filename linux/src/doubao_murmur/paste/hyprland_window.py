"""Query active-window and monitor state on Hyprland via ``hyprctl``."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from doubao_murmur.host_tools import command_candidates

logger = logging.getLogger(__name__)


def _query_json(*args: str) -> Any | None:
    for command in command_candidates("hyprctl"):
        try:
            result = subprocess.run(
                command + list(args) + ["-j"],
                capture_output=True,
                check=True,
                timeout=3,
            )
            return json.loads(result.stdout)
        except Exception as e:  # noqa: BLE001 - fall through to other backends
            logger.debug("Hyprland %s probe failed: %s", args[0], e)
    return None


def active_window_class() -> str:
    """Return the lowercased active-window class, or an empty string."""
    data = _query_json("activewindow")
    value = data.get("class", "") if isinstance(data, dict) else ""
    return value.strip().lower() if isinstance(value, str) else ""


def active_monitor_name() -> str:
    """Return the connector name of the focused Hyprland monitor."""
    active = _query_json("activewindow")
    monitor_id = active.get("monitor") if isinstance(active, dict) else None

    monitors = _query_json("monitors")
    if not isinstance(monitors, list):
        return ""

    if isinstance(monitor_id, int):
        for monitor in monitors:
            if isinstance(monitor, dict) and monitor.get("id") == monitor_id:
                name = monitor.get("name", "")
                return name if isinstance(name, str) else ""

    for monitor in monitors:
        if isinstance(monitor, dict) and monitor.get("focused") is True:
            name = monitor.get("name", "")
            return name if isinstance(name, str) else ""
    return ""
