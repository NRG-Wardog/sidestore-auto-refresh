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
    static let activeManualRequestKey = "liveContainerAutoRefreshActiveRequestID"
    static let activeManualOriginKey = "liveContainerAutoRefreshActiveManualOrigin"
    static let activeManualOriginRunKey = "liveContainerAutoRefreshActiveManualOriginRunID"
    static let retryCountKey = "liveContainerAutoRefreshRetryCount"
    static let currentRunFailureKey = "liveContainerAutoRefreshCurrentRunFailure"
    static let hostHandoffKey = "liveContainerAutoRefreshHostHandoff"
    static let hostHandoffRunKey = "liveContainerAutoRefreshHostHandoffRunID"
    static let hostHandoffStartedKey = "liveContainerAutoRefreshHostHandoffStartedAt"
    static let hostPreviousExpirationKey = "liveContainerAutoRefreshHostPreviousExpiration"
    static let verificationKey = "liveContainerAutoRefreshVerification"
    static let runLedgerKey = "liveContainerAutoRefreshRunLedger"
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
    static let uncertainMutationKey = "liveContainerAutoRefreshUncertainMutationRunID"
    static let runStateChangedNotification = "LiveContainerAutoRefreshRunStateChanged"
    static let maximumRunLedgerEntries = 32
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

    private static func notify(title: String, body: String, kind: String,
                               runID: String? = nil, requestID: String? = nil, origin: String? = nil) {
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
            var identity: [String: String] = ["kind": kind]
            if let runID { identity["run_id"] = runID }
            if let requestID { identity["request_id"] = requestID }
            if let origin { identity["origin"] = origin }
            content.userInfo = identity
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
        guard manual || defaults.string(forKey: uncertainMutationKey) == nil else { return false }
        return LiveContainerRefreshPolicy.workIsDue(now: now,
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

    private static func runLedger() -> [String: [String: Any]] {
        (defaults.dictionary(forKey: runLedgerKey) ?? [:]).compactMapValues { $0 as? [String: Any] }
    }

    private static func saveRunRecord(_ record: [String: Any], runID: String) {
        var ledger = runLedger()
        ledger[runID] = record
        if ledger.count > maximumRunLedgerEntries {
            let activeID = defaults.string(forKey: activeRunKey)
            let removable = ledger.compactMap { key, value -> (String, TimeInterval)? in
                guard key != activeID else { return nil }
                let updated = value["updated_at"] as? TimeInterval ?? value["started_at"] as? TimeInterval ?? 0
                return (key, updated)
            }.sorted { $0.1 < $1.1 }
            for (key, _) in removable.prefix(ledger.count - maximumRunLedgerEntries) {
                ledger.removeValue(forKey: key)
            }
        }
        defaults.set(ledger, forKey: runLedgerKey)
    }

    private static func publishRunState(_ state: String, runID: String, requestID: String?, origin: String? = nil) {
        var identity: [String: String] = ["run_id": runID, "state": state]
        if let requestID { identity["request_id"] = requestID }
        if let origin { identity["origin"] = origin }
        NotificationCenter.default.post(name: Notification.Name(runStateChangedNotification), object: nil,
                                         userInfo: identity)
    }

    private static func canonicalRequestID(_ value: String?) -> String? {
        guard let value, let uuid = UUID(uuidString: value) else { return nil }
        return uuid.uuidString
    }

    private static func canonicalManualOrigin(_ value: String?, source: String) -> String {
        let allowed: Set<String> = ["home", "refreshManager", "setupAssistant", "deadlineAlarm", "vpnReturn", "manualUnknown"]
        if let value, allowed.contains(value) { return value }
        switch source {
        case "alarm_action": return "deadlineAlarm"
        case "vpn_return": return "vpnReturn"
        default: return "manualUnknown"
        }
    }

    private static func beginRun(source: String, manual: Bool, requestID: String? = nil,
                                 manualOrigin: String? = nil) -> UUID? {
        guard activeRun == nil else { return nil }
        if !manual, let last = defaults.object(forKey: lastAttemptKey) as? Date,
           Date().timeIntervalSince(last) < coalescingWindow { return nil }
        let id = UUID()
        let runID = id.uuidString
        let correlatedRequestID = manual ? (canonicalRequestID(requestID) ?? UUID().uuidString) : nil
        let origin = manual ? canonicalManualOrigin(manualOrigin, source: source) : source
        activeRun = id
        if let correlatedRequestID {
            defaults.set(correlatedRequestID, forKey: activeManualRequestKey)
            defaults.set(origin, forKey: activeManualOriginKey)
            defaults.set(runID, forKey: activeManualOriginRunKey)
        } else {
            defaults.removeObject(forKey: activeManualRequestKey)
            defaults.removeObject(forKey: activeManualOriginKey)
            defaults.removeObject(forKey: activeManualOriginRunKey)
        }
        defaults.set(runID, forKey: expectedRunKey)
        defaults.set(Date(), forKey: lastAttemptKey)
        defaults.removeObject(forKey: verificationKey)
        defaults.removeObject(forKey: currentRunFailureKey)
        defaults.removeObject(forKey: lastErrorKey)
        defaults.set(false, forKey: hostVerifiedKey)
        defaults.set("REFRESH_IN_PROGRESS", forKey: healthStateKey)
        saveRunRecord(["run_id": runID, "request_id": correlatedRequestID ?? "", "origin": origin,
                       "source": source, "state": "running",
                       "started_at": Date().timeIntervalSince1970,
                       "updated_at": Date().timeIntervalSince1970], runID: runID)
        defaults.set(runID, forKey: activeRunKey)
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
        print("[LIVE_CONTAINER_REFRESH] RUN_BEGIN source=\(source) origin=\(origin) run_id=\(runID) request_id=\(correlatedRequestID ?? "none")")
        publishRunState("running", runID: runID, requestID: correlatedRequestID, origin: origin)
        notify(title: "Refresh started", body: "Checking SideStore refresh requirements. Success is not yet confirmed.",
               kind: "started", runID: runID, requestID: correlatedRequestID, origin: origin)
        return id
    }

    private static func endRun(_ id: UUID) {
        guard activeRun == id || defaults.string(forKey: activeRunKey) == id.uuidString else { return }
        if activeRun == id { activeRun = nil }
        if defaults.string(forKey: activeRunKey) == id.uuidString {
            defaults.removeObject(forKey: activeRunKey)
            defaults.removeObject(forKey: activeManualRequestKey)
        }
        if defaults.string(forKey: activeManualOriginRunKey) == id.uuidString {
            defaults.removeObject(forKey: activeManualOriginKey)
            defaults.removeObject(forKey: activeManualOriginRunKey)
        }
        if defaults.string(forKey: expectedRunKey) == id.uuidString {
            defaults.removeObject(forKey: expectedRunKey)
        }
    }

    private static func performRefresh(runID: UUID) async throws {
        guard #available(iOS 17.0, *) else {
            throw NSError(domain: "LiveContainerRefresh.UnsupportedOS", code: 17,
                userInfo: [NSLocalizedDescriptionKey: "This combined refresh bridge requires iOS 17 or later. Refresh was not started. Review app expiration and account status; copy these diagnostics if you need assistance."])
        }
        try Task.checkCancellation()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_ATTEMPT_STARTED run_id=\(runID.uuidString)")
        try await LiveContainerRefreshBridge.refreshAllApps()
        try Task.checkCancellation()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_PIPELINE_RETURNED run_id=\(runID.uuidString)")
    }

    private static func verifyRefreshManifest(runID: String) -> (verified: Bool, hostHandoff: Bool, reason: String, failure: CombinedFailure?) {
        let pending = defaults.bool(forKey: hostHandoffKey)
        guard let manifest = defaults.dictionary(forKey: verificationKey) else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=manifest_missing run_id=\(runID)")
            return (false, pending, "SideStore returned without sharing installation results with LiveContainer. Refresh is unconfirmed. Review Refresh history, app expiration, and account status before an explicit retry.",
                    CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID))
        }
        guard manifest["run_id"] as? String == runID else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=run_mismatch expected_run=\(runID)")
            return (false, pending, "LiveContainer received results for a different refresh attempt. This attempt could not be verified. Review Refresh history and app expiration; explicitly retry only after the previous attempt finishes.",
                    CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .staleResult, id: runID))
        }
        guard let results = manifest["results"] as? [[String: Any]], !results.isEmpty else {
            print("[LIVE_CONTAINER_REFRESH] VERIFICATION_FAILED reason=results_empty run_id=\(runID)")
            return (false, pending, "SideStore returned no app installation results. No successful refresh was confirmed. Review eligible apps, account status, and Refresh history before an explicit retry.",
                    CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID))
        }
        guard CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID) else {
            return (false, pending, "SideStore returned incomplete installation results. No successful refresh was confirmed.",
                    CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID))
        }
        let failedResults = results.filter { ($0["success"] as? Bool) != true }
        if !failedResults.isEmpty {
            // Persisted raw error text is not a safe diagnostic boundary. Decode only
            // the bounded, allowlisted, current-run envelope; never display its fallback text.
            let failures = failedResults.compactMap { entry in
                (entry["failure"] as? [String: Any]).flatMap { CombinedFailure.decode($0, expectedID: runID) }
            }
            // A later app's concrete user-action failure must not be hidden by an earlier
            // retryable/unknown failure when the scheduler considers retrying the batch.
            let failure = failures.first { $0.retryable == false || $0.stage == .authentication || $0.code == .cancelled }
                ?? failures.first
                ?? CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID)
            return (false, pending, String(failure.localizedDescription.prefix(2048)), failure)
        }
        return pending ? (false, true, "host_handoff_awaiting_relaunch", nil) : (true, false, "verified_installed_app_records", nil)
    }

    private static func structuredRefreshFailure(_ error: Error, runID: String) -> CombinedFailure {
        if let failure = error as? CombinedFailure {
            guard failure.operation == "refresh", failure.correlationID == runID else {
                return CombinedFailure(operation: "refresh", stage: .refreshVerification,
                                       code: .staleResult, id: runID)
            }
            return failure
        }
        let native = error as NSError
        if native.domain == "LiveContainerRefresh.Network" {
            let cause: CombinedFailure.SafeCause = native.code == 1 ? .wifiUnavailable : .localDevVPNUnavailable
            return CombinedFailure(operation: "refresh", stage: .network, id: runID,
                                   underlying: native, retryable: true, safeCause: cause)
        }
        if native.domain == "LiveContainerRefresh.Verification" {
            return CombinedFailure(operation: "refresh", stage: .refreshVerification,
                                   code: .missingResult, id: runID, underlying: native)
        }
        return CombinedFailure.capture(error, operation: "refresh", stage: .command, id: runID)
    }

    private static func markRunVerifying(runID: String, manifest: [String: Any]? = nil) {
        guard var record = runLedger()[runID],
              !["completed", "failed"].contains(record["state"] as? String ?? "") else { return }
        if let manifest {
            guard manifest["run_id"] as? String == runID else { return }
            // Commit this run's manifest before clearing its active ownership.
            record["manifest"] = manifest
        }
        record["state"] = "verifying"
        record["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(record, runID: runID)
        let requestID = record["request_id"] as? String
        let origin = record["origin"] as? String
        publishRunState("verifying", runID: runID, requestID: requestID?.isEmpty == true ? nil : requestID,
                        origin: origin)
    }

    @discardableResult
    private static func markVerified(runID: String, source: String, detail: String) -> Bool {
        if let uncertain = defaults.string(forKey: uncertainMutationKey), uncertain != runID { return false }
        guard let manifest = defaults.dictionary(forKey: verificationKey),
              manifest["run_id"] as? String == runID,
              var runRecord = runLedger()[runID],
              !["completed", "failed"].contains(runRecord["state"] as? String ?? "") else { return false }
        guard activeRun == nil || activeRun?.uuidString == runID else { return false }
        if let storedActive = defaults.string(forKey: activeRunKey), storedActive != runID { return false }

        // The verified manifest is durable and keyed to this exact run before
        // the scheduler relinquishes active ownership.
        runRecord["manifest"] = manifest
        runRecord["state"] = "verifying"
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)

        if let activeRun { endRun(activeRun) }
        else if defaults.string(forKey: activeRunKey) == runID, let id = UUID(uuidString: runID) { endRun(id) }
        defaults.removeObject(forKey: hostHandoffKey)
        defaults.removeObject(forKey: hostHandoffRunKey)
        defaults.removeObject(forKey: hostHandoffStartedKey)
        defaults.removeObject(forKey: hostBaselineKey)

        let requestValue = runRecord["request_id"] as? String ?? ""
        let requestID = requestValue.isEmpty ? nil : requestValue
        let origin = runRecord["origin"] as? String
        let skippedCount = (manifest["skipped_ids"] as? [String])?.count ?? 0
        let terminalDetail = skippedCount == 0 ? detail :
            "Refresh completed for the verified app targets; \(skippedCount) running app(s) were skipped."
        runRecord["state"] = "completed"
        runRecord["message"] = terminalDetail
        runRecord["terminal_at"] = Date().timeIntervalSince1970
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)

        if defaults.string(forKey: uncertainMutationKey) == runID { defaults.removeObject(forKey: uncertainMutationKey) }
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
        record(source: source, result: "verified", detail: terminalDetail)
        cancelDeadlineProtection()
        publishRunState("completed", runID: runID, requestID: requestID, origin: origin)
        notify(title: "Refresh completed", body: terminalDetail, kind: "verified", runID: runID,
               requestID: requestID, origin: origin)
        return true
    }

    @discardableResult
    private static func markFailed(runID: String, source: String, health: String,
                                   failure: CombinedFailure? = nil, message: String? = nil,
                                   result: String = "failure") -> Bool {
        guard var runRecord = runLedger()[runID],
              !["completed", "failed"].contains(runRecord["state"] as? String ?? ""),
              activeRun == nil || activeRun?.uuidString == runID else { return false }
        if let storedActive = defaults.string(forKey: activeRunKey), storedActive != runID { return false }
        if let manifest = defaults.dictionary(forKey: verificationKey), manifest["run_id"] as? String == runID {
            runRecord["manifest"] = manifest
            runRecord["state"] = "verifying"
            runRecord["updated_at"] = Date().timeIntervalSince1970
            saveRunRecord(runRecord, runID: runID)
        }
        if let activeRun { endRun(activeRun) }
        else if defaults.string(forKey: activeRunKey) == runID, let id = UUID(uuidString: runID) { endRun(id) }

        let requestValue = runRecord["request_id"] as? String ?? ""
        let requestID = requestValue.isEmpty ? nil : requestValue
        let origin = runRecord["origin"] as? String
        let suppliedFailureMatches = failure.map {
            $0.operation == "refresh" && $0.correlationID == runID
        } ?? true
        let structured: CombinedFailure
        if let failure, suppliedFailureMatches {
            structured = failure
        } else if failure != nil {
            structured = CombinedFailure(operation: "refresh", stage: .refreshVerification,
                                         code: .staleResult, id: runID)
        } else {
            structured = CombinedFailure(operation: "refresh", stage: .refreshVerification, id: runID)
        }
        let safeMessage = String(((suppliedFailureMatches ? message : nil) ?? structured.safeMessage).prefix(2048))
        runRecord["state"] = "failed"
        runRecord["message"] = safeMessage
        runRecord["health"] = health
        runRecord["failure"] = structured.wire
        runRecord["terminal_at"] = Date().timeIntervalSince1970
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)
        var terminalFailure: [String: Any] = [
            "run_id": runID, "request_id": requestValue, "origin": origin ?? "unknown",
            "message": safeMessage, "safe_message": safeMessage, "failure": structured.wire
        ]
        for (wireKey, recordKey) in [
            ("operation", "operation"), ("stage", "stage"), ("code", "code"),
            ("correlationID", "correlation"), ("underlyingDomain", "underlying_domain"),
            ("underlyingCode", "underlying_code"), ("safeCause", "safe_cause"),
            ("sourceStep", "source_step")
        ] {
            if let value = structured.wire[wireKey] { terminalFailure[recordKey] = value }
        }
        terminalFailure["retryable"] = structured.retryable.map { $0 as Any } ?? "unknown"
        defaults.set(terminalFailure, forKey: currentRunFailureKey)
        defaults.set(health, forKey: healthStateKey)
        defaults.set(safeMessage, forKey: lastErrorKey)
        record(source: source, result: result, detail: safeMessage)
        publishRunState("failed", runID: runID, requestID: requestID, origin: origin)
        notify(title: "Refresh failed", body: safeMessage, kind: "failed", runID: runID,
               requestID: requestID, origin: origin)
        return true
    }

    private static func verifyPendingHostHandoff() {
        guard activeRun == nil, defaults.bool(forKey: hostHandoffKey) else { return }
        if let uncertain = defaults.string(forKey: uncertainMutationKey), uncertain != defaults.string(forKey: hostHandoffRunKey) { return }
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
                    notify(title: "LiveContainer refresh not confirmed", body: "Its installed signing validity did not advance. Review app expiration and account status, then explicitly retry in Refresh.", kind: "host_failed")
                }
                return
            }
            defaults.set(true, forKey: hostVerifiedKey)
            defaults.removeObject(forKey: hostHandoffKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_VERIFIED evidence=installed_profile_expiration_advanced")
            let batch = verifyRefreshManifest(runID: runID)
            if batch.verified {
                markVerified(runID: runID, source: "relaunch", detail: "LiveContainer's installed profile renewed; all requested app results were confirmed.")
            } else {
                // Host replacement can kill the process before it writes the
                // final batch results. Host success is not whole-batch success.
                defaults.set("HOST_REFRESH_VERIFIED", forKey: healthStateKey)
                record(source: "relaunch", result: "host_verified_batch_unconfirmed", detail: batch.reason)
                notify(title: "LiveContainer refreshed", body: "Its installed profile renewed. Some batch results remain unconfirmed; review Refresh history and app expiration.", kind: "host_verified")
            }
        } catch {
            defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
            defaults.set(error.localizedDescription, forKey: lastErrorKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_UNVERIFIED error=\(error.localizedDescription)")
        }
    }

    private static func verifyGuestSignatures() -> Bool {
        // Guests are not standalone InstalledApps and are never re-signed here.
        let guests = DataManager.shared.model.apps + DataManager.shared.model.hiddenApps
        for guest in guests {
            guard let path = guest.appInfo.bundlePath(), let executable = Bundle(path: path)?.executableURL,
                  executable.path.withCString({ checkCodeSignature($0) }) else {
                print("[LIVE_CONTAINER_REFRESH] GUEST_SIGNATURE_INVALID bundle_id=\(guest.appInfo.bundleIdentifier())")
                return false
            }
        }
        return true
    }

    private static func execute(source: String, task: BGTask? = nil, manualRequestID: String? = nil,
                                manualOrigin: String? = nil,
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
        guard let runID = beginRun(source: source, manual: manual, requestID: manualRequestID,
                                   manualOrigin: manualOrigin) else {
            print("[LIVE_CONTAINER_REFRESH] RUN_COALESCED source=\(source)")
            if manual {
                record(source: source, result: "coalesced", detail: "A refresh is already running. Wait for it to finish before retrying.")
                notify(title: "Refresh already running", body: "A refresh is already running. Wait for it to finish before retrying.", kind: "coalesced")
            }
            finish(true)
            return
        }
        defer { schedule() }
        let correlatedRequestValue = defaults.string(forKey: activeManualRequestKey) ?? ""
        let correlatedRequestID = correlatedRequestValue.isEmpty ? nil : correlatedRequestValue
        let correlatedOrigin = runLedger()[runID.uuidString]?["origin"] as? String
        if manual, defaults.string(forKey: uncertainMutationKey) != nil {
            record(source: source, result: "explicit_retry", detail: "Previous mutation completion was uncertain. This user-requested attempt will reload SideStore's authoritative app state.")
            defaults.removeObject(forKey: uncertainMutationKey)
        }
        do {
            try await LiveContainerNetworkPreflight.check(allowForegroundActivation: manual && source != "vpn_return" && task == nil)
            try await performRefresh(runID: runID)
            markRunVerifying(runID: runID.uuidString,
                             manifest: defaults.dictionary(forKey: verificationKey))
            let verification = verifyRefreshManifest(runID: runID.uuidString)
            if verification.hostHandoff {
                guard gate.claim() else { return }
                endRun(runID)
                defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
                record(source: source, result: "host_handoff_awaiting_relaunch", detail: verification.reason)
                publishRunState("verifying", runID: runID.uuidString, requestID: correlatedRequestID, origin: correlatedOrigin)
                notify(title: "Host refresh awaiting verification", body: "Reopen LiveContainer to check that its installed profile renewed.",
                       kind: "host_handoff", runID: runID.uuidString, requestID: correlatedRequestID,
                       origin: correlatedOrigin)
                // Completion of this handler is not a claim of refresh success.
                task?.setTaskCompleted(success: false)
            } else if verification.verified {
                let guestsValid = verifyGuestSignatures()
                try Task.checkCancellation()
                guard gate.claim() else { return }
                if guestsValid {
                    guard markVerified(runID: runID.uuidString, source: source, detail: "All requested installed-app results were confirmed.") else {
                        let failure = CombinedFailure(operation: "refresh", stage: .refreshVerification,
                            code: .missingResult, id: runID.uuidString)
                        _ = markFailed(runID: runID.uuidString, source: source, health: "REFRESH_FAILED",
                                       failure: failure,
                                       message: "Refresh failed during refreshVerification, but no safe underlying cause was available.")
                        task?.setTaskCompleted(success: false)
                        return
                    }
                    print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\(runID.uuidString) success=true verified=true")
                    task?.setTaskCompleted(success: true)
                } else {
                    let failure = CombinedFailure(operation: "refresh", stage: .refreshVerification,
                        code: .failed, id: runID.uuidString)
                    _ = markFailed(runID: runID.uuidString, source: source, health: "GUEST_SIGNATURE_INVALID",
                                   failure: failure,
                                   message: "Refresh failed during refreshVerification because an installed guest signature did not verify.",
                                   result: "guest_signature_invalid")
                    notify(title: "Guest signature needs attention", body: "Open the affected guest in LiveContainer to check its signing status.",
                           kind: "guest_invalid", runID: runID.uuidString, requestID: correlatedRequestID)
                    task?.setTaskCompleted(success: false)
                }
            } else {
                if let failure = verification.failure { throw failure }
                throw NSError(domain: "LiveContainerRefresh.Verification", code: 1001,
                    userInfo: [NSLocalizedDescriptionKey: verification.reason])
            }
        } catch {
            guard gate.claim() else { return } // Expiration already recorded the outcome.
            let nsError = error as NSError
            let count = defaults.integer(forKey: retryCountKey) + 1
            defaults.set(count, forKey: retryCountKey)
            let structured = error as? CombinedFailure
            // This is a conservative scheduling policy, not a claim that an unknown
            // authentication retryability has become false in the authoritative error.
            let requiresExplicitRetry = structured?.retryable == false || structured?.stage == .authentication || structured?.code == .cancelled
            if defaults.string(forKey: uncertainMutationKey) == nil,
               !requiresExplicitRetry,
               !(error is CancellationError), !LiveContainerRefreshPolicy.isUserActionFailure(nsError),
               let delay = LiveContainerRefreshPolicy.retryDelay(failureCount: count) {
                defaults.set(Date().addingTimeInterval(delay), forKey: nextRetryKey)
            } else {
                defaults.removeObject(forKey: nextRetryKey)
                defaults.set(true, forKey: retryExhaustedKey)
            }
            let failure = structuredRefreshFailure(error, runID: runID.uuidString)
            let networkState = failure.safeCause == .wifiUnavailable ? "WIFI_UNAVAILABLE" :
                (failure.safeCause == .localDevVPNUnavailable ? "VPN_UNAVAILABLE" :
                 (failure.stage == .network ? "REFRESH_FAILED" : "REFRESH_FAILED"))
            let safeMessage = failure.safeMessage
            _ = markFailed(runID: runID.uuidString, source: source, health: networkState,
                           failure: failure, message: safeMessage)
            print("[LIVE_CONTAINER_REFRESH] REFRESH_RESULT run_id=\(runID.uuidString) success=false verified=false \(failure.technicalDetails)")
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
                let count = defaults.integer(forKey: retryCountKey) + 1
                defaults.set(count, forKey: retryCountKey)
                if defaults.string(forKey: uncertainMutationKey) == nil,
                   let delay = LiveContainerRefreshPolicy.retryDelay(failureCount: count) {
                    defaults.set(Date().addingTimeInterval(delay), forKey: nextRetryKey)
                } else { defaults.set(true, forKey: retryExhaustedKey) }
                let detail = "iOS ended the background execution window; refresh was not verified."
                if let id = activeRun?.uuidString ?? defaults.string(forKey: activeRunKey) {
                    let failure = CombinedFailure(operation: "refresh", stage: .refreshVerification,
                        code: .timedOut, id: id, retryable: true)
                    _ = markFailed(runID: id, source: source, health: "REFRESH_INTERRUPTED",
                                   failure: failure, message: detail, result: "expired")
                } else {
                    defaults.set("REFRESH_INTERRUPTED", forKey: healthStateKey)
                    record(source: source, result: "expired", detail: detail)
                    notify(title: "Refresh interrupted", body: "iOS ended background execution before completion. Open LiveContainer to check the result.", kind: "expired")
                }
            }
        }
    }

    static func register() {
        guard !registered else { return }
        registered = true
        hostBundle = Bundle.main
        // A durable marker from a terminated process is not a live mutex.
        if let interruptedRunID = defaults.string(forKey: activeRunKey) {
            defaults.set(interruptedRunID, forKey: uncertainMutationKey)
            defaults.removeObject(forKey: activeRunKey)
            defaults.removeObject(forKey: activeManualRequestKey)
            defaults.removeObject(forKey: activeManualOriginKey)
            defaults.removeObject(forKey: activeManualOriginRunKey)
            defaults.removeObject(forKey: expectedRunKey)
            if !markFailed(runID: interruptedRunID, source: "relaunch", health: "REFRESH_INTERRUPTED",
                           failure: CombinedFailure(operation: "refresh", stage: .refreshVerification,
                               code: .interrupted, id: interruptedRunID, retryable: true),
                           message: "Refresh was interrupted when LiveContainer closed before its result was recorded.",
                           result: "interrupted") {
                defaults.set("REFRESH_INTERRUPTED", forKey: healthStateKey)
                record(source: "relaunch", result: "interrupted", detail: "The previous process ended before recording completion.")
            }
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

    static func requestRefreshNow() async {
        await execute(source: "alarm_action", manualRequestID: UUID().uuidString, manualOrigin: "deadlineAlarm")
    }
    static func runNow(requestID: String? = nil, origin: String? = nil) {
        Task { @MainActor in
            await requestNotificationPermission()
            verifyPendingHostHandoff()
            await execute(source: "manual", manualRequestID: requestID, manualOrigin: origin)
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
        if let ids = identifiers, processingRegistered, defaults.string(forKey: uncertainMutationKey) == nil,
           !defaults.bool(forKey: retryExhaustedKey), !defaults.bool(forKey: hostHandoffKey) {
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
