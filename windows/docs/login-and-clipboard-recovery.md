# Windows login and clipboard recovery

## Why these changes

### Transport failures were deleting valid cached credentials

The Windows controller previously treated every `AsrEvent::Error` as an
authentication failure when cached parameters had been used. A reproduced case
connected successfully, then received a normal WebSocket close (1000). The
controller reported an expired login and deleted the saved parameters.

Generic errors now stop the current microphone/socket session and display a
retry message without deleting credentials. The existing explicit `AuthError`
path still clears rejected credentials and offers sign-in. This does not make
credentials permanent or change the service's authentication rules.

### Clipboard insertion replaced the user's previous copy

On one Windows installation, Weixin 4.1.13.65 with a Pinyin IME corrupted some
characters following punctuation in Typing mode. Switching the IME between its
Chinese and English modes did not help; using an English keyboard layout or
Clipboard mode did. The same Typing behavior occurred in the original release.
This change does **not** claim to fix the Unicode injection/IME interaction.
It makes the existing Clipboard mode more useful by restoring the previous copy.

## Clipboard transaction

1. A single FIFO worker handles completed transcriptions.
2. Immediately before insertion, snapshot the current clipboard and replace it
   while holding the clipboard lock. Nothing is captured at recording start:
   copies made while speaking become the eventual restore target.
3. Publish the transcript with Windows history/cloud exclusion flags, then use
   the existing paste shortcut selection.
4. Wait 700 ms after successful key injection before attempting restoration.
5. Under the clipboard lock, verify both the sequence number and owner. If a
   newer copy exists, keep it; otherwise restore the snapshot. The sequence is
   sampled **after** `CloseClipboard`, which may itself update the sequence.

Snapshots stay in memory. No clipboard payload is logged. HGLOBAL-backed formats
are duplicated byte-for-byte; bitmap, palette and enhanced-metafile handles use
their corresponding copying/cleanup rules. Unsupported private/legacy formats,
failed snapshots and HGLOBAL snapshots over 128 MiB abort before replacement.
The original clipboard is preserved, and the user is told that insertion failed.

## Limits

- The restore delay is a compatibility fallback, not confirmation that every
  target application has consumed the clipboard. Very slow applications may
  need further work.
- Manual paste during the temporary loan can paste the transcript again. A
  shorter delay alone cannot eliminate this race. This was observed and accepted
  in local testing.
- A new copy during the loan is preserved, even when its text equals the
  transcript. A new copy before the simulated paste cancels that insertion.
- A crash/forced exit during the loan or prolonged clipboard contention can
  prevent restoration. Arbitrary application-private formats are not promised.
- Windows history flags do not guarantee compliance by third-party managers.
- Typing mode, hotkey defaults and the user's autostart setting are unchanged.

## Automated validation

On Windows with Rust and the MSVC toolchain:

```powershell
cargo test --manifest-path windows/src-tauri/Cargo.toml
cargo test --manifest-path windows/src-tauri/Cargo.toml clipboard_transaction::tests::isolated_clipboard_roundtrip_and_races -- --ignored --exact --nocapture --test-threads=1
```

The native integration test must be run separately. It selects a noninteractive
window station, rejects the interactive station and non-test clipboard content,
and does not operate on the user's interactive clipboard. Restricted environments
may reject station/desktop creation; that is not evidence of a passed test.

Coverage includes generic-error vs explicit-auth classification, native EDIT
paste, text restoration, the latest pre-insertion copy, newer-copy wins (including
identical text), a valid 1x1 DIB, registered-format bytes, a synthetic file list,
empty clipboard and failure-before-mutation for private formats. The PNG-named
fixture checks byte preservation, not PNG decoding. Local results: 37 regular
tests and the separate native integration test passed.

## Manual validation on a Windows x64 build

- Weixin with Pinyin: Clipboard insertion preserved punctuation and characters.
- A real image could be pasted again after dictation restored the clipboard.
- Repeated manual paste during the short loan showed the documented limitation.
- Tray exit/relaunch preserved login.
- A full computer restart automatically launched the app and preserved login.

These are local user acceptance results, not guarantees across all applications,
IMEs or architectures. No protocol endpoint changes, credentials, logs, binary
artifacts or local release-version stamps are included in this contribution.
