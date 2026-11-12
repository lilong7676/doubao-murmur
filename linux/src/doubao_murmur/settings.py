"""Persist user preferences to a JSON file.

Location: $XDG_CONFIG_HOME/doubao-murmur/settings.json
Every key has a default, so a missing or unreadable file behaves like a
fresh install.
"""

from __future__ import annotations

import json
import logging

from doubao_murmur.config import get_settings_path

logger = logging.getLogger(__name__)

DEFAULTS = {
    # Ctrl+Super+Shift toggles the on-screen keyboard.
    "keyboard_hotkey_enabled": True,
}


def load() -> dict:
    """Return saved settings merged over the defaults."""
    settings = dict(DEFAULTS)
    path = get_settings_path()
    if not path.exists():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read settings, using defaults: %s", e)
        return settings
    if isinstance(data, dict):
        for key, default in DEFAULTS.items():
            if isinstance(data.get(key), type(default)):
                settings[key] = data[key]
    return settings


def save(settings: dict) -> None:
    try:
        get_settings_path().write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Could not save settings: %s", e)
