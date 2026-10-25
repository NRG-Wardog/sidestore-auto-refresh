extension LiveContainerAutoRefreshScheduler {
    static func clearTestState() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("liveContainerAutoRefresh") { defaults.removeObject(forKey: key) }
        activeRun = nil
        LiveContainerRefreshBridge.calls = 0
        LiveContainerRefreshBridge.fails = false
        LiveContainerRefreshBridge.incomplete = false
        LiveContainerRefreshBridge.uncertain = false
        LiveContainerRefreshBridge.resultFailure = nil
        LiveContainerRefreshBridge.resultRetryable = nil
        LiveContainerRefreshBridge.staleFailure = false
        LiveContainerRefreshBridge.malformedFailure = false
        LiveContainerNetworkPreflight.error = nil
        LiveContainerNetworkPreflight.checks = 0
        LiveContainerNetworkPreflight.runIDs = []
        BGTaskScheduler.shared.requests = []
        UNUserNotificationCenter.shared.requests = []
        UNUserNotificationCenter.shared.onAdd = nil
        UNUserNotificationCenter.shared.settings = UNNotificationSettings()
        UIApplication.shared.openedURLs = []
    }
    static func exercise() async {
        // Before any store access: every key below is cross-process state, and
        // the runner has no App Group entitlement to resolve one.
        HarnessContainerFileManager.install()
        // This executable's own Info.plist declares no ALTAppGroups, so with
        // nothing published the resolver has no group and correctly reports an
        // unavailable shared store. Publish one, which is what the host does
        // before the scheduler ever runs.
        V3SharedAppGroup.publishRuntimeGroup(V3SharedAppGroup.packagedGroup)
        clearTestState()
        UNUserNotificationCenter.shared.settings = UNNotificationSettings(authorizationStatus: .denied)
        await LiveContainerAutoRefreshScheduler.requestNotificationPermissionFromUserAction()
        precondition(UIApplication.shared.openedURLs.contains(URL(string: UIApplication.openSettingsURLString)!))
        clearTestState()
        let noOp = BGTask()
        defaults.set(true, forKey: enabledKey)
        defaults.set(Date().addingTimeInterval(3600), forKey: earliestEligibleKey)
        await execute(source: "bgprocessing", task: noOp)
        precondition(LiveContainerRefreshBridge.calls == 0 && noOp.completions == [true])
        precondition(LiveContainerNetworkPreflight.checks == 0)

        for (code, state) in [(1, "WIFI_UNAVAILABLE"), (2, "VPN_UNAVAILABLE")] {
            clearTestState()
            defaults.set(true, forKey: enabledKey)
            LiveContainerNetworkPreflight.error = NSError(domain: "LiveContainerRefresh.Network", code: code)
            let blocked = BGTask()
            await execute(source: "bgprocessing", task: blocked)
            precondition(LiveContainerRefreshBridge.calls == 0 && blocked.completions == [false])
            precondition(defaults.string(forKey: healthStateKey) == state)
            precondition(defaults.object(forKey: nextRetryKey) is Date)
        }

        clearTestState()
        let disabled = BGTask()
        await execute(source: "bgprocessing", task: disabled)
        precondition(LiveContainerRefreshBridge.calls == 0 && disabled.completions == [true])

        clearTestState()
        let successful = BGTask()
        var completionNotificationObservedAfterCommit = false
        UNUserNotificationCenter.shared.onAdd = { notification in
            guard notification.content.title == "Refresh completed",
                  let runID = notification.content.userInfo["run_id"] as? String,
                  let requestID = notification.content.userInfo["request_id"] as? String,
                  let terminal = runLedger()[runID] else { return }
            var attempt = V3RefreshAllAttemptState()
            attempt.begin(requestID: requestID)
            let homeObservedCompletion = attempt.observe(terminal)
            let verifiedSummary = V3RefreshAllTerminalEvidencePolicy.verifiedSummary(
                terminal["manifest_summary"] as? [String: Any], record: terminal, runID: runID)
            completionNotificationObservedAfterCommit =
                defaults.string(forKey: activeRunKey) == nil &&
                defaults.string(forKey: activeManualRequestKey) == nil &&
                terminal["run_id"] as? String == runID &&
                terminal["request_id"] as? String == requestID &&
                terminal["state"] as? String == "completed" &&
                verifiedSummary && homeObservedCompletion && attempt.phase == .completed &&
                defaults.dictionary(forKey: verificationKey)?["run_id"] as? String == runID
        }
        await execute(source: "manual", task: successful)
        precondition(LiveContainerRefreshBridge.calls == 1 && successful.completions == [true])
        guard let successfulRunID = defaults.dictionary(forKey: verificationKey)?["run_id"] as? String else {
            preconditionFailure("successful refresh must persist its run identity")
        }
        precondition(LiveContainerNetworkPreflight.runIDs == [successfulRunID],
                     "preflight must be owned by the same run that reaches the refresh bridge")
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        precondition(defaults.string(forKey: activeRunKey) == nil)
        precondition(defaults.string(forKey: expectedRunKey) == nil)
        precondition(completionNotificationObservedAfterCommit,
                     "success notification preceded the same run's terminal commit and ownership clear")

        // Manager Manual Refresh and a later Home Refresh All keep distinct
        // request/run identities; the old successful manifest cannot satisfy the new request.
        clearTestState()
        let managerRequest = UUID().uuidString
        await execute(source: "manual", manualRequestID: managerRequest, manualOrigin: "refreshManager")
        let managerRecord = runLedger().values.first { $0["request_id"] as? String == managerRequest }!
        let managerRun = managerRecord["run_id"] as! String
        let homeRequest = UUID().uuidString
        let oldManifest = defaults.dictionary(forKey: verificationKey)!
        precondition(managerRecord["manifest"] == nil &&
                     (managerRecord["manifest_summary"] as? [String: Any])?["run_id"] as? String == managerRun,
                     "completed ledger history should retain a compact proof summary, not a full manifest")
        defaults.set(oldManifest, forKey: verificationKey)
        await execute(source: "manual", manualRequestID: homeRequest, manualOrigin: "home")
        let homeRecord = runLedger().values.first { $0["request_id"] as? String == homeRequest }!
        let homeRun = homeRecord["run_id"] as! String
        precondition(managerRecord["origin"] as? String == "refreshManager")
        precondition(homeRecord["origin"] as? String == "home")
        precondition(homeRun != managerRun, "new manual request reused an earlier run ID")
        precondition(homeRecord["state"] as? String == "completed")
        precondition((homeRecord["manifest_summary"] as? [String: Any])?["run_id"] as? String == homeRun &&
                     defaults.dictionary(forKey: verificationKey)?["run_id"] as? String == homeRun,
                     "old manifest satisfied the new request")
        precondition(runLedger()[managerRun]?["state"] as? String == "completed")

        clearTestState()
        LiveContainerRefreshBridge.fails = true
        let failed = BGTask()
        await execute(source: "manual", task: failed)
        precondition(failed.completions == [false])
        precondition(defaults.string(forKey: lastResultKey) == "failure")
        precondition(defaults.object(forKey: nextRetryKey) as? Date != nil)
        let currentFailure = defaults.dictionary(forKey: currentRunFailureKey)!
        precondition(currentFailure["run_id"] as? String == currentFailure["correlation"] as? String)
        precondition(currentFailure["operation"] as? String == "refresh")
        precondition(currentFailure["stage"] as? String == "command")
        precondition(currentFailure["code"] as? String == "failed")
        precondition(currentFailure["safe_message"] as? String ==
                     "Refresh failed during command, but no safe underlying cause was available.\nError ID: SS-CMD-C11")
        precondition(currentFailure["retryable"] as? String == "unknown")
        let failedRunID = currentFailure["run_id"] as! String
        let failedLedgerRecord = runLedger()[failedRunID]!
        precondition(failedLedgerRecord["manifest"] == nil &&
                     (failedLedgerRecord["manifest_summary"] as? [String: Any])?["run_id"] as? String == failedRunID,
                     "failed history should be bounded to the structured failure and manifest summary")
        precondition(UNUserNotificationCenter.shared.requests.contains { $0.content.title == "Refresh failed" })

        clearTestState()
        LiveContainerRefreshBridge.incomplete = true
        
        let omitted = BGTask()
        await execute(source: "manual", task: omitted)
        precondition(omitted.completions == [false])
        precondition(defaults.string(forKey: lastErrorKey) ==
                     "Refresh failed during refreshVerification, but no safe underlying cause was available.\nError ID: SS-VERIFY-C12")
        let omittedFailure = defaults.dictionary(forKey: currentRunFailureKey)!
        precondition(omittedFailure["operation"] as? String == "refresh")
        precondition(omittedFailure["stage"] as? String == "refreshVerification")
        precondition(omittedFailure["code"] as? String == "missingResult")
        precondition(omittedFailure["correlation"] as? String == omittedFailure["run_id"] as? String)
        precondition(omittedFailure["retryable"] as? String == "unknown")

        for stage in [CombinedFailure.Stage.authentication, .signing, .installation, .uniqueDeviceID] {
            clearTestState()
            LiveContainerRefreshBridge.resultFailure = stage
            let failed = BGTask()
            await execute(source: "manual", task: failed)
            precondition(failed.completions == [false])
            let terminal = defaults.dictionary(forKey: currentRunFailureKey)!
            let failure = terminal["failure"] as! [String: Any]
            precondition(failure["stage"] as? String == stage.rawValue &&
                         terminal["underlying_code"] as? Int == 77)
            precondition(terminal["retryable"] as? String == "unknown")
            precondition((terminal["safe_message"] as? String ?? "").contains(stage.rawValue))
            precondition(!String(describing: terminal).contains("SECRET_TOKEN"))
            precondition((defaults.object(forKey: nextRetryKey) == nil) == (stage == .authentication))
        }
        clearTestState()
        LiveContainerRefreshBridge.resultFailure = .installation
        LiveContainerRefreshBridge.resultRetryable = false
        await execute(source: "manual", task: BGTask())
        precondition(defaults.object(forKey: nextRetryKey) == nil)
        precondition(defaults.dictionary(forKey: currentRunFailureKey)?["retryable"] as? Bool == false)
        let mixedRun = UUID().uuidString
        defaults.set(["version": 2, "schema": "LiveContainerRefreshManifestV2",
                      "run_id": mixedRun, "expected_ids": ["first", "second"], "results": [
            ["bundle_id": "first", "success": false, "failure": CombinedFailure(operation: "refresh", stage: .uniqueDeviceID, id: mixedRun).wire],
            ["bundle_id": "second", "success": false, "failure": CombinedFailure(operation: "refresh", stage: .authentication, id: mixedRun).wire]]],
            forKey: verificationKey)
        precondition(verifyRefreshManifest(runID: mixedRun).failure?.stage == .authentication)
        for stale in [true, false] {
            clearTestState()
            LiveContainerRefreshBridge.resultFailure = .signing
            LiveContainerRefreshBridge.staleFailure = stale
            LiveContainerRefreshBridge.malformedFailure = !stale
            await execute(source: "manual", task: BGTask())
            let terminal = defaults.dictionary(forKey: currentRunFailureKey)!
            precondition(terminal["stage"] as? String == "refreshVerification" &&
                         terminal["code"] as? String == "missingResult")
            precondition((terminal["safe_message"] as? String ?? "").contains("no safe underlying cause"))
            precondition(!String(describing: terminal).contains("SECRET_TOKEN") &&
                         !String(describing: terminal).contains("stage=signing"))
        }

        clearTestState()
        defaults.set(true, forKey: enabledKey)
        LiveContainerRefreshBridge.uncertain = true
        await execute(source: "manual", task: BGTask())
        precondition(defaults.string(forKey: uncertainMutationKey) != nil)
        precondition(defaults.object(forKey: nextRetryKey) == nil, "uncertain mutation scheduled an automatic retry")
        let priorCalls = LiveContainerRefreshBridge.calls
        defaults.set(Date.distantPast, forKey: deadlineKey)
        schedule() // Advancing a schedule must not erase uncertainty.
        await execute(source: "bgprocessing", task: BGTask())
        precondition(LiveContainerRefreshBridge.calls == priorCalls, "uncertain mutation was replayed automatically")
        LiveContainerRefreshBridge.uncertain = false
        await execute(source: "manual", task: BGTask())
        precondition(LiveContainerRefreshBridge.calls == priorCalls + 1)
        precondition(defaults.string(forKey: uncertainMutationKey) == nil)

        clearTestState()
        let oldRun = UUID().uuidString, currentRun = UUID().uuidString
        defaults.set(currentRun, forKey: uncertainMutationKey)
        defaults.set("REFRESH_INTERRUPTED", forKey: healthStateKey)
        precondition(!markVerified(runID: oldRun, source: "relaunch", detail: "old result"))
        precondition(defaults.string(forKey: uncertainMutationKey) == currentRun)
        precondition(defaults.string(forKey: healthStateKey) == "REFRESH_INTERRUPTED")
        precondition(defaults.object(forKey: lastSuccessfulKey) == nil)
        defaults.set(true, forKey: hostHandoffKey)
        defaults.set(oldRun, forKey: hostHandoffRunKey)
        verifyPendingHostHandoff()
        precondition(defaults.bool(forKey: hostHandoffKey), "stale handoff mutated current state")
        precondition(defaults.string(forKey: uncertainMutationKey) == currentRun)
        defaults.set(["run_id": currentRun, "version": 2, "schema": "LiveContainerRefreshManifestV2",
                      "expected_ids": ["fixture.app"],
                      "results": [["bundle_id": "fixture.app", "success": true]]], forKey: verificationKey)
        saveRunRecord(["run_id": currentRun, "request_id": UUID().uuidString,
                       "source": "manual", "state": "verifying",
                       "started_at": Date().timeIntervalSince1970], runID: currentRun)
        precondition(markVerified(runID: currentRun, source: "manual", detail: "current authoritative result"))
        precondition(defaults.string(forKey: uncertainMutationKey) == nil)
        precondition(runLedger()[currentRun]?["state"] as? String == "completed")

        // A missing handoff baseline is terminal for this exact run. It must
        // not leave the Refresh All UI stuck at Verifying indefinitely.
        clearTestState()
        let noBaselineRun = UUID().uuidString
        saveRunRecord(["run_id": noBaselineRun, "request_id": "", "origin": "unknown",
                       "source": "relaunch", "state": "verifying",
                       "started_at": Date().timeIntervalSince1970], runID: noBaselineRun)
        defaults.set(true, forKey: hostHandoffKey)
        defaults.set(noBaselineRun, forKey: hostHandoffRunKey)
        defaults.set(Date().addingTimeInterval(-240), forKey: hostHandoffStartedKey)
        verifyPendingHostHandoff()
        let noBaselineTerminal = runLedger()[noBaselineRun]!
        precondition(noBaselineTerminal["state"] as? String == "failed" &&
                     noBaselineTerminal["terminal_intent"] as? String == "failed" &&
                     noBaselineTerminal["result"] as? String == "host_baseline_unavailable",
                     "missing host baseline must terminalize the correlated run")
        precondition(defaults.dictionary(forKey: currentRunFailureKey)?["run_id"] as? String == noBaselineRun)
        precondition(!defaults.bool(forKey: hostHandoffKey) &&
                     defaults.string(forKey: hostHandoffRunKey) == nil &&
                     defaults.object(forKey: hostBaselineKey) == nil,
                     "terminal missing-baseline recovery must release its handoff metadata")

        // Simulate process death after a terminal failure was written but before
        // handoff metadata cleanup. Recovery restores the ledger's health/copy.
        clearTestState()
        let terminalHandoffRun = UUID().uuidString
        let terminalFailureMessage = "The installed host profile did not advance after replacement."
        saveRunRecord(["run_id": terminalHandoffRun, "request_id": "", "origin": "unknown",
                       "source": "relaunch", "state": "failed", "health": "HOST_REFRESH_FAILED",
                       "message": terminalFailureMessage,
                       "terminal_intent": "failed"], runID: terminalHandoffRun)
        defaults.set(true, forKey: hostHandoffKey)
        defaults.set(terminalHandoffRun, forKey: hostHandoffRunKey)
        defaults.set(["run_id": terminalHandoffRun], forKey: hostBaselineKey)
        defaults.set("HOST_REFRESH_AWAITING_RELAUNCH", forKey: healthStateKey)
        defaults.set("Stale awaiting message", forKey: lastErrorKey)
        verifyPendingHostHandoff()
        precondition(defaults.string(forKey: healthStateKey) == "HOST_REFRESH_FAILED" &&
                     defaults.string(forKey: lastErrorKey) == terminalFailureMessage,
                     "a leftover handoff marker must not overwrite a committed terminal failure")
        precondition(!defaults.bool(forKey: hostHandoffKey) &&
                     defaults.string(forKey: hostHandoffRunKey) == nil &&
                     defaults.object(forKey: hostBaselineKey) == nil)

        // Recreate the iOS bundle/profile read with a temporary app bundle.
        // A renewed host profile alone is not batch success when the exact run
        // has no complete app-result manifest.
        clearTestState()
        let fixtureRoot = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let fixtureApp = fixtureRoot.appendingPathComponent("Host.app", isDirectory: true)
        try! FileManager.default.createDirectory(at: fixtureApp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: fixtureRoot) }
        let fixtureBundleID = "com.example.LiveContainer"
        let info = ["CFBundleIdentifier": fixtureBundleID, "CFBundlePackageType": "APPL"]
        let infoData = try! PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        try! infoData.write(to: fixtureApp.appendingPathComponent("Info.plist"))
        hostBundle = Bundle(url: fixtureApp)
        precondition(hostBundle?.bundleIdentifier == fixtureBundleID,
                     "the profile fixture must use the same host bundle identity as production")
        let renewedExpiration = Date().addingTimeInterval(86_400 * 90)
        let profile = ["UUID": "FIXTURE-PROFILE-UUID",
                       "ApplicationIdentifierPrefix": ["ABCDE12345"],
                       "Entitlements": ["application-identifier": "ABCDE12345." + fixtureBundleID],
                       "ExpirationDate": renewedExpiration] as [String: Any]
        let profilePlist = try! PropertyListSerialization.data(fromPropertyList: profile, format: .xml, options: 0)
        let profileData = Data([0x30, 0x82, 0x01, 0x02]) + profilePlist + Data([0x01, 0x02])
        try! profileData.write(to: fixtureApp.appendingPathComponent("embedded.mobileprovision"))
        let incompleteBatchRun = UUID().uuidString
        saveRunRecord(["run_id": incompleteBatchRun, "request_id": "", "origin": "home",
                       "source": "manual", "state": "verifying",
                       "started_at": Date().timeIntervalSince1970], runID: incompleteBatchRun)
        defaults.set(true, forKey: hostHandoffKey)
        defaults.set(incompleteBatchRun, forKey: hostHandoffRunKey)
        defaults.set(Date().addingTimeInterval(-240), forKey: hostHandoffStartedKey)
        defaults.set(["run_id": incompleteBatchRun, "expiration": Date().addingTimeInterval(-86_400),
                      "identifier": "ABCDE12345." + fixtureBundleID,
                      "uuid": "OLD-PROFILE-UUID"], forKey: hostBaselineKey)
        defaults.removeObject(forKey: verificationKey)
        verifyPendingHostHandoff()
        let incompleteBatchTerminal = runLedger()[incompleteBatchRun]!
        precondition(incompleteBatchTerminal["state"] as? String == "failed" &&
                     incompleteBatchTerminal["result"] as? String == "host_verified_batch_unconfirmed" &&
                     incompleteBatchTerminal["health"] as? String == "REFRESH_FAILED",
                     "host profile renewal cannot report success without this run's complete batch manifest")
        precondition(defaults.dictionary(forKey: currentRunFailureKey)?["run_id"] as? String == incompleteBatchRun)
        precondition(!defaults.bool(forKey: hostHandoffKey) &&
                     defaults.string(forKey: hostHandoffRunKey) == nil)

        // The 180-second non-advanced-profile timeout is also exercised
        // against the actual profile reader and terminal failure writer.
        clearTestState()
        let timedOutHandoffRun = UUID().uuidString
        saveRunRecord(["run_id": timedOutHandoffRun, "request_id": "", "origin": "home",
                       "source": "manual", "state": "verifying",
                       "started_at": Date().timeIntervalSince1970], runID: timedOutHandoffRun)
        defaults.set(true, forKey: hostHandoffKey)
        defaults.set(timedOutHandoffRun, forKey: hostHandoffRunKey)
        defaults.set(Date().addingTimeInterval(-181), forKey: hostHandoffStartedKey)
        defaults.set(["run_id": timedOutHandoffRun, "expiration": renewedExpiration,
                      "identifier": "ABCDE12345." + fixtureBundleID,
                      "uuid": "FIXTURE-PROFILE-UUID"], forKey: hostBaselineKey)
        verifyPendingHostHandoff()
        let timedOutTerminal = runLedger()[timedOutHandoffRun]!
        precondition(timedOutTerminal["state"] as? String == "failed" &&
                     timedOutTerminal["result"] as? String == "host_refresh_failed" &&
                     timedOutTerminal["health"] as? String == "HOST_REFRESH_FAILED",
                     "the expired handoff timeout must write one terminal failure for its run")
        precondition(defaults.dictionary(forKey: currentRunFailureKey)?["run_id"] as? String == timedOutHandoffRun)
        precondition(!defaults.bool(forKey: hostHandoffKey) &&
                     defaults.string(forKey: hostHandoffRunKey) == nil)

        // Execute the actual launch-time ledger recovery writer with the two
        // durable terminal-intent states that can be left by process death.
        clearTestState()
        let interruptedFailureRun = UUID().uuidString
        let interruptedFailure = CombinedFailure(operation: "refresh", stage: .signing,
            id: interruptedFailureRun, safeCause: .developerPortalRejectedRequest)
        saveRunRecord(["run_id": interruptedFailureRun, "request_id": "", "origin": "home",
                       "source": "manual", "state": "failing", "terminal_intent": "failed",
                       "health": "REFRESH_FAILED", "message": interruptedFailure.safeMessage,
                       "failure": interruptedFailure.wire, "result": "failure",
                       "started_at": Date().timeIntervalSince1970], runID: interruptedFailureRun)
        recoverOrphanedRunLedger()
        let recoveredFailure = runLedger()[interruptedFailureRun]!
        precondition(recoveredFailure["state"] as? String == "failed" &&
                     recoveredFailure["health"] as? String == "REFRESH_FAILED" &&
                     (recoveredFailure["failure"] as? [String: Any])?["stage"] as? String == "signing",
                     "a failed terminal intent must preserve the current run's typed failure on recovery")
        precondition(defaults.dictionary(forKey: currentRunFailureKey)?["run_id"] as? String == interruptedFailureRun)

        clearTestState()
        let interruptedSuccessRun = UUID().uuidString
        let recoveredManifest: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2",
            "run_id": interruptedSuccessRun, "expected_ids": ["fixture.app"],
            "results": [["bundle_id": "fixture.app", "success": true]]]
        saveRunRecord(["run_id": interruptedSuccessRun, "request_id": "", "origin": "home",
                       "source": "manual", "state": "verifying", "terminal_intent": "verified",
                       "manifest": recoveredManifest,
                       "started_at": Date().timeIntervalSince1970], runID: interruptedSuccessRun)
        recoverOrphanedRunLedger()
        precondition(runLedger()[interruptedSuccessRun]?["state"] as? String == "completed" &&
                     defaults.string(forKey: healthStateKey) == "REFRESH_SUCCEEDED" &&
                     defaults.string(forKey: activeRunKey) == nil,
                     "a verified terminal intent must recover from its exact committed manifest")

        clearTestState()
        activeRun = UUID()
        let coalesced = BGTask()
        await execute(source: "manual", task: coalesced)
        precondition(coalesced.completions == [true] && LiveContainerRefreshBridge.calls == 0)
        precondition(defaults.string(forKey: lastResultKey) == "coalesced")
        precondition(UNUserNotificationCenter.shared.requests.count == 1)

        clearTestState()
        let ended = BGTask()
        let gate = LiveContainerRefreshCompletionGate()
        precondition(gate.claim())
        ended.setTaskCompleted(success: false)
        await execute(source: "manual", task: ended, gate: gate)
        precondition(ended.completions == [false])
        precondition(defaults.string(forKey: lastResultKey) != "verified")
        clearTestState()
        print("SCHEDULER_BEHAVIOR_TESTS_PASSED")
    }
}

@main
struct SchedulerTestMain {
    @MainActor static func main() async { await LiveContainerAutoRefreshScheduler.exercise() }
}
