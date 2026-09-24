import Foundation

@main
struct RefreshFailureCorrelationHarness {
    static func main() {
        let managerRequest = UUID().uuidString
        let managerRun = UUID().uuidString
        let homeRequest = UUID().uuidString
        let homeRun = UUID().uuidString

        func runRecord(request: String, run: String, state: String,
                       message: String = "", failure: CombinedFailure? = nil) -> [String: Any] {
            var value: [String: Any] = ["request_id": request, "run_id": run, "state": state]
            if !message.isEmpty { value["message"] = message }
            if let failure { value["failure"] = failure.wire }
            return value
        }

        // A previous manager run is never allowed to complete or fail the new Home request.
        let managerFailure = CombinedFailure(operation: "refresh", stage: .authentication,
            id: managerRun, retryable: false)
        let managerTerminal = runRecord(request: managerRequest, run: managerRun, state: "failed",
            message: managerFailure.safeMessage, failure: managerFailure)
        var ledger: [String: Any] = [managerRun: managerTerminal]
        var attempt = V3RefreshAllAttemptState()
        attempt.begin(requestID: homeRequest)
        precondition(!attempt.observe(managerTerminal), "stale manager failure matched a Home request")
        precondition(attempt.phase == .starting && attempt.runID.isEmpty)

        let starting = runRecord(request: homeRequest, run: homeRun, state: "running")
        ledger[homeRun] = starting
        precondition(attempt.observe(starting) && attempt.runID == homeRun)
        let currentFailure = CombinedFailure(operation: "refresh", stage: .network,
            id: homeRun, underlying: NSError(domain: "redacted", code: -1005),
            retryable: true, safeCause: .localDevVPNUnavailable)
        let failed = runRecord(request: homeRequest, run: homeRun, state: "failed",
            message: currentFailure.safeMessage, failure: currentFailure)
        ledger[homeRun] = failed
        precondition(attempt.observe(failed) && attempt.phase == .failed)
        precondition(attempt.terminalMessage == currentFailure.safeMessage)

        let diagnostic = V3RefreshAllFailureDiagnostics.text(
            requestID: homeRequest, runID: homeRun, record: failed)!
        for field in ["manual_refresh_request=\(homeRequest)", "run_id=\(homeRun)",
                      "operation=refresh", "stage=network", "correlation=\(homeRun)",
                      "underlying_domain=redacted", "underlying_code=-1005",
                      "retryable=true", "safe_cause=localDevVPNUnavailable"] {
            precondition(diagnostic.contains(field), "missing current-run diagnostic field: \(field)")
        }
        precondition(V3RefreshAllFailureDiagnostics.text(
            requestID: homeRequest, runID: homeRun, record: managerTerminal) == nil,
            "a previous run failure was reused")
        precondition(!attempt.observe(runRecord(request: homeRequest, run: homeRun, state: "completed")),
            "a late success changed the terminal failure")
        let mismatchedFailure = runRecord(request: homeRequest, run: homeRun, state: "failed",
            message: managerFailure.safeMessage, failure: managerFailure)
        var mismatchedAttempt = V3RefreshAllAttemptState()
        mismatchedAttempt.begin(requestID: homeRequest)
        precondition(mismatchedAttempt.observe(starting))
        precondition(mismatchedAttempt.observe(mismatchedFailure))
        precondition(mismatchedAttempt.terminalMessage ==
            "Refresh failed during refreshVerification, but no safe underlying cause was available.")
        let mismatchedDiagnostic = V3RefreshAllFailureDiagnostics.text(
            requestID: homeRequest, runID: homeRun, record: mismatchedFailure)!
        precondition(mismatchedDiagnostic.contains("stage=refreshVerification") &&
                     mismatchedDiagnostic.contains("code=staleResult") &&
                     !mismatchedDiagnostic.contains("stage=authentication"))

        // Unknown causes say exactly what the service could safely establish.
        let unknownRun = UUID().uuidString
        let unknown = CombinedFailure(operation: "refresh", stage: .command, id: unknownRun,
            underlying: NSError(domain: "PrivateDomain", code: 77))
        let unknownRecord = runRecord(request: homeRequest, run: unknownRun, state: "failed",
            message: unknown.safeMessage, failure: unknown)
        precondition(unknown.safeMessage ==
            "Refresh failed during command, but no safe underlying cause was available.")
        precondition(V3RefreshAllFailureDiagnostics.text(
            requestID: homeRequest, runID: unknownRun, record: unknownRecord)?
            .contains("safe_message=Refresh failed during command, but no safe underlying cause was available.") == true)

        // Home's correlated manual intent now uses the same target list as the visible manager.
        // Scheduled background runs retain SideStore's running-app exclusion and disclose it.
        let requested = ["host.app", "running.guest", "inactive.app"]
        let managerTargets = requested
        let homePlan = CombinedRefreshTargetPolicy.plan(requestedIDs: requested,
            runningIDs: ["running.guest"], isCorrelatedManualRun: true)
        precondition(homePlan.attemptedIDs == managerTargets && homePlan.skippedIDs.isEmpty)
        let scheduledPlan = CombinedRefreshTargetPolicy.plan(requestedIDs: requested,
            runningIDs: ["running.guest"], isCorrelatedManualRun: false)
        precondition(scheduledPlan.attemptedIDs == ["host.app", "inactive.app"])
        precondition(scheduledPlan.skippedIDs == ["running.guest"])

        print("V3_REFRESH_FAILURE_CORRELATION_AND_TARGET_POLICY_PASS")
    }
}
