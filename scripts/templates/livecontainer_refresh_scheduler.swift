// Injected into the LiveContainer host, not its embedded SideStore executable.
// There is no timer, persistent network probe, or permanently running task.
@MainActor
enum LiveContainerAutoRefreshScheduler {
    static let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    static let enabledKey = "liveContainerAutoRefreshEnabled"
    static let frequencyKey = "liveContainerAutoRefreshFrequency"
    static let weekdayKey = "liveContainerAutoRefreshWeekday"
    static let minutesKey = "liveContainerAutoRefreshMinutes"
    static let lastResultKey = "liveContainerAutoRefreshLastResult"
    static let lastDateKey = "liveContainerAutoRefreshLastDate"
    static let historyKey = "liveContainerAutoRefreshHistory"
    static let earliestEligibleKey = "liveContainerAutoRefreshEarliestEligibleAt"
    static let deadlineKey = "liveContainerAutoRefreshTargetDeadline"
    static let nextRetryKey = "liveContainerAutoRefreshNextRetryAt"
    static let lastTaskKey = "liveContainerAutoRefreshLastTaskTrigger"
    static let lastAttemptKey = "liveContainerAutoRefreshLastAttempt"
    static let activeRunKey = "liveContainerAutoRefreshActiveRunID"
    static let retryCountKey = "liveContainerAutoRefreshRetryCount"
    static let hostHandoffKey = "liveContainerAutoRefreshHostHandoff"
    static let hostHandoffRunKey = "liveContainerAutoRefreshHostHandoffRunID"
    static let hostHandoffStartedKey = "liveContainerAutoRefreshHostHandoffStartedAt"
    static let hostPreviousExpirationKey = "liveContainerAutoRefreshHostPreviousExpiration"
    static let verificationKey = "liveContainerAutoRefreshVerification"
    static let expectedRunKey = "liveContainerAutoRefreshExpectedRunID"
    static let hostVerifiedKey = "liveContainerAutoRefreshHostVerifiedAfterRelaunch"
    static let strategyKey = "liveContainerAutoRefreshStrategy"
    static let alarmScheduledKey = "liveContainerAutoRefreshAlarmScheduled"
    static let alarmDeadlineKey = "liveContainerAutoRefreshAlarmDeadline"
    static let healthStateKey = "liveContainerAutoRefreshHealthState"
    static let lastErrorKey = "liveContainerAutoRefreshLastError"
    static let lastSuccessfulKey = "liveContainerAutoRefreshLastSuccessfulRefresh"
    static let hostBaselineKey = "liveContainerAutoRefreshInstalledHostBaseline"
    static let satisfiedDeadlineKey = "liveContainerAutoRefreshSatisfiedDeadline"
    static let warnedDeadlineKey = "liveContainerAutoRefreshWarnedDeadline"
    static let configurationKey = "liveContainerAutoRefreshConfiguration"
    static let retryExhaustedKey = "liveContainerAutoRefreshRetryExhausted"
    static let guestDiagnosticsKey = "liveContainerAutoRefreshGuestDiagnostics"
    static let guestDiagnosticWarningKey = "liveContainerAutoRefreshGuestDiagnosticWarning"
    static let guestDiagnosticAffectedIDsKey = "liveContainerAutoRefreshGuestDiagnosticAffectedIDs"
    static let guestDiagnosticsDidChange = Notification.Name("LiveContainerAutoRefreshGuestDiagnosticsChanged")
    static let warningIdentifier = "LiveContainerAutoRefresh.deadline"
    static let leadTime: TimeInterval = 60 * 60 // Provisional policy, not a timing guarantee.
    static let coalescingWindow: TimeInterval = 60

    private static var identifiers: LiveContainerRefreshTaskIdentifiers?
    private static var processingRegistered = false
    private static var watchdogRegistered = false
    private static var registered = false
    private static var activeRun: UUID?
    // Capture the real host before LiveContainer changes Bundle.main for guests.
    private static var hostBundle: Bundle?

    static func requestNotificationPermission() async {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .notDetermined else { return }
        do {
            let granted = try await center.requestAuthorization(options: [.alert, .sound])
            print("[LIVE_CONTAINER_REFRESH] NOTIFICATION_PERMISSION granted=\(granted)")
        } catch {
            print("[LIVE_CONTAINER_REFRESH] NOTIFICATION_PERMISSION_FAIL error=\(error.localizedDescription)")
        }
    }

    private static func notify(title: String, body: String, kind: String) {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else {
                print("[LIVE_CONTAINER_REFRESH] NOTIFICATION_SKIPPED kind=\(kind) reason=not_authorized")
                return
            }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            center.add(UNNotificationRequest(identifier: "LiveContainerAutoRefresh.\(kind)", content: content, trigger: nil)) { error in
                if let error {
                    print("[LIVE_CONTAINER_REFRESH] NOTIFICATION_FAIL kind=\(kind) error=\(error.localizedDescription)")
                } else {
                    // Accepted by notification service, not proof the user saw it.
                    print("[LIVE_CONTAINER_REFRESH] NOTIFICATION_PASS kind=\(kind)")
                }
            }
        }
    }

    private static func record(source: String, result: String, detail: String = "") {
        let now = Date()
        let entry = ["date": ISO8601DateFormatter().string(from: now), "source": source,
                     "result": result, "detail": String(detail.prefix(300))]
        var history = defaults.array(forKey: historyKey) as? [[String: String]] ?? []
        history.insert(entry, at: 0)
        defaults.set(Array(history.prefix(50)), forKey: historyKey)
        defaults.set(result, forKey: lastResultKey)
        defaults.set(now, forKey: lastDateKey)
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshHistoryChanged"), object: nil)
    }

    private static func compactWorkIsDue(now: Date, manual: Bool = false) -> Bool {
        LiveContainerRefreshPolicy.workIsDue(now: now,
            eligible: defaults.object(forKey: earliestEligibleKey) as? Date,
            retry: defaults.object(forKey: nextRetryKey) as? Date,
            pendingHandoff: defaults.bool(forKey: hostHandoffKey),
            retryExhausted: defaults.bool(forKey: retryExhaustedKey), manual: manual)
    }

    private static func observeMissedDeadline(now: Date) {
        guard defaults.bool(forKey: enabledKey),
              let deadline = defaults.object(forKey: deadlineKey) as? Date, now > deadline,
              (defaults.object(forKey: satisfiedDeadlineKey) as? Date) != deadline,
              (defaults.object(forKey: warnedDeadlineKey) as? Date) != deadline else { return }
        defaults.set(deadline, forKey: warnedDeadlineKey)
        defaults.set("REFRESH_DEADLINE_MISSED", forKey: healthStateKey)
        record(source: "watchdog", result: "missed_window", detail: "No verified refresh for the expected deadline. The task may have been delayed, failed, or never launched.")
        print("[LIVE_CONTAINER_REFRESH] MISSED_BACKGROUND_REFRESH deadline=\(deadline.timeIntervalSince1970)")
    }

    private static func beginRun(source: String, manual: Bool) -> UUID? {
        guard activeRun == nil else { return nil }
        if !manual, let last = defaults.object(forKey: lastAttemptKey) as? Date,
           Date().timeIntervalSince(last) < coalescingWindow { return nil }
        let id = UUID()
        activeRun = id
        defaults.set(id.uuidString, forKey: activeRunKey)
        defaults.set(id.uuidString, forKey: expectedRunKey)
        defaults.set(Date(), forKey: lastAttemptKey)
        defaults.removeObject(forKey: verificationKey)
        defaults.set(false, forKey: hostVerifiedKey)
        defaults.set("REFRESH_IN_PROGRESS", forKey: healthStateKey)
        // Snapshot actual installed host metadata before the refresh engine can
        // optimistically update its database. Absence never becomes success.
        if let bundle = hostBundle, let bundleID = bundle.bundleIdentifier,
           let profile = try? LiveContainerHostProfile.read(
               at: bundle.bundleURL.appendingPathComponent("embedded.mobileprovision"), expectedBundleID: bundleID) {
            defaults.set(["run_id": id.uuidString, "identifier": profile.identifier,
                          "uuid": profile.uuid, "expiration": profile.expiration], forKey: hostBaselineKey)
        } else {
            defaults.removeObject(forKey: hostBaselineKey)
        }
        print("[LIVE_CONTAINER_REFRESH] RUN_BEGIN source=\(source) run_id=\(id.uuidString)")
        notify(title: "Refresh started", body: "Checking SideStore refresh requirements. Success is not yet confirmed.", kind: "started")
        return id
    }

    private static func endRun(_ id: UUID) {
        guard activeRun == id else { return }
        activeRun = nil
        defaults.removeObject(forKey: activeRunKey)
        if defaults.string(forKey: expectedRunKey) == id.uuidString {
            defaults.removeObject(forKey: expectedRunKey)
        }
    }

    private static func performRefresh(runID: UUID) async throws {
        guard #available(iOS 17.0, *) else {
            throw NSError(domain: "LiveContainerRefresh.UnsupportedOS", code: 17,
                userInfo: [NSLocalizedDescriptionKey: "This automatic embedded bridge requires iOS 17 or later. Use the existing embedded SideStore manual refresh on this version."])
        }
        try Task.checkCancellation()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_ATTEMPT_STARTED run_id=\(runID.uuidString)")
        try await LiveContainerRefreshBridge.refreshAllApps()
        try Task.checkCancellation()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_PIPELINE_RETURNED run_id=\(runID.uuidString)")
    }

    private static func verifyRefreshManifest(runID: String) -> (verified: Bool, hostHandoff: Bool, reason: String) {
        let pending = defaults.bool(forKey: hostHandoffKey)
        guard let manifest = defaults.dictionary(forKey: verificationKey) else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=manifest_missing run_id=\(runID)")
            return (false, pending, "SideStore returned without sharing installation results with LiveContainer. Refresh is unconfirmed. Open embedded SideStore and check its refresh history before retrying.")
        }
        guard manifest["run_id"] as? String == runID else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=run_mismatch expected_run=\(runID) actual_run=\(manifest["run_id"] as? String ?? "missing")")
            return (false, pending, "LiveContainer received results for a different refresh attempt. This attempt could not be verified. Check embedded SideStore history and retry once the previous refresh finishes.")
        }
        guard let results = manifest["results"] as? [[String: Any]], !results.isEmpty else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=results_empty run_id=\(runID)")
            return (false, pending, "SideStore returned no app installation results. No successful refresh was confirmed. Open embedded SideStore to check eligible apps and its refresh history.")
        }
        guard let expected = manifest["expected_ids"] as? [String], !expected.isEmpty,
              Set(results.compactMap { $0["bundle_id"] as? String }) == Set(expected) else {
            return (false, pending, "verification_manifest_incomplete")
        }
        if results.contains(where: { ($0["success"] as? Bool) != true }) {
            return (false, pending, "verification_manifest_contains_failure")
        }
        return pending ? (false, true, "host_handoff_awaiting_relaunch") : (true, false, "verified_installed_app_records")
    }

    private static func markVerified(source: String, detail: String) {
        defaults.set(Date().addingTimeInterval(6 * 60 * 60), forKey: earliestEligibleKey)
        defaults.removeObject(forKey: nextRetryKey)
        defaults.set(0, forKey: retryCountKey)
        defaults.set(false, forKey: retryExhaustedKey)
        defaults.set("REFRESH_SUCCEEDED", forKey: healthStateKey)
        defaults.set(Date(), forKey: lastSuccessfulKey)
        defaults.removeObject(forKey: lastErrorKey)
        if let deadline = defaults.object(forKey: deadlineKey) as? Date {
            defaults.set(deadline, forKey: satisfiedDeadlineKey)
        }
        record(source: source, result: "verified", detail: detail)
        cancelDeadlineProtection()
        notify(title: "Refresh completed", body: detail, kind: "verified")
    }

    private static func verifyPendingHostHandoff() {
        guard activeRun == nil, defaults.bool(forKey: hostHandoffKey) else { return }
        guard let baseline = defaults.dictionary(forKey: hostBaselineKey),
              let runID = baseline["run_id"] as? String,
              runID == defaults.string(forKey: hostHandoffRunKey),
              let previous = baseline["expiration"] as? Date,
              let bundle = hostBundle, let bundleID = bundle.bundleIdentifier else {
            defaults.set("HOST_REFRESH_UNVERIFIED", forKey: healthStateKey)
            defaults.set("installed_host_baseline_unavailable", forKey: lastErrorKey)
            defaults.removeObject(forKey: hostHandoffKey)
            defaults.set(true, forKey: retryExhaustedKey)
            record(source: "relaunch", result: "host_unverified", detail: "The old run has no installed-profile baseline. Its result is unknown; a new manual attempt can establish one.")
            return
        }
        do {
            let current = try LiveContainerHostProfile.read(
                at: bundle.bundleURL.appendingPathComponent("embedded.mobileprovision"), expectedBundleID: bundleID)
            guard current.expiration > previous, current.expiration > Date() else {
                defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
                defaults.set("installed_host_expiration_not_advanced", forKey: lastErrorKey)
                print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_UNVERIFIED reason=installed_profile_expiration_not_advanced")
                if let started = defaults.object(forKey: hostHandoffStartedKey) as? Date,
                   Date().timeIntervalSince(started) >= 180 {
                    defaults.removeObject(forKey: hostHandoffKey)
                    defaults.set(true, forKey: retryExhaustedKey)
                    defaults.set("HOST_REFRESH_FAILED", forKey: healthStateKey)
                    record(source: "relaunch", result: "host_refresh_failed", detail: "The installed host profile did not advance after replacement. Retry manually; no success was recorded.")
                    notify(title: "LiveContainer refresh not confirmed", body: "Its installed signing validity did not advance. Open SideStore and retry the refresh.", kind: "host_failed")
                }
                return
            }
            defaults.set(true, forKey: hostVerifiedKey)
            defaults.removeObject(forKey: hostHandoffKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_VERIFIED evidence=installed_profile_expiration_advanced")
            let batch = verifyRefreshManifest(runID: runID)
            if batch.verified {
                markVerified(source: "relaunch", detail: "LiveContainer's installed profile renewed; all requested app results were confirmed.")
            } else {
                // Host replacement can kill the process before it writes the
                // final batch results. Host success is not whole-batch success.
                defaults.set("HOST_REFRESH_VERIFIED", forKey: healthStateKey)
                record(source: "relaunch", result: "host_verified_batch_unconfirmed", detail: batch.reason)
                notify(title: "LiveContainer refreshed", body: "Its installed profile renewed. Some batch results remain unconfirmed; check SideStore history.", kind: "host_verified")
            }
        } catch {
            defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
            defaults.set(error.localizedDescription, forKey: lastErrorKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_UNVERIFIED error=\(error.localizedDescription)")
        }
    }

    struct GuestDiagnostic: Equatable {
        let bundleID: String
        let category: String
        let executableFound: Bool
        let readable: Bool
        let checkResult: Bool?
        let reason: String

        var affected: Bool {
            !executableFound || !readable || checkResult != true
        }

        var persisted: [String: String] {
            [
                "bundle_id": bundleID,
                "category": category,
                "executable_found": executableFound ? "true" : "false",
                "readable": readable ? "true" : "false",
                "check_result": checkResult.map { $0 ? "pass" : "fail" } ?? "not_checked",
                "reason": reason
            ]
        }
    }

    struct GuestDiagnosticInput {
        let bundleID: String
        let category: String
        let bundlePath: String?
    }

    static func collectGuestDiagnostics() -> [GuestDiagnostic] {
        let normal = DataManager.shared.model.apps.map {
            GuestDiagnosticInput(bundleID: $0.appInfo.bundleIdentifier(), category: "normal",
                                 bundlePath: $0.appInfo.bundlePath())
        }
        let hidden = DataManager.shared.model.hiddenApps.map {
            GuestDiagnosticInput(bundleID: $0.appInfo.bundleIdentifier(), category: "hidden",
                                 bundlePath: $0.appInfo.bundlePath())
        }
        return (normal + hidden).map(diagnoseGuest)
    }

    static func diagnoseGuest(_ input: GuestDiagnosticInput) -> GuestDiagnostic {
        guard let bundlePath = input.bundlePath,
              let executable = Bundle(path: bundlePath)?.executableURL else {
            return GuestDiagnostic(bundleID: input.bundleID, category: input.category,
                                    executableFound: false, readable: false, checkResult: nil,
                                    reason: "missing_executable")
        }

        let executableFound = FileManager.default.fileExists(atPath: executable.path)
        let readable = executableFound && FileManager.default.isReadableFile(atPath: executable.path)
        guard readable else {
            return GuestDiagnostic(bundleID: input.bundleID, category: input.category,
                                    executableFound: executableFound, readable: false, checkResult: nil,
                                    reason: executableFound ? "unreadable" : "missing_executable")
        }

        let checkResult = executable.path.withCString { checkCodeSignature($0) }
        return GuestDiagnostic(bundleID: input.bundleID, category: input.category,
                                executableFound: true, readable: true, checkResult: checkResult,
                                reason: checkResult ? "signature_check_passed" : "signature_check_false")
    }

    private static func persistGuestDiagnostics(_ diagnostics: [GuestDiagnostic]) {
        let affectedIDs = diagnostics.reduce(into: [String]()) { ids, diagnostic in
            guard diagnostic.affected, !ids.contains(diagnostic.bundleID) else { return }
            ids.append(diagnostic.bundleID)
        }
        defaults.set(diagnostics.map(\.persisted), forKey: guestDiagnosticsKey)
        defaults.set(!affectedIDs.isEmpty, forKey: guestDiagnosticWarningKey)
        defaults.set(affectedIDs, forKey: guestDiagnosticAffectedIDsKey)
        NotificationCenter.default.post(name: guestDiagnosticsDidChange, object: nil)
        for diagnostic in diagnostics {
            let checkResult = diagnostic.checkResult.map { $0 ? "pass" : "fail" } ?? "not_checked"
            print("[LIVE_CONTAINER_REFRESH] GUEST_SIGNATURE_CHECK bundle_id=\(diagnostic.bundleID) category=\(diagnostic.category) executable_found=\(diagnostic.executableFound) readable=\(diagnostic.readable) check_result=\(checkResult) reason=\(diagnostic.reason)")
        }
        let passed = diagnostics.filter { $0.checkResult == true }.count
        let failed = diagnostics.filter { $0.checkResult == false }.count
        let notChecked = diagnostics.count - passed - failed
        print("[LIVE_CONTAINER_REFRESH] GUEST_SIGNATURE_SUMMARY total=\(diagnostics.count) passed=\(passed) failed=\(failed) not_checked=\(notChecked) affected=\(affectedIDs.count) warning=\(!affectedIDs.isEmpty)")
        print("[LIVE_CONTAINER_REFRESH] GUEST_DIAGNOSTICS warning=\(!affectedIDs.isEmpty) affected_count=\(affectedIDs.count)")
    }

    private static func execute(source: String, task: BGTask? = nil,
                                gate: LiveContainerRefreshCompletionGate = LiveContainerRefreshCompletionGate()) async {
        guard !gate.isFinished, !Task.isCancelled else { return }
        let manual = source == "manual" || source == "alarm_action" || source == "vpn_return"
        let now = Date()
        print("[LIVE_CONTAINER_REFRESH] TASK_TRIGGERED source=\(source) at=\(now.timeIntervalSince1970)")
        if source == "bgprocessing" || source == "bgapprefresh" { defaults.set(now, forKey: lastTaskKey) }
        observeMissedDeadline(now: now)
        func finish(_ success: Bool) {
            if gate.claim() { task?.setTaskCompleted(success: success) }
        }
        guard manual || defaults.bool(forKey: enabledKey) else { finish(true); return }
        guard compactWorkIsDue(now: now, manual: manual) else {
            if manual, defaults.bool(forKey: hostHandoffKey) {
                notify(title: "Refresh awaiting verification", body: "A host replacement is still pending. Reopen LiveContainer and check the installed profile before retrying.", kind: "host_handoff")
            }
            print("[LIVE_CONTAINER_REFRESH] NO_OP source=\(source)")
            finish(true)
            if task != nil { schedule() }
            return
        }
        if source == "bgapprefresh" {
            print("[LIVE_CONTAINER_REFRESH] WATCHDOG_DUE action=resubmit_bgprocessing")
            schedule()
            finish(true)
            return
        }
        guard let runID = beginRun(source: source, manual: manual) else {
            print("[LIVE_CONTAINER_REFRESH] RUN_COALESCED source=\(source)")
            finish(true)
            return
        }
        defer { endRun(runID); schedule() }
        do {
            try await LiveContainerNetworkPreflight.check(allowForegroundActivation: manual && source != "vpn_return" && task == nil)
            try await performRefresh(runID: runID)
            let verification = verifyRefreshManifest(runID: runID.uuidString)
            if verification.hostHandoff {
                guard gate.claim() else { return }
                defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
                record(source: source, result: "host_handoff_awaiting_relaunch", detail: verification.reason)
                notify(title: "Host refresh awaiting verification", body: "Reopen LiveContainer to check that its installed profile renewed.", kind: "host_handoff")
                // Completion of this handler is not a claim of refresh success.
                task?.setTaskCompleted(success: false)
            } else if verification.verified {
                try Task.checkCancellation()
                guard gate.claim() else { return }
                markVerified(source: source, detail: "All requested installed-app results were confirmed.")
                print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\(runID.uuidString) success=true verified=true")
                task?.setTaskCompleted(success: true)
                let guestDiagnostics = collectGuestDiagnostics()
                persistGuestDiagnostics(guestDiagnostics)
            } else {
                throw NSError(domain: "LiveContainerRefresh.Verification", code: 1001,
                    userInfo: [NSLocalizedDescriptionKey: verification.reason])
            }
        } catch {
            guard gate.claim() else { return } // Expiration already recorded the outcome.
            let nsError = error as NSError
            let count = defaults.integer(forKey: retryCountKey) + 1
            defaults.set(count, forKey: retryCountKey)
            if !(error is CancellationError), !LiveContainerRefreshPolicy.isUserActionFailure(nsError),
               let delay = LiveContainerRefreshPolicy.retryDelay(failureCount: count) {
                defaults.set(Date().addingTimeInterval(delay), forKey: nextRetryKey)
            } else {
                defaults.removeObject(forKey: nextRetryKey)
                defaults.set(true, forKey: retryExhaustedKey)
            }
            let networkState = nsError.domain == "LiveContainerRefresh.Network"
                ? (nsError.code == 1 ? "WIFI_UNAVAILABLE" : "VPN_UNAVAILABLE") : "REFRESH_FAILED"
            defaults.set(networkState, forKey: healthStateKey)
            defaults.set(error.localizedDescription, forKey: lastErrorKey)
            record(source: source, result: "failure", detail: error.localizedDescription)
            print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\(runID.uuidString) success=false verified=false error_domain=\(nsError.domain) error_code=\(nsError.code) error=\(error.localizedDescription)")
            notify(title: "Refresh failed", body: error.localizedDescription, kind: "failed")
            task?.setTaskCompleted(success: false)
        }
    }

    private static func handle(_ task: BGTask, source: String) {
        let gate = LiveContainerRefreshCompletionGate()
        let work = Task { @MainActor in await execute(source: source, task: task, gate: gate) }
        task.expirationHandler = {
            work.cancel()
            guard gate.claim() else { return }
            task.setTaskCompleted(success: false)
            Task { @MainActor in
                defaults.set("REFRESH_INTERRUPTED", forKey: healthStateKey)
                let count = defaults.integer(forKey: retryCountKey) + 1
                defaults.set(count, forKey: retryCountKey)
                if let delay = LiveContainerRefreshPolicy.retryDelay(failureCount: count) {
                    defaults.set(Date().addingTimeInterval(delay), forKey: nextRetryKey)
                } else { defaults.set(true, forKey: retryExhaustedKey) }
                record(source: source, result: "expired", detail: "iOS ended the background execution window; refresh was not verified.")
                notify(title: "Refresh interrupted", body: "iOS ended background execution before completion. Open LiveContainer to check the result.", kind: "expired")
            }
        }
    }

    static func register() {
        guard !registered else { return }
        registered = true
        hostBundle = Bundle.main
        // A durable marker from a terminated process is not a live mutex.
        if defaults.string(forKey: activeRunKey) != nil {
            defaults.removeObject(forKey: activeRunKey)
            record(source: "relaunch", result: "interrupted", detail: "The previous process ended before recording completion.")
        }
        do {
            let resolved = try LiveContainerRefreshTaskIdentifiers.resolve(info: hostBundle?.infoDictionary ?? [:])
            identifiers = resolved
            processingRegistered = BGTaskScheduler.shared.register(forTaskWithIdentifier: resolved.processing, using: nil) { task in
                Task { @MainActor in handle(task, source: "bgprocessing") }
            }
            watchdogRegistered = BGTaskScheduler.shared.register(forTaskWithIdentifier: resolved.watchdog, using: nil) { task in
                Task { @MainActor in handle(task, source: "bgapprefresh") }
            }
            print("[LIVE_CONTAINER_REFRESH] REGISTER_PASS processing=\(processingRegistered) watchdog=\(watchdogRegistered) task_id=\(resolved.processing)")
            if !processingRegistered || !watchdogRegistered {
                record(source: "scheduler", result: "registration_limited", detail: "iOS did not register every background task. Manual refresh remains available.")
            }
        } catch {
            defaults.set("foreground_recovery_only", forKey: strategyKey)
            defaults.set(error.localizedDescription, forKey: lastErrorKey)
            record(source: "scheduler", result: "configuration_failed", detail: error.localizedDescription)
        }
    }

    static func requestRefreshNow() async { await execute(source: "alarm_action") }
    static func runNow() {
        Task { @MainActor in
            await requestNotificationPermission()
            verifyPendingHostHandoff()
            await execute(source: "manual")
        }
    }

    static func recoverAfterLaunchOrResume() {
        guard activeRun == nil else { return }
        verifyPendingHostHandoff()
        if LiveContainerNetworkPreflight.consumePendingReturn() {
            Task { @MainActor in await execute(source: "vpn_return") }
            return
        }
        observeMissedDeadline(now: Date())
        guard defaults.bool(forKey: enabledKey), defaults.object(forKey: earliestEligibleKey) != nil,
              compactWorkIsDue(now: Date()) else { return }
        Task { @MainActor in await execute(source: "launch_or_resume") }
    }

    static func scheduleChanged() {
        cancelDeadlineProtection()
        defaults.removeObject(forKey: deadlineKey)
        defaults.removeObject(forKey: satisfiedDeadlineKey)
        defaults.set(false, forKey: retryExhaustedKey)
        defaults.set(0, forKey: retryCountKey)
        schedule()
        if defaults.bool(forKey: enabledKey) {
            Task { @MainActor in await requestNotificationPermission(); schedule() }
        }
    }

    static func cancelDeadlineProtection() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [warningIdentifier])
        if #available(iOS 26.1, *), defaults.bool(forKey: alarmScheduledKey) {
            LiveContainerAutoRefreshAlarmProvider.cancelIfAvailable()
        }
    }

    private static func scheduleDeadlineWarning(_ deadline: Date) {
        guard deadline > Date() else { return }
        let content = UNMutableNotificationContent()
        content.title = "Automatic refresh needs checking"
        content.body = "No completed refresh has been confirmed for this deadline. Open LiveContainer to check or refresh. A host replacement may still need verification."
        content.sound = .default
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, deadline.timeIntervalSinceNow), repeats: false)
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: warningIdentifier, content: content, trigger: trigger)) { error in
            if let error { print("[LIVE_CONTAINER_REFRESH] DEADLINE_WARNING_FAIL error=\(error.localizedDescription)") }
        }
    }

    static func schedule() {
        guard defaults.bool(forKey: enabledKey) else {
            if let ids = identifiers {
                BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: ids.processing)
                BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: ids.watchdog)
            }
            cancelDeadlineProtection()
            defaults.set("disabled", forKey: strategyKey)
            return
        }
        let now = Date()
        observeMissedDeadline(now: now)
        var deadline = defaults.object(forKey: deadlineKey) as? Date
        if deadline == nil || deadline == (defaults.object(forKey: satisfiedDeadlineKey) as? Date) ||
            (defaults.bool(forKey: retryExhaustedKey) && (deadline ?? now) < now) {
            let advancingExistingWindow = deadline != nil
            deadline = nextDate(after: now)
            defaults.set(deadline, forKey: deadlineKey)
            // Initial schedule creation must not erase a just-recorded failure/backoff.
            if advancingExistingWindow {
                defaults.removeObject(forKey: nextRetryKey)
                defaults.set(false, forKey: retryExhaustedKey)
                defaults.set(0, forKey: retryCountKey)
            }
        }
        guard let deadline else { return }
        scheduleDeadlineWarning(deadline) // Pre-scheduled; does not require a future app wake.
        defaults.set(processingRegistered ? "native_without_alarmkit" : "foreground_recovery_only", forKey: strategyKey)
        if let ids = identifiers, processingRegistered, !defaults.bool(forKey: retryExhaustedKey), !defaults.bool(forKey: hostHandoffKey) {
            let earliest = LiveContainerRefreshPolicy.earliestUsefulDate(now: now, deadline: deadline, lead: leadTime,
                eligible: defaults.object(forKey: earliestEligibleKey) as? Date,
                retry: defaults.object(forKey: nextRetryKey) as? Date)
            let request = BGProcessingTaskRequest(identifier: ids.processing)
            request.requiresNetworkConnectivity = true
            request.requiresExternalPower = false
            request.earliestBeginDate = earliest
            do {
                try BGTaskScheduler.shared.submit(request)
                print("[LIVE_CONTAINER_REFRESH] SCHEDULE_PASS target_deadline=\(deadline.timeIntervalSince1970) earliest_begin=\(earliest.timeIntervalSince1970)")
            } catch {
                defaults.set(error.localizedDescription, forKey: lastErrorKey)
                record(source: "scheduler", result: "bgprocessing_submit_failed", detail: error.localizedDescription)
            }
            // A watchdog is one bounded opportunity, not a repeated polling job.
            if watchdogRegistered, deadline > now {
                let watchdog = BGAppRefreshTaskRequest(identifier: ids.watchdog)
                watchdog.earliestBeginDate = deadline
                do { try BGTaskScheduler.shared.submit(watchdog) }
                catch { record(source: "scheduler", result: "bgapprefresh_submit_failed", detail: error.localizedDescription) }
            }
        }
        if #available(iOS 26.1, *), deadline > now {
            Task { @MainActor in await LiveContainerAutoRefreshAlarmProvider.scheduleIfAvailable(deadline: deadline) }
        }
    }

    private static func nextDate(after now: Date) -> Date {
        let frequency = defaults.string(forKey: frequencyKey) ?? "interval"
        if frequency == "interval" { return now.addingTimeInterval(6 * 60 * 60) }
        let minutes = max(0, min(1439, defaults.object(forKey: minutesKey) as? Int ?? 600))
        var parts = DateComponents(hour: minutes / 60, minute: minutes % 60, second: 0)
        if frequency == "weekly" { parts.weekday = max(1, min(7, defaults.object(forKey: weekdayKey) as? Int ?? 2)) }
        return Calendar.autoupdatingCurrent.nextDate(after: now, matching: parts, matchingPolicy: .nextTime,
            repeatedTimePolicy: .first) ?? now.addingTimeInterval(6 * 60 * 60)
    }
}
