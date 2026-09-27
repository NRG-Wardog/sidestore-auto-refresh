import Foundation

@main
struct AuthOwnershipReconciliationHarness {
    static func main() {
        let now = Date(timeIntervalSince1970: 20_000)
        let deadline = now.addingTimeInterval(600)
        let prior = UUID().uuidString
        let current = UUID().uuidString
        var ownership = V3AuthSessionOwnership()

        ownership.register(sessionID: prior, deadline: deadline, now: now)
        precondition(ownership.hasActiveSession(now: now))
        ownership.register(sessionID: current, deadline: deadline, now: now)
        ownership.observe(operation: "authBegin", sessionID: current, replySessionID: current,
                          state: "working", now: now)
        precondition(ownership.owns(current, now: now) && !ownership.owns(prior, now: now),
                     "a successful replacement begin proves the prior task unwound")
        ownership.observe(operation: "authPoll", sessionID: current, replySessionID: prior,
                          state: "timedOut", now: now)
        precondition(ownership.owns(current, now: now), "a late old-session reply cannot clear new ownership")
        ownership.observe(operation: "authPoll", sessionID: current, replySessionID: current,
                          state: "awaitingPrompt", now: now)
        precondition(ownership.hasActiveSession(now: now),
                     "an authentication prompt remains an active session")
        ownership.observe(operation: "authPoll", sessionID: current, replySessionID: current,
                          state: "timedOut", now: now)
        precondition(!ownership.hasActiveSession(now: now),
                     "a terminal auth result releases host ownership")

        ownership.register(sessionID: prior, deadline: deadline, now: now)
        precondition(!ownership.hasActiveSession(now: deadline),
                     "auth session ownership expires at its bounded session deadline")

        ownership.register(sessionID: current, deadline: deadline, now: now)
        ownership.clear(sessionID: current)
        precondition(!ownership.hasActiveSession(now: now),
                     "a validated service rejection clears an auth attempt that never started")
        ownership.register(sessionID: current, deadline: deadline, now: now)
        precondition(ownership.hasActiveSession(now: now),
                     "ambiguous delivery retains ownership until a terminal reply or process retirement")
        ownership.clearAll()
        precondition(!ownership.hasActiveSession(now: now),
                     "confirmed service retirement clears host-only auth ownership")
        ownership.register(sessionID: prior, deadline: deadline, now: now)
        ownership.register(sessionID: current, deadline: deadline, now: now)
        ownership.observe(operation: "authPoll", sessionID: current, replySessionID: current,
                          state: "cancelled", now: now)
        precondition(ownership.owns(prior, now: now),
                     "cancelling a replacement attempt does not claim the predecessor already unwound")
        ownership.clearAll()
        precondition(!ownership.hasActiveSession(now: now),
                     "the correlated service-retirement path releases both stale auth owners")

        precondition(V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal("timedOut"))
        precondition(!V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal("failed"))
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: true, provisioningIncomplete: false) == "completed",
            "a late authenticated account snapshot wins over a stale timeout screen")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: true, provisioningIncomplete: true) ==
                "authenticatedProvisioningIncomplete")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: false, provisioningIncomplete: false) == "timedOut")

        precondition(V3ProvisioningResumeAvailabilityPolicy.canResume(
            authenticated: true, currentAppleID: "Dev@Example.com", resumableAppleID: "dev@example.com"))
        precondition(!V3ProvisioningResumeAvailabilityPolicy.canResume(
            authenticated: true, currentAppleID: "dev@example.com", resumableAppleID: nil),
            "authentication alone does not prove process-local provisioning state survived")
        precondition(!V3ProvisioningResumeAvailabilityPolicy.canResume(
            authenticated: false, currentAppleID: "dev@example.com", resumableAppleID: "dev@example.com"))
        precondition(V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn(forceProvisioningRetry: false))
        precondition(!V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn(forceProvisioningRetry: true),
                     "Retry Provisioning cannot take the cached fast path that skips device registration")
        precondition(V3ProvisioningResumeExecutionPolicy.mayPromptForCredentials(forceProvisioningRetry: false))
        precondition(!V3ProvisioningResumeExecutionPolicy.mayPromptForCredentials(forceProvisioningRetry: true),
                     "Retry Provisioning never silently falls back to a credential prompt")
        precondition(!V3ProvisioningRetryRecoveryPolicy.availabilityAfterFailure(
            snapshotConfirmed: true, snapshotAllowsRetry: false, previouslyConfirmedAvailable: true),
            "a confirmed unavailable session cannot be overwritten by a retry catch")
        precondition(V3ProvisioningRetryRecoveryPolicy.availabilityAfterFailure(
            snapshotConfirmed: false, snapshotAllowsRetry: false, previouslyConfirmedAvailable: true),
            "an unconfirmed snapshot preserves the last confirmed resumability fact")

        precondition(V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry(authFailureKind: "invalidCode"))
        precondition(!V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry(authFailureKind: "invalidCredentials"))
        precondition(V3TwoFactorRetryPolicy.recoveryMessage(authFailureKind: "invalidCode") ==
            "The verification code was not accepted. Enter a new code and try again.")
        precondition(V3TwoFactorRetryPolicy.recoveryMessage(authFailureKind: "networkFailure") == nil)

        precondition(V3AuthPromptResponsePolicy.shouldClearSubmissionFailure(
            oldPromptID: "prompt-a", newPromptID: "prompt-b", state: "awaitingPrompt"))
        precondition(!V3AuthPromptResponsePolicy.shouldClearSubmissionFailure(
            oldPromptID: "prompt-a", newPromptID: "prompt-a", state: "awaitingPrompt"))
        let networkFailure = CombinedFailure(operation: "authRespond", stage: .network,
            code: .interrupted, id: UUID().uuidString, retryable: true,
            safeCause: .networkConnectionLost)
        let responseMessage = V3AuthPromptResponsePolicy.failureMessage(networkFailure)
        precondition(responseMessage.contains("connection") || responseMessage.contains("network"),
                     "the host keeps typed response-failure guidance")
        precondition(!V3AuthPromptResponsePolicy.blocksResubmission(networkFailure))
        let deterministicResponseFailure = CombinedFailure(operation: "authRespond", stage: .replyEncoding,
            code: .invalidResponse, id: UUID().uuidString, retryable: false,
            safeCause: .responseEncodingFailed)
        precondition(V3AuthPromptResponsePolicy.blocksResubmission(deterministicResponseFailure),
                     "a deterministic response defect cannot submit the same code again")
        precondition(V3AuthPromptResponsePolicy.diagnostics(deterministicResponseFailure)
            .contains("responseEncodingFailed"))
        precondition(!ownership.owns(current, now: deadline),
                     "an expired auth session cannot authorize a late prompt response")
        precondition(!V3AuthPromptResponsePolicy.failureMessage(NSError(domain: "hidden", code: 2))
            .contains("hidden"), "unknown response failures do not expose a raw error domain")

        print("V3_AUTH_OWNERSHIP_RECONCILIATION_PASS")
    }
}
