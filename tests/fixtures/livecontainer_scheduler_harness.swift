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
        BGTaskScheduler.shared.requests = []
        UNUserNotificationCenter.shared.requests = []
        UNUserNotificationCenter.shared.onAdd = nil
    }
    static func exercise() async {
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
            completionNotificationObservedAfterCommit =
                defaults.string(forKey: activeRunKey) == nil &&
                defaults.string(forKey: activeManualRequestKey) == nil &&
                terminal["run_id"] as? String == runID &&
                terminal["request_id"] as? String == requestID &&
                terminal["state"] as? String == "completed" &&
                (terminal["manifest"] as? [String: Any])?["run_id"] as? String == runID
        }
        await execute(source: "manual", task: successful)
        precondition(LiveContainerRefreshBridge.calls == 1 && successful.completions == [true])
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
        let oldManifest = managerRecord["manifest"] as! [String: Any]
        defaults.set(oldManifest, forKey: verificationKey)
        await execute(source: "manual", manualRequestID: homeRequest, manualOrigin: "home")
        let homeRecord = runLedger().values.first { $0["request_id"] as? String == homeRequest }!
        let homeRun = homeRecord["run_id"] as! String
        precondition(managerRecord["origin"] as? String == "refreshManager")
        precondition(homeRecord["origin"] as? String == "home")
        precondition(homeRun != managerRun, "new manual request reused an earlier run ID")
        precondition(homeRecord["state"] as? String == "completed")
        precondition((homeRecord["manifest"] as? [String: Any])?["run_id"] as? String == homeRun,
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
                     "Refresh failed during command, but no safe underlying cause was available.")
        precondition(currentFailure["retryable"] as? String == "unknown")
        precondition(UNUserNotificationCenter.shared.requests.contains { $0.content.title == "Refresh failed" })

        clearTestState()
        LiveContainerRefreshBridge.incomplete = true
        
        let omitted = BGTask()
        await execute(source: "manual", task: omitted)
        precondition(omitted.completions == [false])
        precondition(defaults.string(forKey: lastErrorKey) ==
                     "Refresh failed during refreshVerification, but no safe underlying cause was available.")
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
        precondition(defaults.string(forKey: lastErrorKey)!.contains("retryable=false"))
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
            let error = defaults.string(forKey: lastErrorKey)!
            precondition(error.contains("stage=refreshVerification") && error.contains("code=missingResult"))
            precondition(!error.contains("SECRET_TOKEN") && !error.contains("stage=signing"))
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
