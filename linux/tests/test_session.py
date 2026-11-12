"""Tests for session-protocol detection."""

import pytest

from doubao_murmur.session import is_pure_x11_session


@pytest.fixture(autouse=True)
def _clean_session_env(monkeypatch):
    for name in ("XDG_SESSION_TYPE", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"XDG_SESSION_TYPE": "x11"}, True),
        ({"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"XDG_SESSION_TYPE": "wayland"}, False),
        ({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}, False),
        ({"DISPLAY": ":0"}, True),
        ({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, False),
        ({"WAYLAND_DISPLAY": "wayland-0"}, False),
        ({}, False),
        ({"XDG_SESSION_TYPE": " X11 "}, True),
        ({"XDG_SESSION_TYPE": "Wayland", "DISPLAY": ":0"}, False),
        ({"XDG_SESSION_TYPE": "", "DISPLAY": ":0"}, True),
    ],
)
def test_is_pure_x11_session(monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert is_pure_x11_session() is expected
