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
        await execute(source: "manual", task: successful)
        precondition(LiveContainerRefreshBridge.calls == 1 && successful.completions == [true])
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        precondition(defaults.string(forKey: activeRunKey) == nil)
        precondition(defaults.string(forKey: expectedRunKey) == nil)

        clearTestState()
        LiveContainerRefreshBridge.fails = true
        let failed = BGTask()
        await execute(source: "manual", task: failed)
        precondition(failed.completions == [false])
        precondition(defaults.string(forKey: lastResultKey) == "failure")
        precondition(defaults.object(forKey: nextRetryKey) as? Date != nil)
        precondition(UNUserNotificationCenter.shared.requests.contains { $0.content.title == "Refresh failed" })

        clearTestState()
        LiveContainerRefreshBridge.incomplete = true
        
        let omitted = BGTask()
        await execute(source: "manual", task: omitted)
        precondition(omitted.completions == [false])
        precondition(defaults.string(forKey: lastErrorKey) == "verification_manifest_incomplete")

        for stage in [CombinedFailure.Stage.authentication, .signing, .installation, .uniqueDeviceID] {
            clearTestState()
            LiveContainerRefreshBridge.resultFailure = stage
            let failed = BGTask()
            await execute(source: "manual", task: failed)
            precondition(failed.completions == [false])
            let error = defaults.string(forKey: lastErrorKey)!
            precondition(error.contains("stage=" + stage.rawValue) && error.contains("underlying_code=77"))
            precondition(error.contains("retryable=unknown") && !error.contains("SECRET_TOKEN"))
            precondition((defaults.object(forKey: nextRetryKey) == nil) == (stage == .authentication))
        }
        clearTestState()
        LiveContainerRefreshBridge.resultFailure = .installation
        LiveContainerRefreshBridge.resultRetryable = false
        await execute(source: "manual", task: BGTask())
        precondition(defaults.object(forKey: nextRetryKey) == nil)
        precondition(defaults.string(forKey: lastErrorKey)!.contains("retryable=false"))
        let mixedRun = UUID().uuidString
        defaults.set(["run_id": mixedRun, "expected_ids": ["first", "second"], "results": [
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
        precondition(markVerified(runID: currentRun, source: "manual", detail: "current authoritative result"))
        precondition(defaults.string(forKey: uncertainMutationKey) == nil)

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
