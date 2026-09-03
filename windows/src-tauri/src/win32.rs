//! Win32 bits Tauri does not expose.

use std::ffi::c_void;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HWND, MAX_PATH,
};
use windows::Win32::System::Threading::{
    CreateMutexW, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_FORMAT,
    PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::Shell::ShellExecuteW;
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetWindowLongPtrW, GetWindowThreadProcessId, IsWindow, MessageBoxW,
    SetWindowLongPtrW, GWL_EXSTYLE, IDYES, MB_ICONINFORMATION, MB_ICONQUESTION, MB_ICONWARNING,
    MB_OK, MB_SETFOREGROUND, MB_TOPMOST, MB_YESNO, SW_SHOWNORMAL, WS_EX_APPWINDOW,
    WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
};

use crate::log_warn;

pub fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Tauri hands back an HWND from its own `windows` crate version; going through
/// an integer keeps this independent of which version that is.
pub fn hwnd_from_isize(raw: isize) -> HWND {
    HWND(raw as *mut c_void)
}

/// Whether a raw HWND still refers to a live window.
///
/// The overlay can leave a zombie Tauri window object behind once the WebView2
/// browser process dies, and the manager keeps reporting it. IsWindow() is the
/// ground truth on the Win32 side.
pub fn is_window(raw: isize) -> bool {
    unsafe { IsWindow(hwnd_from_isize(raw)).as_bool() }
}

/// Makes a window that never takes focus and never appears in the taskbar or
/// Alt+Tab.
///
/// Load-bearing for the overlay: if it ever took focus the transcription would be
/// pasted into the overlay itself instead of the app the user was typing in.
pub fn make_non_activating(hwnd: HWND) {
    unsafe {
        let current = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let updated = (current
            | WS_EX_NOACTIVATE.0 as isize
            | WS_EX_TOOLWINDOW.0 as isize)
            & !(WS_EX_APPWINDOW.0 as isize);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, updated);
    }
}

/// Lowercased executable name of the foreground window's process, without the
/// extension.
pub fn foreground_process_name() -> Option<String> {
    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.0.is_null() {
            return None;
        }

        let mut pid: u32 = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid == 0 {
            return None;
        }

        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;

        let mut buffer = [0u16; MAX_PATH as usize];
        let mut length = buffer.len() as u32;
        let query = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_FORMAT(0),
            windows::core::PWSTR(buffer.as_mut_ptr()),
            &mut length,
        );
        let _ = CloseHandle(handle);
        query.ok()?;

        let path = String::from_utf16_lossy(&buffer[..length as usize]);
        let name = path
            .rsplit('\\')
            .next()
            .unwrap_or(&path)
            .rsplit_once('.')
            .map(|(stem, _)| stem.to_string())
            .unwrap_or_else(|| path.clone());

        (!name.is_empty()).then(|| name.to_lowercase())
    }
}

/// Opens a file or URL with the shell's default handler.
pub fn shell_open(target: &str) {
    let verb = wide("open");
    let target_wide = wide(target);
    unsafe {
        let result = ShellExecuteW(
            HWND::default(),
            PCWSTR(verb.as_ptr()),
            PCWSTR(target_wide.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        );
        // ShellExecuteW returns a value <= 32 on failure.
        if result.0 as isize <= 32 {
            log_warn!("ShellExecute failed for {target}");
        }
    }
}

/// Native message boxes: a tray-only app has no reliably visible window to parent
/// a WebView dialog to, and this keeps errors visible even if the UI is broken.
fn message_box(text: &str, style: u32) -> i32 {
    let body = wide(text);
    let caption = wide("Doubao Murmur");
    unsafe {
        MessageBoxW(
            HWND::default(),
            PCWSTR(body.as_ptr()),
            PCWSTR(caption.as_ptr()),
            windows::Win32::UI::WindowsAndMessaging::MESSAGEBOX_STYLE(
                style | MB_SETFOREGROUND.0 | MB_TOPMOST.0,
            ),
        )
        .0
    }
}

/// Returns false when another instance already holds the mutex. The handle is
/// intentionally leaked: it should live as long as the process.
pub fn acquire_single_instance() -> bool {
    let name = wide(r"Local\DoubaoMurmur.SingleInstance");
    unsafe {
        match CreateMutexW(None, true, PCWSTR(name.as_ptr())) {
            Ok(_handle) => GetLastError() != ERROR_ALREADY_EXISTS,
            // If the mutex cannot be created, allow the launch rather than leaving
            // the user with an app that refuses to start.
            Err(e) => {
                log_warn!("Single-instance check failed, continuing: {e}");
                true
            }
        }
    }
}

pub fn info_box(text: &str) {
    message_box(text, MB_OK.0 | MB_ICONINFORMATION.0);
}

pub fn warn_box(text: &str) {
    message_box(text, MB_OK.0 | MB_ICONWARNING.0);
}

pub fn confirm_box(text: &str) -> bool {
    message_box(text, MB_YESNO.0 | MB_ICONQUESTION.0) == IDYES.0
}
