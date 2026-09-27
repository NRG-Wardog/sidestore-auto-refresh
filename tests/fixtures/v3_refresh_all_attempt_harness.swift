import Foundation

@main
struct RefreshAllAttemptHarness {
    static func main() {
        let oldRequest = UUID().uuidString
        let oldRun = UUID().uuidString
        let newRequest = UUID().uuidString
        let newRun = UUID().uuidString

        func manifest(_ runID: String) -> [String: Any] {
            ["version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": runID,
             "expected_ids": ["fixture.app"],
             "results": [["bundle_id": "fixture.app", "success": true]]]
        }
        func record(_ requestID: String, _ runID: String, _ state: String,
                    _ result: [String: Any]? = nil) -> [String: Any] {
            var value: [String: Any] = ["request_id": requestID, "run_id": runID, "state": state]
            if let result { value["manifest"] = result }
            return value
        }

        // A prior manager/settings Manual Refresh has its own request and run.
        let oldCompleted = record(oldRequest, oldRun, "completed", manifest(oldRun))
        var ledger: [String: Any] = [oldRun: oldCompleted]
        var attempt = V3RefreshAllAttemptState()
        attempt.begin(requestID: newRequest)
        precondition(V3RefreshAllAttemptState.record(in: ledger, requestID: newRequest) == nil)
        precondition(!attempt.observe(oldCompleted), "a previous manual refresh satisfied the new request")
        precondition(attempt.phase == .starting && attempt.runID.isEmpty)

        let started = record(newRequest, newRun, "running")
        ledger[newRun] = started
        precondition(V3RefreshAllAttemptState.record(in: ledger, requestID: newRequest)?["run_id"] as? String == newRun)
        precondition(attempt.observe(started) && attempt.runID == newRun && attempt.phase == .refreshing)

        // Global health may turn successful before run cleanup. It is not a
        // terminal input; the exact run remains Verifying until its record commits.
        let verifying = record(newRequest, newRun, "verifying")
        ledger[newRun] = verifying
        let globalHealth = "REFRESH_SUCCEEDED"
        precondition(attempt.observe(verifying, schedulerHealth: globalHealth, activeRunID: newRun) &&
                     attempt.phase == .verifying,
                     "early global success must not terminalize the active exact run")

        // Polling the durable ledger wins even if an activeRun defaults-change
        // event is delayed or missed; the UI does not gate on that event.
        let completed = record(newRequest, newRun, "completed", manifest(newRun))
        ledger[newRun] = completed
        precondition(attempt.observe(completed, schedulerHealth: globalHealth, activeRunID: newRun) &&
                     attempt.phase == .completed,
                     "a delayed activeRun removal event must not hide the committed terminal record")
        let laterFailure = record(newRequest, newRun, "failed")
        let laterVerifying = record(newRequest, newRun, "verifying")
        precondition(!attempt.observe(laterFailure) && !attempt.observe(laterVerifying))
        precondition(attempt.phase == .completed, "success was not absorbing")

        // Terminal ledger entries compact the full per-app manifest. Home must
        // accept the verified, run-correlated summary that replaces it.
        let compactRequest = UUID().uuidString
        let compactRun = UUID().uuidString
        var compactTerminal = record(compactRequest, compactRun, "completed")
        compactTerminal["terminal_intent"] = "verified"
        compactTerminal["health"] = "REFRESH_SUCCEEDED"
        compactTerminal["manifest_run_id"] = compactRun
        compactTerminal["manifest_summary"] = [
            "version": 1, "schema": "LiveContainerRefreshManifestSummaryV1",
            "run_id": compactRun, "verified": true,
            "expected_count": 3, "result_count": 3, "failed_count": 0, "skipped_count": 1,
            "requested_count": 4,
            "requested_ids": ["a.app", "b.app", "c.app", "d.app"],
            "expected_ids": ["a.app", "b.app", "c.app"], "skipped_ids": ["d.app"]
        ] as [String: Any]
        var compactAttempt = V3RefreshAllAttemptState()
        compactAttempt.begin(requestID: compactRequest)
        precondition(compactAttempt.observe(compactTerminal) && compactAttempt.phase == .completed &&
                     compactAttempt.terminalMessage.contains("1 running app(s) were skipped"),
                     "Home must resolve a completed terminal record from its compact summary")
        var unverifiedSummary = compactTerminal
        unverifiedSummary["manifest_summary"] = [
            "version": 1, "schema": "LiveContainerRefreshManifestSummaryV1",
            "run_id": compactRun, "verified": 1,
            "expected_count": 3, "result_count": 3, "failed_count": 0, "skipped_count": 0,
            "requested_count": 3
        ] as [String: Any]
        var unverifiedAttempt = V3RefreshAllAttemptState()
        unverifiedAttempt.begin(requestID: compactRequest)
        precondition(unverifiedAttempt.observe(unverifiedSummary) && unverifiedAttempt.phase == .failed,
                     "a numeric truthy value cannot impersonate a verified terminal summary")

        for malformedCounts: [String: Any] in [
            ["expected_count": 1025, "result_count": 1025, "failed_count": 0,
             "skipped_count": 0, "requested_count": 1025],
            ["expected_count": 3, "result_count": 3, "failed_count": 0,
             "skipped_count": 0, "requested_count": 4],
            ["expected_count": 3.5, "result_count": 3, "failed_count": 0,
             "skipped_count": 0, "requested_count": 3],
            ["expected_count": true, "result_count": 3, "failed_count": 0,
             "skipped_count": 0, "requested_count": 3]
        ] {
            var malformed = compactTerminal
            var summary = compactTerminal["manifest_summary"] as! [String: Any]
            for (key, value) in malformedCounts { summary[key] = value }
            malformed["manifest_summary"] = summary
            var rejected = V3RefreshAllAttemptState()
            rejected.begin(requestID: compactRequest)
            precondition(rejected.observe(malformed) && rejected.phase == .failed,
                         "oversized, inconsistent, fractional, or Boolean summary counts cannot prove success")
        }

        // An old global manifest cannot verify a new run. The per-run terminal
        // record must contain a complete manifest carrying the same run_id.
        var missingManifest = V3RefreshAllAttemptState()
        missingManifest.begin(requestID: UUID().uuidString)
        let anotherRequest = missingManifest.requestID
        let anotherRun = UUID().uuidString
        let oldOnly = record(anotherRequest, anotherRun, "verifying")
        precondition(missingManifest.observe(oldOnly) && missingManifest.phase == .verifying)
        let wrongManifestTerminal = record(anotherRequest, anotherRun, "completed", manifest(oldRun))
        precondition(missingManifest.observe(wrongManifestTerminal) && missingManifest.phase == .failed)

        // A terminal failure is likewise absorbing; a later success callback
        // cannot reverse it.
        var failedAttempt = V3RefreshAllAttemptState()
        let failureRequest = UUID().uuidString
        let failureRun = UUID().uuidString
        failedAttempt.begin(requestID: failureRequest)
        let failure = record(failureRequest, failureRun, "failed")
        precondition(failedAttempt.observe(failure) && failedAttempt.phase == .failed)
        precondition(!failedAttempt.observe(record(failureRequest, failureRun, "completed", manifest(failureRun))))
        precondition(failedAttempt.phase == .failed, "failure was not absorbing")

        precondition(V3RefreshTerminalRecoveryPolicy.action(state: "verifying",
            terminalIntent: "verified", manifestIsComplete: true,
            hostHandoffPending: false) == .finalizeVerified,
            "a crash after run release recovers only the committed verified manifest")
        precondition(V3RefreshTerminalRecoveryPolicy.action(state: "failing",
            terminalIntent: "failed", manifestIsComplete: false,
            hostHandoffPending: false) == .finalizeFailed,
            "a crash after run release preserves its committed failure intent")
        precondition(V3RefreshTerminalRecoveryPolicy.action(state: "verifying",
            terminalIntent: "failed", manifestIsComplete: false,
            hostHandoffPending: false) == .finalizeFailed,
            "a host-handoff timeout terminalizes the exact verifying run")
        precondition(V3RefreshTerminalRecoveryPolicy.action(state: "running",
            terminalIntent: nil, manifestIsComplete: false,
            hostHandoffPending: false) == .markInterrupted,
            "an orphan running ledger entry becomes an explicit interrupted result")
        precondition(V3RefreshTerminalRecoveryPolicy.action(state: "verifying",
            terminalIntent: nil, manifestIsComplete: false,
            hostHandoffPending: true) == nil,
            "a pending host handoff is not prematurely marked terminal")

        print("V3_REFRESH_ALL_REQUEST_TERMINAL_PASS")
    }
}
