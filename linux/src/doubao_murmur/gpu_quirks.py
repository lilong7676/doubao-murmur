"""Rendering workarounds for the NVIDIA proprietary driver.

On NVIDIA + X11, GTK4's GPU renderer makes the windows flicker and
WebKitGTK's DMA-BUF renderer leaves the login page blank white. Both
toolkits read their renderer choice from the environment at startup, so
this must run before GTK/WebKit are imported.

Only NVIDIA machines are affected, and anything the user already set in
the environment wins.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import MutableMapping

logger = logging.getLogger(__name__)

# Any of these is present while the proprietary driver is loaded. The
# device node is the one the Flatpak sandbox reliably sees, since the
# manifest grants --device=all.
NVIDIA_MARKERS = (
    Path("/proc/driver/nvidia/version"),
    Path("/sys/module/nvidia/version"),
    Path("/dev/nvidiactl"),
)

NVIDIA_ENV = {
    # GTK4: draw with cairo instead of GL/Vulkan (stops window flicker).
    "GSK_RENDERER": "cairo",
    # WebKitGTK: disable the DMA-BUF renderer (fixes blank login page).
    "WEBKIT_DISABLE_DMABUF_RENDERER": "1",
}


def has_nvidia_driver(markers=NVIDIA_MARKERS) -> bool:
    return any(marker.exists() for marker in markers)


def apply(
    environ: MutableMapping[str, str] = os.environ,
    markers=NVIDIA_MARKERS,
) -> dict[str, str]:
    """Set the NVIDIA workarounds that the user has not set themselves.

    Returns the variables that were actually set.
    """
    if not has_nvidia_driver(markers):
        return {}
    applied = {}
    for name, value in NVIDIA_ENV.items():
        if name not in environ:
            environ[name] = value
            applied[name] = value
    if applied:
        logger.info("NVIDIA driver detected, applying %s", applied)
    return applied
