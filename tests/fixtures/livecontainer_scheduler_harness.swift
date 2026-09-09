extension LiveContainerAutoRefreshScheduler {
    private static let guestDiagnosticsStoreKey = "liveContainerAutoRefreshGuestDiagnostics"
    private static let guestWarningStoreKey = "liveContainerAutoRefreshGuestDiagnosticWarning"
    private static let guestAffectedIDsStoreKey = "liveContainerAutoRefreshGuestDiagnosticAffectedIDs"

    static func clearTestState() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("liveContainerAutoRefresh") {
            defaults.removeObject(forKey: key)
        }
        activeRun = nil
        LiveContainerRefreshBridge.calls = 0
        LiveContainerRefreshBridge.fails = false
        LiveContainerRefreshBridge.manifestMode = .valid
        LiveContainerRefreshBridge.hostHandoff = false
        LiveContainerNetworkPreflight.error = nil
        LiveContainerNetworkPreflight.checks = 0
        FakeGuestSignatureProbe.calls = 0
        FakeGuestSignatureProbe.firstCompletionProbeCalls = nil
        FakeGuestSignatureProbe.invalidPaths.removeAll()
        DataManager.shared.model.apps = []
        DataManager.shared.model.hiddenApps = []
        BGTaskScheduler.shared.requests = []
        UNUserNotificationCenter.shared.requests = []
    }

    private static func makeGuest(_ identifier: String, kind: GuestFixtureKind, root: URL) -> FakeGuest {
        let bundle = root.appendingPathComponent(identifier + ".app")
        switch kind {
        case .missingBundle:
            return FakeGuest(appInfo: FakeAppInfo(identifier: identifier, path: bundle.path))
        case .valid, .invalidSignature, .missingExecutable, .unreadableExecutable:
            try! FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
            let executable = bundle.appendingPathComponent("GuestExecutable")
            let plist: [String: Any] = ["CFBundleIdentifier": identifier,
                "CFBundleExecutable": "GuestExecutable", "CFBundlePackageType": "APPL"]
            let data = try! PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            try! data.write(to: bundle.appendingPathComponent("Info.plist"))
            if case .missingExecutable = kind {
                // The bundle advertises an executable that is absent.
            } else {
                try! Data("fixture".utf8).write(to: executable)
            }
            if case .invalidSignature = kind { FakeGuestSignatureProbe.invalidPaths.insert(executable.path) }
            if case .unreadableExecutable = kind {
                try! FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: executable.path)
            }
            return FakeGuest(appInfo: FakeAppInfo(identifier: identifier, path: bundle.path))
        }
    }

    private enum GuestFixtureKind { case valid, invalidSignature, missingBundle, missingExecutable, unreadableExecutable }

    private static func guestRoot() -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(
            "livecontainer-guest-tests-" + UUID().uuidString)
        try! FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private static func setGuests(_ active: [FakeGuest], _ hidden: [FakeGuest]) {
        DataManager.shared.model.apps = active
        DataManager.shared.model.hiddenApps = hidden
    }

    private static func storedText(_ key: String) -> String {
        String(describing: defaults.object(forKey: key) ?? "")
    }

    private static func assertGuestWarningPresent(ids: [String], affectedIDs: [String], root: URL) {
        let diagnostics = storedText(guestDiagnosticsStoreKey)
        precondition(defaults.bool(forKey: guestWarningStoreKey))
        precondition(Set(defaults.stringArray(forKey: guestAffectedIDsStoreKey) ?? []) == Set(affectedIDs))
        for id in ids { precondition(diagnostics.contains(id)) }
        precondition(!diagnostics.contains(root.path))
    }

    private static func assertGuestWarningCleared() {
        precondition(!defaults.bool(forKey: guestWarningStoreKey))
        precondition((defaults.stringArray(forKey: guestAffectedIDsStoreKey) ?? []).isEmpty)
    }

    private static func executeManual() async -> BGTask {
        let task = BGTask()
        await execute(source: "manual", task: task)
        return task
    }

    static func exercise() async {
        clearTestState()
        let root = guestRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        // Case A: an authoritative manifest succeeds with no guests.
        defaults.set("stale host/network error", forKey: lastErrorKey)
        let empty = await executeManual()
        precondition(empty.completions == [true])
        precondition(FakeGuestSignatureProbe.calls == 0)
        precondition(defaults.string(forKey: healthStateKey) == "REFRESH_SUCCEEDED")
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        precondition(defaults.object(forKey: lastSuccessfulKey) is Date)
        precondition(defaults.object(forKey: lastErrorKey) == nil)
        assertGuestWarningCleared()

        // Case B: both visible and hidden guests are enumerated when valid.
        clearTestState()
        setGuests([makeGuest("visible.valid", kind: .valid, root: root)],
                  [makeGuest("hidden.valid", kind: .valid, root: root)])
        let allValid = await executeManual()
        precondition(allValid.completions == [true])
        precondition(FakeGuestSignatureProbe.calls == 2)
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        assertGuestWarningCleared()

        // Case C: at least two false signatures are advisory; later guests are still checked.
        clearTestState()
        let activeBad = makeGuest("active.bad", kind: .invalidSignature, root: root)
        let hiddenBad = makeGuest("hidden.bad", kind: .invalidSignature, root: root)
        setGuests([activeBad, makeGuest("active.after", kind: .valid, root: root)],
                  [hiddenBad, makeGuest("hidden.after", kind: .valid, root: root)])
        let advisory = await executeManual()
        precondition(advisory.completions == [true])
        precondition(FakeGuestSignatureProbe.calls == 4)
        precondition(FakeGuestSignatureProbe.firstCompletionProbeCalls == 0)
        precondition(defaults.string(forKey: healthStateKey) == "REFRESH_SUCCEEDED")
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        precondition(defaults.object(forKey: lastSuccessfulKey) is Date)
        precondition(defaults.object(forKey: lastErrorKey) == nil)
        precondition(!defaults.bool(forKey: retryExhaustedKey))
        precondition(defaults.object(forKey: nextRetryKey) == nil)
        assertGuestWarningPresent(ids: ["active.bad", "active.after", "hidden.bad", "hidden.after"],
                                  affectedIDs: ["active.bad", "hidden.bad"], root: root)

        // A genuine refresh failure preserves the previous advisory diagnostics.
        LiveContainerRefreshBridge.calls = 0
        LiveContainerRefreshBridge.fails = true
        let failedAfterAdvisory = await executeManual()
        precondition(failedAfterAdvisory.completions == [false])
        precondition(FakeGuestSignatureProbe.calls == 4)
        precondition(defaults.string(forKey: lastResultKey) == "failure")
        precondition(defaults.string(forKey: lastErrorKey) != nil)
        assertGuestWarningPresent(ids: ["active.bad", "active.after", "hidden.bad", "hidden.after"],
                                  affectedIDs: ["active.bad", "hidden.bad"], root: root)

        // Case D: missing/unreadable executables are safe diagnostics, not crashes.
        clearTestState()
        setGuests([makeGuest("active.missing", kind: .missingBundle, root: root),
                   makeGuest("active.no_executable", kind: .missingExecutable, root: root),
                   makeGuest("active.unreadable_executable", kind: .unreadableExecutable, root: root)],
                  [makeGuest("hidden.bad.one", kind: .invalidSignature, root: root),
                   makeGuest("hidden.bad.two", kind: .invalidSignature, root: root),
                   makeGuest("hidden.valid", kind: .valid, root: root)])
        let unsafe = await executeManual()
        precondition(unsafe.completions == [true])
        precondition(FakeGuestSignatureProbe.calls == 3)
        precondition(FakeGuestSignatureProbe.firstCompletionProbeCalls == 0)
        assertGuestWarningPresent(ids: ["active.missing", "active.no_executable", "active.unreadable_executable",
                                        "hidden.bad.one", "hidden.bad.two", "hidden.valid"],
                                  affectedIDs: ["active.missing", "active.no_executable", "active.unreadable_executable",
                                                "hidden.bad.one", "hidden.bad.two"], root: root)

        // Case E: a complete clean probe clears the persisted guest warning and old error.
        setGuests([makeGuest("active.repaired", kind: .valid, root: root)], [])
        FakeGuestSignatureProbe.calls = 0
        defaults.set("old authoritative error", forKey: lastErrorKey)
        let repaired = await executeManual()
        precondition(repaired.completions == [true])
        precondition(FakeGuestSignatureProbe.calls == 1)
        precondition(FakeGuestSignatureProbe.firstCompletionProbeCalls == 0)
        precondition(defaults.string(forKey: healthStateKey) == "REFRESH_SUCCEEDED")
        precondition(defaults.string(forKey: lastResultKey) == "verified")
        precondition(defaults.object(forKey: lastErrorKey) == nil)
        assertGuestWarningCleared()

        // Case F: genuine manifest, network, and host-handoff failures stay false and do not probe guests.
        for mode in [FakeManifestMode.missing, .mismatch, .incomplete, .failed] {
            clearTestState()
            setGuests([makeGuest("failure.guest", kind: .invalidSignature, root: root)], [])
            LiveContainerRefreshBridge.manifestMode = mode
            let failedManifest = await executeManual()
            precondition(failedManifest.completions == [false])
            precondition(FakeGuestSignatureProbe.calls == 0)
            precondition(defaults.string(forKey: lastResultKey) != "verified")
            precondition(defaults.string(forKey: lastErrorKey) != nil)
        }
        clearTestState()
        setGuests([makeGuest("handoff.guest", kind: .invalidSignature, root: root)], [])
        LiveContainerRefreshBridge.hostHandoff = true
        let handoff = await executeManual()
        precondition(handoff.completions == [false])
        precondition(FakeGuestSignatureProbe.calls == 0)
        precondition(defaults.bool(forKey: hostHandoffKey))
        precondition(defaults.string(forKey: healthStateKey) == "HOST_REFRESH_AWAITING_RELAUNCH")

        // Completed manual calls are not coalesced; only an overlapping active run is.
        clearTestState()
        let firstManual = await executeManual()
        let secondManual = await executeManual()
        precondition(firstManual.completions == [true] && secondManual.completions == [true])
        precondition(LiveContainerRefreshBridge.calls == 2)

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
            setGuests([makeGuest("network.guest", kind: .invalidSignature, root: root)], [])
            LiveContainerNetworkPreflight.error = NSError(domain: "LiveContainerRefresh.Network", code: code)
            let blocked = BGTask()
            await execute(source: "bgprocessing", task: blocked)
            precondition(LiveContainerRefreshBridge.calls == 0 && blocked.completions == [false])
            precondition(FakeGuestSignatureProbe.calls == 0)
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
        LiveContainerRefreshBridge.manifestMode = .incomplete
        let omitted = BGTask()
        await execute(source: "manual", task: omitted)
        precondition(omitted.completions == [false])
        precondition(defaults.string(forKey: lastErrorKey) == "verification_manifest_incomplete")

        clearTestState()
        activeRun = UUID()
        let coalesced = BGTask()
        await execute(source: "manual", task: coalesced)
        precondition(coalesced.completions == [true] && LiveContainerRefreshBridge.calls == 0)

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
