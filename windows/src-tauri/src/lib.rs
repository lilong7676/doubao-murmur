//! Doubao Murmur for Windows.
//!
//! A tray-resident dictation tool: a low-level keyboard hook starts and stops
//! recording, audio is streamed to Doubao's ASR service over a WebSocket, and the
//! result is pasted into whatever window had focus.
//!
//! Tauri supplies the shell — tray, windows, the WebView2 login page — but the
//! parts that make this app work (bare-modifier hotkey, synthetic paste, a window
//! that never takes focus) are Win32 code in `hotkey`, `paste` and `win32`.

mod asr;
mod audio;
mod clipboard_transaction;
mod autostart;
mod config;
mod controller;
mod help;
mod hotkey;
mod login;
pub mod logging;
mod overlay;
mod params;
mod paste;
mod resample;
mod settings;
mod tray;
mod updater;
mod win32;

use tauri::{RunEvent, WindowEvent};

use crate::settings::Settings;
use crate::tray::TrayState;

pub fn run() {
    if !win32::acquire_single_instance() {
        log_info!("Another instance is already running");
        return;
    }

    log_info!("Starting Doubao Murmur {}", env!("CARGO_PKG_VERSION"));

    let result = tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            let settings = Settings::load();

            overlay::build(&handle).map_err(|e| format!("overlay window: {e}"))?;
            overlay::start_heartbeat(&handle);

            tray::create(
                &handle,
                &TrayState {
                    logged_in: params::has_saved(),
                    recording: false,
                    toggle_key: settings.toggle_key,
                    suppress_toggle_key: settings.suppress_toggle_key,
                },
            )?;

            controller::spawn(&handle, settings);
            log_info!("All components initialised");
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != overlay::WINDOW_LABEL {
                return;
            }
            // The overlay is draggable; remember where the user parked it.
            if let WindowEvent::Moved(position) = event {
                overlay::remember_position(position.x, position.y);
            }
        })
        .build(tauri::generate_context!());

    let app = match result {
        Ok(app) => app,
        Err(e) => {
            log_error!("Failed to start: {e}");
            win32::warn_box(&format!(
                "启动失败：{e}\n\n详情见日志：\n{}",
                config::log_path().display()
            ));
            return;
        }
    };

    app.run(|_app, event| {
        if let RunEvent::ExitRequested { api, code, .. } = event {
            // Closing the last window must not quit a tray app; an explicit
            // app.exit() carries a code and is allowed through.
            if code.is_none() {
                api.prevent_exit();
            }
        }
    });
}
