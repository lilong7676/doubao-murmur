import Foundation
import Combine
import os

private let tmLogger = Logger(subsystem: "com.doubao.murmur", category: "TranscriptionManager")

@MainActor
class TranscriptionManager {
    /// How long the result stream must stay quiet before the transcript is accepted.
    /// After the audio ends the server keeps correcting for a few hundred ms; the
    /// gap between the last partial result and the final one measures ~150ms, so
    /// this needs to be comfortably longer than that.
    private static let finalResultQuietPeriod: TimeInterval = 0.25
    /// Upper bound on waiting for the final results before giving up.
    private static let stopSafetyTimeout: TimeInterval = 1.5

    private let appState: AppState
    private let webViewManager: WebViewManager
    private let overlayPanel: OverlayPanel
    private let hotkeyManager: HotkeyManager
    private let asrClient = DoubaoASRClient()
    private let audioCapture = AudioCaptureManager()

    /// True after stopRecording(): incoming results are the tail of the utterance
    /// and completion is deferred until the stream falls quiet.
    private var awaitingFinalResult = false

    /// Completion scheduled by `scheduleFinalCompletion()`, rescheduled on each
    /// late-arriving result so only the last one takes effect.
    private var finalResultDebounce: DispatchWorkItem?

    /// Called when auth has expired and user needs to re-login.
    var onAuthExpired: (() -> Void)?

    init(
        appState: AppState,
        webViewManager: WebViewManager,
        overlayPanel: OverlayPanel,
        hotkeyManager: HotkeyManager
    ) {
        self.appState = appState
        self.webViewManager = webViewManager
        self.overlayPanel = overlayPanel
        self.hotkeyManager = hotkeyManager
    }

    func start() {
        // Wire up hotkey events
        hotkeyManager.onHotkeyEvent = { [weak self] event in
            tmLogger.notice("Received hotkey event: \(String(describing: event))")
            Task { @MainActor in
                guard let self = self else { return }
                switch event {
                case .toggleRecording:
                    self.handleToggle()
                case .cancel:
                    self.handleCancel()
                }
            }
        }
        hotkeyManager.setEscapeHandlingEnabled(false)

        // Wire up native ASR client callbacks
        asrClient.onOpen = { [weak self] in
            Task { @MainActor in
                guard let self = self else { return }
                print("[TranscriptionManager] ASR WebSocket opened, buffered audio flushed")
                guard self.appState.recordingState == .starting else { return }
                self.setRecordingState(.recording)
                // Audio capture is already running; buffered data was flushed by ASR client.
            }
        }

        asrClient.onResult = { [weak self] text in
            Task { @MainActor in
                guard let self = self else { return }
                self.appState.transcriptionText = text
                if self.appState.recordingState == .starting {
                    self.setRecordingState(.recording)
                }
                if self.awaitingFinalResult {
                    self.scheduleFinalCompletion()
                }
            }
        }

        asrClient.onFinish = { [weak self] in
            Task { @MainActor in
                guard let self = self else { return }
                self.awaitingFinalResult = false
                if self.appState.recordingState == .stopping || self.appState.recordingState == .recording {
                    self.completeTranscription()
                }
            }
        }

        asrClient.onError = { [weak self] error in
            Task { @MainActor in
                guard let self = self else { return }
                guard self.appState.recordingState != .idle else { return }
                print("[TranscriptionManager] ASR error: \(error?.localizedDescription ?? "unknown")")

                // Keep the login: an expired one is reported through onAuthError.
                // Any connection error used to clear cached params, so a network
                // hiccup or the server ending a long session forced a re-login.
                self.appState.errorMessage = "连接出错"
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    self?.resetToIdle()
                }
            }
        }

        asrClient.onAuthError = { [weak self] in
            Task { @MainActor in
                guard let self = self else { return }
                self.handleAuthFailure()
            }
        }

        // Pipe captured audio directly to the ASR client
        audioCapture.onAudioData = { [weak self] data in
            self?.asrClient.sendAudio(data)
        }

        // Wire up overlay panel ESC key (works even with Secure Keyboard Entry)
        overlayPanel.onCancel = { [weak self] in
            Task { @MainActor in
                self?.handleCancel()
            }
        }

        hotkeyManager.start()
        print("[TranscriptionManager] ✅ Started")
    }

    // MARK: - Toggle Recording

    private func handleToggle() {
        print("[TranscriptionManager] handleToggle called, current state: \(appState.recordingState)")
        switch appState.recordingState {
        case .idle:
            startRecording()
        case .starting, .recording:
            stopRecording()
        case .stopping:
            break
        }
    }

    private func startRecording() {
        guard appState.loginStatus == .loggedIn else {
            print("[TranscriptionManager] ⚠️ Not logged in, showing login window")
            webViewManager.showLoginWindow()
            return
        }

        print("[TranscriptionManager] 🎤 Starting recording...")
        setRecordingState(.starting)
        appState.transcriptionText = ""
        appState.errorMessage = nil
        overlayPanel.showOverlay()

        // Start audio capture immediately — audio is buffered in ASR client until WebSocket connects.
        do {
            try audioCapture.startCapture()
        } catch {
            print("[TranscriptionManager] ❌ Audio capture failed: \(error)")
            appState.errorMessage = "麦克风启动失败"
            resetToIdle()
            return
        }

        Task {
            // 1. Try cached params from local config file first
            if let cachedParams = ASRParamsStore.load() {
                print("[TranscriptionManager] Using cached ASR params")
                self.asrClient.connect(params: cachedParams)
                return
            }

            // 2. Fall back to extracting from active WebView
            if webViewManager.isActive {
                guard let params = await webViewManager.extractASRParams() else {
                    self.appState.errorMessage = "无法获取连接参数，请重新登录"
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                        self?.resetToIdle()
                    }
                    return
                }
                // Save for future use
                ASRParamsStore.save(params)
                self.asrClient.connect(params: params)
                return
            }

            // 3. No params available at all
            self.appState.errorMessage = "无法获取连接参数，请重新登录"
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                self?.resetToIdle()
            }
        }
    }

    private func stopRecording() {
        print("[TranscriptionManager] ⏹ Stopping recording...")
        setRecordingState(.stopping)
        audioCapture.stopCapture()
        // Flushes trailing silence so the server finalises the last word.
        asrClient.finishSending()
        awaitingFinalResult = true

        // Safety timeout: if the stream never falls quiet, complete with current text.
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.stopSafetyTimeout) { [weak self] in
            guard let self = self, self.appState.recordingState == .stopping else { return }
            print("[TranscriptionManager] ⏱ Safety timeout, completing with current text")
            self.awaitingFinalResult = false
            self.completeTranscription()
        }
    }

    /// Wait for the result stream to go quiet before accepting the transcript.
    ///
    /// The server streams corrections right up to the end: after the audio stops it
    /// first replays the pending partial results, and only then sends the one that
    /// carries the final word. Completing on the first result that arrives after
    /// stopping would truncate the tail — which is exactly the "last two characters
    /// are missing" symptom — so each late result pushes the deadline back instead.
    private func scheduleFinalCompletion() {
        finalResultDebounce?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self = self, self.awaitingFinalResult else { return }
            print("[TranscriptionManager] Result stream quiet, completing")
            self.awaitingFinalResult = false
            self.completeTranscription()
        }
        finalResultDebounce = item
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.finalResultQuietPeriod, execute: item)
    }

    // MARK: - Auth Failure

    private func handleAuthFailure() {
        print("[TranscriptionManager] ⚠️ Auth failure detected, clearing cached params")
        ASRParamsStore.clear()
        audioCapture.stopCapture()
        asrClient.disconnect()
        resetToIdle()

        // Notify delegate (AppDelegate) to prompt user for re-auth
        appState.loginStatus = .notLoggedIn
        onAuthExpired?()
    }

    // MARK: - Cancel

    private func handleCancel() {
        tmLogger.notice("handleCancel called, current state: \(String(describing: self.appState.recordingState))")
        guard appState.recordingState != .idle else {
            tmLogger.warning("Cancel ignored: already idle")
            return
        }
        tmLogger.notice("Cancelling transcription...")
        awaitingFinalResult = false
        audioCapture.stopCapture()
        asrClient.disconnect()
        resetToIdle()
    }

    // MARK: - Completion

    private func completeTranscription() {
        let text = appState.transcriptionText.trimmingCharacters(in: .whitespacesAndNewlines)
        print("[TranscriptionManager] ✅ Completing transcription, text=\"\(text)\"")

        if !text.isEmpty {
            PasteHelper.copyAndPaste(text)
        }

        resetToIdle()
    }

    private func resetToIdle() {
        print("[TranscriptionManager] Resetting to idle")
        awaitingFinalResult = false
        finalResultDebounce?.cancel()
        finalResultDebounce = nil
        audioCapture.stopCapture()
        asrClient.disconnect()
        setRecordingState(.idle)
        appState.showOverlay = false
        appState.errorMessage = nil
        overlayPanel.hideOverlay()

        // Clear transcription text after a short delay (so paste can complete)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
            self?.appState.transcriptionText = ""
        }
    }

    private func setRecordingState(_ newState: RecordingState) {
        appState.recordingState = newState
        hotkeyManager.setEscapeHandlingEnabled(newState != .idle)
    }
}
