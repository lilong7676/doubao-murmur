//! Copies the transcription to the clipboard and synthesises the paste shortcut.
//! Mirrors PasteHelper.swift.

use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_UNICODE, VIRTUAL_KEY,
    VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_RCONTROL, VK_RMENU, VK_RSHIFT, VK_RWIN,
};

use crate::settings::PasteMode;
use crate::win32;
use crate::{log_error, log_info, log_warn};

// Scan codes (set 1).
const SC_LCONTROL: u16 = 0x1D;
const SC_LSHIFT: u16 = 0x2A;
const SC_V: u16 = 0x2F;
const SC_INSERT: u16 = 0x52;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Shortcut {
    CtrlV,
    CtrlShiftV,
    ShiftInsert,
}

/// Apps whose paste shortcut is not Ctrl+V. Windows Terminal and modern conhost
/// both accept Ctrl+V, so unlike Linux this list stays very short.
fn shortcut_for(process: Option<&str>) -> Shortcut {
    match process {
        Some("mintty") => Shortcut::CtrlShiftV, // Git Bash / Cygwin
        Some("putty") => Shortcut::ShiftInsert,
        Some("kitty") | Some("alacritty") | Some("wezterm-gui") => Shortcut::CtrlShiftV,
        _ => Shortcut::CtrlV,
    }
}

pub fn deliver(text: &str, mode: PasteMode) {
    // One worker preserves completion order and prevents overlapping restores.
    static QUEUE: std::sync::OnceLock<std::sync::mpsc::Sender<(String, PasteMode)>> =
        std::sync::OnceLock::new();
    let queue = QUEUE.get_or_init(|| {
        let (tx, rx) = std::sync::mpsc::channel::<(String, PasteMode)>();
        std::thread::spawn(move || {
            for (text, mode) in rx {
                deliver_now(&text, mode);
            }
        });
        tx
    });
    if queue.send((text.to_owned(), mode)).is_err() {
        log_error!("Text delivery worker unavailable");
    }
}

fn deliver_now(text: &str, mode: PasteMode) {
    if text.is_empty() {
        return;
    }

    if mode == PasteMode::Typing {
        release_stuck_modifiers();
        type_unicode(text);
        log_info!("Typed {} chars directly", text.chars().count());
        return;
    }

    let process = win32::foreground_process_name();
    let shortcut = shortcut_for(process.as_deref());
    release_stuck_modifiers();
    let transaction = match crate::clipboard_transaction::Transaction::begin(text) {
        Ok(transaction) => transaction,
        Err(e) => {
            log_error!("Protected clipboard insertion aborted: {e}");
            win32::warn_box(
                "无法安全备份剪贴板，已保留原内容，本次文字未输入。请复制普通文本或图片后重试。",
            );
            return;
        }
    };
    std::thread::sleep(crate::config::PASTE_DELAY);
    if !transaction.still_owned() {
        log_warn!("Clipboard changed before paste; insertion cancelled to preserve new copy");
        return;
    }
    let pasted = send_shortcut(shortcut);
    // SendInput only queues keys. Allow the target time to read the clipboard.
    // This is a conservative fallback, not a cross-application paste ACK.
    if pasted {
        std::thread::sleep(std::time::Duration::from_millis(700));
    }
    match transaction.restore() {
        Ok(true) => log_info!("Original clipboard restored"),
        Ok(false) => log_info!("New clipboard content retained; restore skipped"),
        Err(e) => log_warn!("Clipboard restore failed: {e}"),
    }
    if pasted {
        log_info!(
            "Pasted into '{}' using {shortcut:?}",
            process.as_deref().unwrap_or("unknown")
        );
    }
}

/// Drops any modifier the user might still be holding. Without this a held Alt
/// turns our Ctrl+V into Ctrl+Alt+V and nothing is pasted.
fn release_stuck_modifiers() {
    let modifiers = [
        VK_LMENU,
        VK_RMENU,
        VK_LCONTROL,
        VK_RCONTROL,
        VK_LSHIFT,
        VK_RSHIFT,
        VK_LWIN,
        VK_RWIN,
    ];

    let mut inputs = Vec::new();
    for key in modifiers {
        let down = unsafe { GetAsyncKeyState(key.0 as i32) };
        if (down as u16 & 0x8000) == 0 {
            continue;
        }
        inputs.push(key_input(key.0, 0, KEYEVENTF_KEYUP));
    }

    if !inputs.is_empty() {
        send(&inputs);
    }
}

fn send_shortcut(shortcut: Shortcut) -> bool {
    let inputs = match shortcut {
        Shortcut::CtrlShiftV => vec![
            scan_input(SC_LCONTROL, false, false),
            scan_input(SC_LSHIFT, false, false),
            scan_input(SC_V, false, false),
            scan_input(SC_V, true, false),
            scan_input(SC_LSHIFT, true, false),
            scan_input(SC_LCONTROL, true, false),
        ],
        Shortcut::ShiftInsert => vec![
            scan_input(SC_LSHIFT, false, false),
            scan_input(SC_INSERT, false, true),
            scan_input(SC_INSERT, true, true),
            scan_input(SC_LSHIFT, true, false),
        ],
        Shortcut::CtrlV => vec![
            scan_input(SC_LCONTROL, false, false),
            scan_input(SC_V, false, false),
            scan_input(SC_V, true, false),
            scan_input(SC_LCONTROL, true, false),
        ],
    };

    send(&inputs)
}

fn type_unicode(text: &str) {
    let mut inputs = Vec::new();
    for unit in text.encode_utf16() {
        inputs.push(key_input(0, unit, KEYEVENTF_UNICODE));
        inputs.push(key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));

        // Chunked so a long transcription does not allocate one huge block and
        // so the target app can keep up.
        if inputs.len() >= 200 {
            send(&inputs);
            inputs.clear();
        }
    }

    if !inputs.is_empty() {
        send(&inputs);
    }
}

/// Scan-code input: some apps and games ignore virtual-key events.
fn scan_input(scan_code: u16, key_up: bool, extended: bool) -> INPUT {
    let mut flags = KEYEVENTF_SCANCODE;
    if key_up {
        flags |= KEYEVENTF_KEYUP;
    }
    if extended {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    key_input(0, scan_code, flags)
}

fn key_input(virtual_key: u16, scan_code: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(virtual_key),
                wScan: scan_code,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

fn send(inputs: &[INPUT]) -> bool {
    let sent = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent as usize != inputs.len() {
        // Most common cause: the focused window belongs to an elevated process and
        // UIPI blocks synthetic input from this non-elevated one.
        log_warn!(
            "SendInput delivered {sent}/{} events; is the target window elevated?",
            inputs.len()
        );
    }
    sent as usize == inputs.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminals_that_need_ctrl_shift_v_are_recognised() {
        assert_eq!(shortcut_for(Some("mintty")), Shortcut::CtrlShiftV);
        assert_eq!(shortcut_for(Some("putty")), Shortcut::ShiftInsert);
    }

    #[test]
    fn everything_else_uses_plain_ctrl_v() {
        assert_eq!(shortcut_for(Some("chrome")), Shortcut::CtrlV);
        // Windows Terminal handles Ctrl+V itself.
        assert_eq!(shortcut_for(Some("windowsterminal")), Shortcut::CtrlV);
        assert_eq!(shortcut_for(None), Shortcut::CtrlV);
    }
}
