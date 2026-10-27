//! Constants for the Doubao ASR service and local paths.
//!
//! Mirrors DoubaoASRClient.swift and linux/src/doubao_murmur/config.py — keep the
//! three implementations in sync (see the table in windows/README.md).

use std::path::PathBuf;
use std::time::Duration;

// --- Doubao ASR WebSocket ---

pub const WSS_BASE_URL: &str = "wss://ws-samantha.doubao.com/samantha/audio/asr";
pub const ORIGIN: &str = "https://www.doubao.com";
pub const LOGIN_URL: &str = "https://www.doubao.com/chat";
pub const GITHUB_REPO: &str = "lilong7676/doubao-murmur";

/// Query parameters that never change, in the order the macOS client sends them.
/// device_id / web_id / tea_uuid / web_tab_id are filled in per connection.
pub const FIXED_QUERY_PARAMS: &[(&str, &str)] = &[
    ("version_code", "20800"),
    ("language", "zh"),
    ("device_platform", "web"),
    ("aid", "497858"),
    ("real_aid", "497858"),
    ("pkg_type", "release_version"),
    ("pc_version", "3.12.3"),
    ("region", ""),
    ("sys_region", ""),
    ("samantha_web", "1"),
    ("use-olympus-account", "1"),
    ("format", "pcm"),
];

// --- Auth error detection ---

/// 709599054: "Invalid". 710012001: "login invalid", the HTTP 200 body that
/// answers the handshake when the cookie is not a login at all. 710022013:
/// "tourist reach limited", sometimes sent once right before the server closes
/// a connection whose login has expired.
pub const AUTH_ERROR_CODES: &[i64] = &[709_599_054, 710_012_001, 710_022_013];
pub const AUTH_ERROR_KEYWORDS: &[&str] = &[
    "cookie",
    "auth",
    "login",
    "session",
    "unauthorized",
    "expired",
];

// --- Audio ---

pub const AUDIO_SAMPLE_RATE: u32 = 16_000;
/// Chunk length pushed to the socket. 100 ms keeps partial results snappy.
pub const AUDIO_CHUNK_MS: u32 = 100;

pub fn audio_chunk_samples() -> usize {
    (AUDIO_SAMPLE_RATE as usize) * (AUDIO_CHUNK_MS as usize) / 1000
}

/// Trailing digital silence flushed when recording stops. The service emits one
/// result per audio message and withholds the last word until further audio
/// arrives, so a stream that simply stops never gets its tail transcribed.
/// Measured: losing ~150ms of tail audio costs the final two characters, and
/// ~100ms of silence recovers them; 200ms leaves margin.
pub const STOP_TRAILING_SILENCE_MS: u32 = 200;

pub fn stop_trailing_silence_chunks() -> usize {
    ((STOP_TRAILING_SILENCE_MS / AUDIO_CHUNK_MS) as usize).max(1)
}

// --- Timeouts ---

pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
pub const STOP_SAFETY_TIMEOUT: Duration = Duration::from_millis(1500);
/// The result stream must stay quiet this long before the transcript is accepted.
/// After the audio ends the server replays pending partials before sending the
/// one carrying the final word; that gap measures ~150ms.
pub const FINAL_RESULT_QUIET_PERIOD: Duration = Duration::from_millis(250);
pub const DEBOUNCE_INTERVAL: Duration = Duration::from_millis(300);
pub const PASTE_DELAY: Duration = Duration::from_millis(80);
pub const AUTH_EXPIRY_DELAY: Duration = Duration::from_secs(2);
pub const LOGIN_POLL_INTERVAL: Duration = Duration::from_millis(1200);

// --- Overlay ---

pub const OVERLAY_WIDTH: f64 = 760.0;
pub const OVERLAY_HEIGHT: f64 = 88.0;
pub const OVERLAY_TOP_MARGIN: f64 = 60.0;

// --- WebView ---

pub const WEBVIEW_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) \
AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

/// Cookie the injected script uses to hand the localStorage ids to the host.
pub const IDS_COOKIE: &str = "__dm_ids";
/// Cookie the injected script sets once the profile API confirms a login.
pub const LOGIN_COOKIE: &str = "__dm_login";

// --- Paths ---

const APP_DIR_NAME: &str = "doubao-murmur";

fn dir_from_env(var: &str) -> PathBuf {
    let base = std::env::var(var).unwrap_or_else(|_| ".".to_string());
    let dir = PathBuf::from(base).join(APP_DIR_NAME);
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// %APPDATA%\doubao-murmur — credentials and window geometry.
pub fn config_dir() -> PathBuf {
    dir_from_env("APPDATA")
}

/// %LOCALAPPDATA%\doubao-murmur — logs and the WebView2 profile.
pub fn local_dir() -> PathBuf {
    dir_from_env("LOCALAPPDATA")
}

pub fn params_path() -> PathBuf {
    config_dir().join("asr_params.json")
}

pub fn settings_path() -> PathBuf {
    config_dir().join("settings.json")
}

pub fn overlay_path() -> PathBuf {
    config_dir().join("overlay.json")
}

pub fn log_path() -> PathBuf {
    local_dir().join("app.log")
}
