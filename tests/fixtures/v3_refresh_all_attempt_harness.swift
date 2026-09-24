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

        print("V3_REFRESH_ALL_REQUEST_TERMINAL_PASS")
    }
}
