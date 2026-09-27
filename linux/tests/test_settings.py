"""Tests for user preferences persistence."""

import json

import pytest

from doubao_murmur import settings
from doubao_murmur.config import get_settings_path


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_defaults_when_missing():
    assert settings.load() == settings.DEFAULTS


def test_keyboard_hotkey_on_by_default():
    assert settings.load()["keyboard_hotkey_enabled"] is True


def test_save_and_load_roundtrip():
    prefs = settings.load()
    prefs["keyboard_hotkey_enabled"] = False
    settings.save(prefs)
    assert settings.load()["keyboard_hotkey_enabled"] is False


def test_corrupt_file_falls_back_to_defaults():
    get_settings_path().write_text("{not json")
    assert settings.load() == settings.DEFAULTS


def test_wrong_types_and_unknown_keys_are_ignored():
    get_settings_path().write_text(
        json.dumps({"keyboard_hotkey_enabled": "no", "unknown": 1})
    )
    assert settings.load() == settings.DEFAULTS
