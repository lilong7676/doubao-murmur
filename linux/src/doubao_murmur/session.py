"""Session-protocol detection.

Kept free of GTK imports so both the UI and the paste layers can use it:
a Wayland session running an X11 client (XWayland) is not a pure X11 session,
but it still exposes ``DISPLAY``.
"""

from __future__ import annotations

import os


def is_pure_x11_session() -> bool:
    """Whether X11 is the session protocol rather than XWayland."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type == "x11":
        return True
    if session_type == "wayland":
        return False
    # Some launchers do not set XDG_SESSION_TYPE. Having only DISPLAY is
    # the usual X11 fallback; Wayland sessions normally also expose
    # WAYLAND_DISPLAY.
    return bool(os.environ.get("DISPLAY")) and not os.environ.get(
        "WAYLAND_DISPLAY"
    )
