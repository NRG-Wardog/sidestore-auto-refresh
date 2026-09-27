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
        precondition(ownership.owns(current) && !ownership.owns(prior),
                     "a successful replacement begin proves the prior task unwound")
        ownership.observe(operation: "authPoll", sessionID: current, replySessionID: prior,
                          state: "timedOut", now: now)
        precondition(ownership.owns(current), "a late old-session reply cannot clear new ownership")
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
        precondition(!ownership.owns(current, now: deadline),
                     "an expired auth session cannot authorize a late prompt response")
        precondition(!V3AuthPromptResponsePolicy.failureMessage(NSError(domain: "hidden", code: 2))
            .contains("hidden"), "unknown response failures do not expose a raw error domain")

        print("V3_AUTH_OWNERSHIP_RECONCILIATION_PASS")
    }
}
