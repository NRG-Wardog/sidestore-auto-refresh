import Foundation

@main
struct RefreshAllAttemptHarness {
    static func main() {
        let oldRequest = UUID().uuidString
        let oldRun = UUID().uuidString
        let newRequest = UUID().uuidString
        let newRun = UUID().uuidString

        precondition(V3RefreshAllButtonPresentationPolicy.title(phase: .idle,
            activeRunID: oldRun) == "Refresh Already Running" &&
                     V3RefreshAllButtonPresentationPolicy.explainsConcurrentRun(
                        phase: .idle, activeRunID: oldRun),
            "a manager or scheduled refresh visibly explains why Home cannot start another run")
        precondition(V3RefreshAllButtonPresentationPolicy.title(phase: .idle,
            activeRunID: "") == "Refresh All" &&
                     V3RefreshAllButtonPresentationPolicy.title(phase: .starting,
                        activeRunID: oldRun) == "Starting Refresh...",
            "the concurrent-run explanation does not change this attempt's own visible stages")

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
            "version": 2, "schema": "LiveContainerRefreshManifestSummaryV2",
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
            "version": 2, "schema": "LiveContainerRefreshManifestSummaryV2",
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

        precondition(V3SetupRefreshTerminalEvidencePolicy.outcome(state: "running",
            hasVerifiedManifest: false, hasVerifiedSummary: false) == .pending,
            "an active setup test keeps monitoring")
        precondition(V3SetupRefreshTerminalEvidencePolicy.outcome(state: "failed",
            hasVerifiedManifest: false, hasVerifiedSummary: false) == .failed,
            "a terminal scheduler failure ends setup polling even when it has no success manifest")
        precondition(V3SetupRefreshTerminalEvidencePolicy.outcome(state: "completed",
            hasVerifiedManifest: false, hasVerifiedSummary: false) == .completedUnverified,
            "a completed ledger with missing or malformed proof fails promptly instead of timing out")
        precondition(V3SetupRefreshTerminalEvidencePolicy.outcome(state: "completed",
            hasVerifiedManifest: true, hasVerifiedSummary: false) == .verified &&
                     V3SetupRefreshTerminalEvidencePolicy.outcome(state: "completed",
                        hasVerifiedManifest: false, hasVerifiedSummary: true) == .verified,
            "either authoritative per-run manifest or valid compact summary can verify completion")

        let firstTestAttempt = UUID().uuidString
        let secondTestAttempt = UUID().uuidString
        var activeTestAttempt: String? = firstTestAttempt
        precondition(V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: firstTestAttempt,
            currentAttemptID: activeTestAttempt, taskCancelled: false))
        activeTestAttempt = nil // Cancel R1 before its Task resumes from cancellation.
        activeTestAttempt = secondTestAttempt
        precondition(!V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: firstTestAttempt,
            currentAttemptID: activeTestAttempt, taskCancelled: false) &&
                     V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: secondTestAttempt,
                        currentAttemptID: activeTestAttempt, taskCancelled: false),
            "a cancelled Test Refresh R1 cannot overwrite the new R2 UI or diagnostics")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-r1",
            pendingAge: 3, pendingState: "running", activeRunID: "run-r1",
            activeRunRequestID: "request-r1") == .resumeExisting("request-r1"),
            "Cancel Test stops only its watcher; an immediate retry resumes the exact R1 scheduler request")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-r1",
            pendingAge: 3, pendingState: nil, activeRunID: nil,
            activeRunRequestID: nil) == .resumeExisting("request-r1"),
            "a request posted but not yet admitted is resumed during its bounded start window")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-r1",
            pendingAge: 31, pendingState: nil, activeRunID: nil,
            activeRunRequestID: nil) == .startNew,
            "a request never admitted by the scheduler can be replaced after the start window")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: nil,
            pendingAge: 0, pendingState: nil, activeRunID: "manager-run",
            activeRunRequestID: "manager-request") == .waitForActiveRun,
            "Setup Test does not post a request that the scheduler will coalesce behind a manager run")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-old",
            pendingAge: 0, pendingState: "completed", activeRunID: "manager-run",
            activeRunRequestID: "manager-request") == .resumeExisting("request-old"),
            "a terminal prior Test is consumed even if another run started afterwards")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-r1",
            pendingAge: 40, pendingState: "completed", activeRunID: nil,
            activeRunRequestID: nil) == .resumeExisting("request-r1"),
            "after Stop Waiting, a terminal R1 result is consumed before another test request is created")
        precondition(V3SetupTestRequestPolicy.select(pendingRequestID: "request-old",
            pendingAge: 1, pendingState: "running", activeRunID: "manager-run",
            activeRunRequestID: "manager-request") == .waitForActiveRun,
            "a stale ledger entry does not attach Setup Test to a different active run")

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

        // The production sanitizer sits before the refresh verification plist
        // crosses XPC. An integer NSNumber can bridge to Bool with `as? Bool`,
        // so exercise the malformed bytes through that exact boundary and then
        // through the production terminal verifier.
        let numericRun = UUID().uuidString
        let numericManifest: [String: Any] = [
            "version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": numericRun,
            "expected_ids": ["fixture.app"],
            "results": [["bundle_id": "fixture.app", "success": NSNumber(value: 1)]]
        ]
        let numericInput = try! PropertyListSerialization.data(
            fromPropertyList: ["liveContainerAutoRefreshVerification": numericManifest],
            format: .binary, options: 0)
        let numericDecoded = try! PropertyListSerialization.propertyList(from: numericInput, format: nil) as! [String: Any]
        let numericDecodedManifest = numericDecoded["liveContainerAutoRefreshVerification"] as! [String: Any]
        let numericDecodedRows = numericDecodedManifest["results"] as! [[String: Any]]
        let numericDecodedSuccess = numericDecodedRows[0]["success"] as! NSNumber
        precondition(CFGetTypeID(numericDecodedSuccess) != CFBooleanGetTypeID(),
            "the serialized input fixture must remain an integer NSNumber")
        let numericSanitized = CombinedVerification.sanitized(numericDecoded, runID: numericRun)
        let numericOutput = try! PropertyListSerialization.data(fromPropertyList: numericSanitized,
            format: .binary, options: 0)
        let numericRoundTrip = try! PropertyListSerialization.propertyList(from: numericOutput, format: nil) as! [String: Any]
        let numericRoundTripManifest = numericRoundTrip["liveContainerAutoRefreshVerification"] as? [String: Any]
        precondition(!CombinedVerification.hasCompleteTerminalResults(numericRoundTripManifest ?? [:], runID: numericRun),
            "plist integer 1 must not become a verified successful app result")
        var numericAttempt = V3RefreshAllAttemptState()
        let numericRequest = UUID().uuidString
        numericAttempt.begin(requestID: numericRequest)
        let numericTerminal = record(numericRequest, numericRun, "completed", numericRoundTripManifest)
        precondition(numericAttempt.observe(numericTerminal) && numericAttempt.phase == .failed,
            "the actual refresh terminal policy must reject a numeric-Boolean manifest")

        // A malformed result also leaves uncertainty intact: completedRefresh
        // returns at its terminal-manifest guard before clearing this run marker.
        let defaultsSuite = "V3RefreshBoolRegression-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: defaultsSuite)!
        defaults.set(numericRun, forKey: CombinedVerification.uncertainMutationKey)
        if CombinedVerification.hasCompleteTerminalResults(numericRoundTripManifest ?? [:], runID: numericRun) {
            CombinedVerification.clearUncertainty(defaults, runID: numericRun)
        }
        precondition(defaults.string(forKey: CombinedVerification.uncertainMutationKey) == numericRun,
            "a malformed terminal result must not clear the uncertain-mutation lease")
        defaults.removePersistentDomain(forName: defaultsSuite)

        let stringManifest: [String: Any] = [
            "version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": numericRun,
            "expected_ids": ["fixture.app"],
            "results": [["bundle_id": "fixture.app", "success": "true"]]
        ]
        precondition(CombinedVerification.sanitized(
            ["liveContainerAutoRefreshVerification": stringManifest], runID: numericRun).isEmpty,
            "the string \"true\" must be rejected as a malformed result flag")
        var extraRowManifest = manifest(numericRun)
        extraRowManifest["results"] = [
            ["bundle_id": "fixture.app", "success": true],
            ["bundle_id": "unexpected.app", "success": true]
        ]
        precondition(CombinedVerification.sanitized(
            ["liveContainerAutoRefreshVerification": extraRowManifest], runID: numericRun).isEmpty,
            "an unexpected row must be rejected instead of disappearing during sanitization")

        // Both locations that can bypass host replacement verification require
        // a real plist Boolean. Rejection removes the manifest, so the host
        // callback cannot reach its host_handoff shortcut.
        var badManifestHandoff = manifest(numericRun)
        badManifestHandoff["host_handoff"] = NSNumber(value: 1)
        let badManifestHandoffSafe = CombinedVerification.sanitized(
            ["liveContainerAutoRefreshVerification": badManifestHandoff], runID: numericRun)
        precondition(badManifestHandoffSafe.isEmpty,
            "numeric manifest host_handoff must not bypass host verification")
        precondition(!CombinedVerification.hasCompleteTerminalResults(badManifestHandoff, runID: numericRun),
            "the receiving terminal verifier must reject a numeric manifest host_handoff")
        var badOuterHandoff: [String: Any] = ["liveContainerAutoRefreshVerification": manifest(numericRun),
            "liveContainerAutoRefreshHostHandoffRunID": numericRun,
            "liveContainerAutoRefreshHostHandoff": NSNumber(value: 1)]
        let badOuterHandoffSafe = CombinedVerification.sanitized(badOuterHandoff, runID: numericRun)
        let badOuterBytes = try! PropertyListSerialization.data(fromPropertyList: badOuterHandoffSafe,
            format: .binary, options: 0)
        let badOuterRoundTrip = try! PropertyListSerialization.propertyList(from: badOuterBytes, format: nil) as! [String: Any]
        precondition(badOuterRoundTrip["liveContainerAutoRefreshVerification"] == nil,
            "numeric outer host handoff must fail before completedRefresh's bypass check")
        badOuterHandoff["liveContainerAutoRefreshHostHandoff"] = "true"
        precondition(CombinedVerification.sanitized(badOuterHandoff, runID: numericRun).isEmpty,
            "a string outer host handoff marker must be rejected")

        let validRun = UUID().uuidString
        var validManifest = manifest(validRun)
        validManifest["host_handoff"] = true
        let validSanitized = CombinedVerification.sanitized(
            ["liveContainerAutoRefreshVerification": validManifest,
             "liveContainerAutoRefreshHostHandoffRunID": validRun,
             "liveContainerAutoRefreshHostHandoff": true], runID: validRun)
        let validBytes = try! PropertyListSerialization.data(fromPropertyList: validSanitized,
            format: .binary, options: 0)
        let validRoundTrip = try! PropertyListSerialization.propertyList(from: validBytes, format: nil) as! [String: Any]
        let validRoundTripManifest = validRoundTrip["liveContainerAutoRefreshVerification"] as! [String: Any]
        precondition(CombinedVerification.hasCompleteTerminalResults(validRoundTripManifest, runID: validRun),
            "a real plist Boolean true must remain valid through sanitization and round-trip")
        precondition(validRoundTripManifest["host_handoff"] as? Bool == true,
            "a real manifest host handoff Boolean must remain valid")
        precondition(validRoundTrip["liveContainerAutoRefreshHostHandoff"] as? Bool == true,
            "a real outer host handoff Boolean must remain valid")

        print("V3_REFRESH_ALL_REQUEST_TERMINAL_PASS")
    }
}
