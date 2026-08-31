"""Constants and configuration for Doubao Murmur Linux port.

Mirrors the fixed parameters from the macOS DoubaoASRClient.swift.
"""

import os
from pathlib import Path

# --- Doubao ASR WebSocket ---

WSS_BASE_URL = "wss://ws-samantha.doubao.com/samantha/audio/asr"

FIXED_QUERY_PARAMS = {
    "version_code": "20800",
    "language": "zh",
    "device_platform": "web",
    "aid": "497858",
    "real_aid": "497858",
    "pkg_type": "release_version",
    "pc_version": "3.12.3",
    "region": "",
    "sys_region": "",
    "samantha_web": "1",
    "use-olympus-account": "1",
    "format": "pcm",
}

ORIGIN = "https://www.doubao.com"
LOGIN_URL = "https://www.doubao.com/chat"

# --- Audio capture ---

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_DTYPE = "int16"
AUDIO_BLOCKSIZE = 4096  # samples per callback (~256ms at 16kHz)

# --- Auth error detection ---

AUTH_ERROR_CODE = 709599054
AUTH_ERROR_KEYWORDS = [
    "cookie", "auth", "login", "session", "unauthorized", "expired",
]

# --- Paths ---

CONFIG_DIR_NAME = "doubao-murmur"
PARAMS_FILE = "asr_params.json"
KEYBOARD_FILE = "keyboard.json"
OVERLAY_FILE = "overlay.json"


def get_config_dir() -> Path:
    """Get the XDG config directory for the app."""
    config_home = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    app_dir = Path(config_home) / CONFIG_DIR_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_params_path() -> Path:
    """Get the path to the ASR params JSON file."""
    return get_config_dir() / PARAMS_FILE


def get_keyboard_config_path() -> Path:
    """Get the path to the on-screen keyboard geometry JSON file."""
    return get_config_dir() / KEYBOARD_FILE


def get_overlay_config_path() -> Path:
    """Get the path to the overlay window position JSON file."""
    return get_config_dir() / OVERLAY_FILE


# --- Timeouts ---

STOP_SAFETY_TIMEOUT = 1.5  # seconds; upper bound on waiting for final results
# Trailing digital silence flushed when recording stops. The service emits one
# result per audio message and withholds the last word until more audio arrives,
# so without this padding the tail of the utterance is never transcribed.
# Measured: losing ~150ms of tail audio costs the final two characters, and
# ~100ms of silence recovers them; 200ms leaves margin.
STOP_TRAILING_SILENCE_MS = 200
# The result stream must stay quiet this long before the transcript is accepted.
# After the audio ends the server replays pending partials before sending the
# one carrying the final word; that gap measures ~150ms.
FINAL_RESULT_QUIET_PERIOD = 0.25  # seconds
DEBOUNCE_INTERVAL = 0.3  # seconds
PASTE_DELAY = 0.05  # seconds between copy and paste simulation
# After reactivating the target window, wait this long for slow-to-focus apps
# (notably Electron) to route input focus to the editor widget before the
# synthetic paste chord is injected.
PASTE_FOCUS_DELAY = 0.1  # seconds
# Inter-key delay for the xdotool paste chord. Electron/Chromium can drop a
# chord whose Ctrl and V arrive in the same instant; a short pause lets it
# register the modifier before the letter.
PASTE_KEY_DELAY_MS = 50
AUTH_EXPIRY_DELAY = 2.0  # seconds before resetting after auth error

# --- Overlay UI ---

OVERLAY_WIDTH = 760
OVERLAY_HEIGHT = 88
# Transcription text wraps within this many "characters" (Pango's average
# char-width unit ~= the old 760px text column) and the overlay grows in
# height up to OVERLAY_MAX_LINES before the oldest words scroll off.
OVERLAY_TEXT_CHARS = 88
OVERLAY_MAX_LINES = 5
PTT_BUTTON_SIZE = 60

# --- User-Agent for WebView ---

WEBVIEW_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
