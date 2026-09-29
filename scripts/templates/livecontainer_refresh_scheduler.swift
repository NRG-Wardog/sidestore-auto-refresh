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
    static let directRunClaimKey = V3DirectRefreshRunClaimPolicy.defaultsKey
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

    // V3_FAILURE_GUIDANCE_V1: lastErrorKey is rendered as red product copy on
    // Home, under a "Last Refresh Warning" heading. It used to hold
    // error.localizedDescription, which for a bridged NSError is a numeric
    // domain and code that means nothing to a reader, and it also held opaque
    // snake_case tokens. Each of these states now says what happened and what
    // remains true, and the raw error text stays in the log and the run record.
    static let hostRelaunchUnverifiedMessage =
        "LiveContainer refreshed its installed profile, but the new one could not be confirmed until you relaunch. Relaunch to finish verifying it."
    static let hostBaselineUnavailableMessage =
        "LiveContainer could not read its previous installed profile, so a background refresh cannot be confirmed as having renewed it. Open Refresh History for details."
    static let hostExpirationNotAdvancedMessage =
        "LiveContainer refreshed, but its installed profile has not advanced yet. Relaunch LiveContainer, then check Refresh History."
    static let schedulerConfigurationMessage =
        "iOS did not register every background refresh task, so refreshes will not run on their own. Manual Refresh All still works."
    static let backgroundSubmitFailedMessage =
        "iOS would not accept the next scheduled background refresh. Refresh All still works now; check Settings for Background App Refresh."
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

    static func requestNotificationPermissionFromUserAction() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        if settings.authorizationStatus == .denied {
            if let url = URL(string: UIApplication.openSettingsURLString) {
                _ = await UIApplication.shared.open(url)
            }
            return
        }
        await requestNotificationPermission()
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

    private static func terminalManifestSummary(_ manifest: [String: Any]?, runID: String,
                                               verified: Bool) -> [String: Any] {
        guard let manifest, manifest["run_id"] as? String == runID else {
            return ["version": 2, "schema": "LiveContainerRefreshManifestSummaryV2",
                    "run_id": runID, "verified": verified,
                    "expected_count": 0, "result_count": 0,
                    "failed_count": 0, "skipped_count": 0, "requested_count": 0]
        }
        let expected = manifest["expected_ids"] as? [String] ?? []
        let results = manifest["results"] as? [[String: Any]] ?? []
        let failed = results.filter { ($0["success"] as? Bool) == false }.count
        let skipped = manifest["skipped_ids"] as? [String] ?? []
        var summary: [String: Any] = ["version": 2,
            "schema": "LiveContainerRefreshManifestSummaryV2", "run_id": runID,
            "verified": verified, "expected_count": expected.count,
            "result_count": results.count, "failed_count": failed,
            "skipped_count": skipped.count,
            "requested_count": (manifest["requested_ids"] as? [String] ?? []).count]
        if let verifiedAt = manifest["date"] as? Date { summary["verified_at"] = verifiedAt }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        for key in ["requested_ids", "expected_ids", "skipped_ids"] {
            guard let values = manifest[key] as? [String] else { continue }
            summary[key] = values.prefix(64).map { value in
                String(value.filter { character in
                    character.unicodeScalars.allSatisfy { allowed.contains($0) }
                }.prefix(160))
            }
        }
        return summary
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

    private static func recordNetworkPreflight(_ status: String, runID: String) {
        guard var record = runLedger()[runID],
              !["completed", "failed"].contains(record["state"] as? String ?? "") else { return }
        record["network_preflight"] = status
        record["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(record, runID: runID)
    }

    private static func publishRunState(_ state: String, runID: String, requestID: String?, origin: String? = nil) {
        var identity: [String: String] = ["run_id": runID, "state": state]
        if let requestID { identity["request_id"] = requestID }
        if let origin { identity["origin"] = origin }
        NotificationCenter.default.post(name: Notification.Name(runStateChangedNotification), object: nil,
                                         userInfo: identity)
    }

    private static func beginRun(source: String, manual: Bool, requestID: String? = nil,
                                 manualOrigin: String? = nil) -> UUID? {
        guard activeRun == nil else { return nil }
        if let directClaim = defaults.dictionary(forKey: directRunClaimKey) {
            if V3DirectRefreshRunClaimPolicy.isActive(
                runID: directClaim["run_id"] as? String,
                deadline: directClaim["deadline"] as? Date) {
                return nil
            }
            defaults.removeObject(forKey: directRunClaimKey)
        }
        if !manual, let last = defaults.object(forKey: lastAttemptKey) as? Date,
           Date().timeIntervalSince(last) < coalescingWindow { return nil }
        let id = UUID()
        let correlation = V3RefreshRunCorrelation.make(source: source, manual: manual,
            requestID: requestID, manualOrigin: manualOrigin, runID: id)
        let runID = correlation.runID
        let correlatedRequestID = correlation.requestID
        let origin = correlation.origin
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
                       "network_preflight": "pending",
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
        try await LiveContainerRefreshBridge.refreshAllApps(runID: runID)
        try Task.checkCancellation()
        print("[LIVE_CONTAINER_REFRESH] REFRESH_PIPELINE_RETURNED run_id=\(runID.uuidString)")
    }

    private static func verifyRefreshManifest(runID: String) -> (verified: Bool, hostHandoff: Bool, reason: String, failure: CombinedFailure?) {
        let pending = defaults.bool(forKey: hostHandoffKey) && !defaults.bool(forKey: hostVerifiedKey)
        if let manifest = defaults.dictionary(forKey: verificationKey), manifest["run_id"] as? String == runID {
            let record = runLedger()[runID] ?? [:]
            func ids(_ key: String) -> String {
                ((manifest[key] as? [String]) ?? []).prefix(64).joined(separator: ",")
            }
            let requestID = record["request_id"] as? String ?? ""
            let origin = record["origin"] as? String ?? "unknown"
            print("[LIVE_CONTAINER_REFRESH] MANIFEST run_id=\(runID) request_id=\(requestID) origin=\(origin) requested_ids=\(ids("requested_ids")) expected_ids=\(ids("expected_ids")) skipped_ids=\(ids("skipped_ids"))")
        }
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
        runRecord["terminal_intent"] = "verified"
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
        // Publish the authoritative terminal ledger only after the run marker
        // is cleared and verified health is durable. A crash before this write
        // is recovered from terminal_intent=verified and the saved manifest.
        runRecord["state"] = "completed"
        runRecord["message"] = terminalDetail
        runRecord["health"] = "REFRESH_SUCCEEDED"
        runRecord["manifest_run_id"] = runID
        runRecord["manifest_summary"] = terminalManifestSummary(manifest, runID: runID, verified: true)
        runRecord.removeValue(forKey: "manifest")
        runRecord["terminal_at"] = Date().timeIntervalSince1970
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)
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
        // Persist a terminal intent before releasing the run marker. If iOS
        // stops LiveContainer between the release and final ledger write,
        // startup recovery can complete this exact failure instead of leaving
        // an orphaned running/verifying row.
        runRecord["terminal_intent"] = "failed"
        runRecord["state"] = "failing"
        runRecord["message"] = safeMessage
        runRecord["health"] = health
        runRecord["failure"] = structured.wire
        runRecord["active_run_id"] = "none"
        runRecord["manifest_run_id"] = ((runRecord["manifest"] as? [String: Any])?["run_id"] as? String) ?? "unknown"
        runRecord["source"] = source
        runRecord["result"] = result
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)
        if let activeRun { endRun(activeRun) }
        else if defaults.string(forKey: activeRunKey) == runID, let id = UUID(uuidString: runID) { endRun(id) }

        var terminalFailure: [String: Any] = [
            "run_id": runID, "request_id": requestValue, "origin": origin ?? "unknown",
            "source": source, "network_preflight": runRecord["network_preflight"] as? String ?? "unknown",
            "active_run_id": "none", "health": health,
            "manifest_run_id": runRecord["manifest_run_id"] as? String ?? "unknown",
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
        runRecord["state"] = "failed"
        runRecord["manifest_summary"] = terminalManifestSummary(
            runRecord["manifest"] as? [String: Any], runID: runID, verified: false)
        runRecord.removeValue(forKey: "manifest")
        runRecord["terminal_at"] = Date().timeIntervalSince1970
        runRecord["updated_at"] = Date().timeIntervalSince1970
        saveRunRecord(runRecord, runID: runID)
        record(source: source, result: result, detail: safeMessage)
        publishRunState("failed", runID: runID, requestID: requestID, origin: origin)
        notify(title: "Refresh failed", body: safeMessage, kind: "failed", runID: runID,
               requestID: requestID, origin: origin)
        return true
    }

    private static func clearHostHandoffState(runID: String? = nil) {
        if let runID, defaults.string(forKey: hostHandoffRunKey) != runID { return }
        defaults.removeObject(forKey: hostHandoffKey)
        defaults.removeObject(forKey: hostHandoffRunKey)
        defaults.removeObject(forKey: hostHandoffStartedKey)
        defaults.removeObject(forKey: hostBaselineKey)
    }

    // A crash can happen after the terminal ledger/health is committed but
    // before the host-handoff metadata is removed. Reconcile that durable
    // terminal record first so a later profile check cannot rewrite failure
    // health back to HOST_REFRESH_AWAITING_RELAUNCH.
    private static func restoreTerminalHostHandoffIfNeeded() -> Bool {
        guard let runID = defaults.string(forKey: hostHandoffRunKey),
              let record = runLedger()[runID],
              let state = record["state"] as? String,
              ["completed", "failed"].contains(state) else { return false }
        if let health = record["health"] as? String { defaults.set(health, forKey: healthStateKey) }
        if state == "failed", let message = record["message"] as? String, !message.isEmpty {
            defaults.set(message, forKey: lastErrorKey)
        } else {
            defaults.removeObject(forKey: lastErrorKey)
        }
        clearHostHandoffState(runID: runID)
        return true
    }

    private static func verifyPendingHostHandoff() {
        guard activeRun == nil, defaults.bool(forKey: hostHandoffKey) else { return }
        if let uncertain = defaults.string(forKey: uncertainMutationKey), uncertain != defaults.string(forKey: hostHandoffRunKey) { return }
        if restoreTerminalHostHandoffIfNeeded() { return }
        guard let baseline = defaults.dictionary(forKey: hostBaselineKey),
              let runID = baseline["run_id"] as? String,
              runID == defaults.string(forKey: hostHandoffRunKey),
              let previous = baseline["expiration"] as? Date,
              let bundle = hostBundle, let bundleID = bundle.bundleIdentifier else {
            defaults.set(true, forKey: retryExhaustedKey)
            let message = "The refresh could not verify the installed host profile because its handoff record or baseline is incomplete. Review Refresh history and app expiration before retrying."
            if let runID = defaults.string(forKey: hostHandoffRunKey),
               UUID(uuidString: runID) != nil,
               runLedger()[runID] != nil {
                let failure = CombinedFailure(operation: "refresh", stage: .refreshVerification,
                    code: .missingResult, id: runID, retryable: false)
                if markFailed(runID: runID, source: "relaunch", health: "HOST_REFRESH_UNVERIFIED",
                    failure: failure, message: message, result: "host_baseline_unavailable") {
                    clearHostHandoffState(runID: runID)
                    return
                }
                if restoreTerminalHostHandoffIfNeeded() { return }
                // Preserve the handoff record if a different active run still
                // owns refresh state; do not clear another run's evidence.
                return
            }
            defaults.set("HOST_REFRESH_UNVERIFIED", forKey: healthStateKey)
            defaults.set(message, forKey: lastErrorKey)
            clearHostHandoffState()
            record(source: "relaunch", result: "host_unverified", detail: message)
            return
        }
        do {
            let current = try LiveContainerHostProfile.read(
                at: bundle.bundleURL.appendingPathComponent("embedded.mobileprovision"), expectedBundleID: bundleID)
            guard current.expiration > previous, current.expiration > Date() else {
                defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
                defaults.set(Self.hostExpirationNotAdvancedMessage, forKey: lastErrorKey)
                print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_UNVERIFIED reason=installed_profile_expiration_not_advanced")
                if let started = defaults.object(forKey: hostHandoffStartedKey) as? Date,
                   Date().timeIntervalSince(started) >= 180 {
                    defaults.set(true, forKey: retryExhaustedKey)
                    let message = "The installed host profile did not advance after replacement. Retry manually; no success was recorded."
                    let failed = markFailed(runID: runID, source: "relaunch", health: "HOST_REFRESH_FAILED",
                        failure: CombinedFailure(operation: "refresh", stage: .refreshVerification,
                            code: .timedOut, id: runID, retryable: true),
                        message: message, result: "host_refresh_failed")
                    if failed { clearHostHandoffState(runID: runID) }
                    else { _ = restoreTerminalHostHandoffIfNeeded() }
                }
                return
            }
            defaults.set(true, forKey: hostVerifiedKey)
            print("[LIVE_CONTAINER_REFRESH] HOST_REFRESH_VERIFIED evidence=installed_profile_expiration_advanced")
            let batch = verifyRefreshManifest(runID: runID)
            if batch.verified {
                markVerified(runID: runID, source: "relaunch", detail: "LiveContainer's installed profile renewed; all requested app results were confirmed.")
            } else {
                // Host profile renewal is only one part of the run. Persist a
                // terminal failure for the batch if its per-app manifest is
                // absent or incomplete; never announce refresh success here.
                let failure = batch.failure ?? CombinedFailure(operation: "refresh",
                    stage: .refreshVerification, code: .missingResult, id: runID, retryable: false)
                let failed = markFailed(runID: runID, source: "relaunch", health: "REFRESH_FAILED",
                    failure: failure, message: batch.reason, result: "host_verified_batch_unconfirmed")
                if failed { clearHostHandoffState(runID: runID) }
                else { _ = restoreTerminalHostHandoffIfNeeded() }
            }
        } catch {
            defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
            // lastErrorKey is rendered as product copy on Home. A bridged error
            // description is a numeric domain and code there, so the raw text
            // stays in the log and the user gets something readable.
            defaults.set(Self.hostRelaunchUnverifiedMessage, forKey: lastErrorKey)
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
            if task != nil { schedule() }
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
            print("[LIVE_CONTAINER_REFRESH] NETWORK_PREFLIGHT_START run_id=\(runID.uuidString) request_id=\(correlatedRequestID ?? "none") origin=\(correlatedOrigin ?? "unknown") source=\(source)")
            try await LiveContainerNetworkPreflight.check(
                allowForegroundActivation: manual && source != "vpn_return" && task == nil,
                runID: runID.uuidString)
            recordNetworkPreflight("passed", runID: runID.uuidString)
            print("[LIVE_CONTAINER_REFRESH] NETWORK_PREFLIGHT_PASS run_id=\(runID.uuidString) request_id=\(correlatedRequestID ?? "none") origin=\(correlatedOrigin ?? "unknown")")
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
            if runLedger()[runID.uuidString]?["network_preflight"] as? String == "pending" {
                recordNetworkPreflight("failed", runID: runID.uuidString)
            }
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

    private static func recoverOrphanedRunLedger() {
        let ledger = runLedger()
        for (runID, record) in ledger {
            let currentState = record["state"] as? String ?? "unknown"
            if ["completed", "failed"].contains(currentState) { continue }
            let storedManifest = record["manifest"] as? [String: Any]
            let sharedManifest = defaults.dictionary(forKey: verificationKey)
            let manifest = storedManifest ?? (sharedManifest?["run_id"] as? String == runID ? sharedManifest : nil)
            let manifestIsComplete = manifest.map {
                $0["run_id"] as? String == runID &&
                    CombinedVerification.hasCompleteTerminalResults($0, runID: runID)
            } ?? false
            let hostHandoffPending = defaults.bool(forKey: hostHandoffKey) &&
                defaults.string(forKey: hostHandoffRunKey) == runID
            guard let action = V3RefreshTerminalRecoveryPolicy.action(
                state: record["state"] as? String ?? "unknown",
                terminalIntent: record["terminal_intent"] as? String,
                manifestIsComplete: manifestIsComplete,
                hostHandoffPending: hostHandoffPending) else { continue }

            switch action {
            case .finalizeVerified:
                guard let manifest else { continue }
                defaults.set(manifest, forKey: verificationKey)
                _ = markVerified(runID: runID, source: "relaunch_recovery",
                    detail: "All requested installed-app results were confirmed before LiveContainer closed.")
            case .finalizeFailed:
                let failure = (record["failure"] as? [String: Any]).flatMap {
                    CombinedFailure.decode($0, expectedID: runID)
                }
                _ = markFailed(runID: runID,
                    source: record["source"] as? String ?? "relaunch_recovery",
                    health: record["health"] as? String ?? "REFRESH_FAILED",
                    failure: failure, message: record["message"] as? String,
                    result: record["result"] as? String ?? "failure")
            case .markInterrupted:
                if defaults.string(forKey: activeRunKey) == runID { continue }
                _ = markFailed(runID: runID, source: "relaunch_recovery", health: "REFRESH_INTERRUPTED",
                    failure: CombinedFailure(operation: "refresh", stage: .refreshVerification,
                        code: .interrupted, id: runID, retryable: true),
                    message: "Refresh was interrupted before its terminal result was recorded.",
                    result: "interrupted")
            }
        }
    }

    static func register() {
        guard !registered else { return }
        registered = true
        hostBundle = Bundle.main
        recoverOrphanedRunLedger()
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
            defaults.set(Self.schedulerConfigurationMessage, forKey: lastErrorKey)
            record(source: "scheduler", result: "configuration_failed", detail: error.localizedDescription)
        }
    }

    static func requestRefreshNow() async {
        await execute(source: "alarm_action", manualRequestID: UUID().uuidString, manualOrigin: "deadlineAlarm")
    }
    static func runNow(requestID: String? = nil, origin: String? = nil) {
        Task { @MainActor in
            // Manual refresh must reach scheduler admission promptly. The
            // system permission dialog is requested only by the explicit
            // notification-settings action, not as part of a refresh request.
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
                defaults.set(Self.backgroundSubmitFailedMessage, forKey: lastErrorKey)
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
