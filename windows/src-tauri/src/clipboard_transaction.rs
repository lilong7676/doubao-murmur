//! A short-lived clipboard loan. Snapshot at insertion, never at recording start.
//! Payloads remain in memory. Unknown/non-copyable formats fail before mutation.
use std::{ptr, thread, time::Duration};
use windows::{
    core::w,
    Win32::{
        Foundation::{GetLastError, GlobalFree, SetLastError, HANDLE, HGLOBAL, HWND, WIN32_ERROR},
        Graphics::Gdi::{CopyEnhMetaFileW, DeleteEnhMetaFile, DeleteObject, HENHMETAFILE, HGDIOBJ},
        System::{
            DataExchange::*,
            Memory::*,
            Ole::{OleDuplicateData, CLIPBOARD_FORMAT},
        },
        UI::WindowsAndMessaging::{
            CreateWindowExW, DestroyWindow, HWND_MESSAGE, WINDOW_EX_STYLE, WINDOW_STYLE,
        },
    },
};

const TEXT: u32 = 13;
const MAX_SNAPSHOT_BYTES: usize = 128 * 1024 * 1024;

struct Owner(HWND);
impl Owner {
    fn new() -> Result<Self, String> {
        unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                w!("STATIC"),
                w!("MurmurClipboard"),
                WINDOW_STYLE(0),
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                None,
                None,
                None,
            )
            .map(Self)
            .map_err(|e| e.to_string())
        }
    }
}
impl Drop for Owner {
    fn drop(&mut self) {
        unsafe {
            let _ = DestroyWindow(self.0);
        }
    }
}

struct Lock;
impl Lock {
    fn open(owner: &Owner) -> Result<Self, String> {
        for _ in 0..50 {
            if unsafe { OpenClipboard(owner.0) }.is_ok() {
                return Ok(Self);
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err("clipboard is busy".into())
    }
}
impl Drop for Lock {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseClipboard();
        }
    }
}

struct Entry {
    format: u32,
    handle: HANDLE,
}
impl Entry {
    fn bytes(format: u32, bytes: &[u8]) -> Result<Self, String> {
        unsafe {
            let mem = GlobalAlloc(GMEM_MOVEABLE, bytes.len().max(1)).map_err(|e| e.to_string())?;
            let dest = GlobalLock(mem);
            if dest.is_null() {
                let _ = GlobalFree(mem);
                return Err("GlobalLock failed".into());
            }
            ptr::copy_nonoverlapping(bytes.as_ptr(), dest.cast(), bytes.len());
            let _ = GlobalUnlock(mem);
            Ok(Self {
                format,
                handle: HANDLE(mem.0),
            })
        }
    }
    fn publish(&mut self) -> Result<(), String> {
        unsafe {
            SetClipboardData(self.format, self.handle)
                .map_err(|e| format!("format {}: {e}", self.format))?;
        }
        self.handle = HANDLE::default(); // Ownership transferred to Windows.
        Ok(())
    }
}
impl Drop for Entry {
    fn drop(&mut self) {
        if self.handle.is_invalid() {
            return;
        }
        unsafe {
            match self.format {
                2 | 9 => {
                    let _ = DeleteObject(HGDIOBJ(self.handle.0));
                }
                14 => {
                    let _ = DeleteEnhMetaFile(HENHMETAFILE(self.handle.0));
                }
                _ => {
                    let _ = GlobalFree(HGLOBAL(self.handle.0));
                }
            }
        }
    }
}

struct Snapshot(Vec<Entry>);
impl Snapshot {
    // Caller holds the clipboard lock across snapshot AND replacement.
    fn capture() -> Result<Self, String> {
        let mut entries = Vec::new();
        let mut format = 0;
        let mut total = 0usize;
        loop {
            unsafe {
                SetLastError(WIN32_ERROR(0));
            }
            format = unsafe { EnumClipboardFormats(format) };
            if format == 0 {
                if unsafe { GetLastError() }.0 != 0 {
                    return Err("format enumeration failed".into());
                }
                break;
            }
            // Owner-display, legacy metafiles and private GDI/pointer formats
            // cannot be safely cloned as HGLOBAL blocks. Preserve original data.
            if format == 3 || (0x80..0xc000).contains(&format) {
                return Err(format!(
                    "unsupported clipboard format {format}; original retained"
                ));
            }
            let source = unsafe { GetClipboardData(format) }
                .map_err(|e| format!("cannot snapshot format {format}: {e}"))?;
            let handle = unsafe {
                match format {
                    14 => HANDLE(CopyEnhMetaFileW(HENHMETAFILE(source.0), None).0),
                    2 | 9 => {
                        OleDuplicateData(source, CLIPBOARD_FORMAT(format as u16), GMEM_MOVEABLE)
                    }
                    _ => {
                        let size = GlobalSize(HGLOBAL(source.0));
                        total = total.checked_add(size).ok_or("snapshot size overflow")?;
                        if size == 0 || total > MAX_SNAPSHOT_BYTES {
                            return Err(
                                "clipboard snapshot unavailable or too large; original retained"
                                    .into(),
                            );
                        }
                        OleDuplicateData(source, CLIPBOARD_FORMAT(format as u16), GMEM_MOVEABLE)
                    }
                }
            };
            if handle.is_invalid() {
                return Err(format!(
                    "cannot duplicate format {format}; original retained"
                ));
            }
            entries.push(Entry { format, handle });
        }
        Ok(Self(entries))
    }
    fn publish(&mut self) -> Result<(), String> {
        unsafe {
            EmptyClipboard().map_err(|e| e.to_string())?;
        }
        for entry in &mut self.0 {
            entry.publish()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::System::{StationsAndDesktops::*, Threading::GetCurrentThreadId};

    fn read(format: u32) -> Vec<u8> {
        let owner = Owner::new().unwrap();
        let _lock = Lock::open(&owner).unwrap();
        unsafe {
            let mem = HGLOBAL(GetClipboardData(format).unwrap().0);
            let size = GlobalSize(mem);
            let p = GlobalLock(mem);
            assert!(!p.is_null());
            let value = std::slice::from_raw_parts(p.cast::<u8>(), size).to_vec();
            let _ = GlobalUnlock(mem);
            value
        }
    }
    fn put(formats: &[(u32, &[u8])]) {
        let owner = Owner::new().unwrap();
        let _lock = Lock::open(&owner).unwrap();
        unsafe {
            EmptyClipboard().unwrap();
        }
        for (format, bytes) in formats {
            Entry::bytes(*format, bytes).unwrap().publish().unwrap();
        }
    }
    fn text(value: &str) -> Vec<u8> {
        value
            .encode_utf16()
            .chain(Some(0))
            .flat_map(u16::to_le_bytes)
            .collect()
    }

    /// Run alone in a test subprocess: the new window station has its OWN
    /// clipboard. Never read, clear, or snapshot the interactive user's data.
    #[test]
    #[ignore = "explicit isolated Windows clipboard integration test"]
    fn isolated_clipboard_roundtrip_and_races() {
        unsafe {
            let old_station = GetProcessWindowStation().unwrap();
            let old_desktop = GetThreadDesktop(GetCurrentThreadId()).unwrap();
            // Named stations require admin. Windows selects the logon session's
            // noninteractive station. Refuse the interactive station or any data.
            let station = CreateWindowStationW(None, 0, 0x10000000, None).unwrap();
            let station_name = |handle: HWINSTA| {
                let mut name = [0u16; 256];
                GetUserObjectInformationW(
                    HANDLE(handle.0),
                    UOI_NAME,
                    Some(name.as_mut_ptr().cast()),
                    512,
                    None,
                )
                .unwrap();
                String::from_utf16_lossy(&name[..name.iter().position(|c| *c == 0).unwrap()])
            };
            assert_ne!(station_name(station), station_name(old_station));
            assert_ne!(station_name(station).to_lowercase(), "winsta0");
            SetProcessWindowStation(station).unwrap();
            let desktop = CreateDesktopW(
                w!("test"),
                None,
                None,
                DESKTOP_CONTROL_FLAGS(0),
                0x10000000,
                None,
            )
            .unwrap();
            SetThreadDesktop(desktop).unwrap();
            let result = std::panic::catch_unwind(|| {
                // A crashed earlier test can leave our literal fixture behind.
                if CountClipboardFormats() != 0 {
                    assert_eq!(
                        read(TEXT),
                        text("听写，标点后首字。"),
                        "refuse non-test clipboard content"
                    );
                    put(&[]);
                }
                {
                    let owner = Owner::new().unwrap();
                    let _lock = Lock::open(&owner).unwrap();
                    assert_eq!(
                        CountClipboardFormats(),
                        0,
                        "noninteractive clipboard must be empty"
                    );
                }
                let original = text("录音中刚复制的内容 A");
                let newer = text("粘贴期间新复制的 B");
                put(&[(TEXT, &original)]);
                let tx = Transaction::begin("听写，标点后首字。").unwrap();
                assert_ne!(read(TEXT), original);
                {
                    use windows::Win32::{
                        Foundation::{LPARAM, WPARAM},
                        UI::WindowsAndMessaging::{GetWindowTextW, SendMessageW, WM_PASTE},
                    };
                    let edit = Owner(
                        CreateWindowExW(
                            WINDOW_EX_STYLE(0),
                            w!("EDIT"),
                            w!(""),
                            WINDOW_STYLE(0x80),
                            0,
                            0,
                            200,
                            50,
                            HWND_MESSAGE,
                            None,
                            None,
                            None,
                        )
                        .unwrap(),
                    ); // ES_AUTOHSCROLL
                    SendMessageW(edit.0, WM_PASTE, WPARAM(0), LPARAM(0));
                    let mut buffer = [0u16; 100];
                    let count = GetWindowTextW(edit.0, &mut buffer);
                    assert_eq!(
                        String::from_utf16_lossy(&buffer[..count as usize]),
                        "听写，标点后首字。"
                    );
                }
                for name in [
                    w!("CanIncludeInClipboardHistory"),
                    w!("CanUploadToCloudClipboard"),
                ] {
                    let f = RegisterClipboardFormatW(name);
                    assert_eq!(&read(f)[..4], &0u32.to_le_bytes());
                }
                assert!(tx.restore().unwrap());
                assert_eq!(read(TEXT), original);

                // A copy while speaking is the baseline at begin, not an older value.
                put(&[(TEXT, &newer)]);
                assert!(Transaction::begin("第二次听写").unwrap().restore().unwrap());
                assert_eq!(read(TEXT), newer);

                // A newer copy during the loan wins, even if its text equals ours.
                let tx = Transaction::begin("同样的文字").unwrap();
                put(&[(TEXT, &text("同样的文字"))]);
                assert!(!tx.restore().unwrap());
                assert_eq!(read(TEXT), text("同样的文字"));

                // Valid 1x1, 32-bit DIB + PNG-like registered byte payload + HTML.
                let mut dib = vec![0u8; 44];
                dib[0..4].copy_from_slice(&40u32.to_le_bytes());
                dib[4..8].copy_from_slice(&1i32.to_le_bytes());
                dib[8..12].copy_from_slice(&1i32.to_le_bytes());
                dib[12..14].copy_from_slice(&1u16.to_le_bytes());
                dib[14..16].copy_from_slice(&32u16.to_le_bytes());
                dib[40..44].copy_from_slice(&[10, 20, 30, 255]);
                let png = RegisterClipboardFormatW(w!("PNG"));
                let html = RegisterClipboardFormatW(w!("HTML Format"));
                let payload = b"registered format byte preservation test\0";
                put(&[(8, &dib), (png, payload), (html, b"<b>rich</b>\0")]);
                assert!(Transaction::begin("图片保持").unwrap().restore().unwrap());
                assert_eq!(read(8), dib);
                assert_eq!(read(png), payload);
                assert_eq!(read(html), b"<b>rich</b>\0");

                // CF_HDROP holds a DROPFILES header and double-NUL UTF16 list.
                let mut files = vec![0u8; 20];
                files[0..4].copy_from_slice(&20u32.to_le_bytes());
                files[16..20].copy_from_slice(&1u32.to_le_bytes());
                files.extend(text("C:\\test.txt\0"));
                put(&[(15, &files)]);
                assert!(Transaction::begin("文件保持").unwrap().restore().unwrap());
                assert_eq!(read(15), files);

                // Private handle formats must abort BEFORE destroying data.
                put(&[(0x300, b"private fixture\0"), (TEXT, &original)]);
                assert!(Transaction::begin("must not replace").is_err());
                assert_eq!(read(TEXT), original);
                assert_eq!(read(0x300), b"private fixture\0");

                put(&[]);
                assert!(Transaction::begin("空剪贴板").unwrap().restore().unwrap());
                let owner = Owner::new().unwrap();
                let _lock = Lock::open(&owner).unwrap();
                assert_eq!(CountClipboardFormats(), 0);
            });
            if result.is_ok() {
                put(&[]);
            }
            SetThreadDesktop(old_desktop).unwrap();
            CloseDesktop(desktop).unwrap();
            SetProcessWindowStation(old_station).unwrap();
            CloseWindowStation(station).unwrap();
            if let Err(panic) = result {
                std::panic::resume_unwind(panic);
            }
        }
    }
}

fn history_flags() -> Result<Vec<Entry>, String> {
    let mut result = Vec::new();
    for name in [
        w!("CanIncludeInClipboardHistory"),
        w!("CanUploadToCloudClipboard"),
    ] {
        let format = unsafe { RegisterClipboardFormatW(name) };
        if format == 0 {
            return Err("cannot register history exclusion format".into());
        }
        result.push(Entry::bytes(format, &0u32.to_le_bytes())?);
    }
    Ok(result)
}

pub struct Transaction {
    owner: Owner,
    snapshot: Snapshot,
    sequence: u32,
}

impl Transaction {
    pub fn begin(text: &str) -> Result<Self, String> {
        let owner = Owner::new()?;
        let bytes: Vec<u8> = text
            .encode_utf16()
            .chain(Some(0))
            .flat_map(u16::to_le_bytes)
            .collect();
        let mut payload = Entry::bytes(TEXT, &bytes)?;
        let mut flags = history_flags()?;
        let lock = Lock::open(&owner)?;
        let mut snapshot = Snapshot::capture()?;
        unsafe {
            EmptyClipboard().map_err(|e| e.to_string())?;
        }
        let write = (|| {
            for flag in &mut flags {
                flag.publish()?;
            }
            payload.publish()
        })();
        if let Err(error) = write {
            let rollback = snapshot.publish();
            return Err(format!(
                "clipboard write failed: {error}; rollback: {rollback:?}"
            ));
        }
        drop(lock);
        // CloseClipboard may materialize synthesized formats and bump sequence.
        // Owner identity closes the race if another process copies just here.
        let sequence = unsafe { GetClipboardSequenceNumber() };
        Ok(Self {
            owner,
            snapshot,
            sequence,
        })
    }

    pub fn still_owned(&self) -> bool {
        unsafe {
            self.sequence != 0
                && GetClipboardSequenceNumber() == self.sequence
                && GetClipboardOwner().ok() == Some(self.owner.0)
        }
    }

    /// false means another copy has won. Check under the same lock as restore.
    pub fn restore(mut self) -> Result<bool, String> {
        let _lock = Lock::open(&self.owner)?;
        if !self.still_owned() {
            return Ok(false);
        }
        self.snapshot.publish()?;
        Ok(true)
    }
}
