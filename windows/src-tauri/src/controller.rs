//! State machine for the recording lifecycle. Mirrors TranscriptionManager.swift:
//! idle -> starting -> recording -> stopping -> idle.
//!
//! Everything that mutates state runs on one thread and is driven by a single
//! event channel, so no locking is needed. Hotkeys, ASR callbacks and tray
//! commands all funnel into the same queue.

use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};
use tokio::sync::mpsc::UnboundedReceiver;

use crate::asr::{AsrClient, AsrEvent, ErrorRecovery};
use crate::audio::AudioCapture;
use crate::hotkey::{self, HotkeyEvent};
use crate::overlay::{self, OverlayState};
use crate::params::{self, AsrParams};
use crate::settings::{Settings, ToggleKey};
use crate::tray::{self, TrayState};
use crate::{autostart, config, help, login, paste, updater, win32};
use crate::{log_error, log_info, log_warn};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecordingState {
    Idle,
    Starting,
    Recording,
    Stopping,
}

pub enum Event {
    HotkeyToggle,
    HotkeyCancel,
    Asr(AsrEvent),
    SafetyTimeout(u64),
    /// Debounced completion after the stop: (generation, quiet_epoch). Superseded
    /// whenever a later result reschedules it with a fresh epoch.
    StreamQuiet(u64, u64),
    ResetIdle(u64),
    LoginPoll,
    ShowLogin,
    Logout,
    ShowHelp,
    OpenLog,
    CheckUpdate,
    ToggleAutostart,
    ToggleSuppress,
    SetToggleKey(ToggleKey),
    Quit,
}

pub struct EventSender(Mutex<Sender<Event>>);

pub fn send(app: &AppHandle, event: Event) {
    if let Some(state) = app.try_state::<EventSender>() {
        if let Ok(sender) = state.0.lock() {
            let _ = sender.send(event);
        }
    }
}

/// Creates the controller thread and registers its sender with the app.
pub fn spawn(app: &AppHandle, settings: Settings) {
    let (tx, rx) = channel::<Event>();
    app.manage(EventSender(Mutex::new(tx.clone())));

    // The hook thread speaks its own vocabulary; bridge it onto the event queue.
    let (hotkey_tx, hotkey_rx) = channel::<HotkeyEvent>();
    hotkey::start(hotkey_tx, settings.toggle_key, settings.suppress_toggle_key);
    {
        let tx = tx.clone();
        std::thread::spawn(move || {
            while let Ok(event) = hotkey_rx.recv() {
                let mapped = match event {
                    HotkeyEvent::Toggle => Event::HotkeyToggle,
                    HotkeyEvent::Cancel => Event::HotkeyCancel,
                };
                if tx.send(mapped).is_err() {
                    break;
                }
            }
        });
    }

    let controller = Controller {
        app: app.clone(),
        tx,
        settings,
        state: RecordingState::Idle,
        generation: 0,
        logged_in: params::has_saved(),
        text: String::new(),
        awaiting_final: false,
        quiet_epoch: 0,
        asr: AsrClient::new(),
        audio: AudioCapture::new(),
        last_toggle: None,
        login_polling: false,
    };

    std::thread::spawn(move || controller.run(rx));
}

struct Controller {
    app: AppHandle,
    tx: Sender<Event>,
    settings: Settings,
    state: RecordingState,
    generation: u64,
    logged_in: bool,
    text: String,
    awaiting_final: bool,
    /// Bumped each time completion is rescheduled, so only the newest
    /// `StreamQuiet` event is honoured.
    quiet_epoch: u64,
    asr: AsrClient,
    audio: AudioCapture,
    last_toggle: Option<Instant>,
    login_polling: bool,
}

impl Controller {
    fn run(mut self, rx: Receiver<Event>) {
        log_info!(
            "Controller started, {}",
            if self.logged_in {
                "cached params found"
            } else {
                "login required"
            }
        );

        while let Ok(event) = rx.recv() {
            self.handle(event);
        }
    }

    fn handle(&mut self, event: Event) {
        match event {
            Event::HotkeyToggle => self.on_toggle(),
            Event::HotkeyCancel => self.cancel(),
            Event::Asr(event) => self.on_asr(event),
            Event::SafetyTimeout(generation) => self.on_safety_timeout(generation),
            Event::StreamQuiet(generation, epoch) => self.on_stream_quiet(generation, epoch),
            Event::ResetIdle(generation) => {
                if generation == self.generation {
                    self.reset_to_idle();
                }
            }
            Event::LoginPoll => self.poll_login(),
            Event::ShowLogin => self.show_login(),
            Event::Logout => self.logout(),
            Event::ShowHelp => self.show_help(),
            Event::OpenLog => win32::shell_open(&config::log_path().to_string_lossy()),
            Event::CheckUpdate => self.check_update(),
            Event::ToggleAutostart => {
                autostart::set_enabled(!autostart::is_enabled());
                self.refresh_tray();
            }
            Event::ToggleSuppress => {
                self.settings.suppress_toggle_key = !self.settings.suppress_toggle_key;
                self.settings.save();
                hotkey::apply(self.settings.toggle_key, self.settings.suppress_toggle_key);
                self.refresh_tray();
            }
            Event::SetToggleKey(key) => {
                self.settings.toggle_key = key;
                self.settings.save();
                hotkey::apply(self.settings.toggle_key, self.settings.suppress_toggle_key);
                self.refresh_tray();
            }
            Event::Quit => self.quit(),
        }
    }

    // --- Recording ---

    fn on_toggle(&mut self) {
        let now = Instant::now();
        if let Some(previous) = self.last_toggle {
            if now.duration_since(previous) < config::DEBOUNCE_INTERVAL {
                return;
            }
        }
        self.last_toggle = Some(now);

        match self.state {
            RecordingState::Idle => self.start_recording(),
            RecordingState::Starting | RecordingState::Recording => self.stop_recording(),
            // Already finishing up.
            RecordingState::Stopping => {}
        }
    }

    fn start_recording(&mut self) {
        if !self.logged_in {
            log_warn!("Not logged in, showing the login window");
            self.show_login();
            return;
        }

        log_info!("Starting recording");
        self.set_state(RecordingState::Starting);
        self.text.clear();
        overlay::show(&self.app);
        self.push_overlay(None);

        // The channel is created before the socket so audio captured during the
        // handshake queues up instead of being lost.
        let (audio_tx, audio_rx) = tokio::sync::mpsc::unbounded_channel::<Vec<u8>>();
        let sink = audio_tx.clone();
        drop(audio_tx);

        if let Err(e) = self.audio.start(move |chunk| {
            let _ = sink.send(chunk);
        }) {
            log_error!("Audio capture failed: {e}");
            self.push_overlay(Some("麦克风启动失败"));
            self.schedule_reset(config::AUTH_EXPIRY_DELAY);
            return;
        }

        match params::load() {
            Some(params) => {
                log_info!("Using cached ASR params");
                self.connect(params, audio_rx);
            }
            None => {
                // Should not happen: logged_in tracks the presence of this file.
                self.logged_in = false;
                self.refresh_tray();
                self.push_overlay(Some("请先登录豆包"));
                self.schedule_reset(config::AUTH_EXPIRY_DELAY);
                self.show_login();
            }
        }
    }

    fn connect(&mut self, params: AsrParams, audio_rx: UnboundedReceiver<Vec<u8>>) {
        let tx = self.tx.clone();
        self.asr.connect(params, audio_rx, move |event| {
            let _ = tx.send(Event::Asr(event));
        });
    }

    fn stop_recording(&mut self) {
        log_info!("Stopping recording");
        self.set_state(RecordingState::Stopping);
        self.audio.stop();
        // Flushes trailing silence so the server finalises the last word.
        self.asr.finish_sending();
        self.awaiting_final = true;
        self.later(config::STOP_SAFETY_TIMEOUT, Event::SafetyTimeout(self.generation));
    }

    /// Wait for the result stream to go quiet before accepting the transcript.
    ///
    /// The server streams corrections right up to the end: after the audio stops it
    /// replays the pending partial results first, and only then sends the one
    /// carrying the final word. Completing on the first result to arrive after
    /// stopping truncates the tail -- the "last two characters are missing"
    /// symptom -- so each late result pushes the deadline back instead.
    fn schedule_final_completion(&mut self) {
        self.quiet_epoch += 1;
        self.later(
            config::FINAL_RESULT_QUIET_PERIOD,
            Event::StreamQuiet(self.generation, self.quiet_epoch),
        );
    }

    fn on_stream_quiet(&mut self, generation: u64, epoch: u64) {
        if generation != self.generation
            || epoch != self.quiet_epoch
            || !self.awaiting_final
            || self.state != RecordingState::Stopping
        {
            return;
        }
        log_info!("Result stream quiet, completing");
        self.awaiting_final = false;
        self.complete();
    }

    fn on_safety_timeout(&mut self, generation: u64) {
        if generation != self.generation || self.state != RecordingState::Stopping {
            return;
        }
        log_info!("Safety timeout, completing with the text received so far");
        self.awaiting_final = false;
        self.complete();
    }

    fn cancel(&mut self) {
        if self.state == RecordingState::Idle {
            return;
        }
        log_info!("Cancelling transcription");
        self.awaiting_final = false;
        self.text.clear();
        self.reset_to_idle();
    }

    fn on_asr(&mut self, event: AsrEvent) {
        if let Some(recovery) = event.error_recovery() {
            match recovery {
                ErrorRecovery::Reauthenticate => self.handle_auth_failure(),
                ErrorRecovery::KeepCredentials => {
                    if self.state == RecordingState::Idle {
                        return;
                    }
                    if let AsrEvent::Error(message) = &event {
                        log_error!("ASR connection error (credentials retained): {message}");
                    }
                    // Release the microphone/socket immediately. Do not paste a
                    // partial transcript or invalidate login on a transport error.
                    self.reset_to_idle();
                    overlay::show(&self.app);
                    self.push_overlay(Some("连接已结束，请重试"));
                    self.schedule_reset(config::AUTH_EXPIRY_DELAY);
                }
            }
            return;
        }
        match event {
            AsrEvent::Opened => {
                if self.state == RecordingState::Starting {
                    self.set_state(RecordingState::Recording);
                    self.push_overlay(None);
                }
            }
            AsrEvent::Result(text) => {
                self.text = text;
                if self.state == RecordingState::Starting {
                    self.set_state(RecordingState::Recording);
                }
                self.push_overlay(None);

                if self.awaiting_final {
                    self.schedule_final_completion();
                }
            }
            AsrEvent::Finished => {
                self.awaiting_final = false;
                if matches!(
                    self.state,
                    RecordingState::Stopping | RecordingState::Recording
                ) {
                    self.complete();
                }
            }
            AsrEvent::Error(_) | AsrEvent::AuthError => unreachable!("handled above"),
        }
    }

    fn complete(&mut self) {
        let text = self.text.trim().to_string();
        log_info!("Completing transcription ({} chars)", text.chars().count());

        if !text.is_empty() {
            // Delivery queues onto a single worker; snapshots happen there,
            // immediately before insertion, never when recording starts.
            let mode = self.settings.paste_mode;
            paste::deliver(&text, mode);
        }

        self.reset_to_idle();
    }

    fn reset_to_idle(&mut self) {
        self.awaiting_final = false;
        self.audio.stop();
        self.asr.disconnect();
        self.text.clear();
        self.set_state(RecordingState::Idle);
        overlay::hide(&self.app);
    }

    fn handle_auth_failure(&mut self) {
        log_warn!("Auth failure, clearing cached params");
        params::clear();
        self.logged_in = false;
        self.reset_to_idle();
        self.refresh_tray();

        let tx = self.tx.clone();
        std::thread::spawn(move || {
            if win32::confirm_box("豆包登录凭证已失效，是否重新登录？") {
                let _ = tx.send(Event::ShowLogin);
            }
        });
    }

    // --- Login ---

    fn show_login(&mut self) {
        on_main(&self.app, |app| {
            if let Some(window) = app.get_webview_window(login::WINDOW_LABEL) {
                let _ = window.show();
                let _ = window.set_focus();
                return;
            }
            if let Err(e) = login::build(&app) {
                log_error!("Could not open the login window: {e}");
                win32::warn_box(&format!("打开登录窗口失败：{e}"));
            }
        });

        if !self.login_polling {
            self.login_polling = true;
            self.later(config::LOGIN_POLL_INTERVAL, Event::LoginPoll);
        }
    }

    fn poll_login(&mut self) {
        let Some(window) = self.app.get_webview_window(login::WINDOW_LABEL) else {
            self.login_polling = false;
            return;
        };

        match login::extract(&window) {
            Some(params) => {
                params::save(&params);
                login::clear_markers(&window);
                self.logged_in = true;
                self.login_polling = false;
                self.refresh_tray();

                // Mirrors destroyWebView(): drop the browser once we have the
                // credentials.
                on_main(&self.app, |app| {
                    if let Some(window) = app.get_webview_window(login::WINDOW_LABEL) {
                        let _ = window.close();
                    }
                });
                log_info!("Login complete, credentials stored");
            }
            None => self.later(config::LOGIN_POLL_INTERVAL, Event::LoginPoll),
        }
    }

    fn logout(&mut self) {
        params::clear();
        self.logged_in = false;
        self.login_polling = false;
        self.refresh_tray();

        on_main(&self.app, |app| {
            if let Some(window) = app.get_webview_window(login::WINDOW_LABEL) {
                let _ = window.close();
            }
        });

        // The login window owns its own WebView2 profile, so removing the folder
        // guarantees the next sign-in starts clean.
        let profile = config::local_dir().join("WebView2");
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(500));
            if profile.exists() {
                if let Err(e) = std::fs::remove_dir_all(&profile) {
                    log_warn!("Could not delete the WebView2 profile: {e}");
                }
            }
            win32::info_box("已退出登录。");
        });
    }

    // --- Menu actions ---

    fn show_help(&mut self) {
        let key = self.settings.toggle_key;
        on_main(&self.app, move |app| {
            if let Err(e) = help::show(&app, key) {
                log_error!("Could not open help: {e}");
            }
        });
    }

    fn check_update(&mut self) {
        let current = env!("CARGO_PKG_VERSION").to_string();
        std::thread::spawn(move || match updater::check(&current) {
            Ok(Some(release)) => {
                if win32::confirm_box(&format!(
                    "发现新版本 {}（当前 {current}），是否前往下载？",
                    release.version
                )) {
                    win32::shell_open(&release.url);
                }
            }
            Ok(None) => win32::info_box(&format!("已经是最新版本（{current}）。")),
            Err(e) => {
                log_warn!("Update check failed: {e}");
                win32::warn_box("检查更新失败，请稍后再试。");
            }
        });
    }

    fn quit(&mut self) {
        log_info!("Shutting down");
        self.audio.stop();
        self.asr.disconnect();
        let app = self.app.clone();
        on_main(&app, |app| app.exit(0));
    }

    // --- Helpers ---

    fn set_state(&mut self, state: RecordingState) {
        if self.state == state {
            return;
        }
        self.state = state;
        self.generation = self.generation.wrapping_add(1);
        self.refresh_tray();
    }

    fn push_overlay(&self, error: Option<&str>) {
        overlay::update(
            &self.app,
            OverlayState {
                text: error.unwrap_or(&self.text).to_string(),
                recording: self.state == RecordingState::Recording,
                error: error.is_some(),
            },
        );
    }

    fn schedule_reset(&mut self, delay: Duration) {
        self.later(delay, Event::ResetIdle(self.generation));
    }

    fn later(&self, delay: Duration, event: Event) {
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            std::thread::sleep(delay);
            let _ = tx.send(event);
        });
    }

    fn refresh_tray(&self) {
        let state = TrayState {
            logged_in: self.logged_in,
            recording: self.state != RecordingState::Idle,
            toggle_key: self.settings.toggle_key,
            suppress_toggle_key: self.settings.suppress_toggle_key,
        };
        on_main(&self.app, move |app| tray::refresh(&app, &state));
    }
}

fn on_main<F>(app: &AppHandle, action: F)
where
    F: FnOnce(AppHandle) + Send + 'static,
{
    let handle = app.clone();
    if let Err(e) = app.run_on_main_thread(move || action(handle)) {
        log_warn!("Could not dispatch to the main thread: {e}");
    }
}
