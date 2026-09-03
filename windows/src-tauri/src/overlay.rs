//! Borderless status overlay pinned above every other window.
//! Mirrors OverlayPanel.swift + OverlayView.swift.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Listener, Manager, PhysicalPosition, WebviewUrl, WebviewWindow,
            WebviewWindowBuilder};

use crate::config;
use crate::win32;
use crate::{log_info, log_warn};

pub const WINDOW_LABEL: &str = "overlay";
pub const STATE_EVENT: &str = "overlay-state";
const HEARTBEAT_EVENT: &str = "overlay-heartbeat";

/// The webview beats every few seconds; missing this many milliseconds of
/// heartbeats means the display layer is dead even though every Tauri API
/// still reports the window as alive and script execution "succeeds".
const HEARTBEAT_STALE_MS: u64 = 10_000;
/// A freshly built window needs a moment to boot before the first heartbeat.
const HEARTBEAT_GRACE_MS: u64 = 15_000;

static LAST_HEARTBEAT: AtomicU64 = AtomicU64::new(0);
static LAST_BUILD: AtomicU64 = AtomicU64::new(0);

fn now_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Listens for the overlay page's heartbeat. Registered once at startup; the
/// rebuilt windows keep beating on the same global event.
pub fn start_heartbeat(app: &AppHandle) {
    app.listen(HEARTBEAT_EVENT, |_event| {
        LAST_HEARTBEAT.store(now_millis(), Ordering::Relaxed);
    });
}

/// Set while the app moves the window itself, so the resulting Moved event is not
/// mistaken for the user dragging it.
static PROGRAMMATIC_MOVE: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Serialize)]
pub struct OverlayState {
    pub text: String,
    pub recording: bool,
    pub error: bool,
}

#[derive(Serialize, Deserialize)]
struct Geometry {
    x: i32,
    y: i32,
}

/// Must run on the main thread.
pub fn build(app: &AppHandle) -> Result<WebviewWindow, String> {
    let window = WebviewWindowBuilder::new(
        app,
        WINDOW_LABEL,
        WebviewUrl::App("overlay.html".into()),
    )
    .title("Doubao Murmur")
    .inner_size(config::OVERLAY_WIDTH, config::OVERLAY_HEIGHT)
    .decorations(false)
    .always_on_top(true)
    .skip_taskbar(true)
    .resizable(false)
    .transparent(true)
    .shadow(false)
    .focused(false)
    .visible(false)
    .build()
    .map_err(|e| e.to_string())?;

    LAST_BUILD.store(now_millis(), Ordering::Relaxed);

    // Applied before the window is ever shown: if the overlay took focus, the
    // transcription would be pasted into the overlay instead of the app the user
    // was typing in.
    if let Ok(handle) = window.hwnd() {
        win32::make_non_activating(win32::hwnd_from_isize(handle.0 as isize));
    } else {
        log_warn!("Could not read the overlay window handle; it may steal focus");
    }

    Ok(window)
}

pub fn show(app: &AppHandle) {
    let Some(window) = app.get_webview_window(WINDOW_LABEL) else {
        // The window object is gone entirely (e.g. it was closed externally);
        // a fresh one is the only way to ever show anything again.
        log_warn!("Overlay window is missing; rebuilding it");
        rebuild(app);
        return;
    };

    // The manager can hand back a window object whose native window is long
    // gone (observed after the WebView2 browser process dies), so ask Win32
    // whether the HWND is still real.
    let hwnd_ok = window
        .hwnd()
        .map(|hwnd| win32::is_window(hwnd.0 as isize))
        .unwrap_or(false);
    if !hwnd_ok {
        log_warn!("Overlay native window is gone; rebuilding it");
        rebuild(app);
        return;
    }

    // Heartbeat check: when the WebView2 side dies the HWND stays valid and
    // eval() still "succeeds" (the call is fire-and-forget), so the page's own
    // heartbeat is the only trustworthy sign of life. Skip the check while a
    // freshly built window has not had time to boot yet.
    let now = now_millis();
    let last_beat = LAST_HEARTBEAT.load(Ordering::Relaxed);
    let stale = last_beat == 0 || now.saturating_sub(last_beat) > HEARTBEAT_STALE_MS;
    let fresh = now.saturating_sub(LAST_BUILD.load(Ordering::Relaxed)) < HEARTBEAT_GRACE_MS;
    if stale && !fresh {
        log_warn!("Overlay webview stopped responding (heartbeat lost); rebuilding it");
        rebuild(app);
        return;
    }

    // Secondary heartbeat: a renderer that crashed and restarted comes back
    // with a blank page that still accepts script. The page sets __murmurReady
    // once it has booted; if the flag is gone the page reloads itself (bounded
    // via sessionStorage so a broken page cannot loop).
    const HEARTBEAT: &str = "if(window.__murmurReady){sessionStorage.removeItem('murmurReloads')}else{var n=(+sessionStorage.murmurReloads||0)+1;sessionStorage.murmurReloads=n;if(n<=3)location.reload()}";
    if let Err(e) = window.eval(HEARTBEAT) {
        log_warn!("Overlay webview is not responding: {e}; rebuilding it");
        rebuild(app);
        return;
    }

    reposition(app, &window);
    if let Err(e) = window.show() {
        log_warn!("Overlay show() failed: {e}");
    }
}

/// Destroy whatever remains of the overlay window and build a fresh one.
///
/// `destroy()` frees the window label asynchronously, so the rebuild runs on a
/// helper thread and retries on the main thread until the old label is gone.
fn rebuild(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.destroy();
    }

    let handle = app.clone();
    std::thread::spawn(move || {
        for attempt in 1..=5u32 {
            std::thread::sleep(Duration::from_millis(200 * attempt as u64));
            if handle.get_webview_window(WINDOW_LABEL).is_some() {
                continue;
            }
            let h = handle.clone();
            if let Err(e) = handle.run_on_main_thread(move || match build(&h) {
                Ok(window) => {
                    reposition(&h, &window);
                    if let Err(e) = window.show() {
                        log_warn!("Rebuilt overlay show() failed: {e}");
                    } else {
                        log_info!("Overlay window rebuilt");
                    }
                }
                Err(e) => log_warn!("Overlay rebuild failed: {e}"),
            }) {                log_warn!("Could not schedule overlay rebuild: {e}");
                return;
            }
            return;
        }
        log_warn!("Overlay rebuild gave up: old window label was never freed");
    });
}

pub fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.hide();
    }
}

pub fn update(app: &AppHandle, state: OverlayState) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.emit(STATE_EVENT, state);
    }
}

fn reposition(app: &AppHandle, window: &WebviewWindow) {
    let Some(position) = target_position(app, window) else {
        return;
    };

    PROGRAMMATIC_MOVE.store(true, Ordering::SeqCst);
    let _ = window.set_position(position);
    PROGRAMMATIC_MOVE.store(false, Ordering::SeqCst);
}

fn target_position(app: &AppHandle, window: &WebviewWindow) -> Option<PhysicalPosition<i32>> {
    if let Some(saved) = load_geometry() {
        if is_on_a_monitor(app, saved.x, saved.y) {
            return Some(PhysicalPosition::new(saved.x, saved.y));
        }
    }

    // Top-centre of whichever monitor the pointer is on.
    let monitor = app
        .cursor_position()
        .ok()
        .and_then(|point| app.monitor_from_point(point.x, point.y).ok().flatten())
        .or_else(|| window.current_monitor().ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten())?;

    let scale = monitor.scale_factor();
    let area = monitor.size();
    let origin = monitor.position();

    let width = (config::OVERLAY_WIDTH * scale) as i32;
    let x = origin.x + (area.width as i32 - width) / 2;
    let y = origin.y + (config::OVERLAY_TOP_MARGIN * scale) as i32;

    Some(PhysicalPosition::new(x, y))
}

fn is_on_a_monitor(app: &AppHandle, x: i32, y: i32) -> bool {
    let Ok(monitors) = app.available_monitors() else {
        return false;
    };

    monitors.iter().any(|monitor| {
        let origin = monitor.position();
        let size = monitor.size();
        // Only the top-left corner has to land on a monitor; that is enough to
        // guarantee the window is reachable after a display layout change.
        x >= origin.x - 40
            && y >= origin.y - 10
            && x < origin.x + size.width as i32 - 80
            && y < origin.y + size.height as i32 - 20
    })
}

/// Called from the window's Moved handler.
pub fn remember_position(x: i32, y: i32) {
    if PROGRAMMATIC_MOVE.load(Ordering::SeqCst) {
        return;
    }

    if let Ok(text) = serde_json::to_string(&Geometry { x, y }) {
        let _ = std::fs::write(config::overlay_path(), text);
    }
}

fn load_geometry() -> Option<Geometry> {
    let text = std::fs::read_to_string(config::overlay_path()).ok()?;
    serde_json::from_str(&text).ok()
}
