"""Tests for config module."""

import os
from pathlib import Path

import pytest

from doubao_murmur.config import (
    AUDIO_BLOCKSIZE,
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    AUTH_ERROR_CODES,
    AUTH_ERROR_KEYWORDS,
    WSS_BASE_URL,
    get_config_dir,
    get_params_path,
)


def test_wss_url():
    assert WSS_BASE_URL == "wss://ws-samantha.doubao.com/samantha/audio/asr"


def test_audio_params():
    assert AUDIO_SAMPLE_RATE == 16000
    assert AUDIO_CHANNELS == 1
    assert AUDIO_BLOCKSIZE == 4096


def test_auth_error_codes():
    assert AUTH_ERROR_CODES == {709599054, 710012001, 710022013}
    assert "cookie" in AUTH_ERROR_KEYWORDS
    assert "auth" in AUTH_ERROR_KEYWORDS
    assert "session" in AUTH_ERROR_KEYWORDS


def test_get_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_dir = get_config_dir()
    assert config_dir == tmp_path / "doubao-murmur"
    assert config_dir.exists()


def test_get_params_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = get_params_path()
    assert path == tmp_path / "doubao-murmur" / "asr_params.json"
