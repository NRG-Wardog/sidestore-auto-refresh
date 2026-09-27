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

        precondition(!V3CancellationRecoveryReplyPolicy.mayCancelRetirement(
            operation: "authCancel", requestStillPending: false),
                     "a late authCancel reply after request timeout cannot cancel service retirement")
        precondition(!V3CancellationRecoveryReplyPolicy.mayCancelRetirement(
            operation: "authBegin", requestStillPending: false),
                     "a late authBegin session creation cannot cancel service retirement")
        precondition(V3CancellationRecoveryReplyPolicy.mayCancelRetirement(
            operation: "install", requestStillPending: false),
                     "a late terminal non-session mutation preserves the existing recovery behavior")
        precondition(V3CancellationRecoveryReplyPolicy.mayCancelRetirement(
            operation: "authCancel", requestStillPending: true),
                     "a reply for a live request may resolve its pending recovery entry")

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
        precondition(V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal("cancelled"))
        precondition(V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal("failed"))
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: true, provisioningIncomplete: false) == "timedOut",
            "an authenticated snapshot cannot rewrite a timed-out attempt as success")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: true, provisioningIncomplete: true) ==
                "timedOut")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "timedOut", authenticated: false, provisioningIncomplete: false) == "timedOut")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "cancelled", authenticated: true, provisioningIncomplete: false) == "cancelled",
            "an authenticated snapshot cannot rewrite a cancelled attempt as success")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "cancelled", authenticated: true, provisioningIncomplete: true) ==
                "cancelled")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "failed", authenticated: true, provisioningIncomplete: false) == "failed",
            "a previously active account cannot turn an explicit failed attempt into success")
        precondition(V3AuthTimeoutReconciliationPolicy.reconciledState(
            reportedState: "resultUnknown", authenticated: true, provisioningIncomplete: false) == "resultUnknown")

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
        let pollDeadline = now.addingTimeInterval(60)
        let pollTimeout = CombinedFailure(operation: "authPoll", stage: .xpcConnection,
            code: .timedOut, id: current, retryable: true)
        precondition(V3AuthPollRecoveryPolicy.shouldRetry(pollTimeout, now: now,
            sessionDeadline: pollDeadline),
            "one lost auth poll keeps monitoring the same session")
        precondition(V3AuthPollRecoveryPolicy.retryDelay(attempt: 3, remaining: 0.25) == 0.25,
            "the final poll backoff is clamped to the exact session deadline")
        precondition(V3AuthPollRecoveryPolicy.retryDelay(attempt: 0, remaining: 0) == 0,
            "no polling retry starts after the session deadline")
        precondition(V3AuthCancellationRetryPolicy.canRetry(isCancelling: false,
            cancellationConfirmed: false, hasSession: true),
            "an unconfirmed cancellation failure exposes a usable recovery action")
        precondition(!V3AuthCancellationRetryPolicy.canRetry(isCancelling: false,
            cancellationConfirmed: true, hasSession: true),
            "confirmed cancellation does not offer a duplicate cancellation")
        precondition(!V3AuthPollRecoveryPolicy.shouldRetry(pollTimeout, now: pollDeadline,
            sessionDeadline: pollDeadline),
            "auth poll recovery stops at the bounded session deadline")
        precondition(V3AuthPollRecoveryPolicy.shouldFinishTimedOut(pollTimeout, now: pollDeadline,
            sessionDeadline: pollDeadline),
            "a transport failure at the deadline terminates polling as timed out")
        let authFailure = CombinedFailure(operation: "authPoll", stage: .authentication,
            code: .invalidResponse, id: current, retryable: false)
        precondition(!V3AuthPollRecoveryPolicy.shouldRetry(authFailure, now: now,
            sessionDeadline: pollDeadline),
            "typed terminal auth errors are not treated as transport interruptions")
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

        precondition(V3AuthStatusTextPolicy.label(state: "timedOut", isSignedIn: false,
            provisioningFinishedLater: false) == "Timed out",
            "the visible status agrees with the sign-in timeout message")
        precondition(V3AuthStatusTextPolicy.label(state: "promptExpired", isSignedIn: false,
            provisioningFinishedLater: false) == "Verification expired",
            "the visible status agrees with the expired verification prompt")
        precondition(V3AuthStatusTextPolicy.label(state: "resultUnknown", isSignedIn: false,
            provisioningFinishedLater: false) == "Result not confirmed")
        precondition(V3AuthStatusTextPolicy.accountLabel(state: "resultUnknown", isSignedIn: true) ==
            "Account currently signed in")
        precondition(V3AuthStatusTextPolicy.accountLabel(state: "completed", isSignedIn: true) ==
            "Signed in successfully")
        var attemptNotice = V3AuthAttemptFailureNotice()
        attemptNotice.record(snapshotConfirmed: true, authenticated: true,
            failureMessage: "Connection to SideStore was interrupted.", technicalDetails: "stage=xpcConnection")
        precondition(attemptNotice.message.contains("currently reports an account as signed in") &&
                     !attemptNotice.message.contains("existing account") &&
                     attemptNotice.technicalDetails == "stage=xpcConnection",
            "a reconciled account does not prove whether it predated the attempt")
        attemptNotice.record(snapshotConfirmed: false, authenticated: false,
            failureMessage: "Connection to SideStore was interrupted.", technicalDetails: "")
        precondition(attemptNotice.message.contains("could not confirm whether sign-in completed"),
            "a failed snapshot leaves the auth attempt outcome explicitly unknown")
        attemptNotice.clear()
        precondition(attemptNotice.message.isEmpty && attemptNotice.technicalDetails.isEmpty,
            "a new provisioning retry clears the old auth-attempt notice")

        let malformedFailure: [String: Any] = [
            "kind": "networkFailure", "stage": "network", "code": "interrupted",
            "correlationID": current, "underlyingDomain": "redacted",
            "underlyingCode": true, "retryable": 1
        ]
        let malformedDiagnostics = V3AuthFailureDiagnosticsPolicy.render(malformedFailure,
            underlyingCode: V3WireContract.strictInt(malformedFailure["underlyingCode"]),
            retryableValue: V3WireContract.strictBool(malformedFailure["retryable"]))
        precondition(malformedDiagnostics.contains("underlying=redacted/unknown") &&
                     malformedDiagnostics.hasSuffix("retryable=unknown"),
            "malformed diagnostic NSNumber values stay unknown rather than becoming false values")

        print("V3_AUTH_OWNERSHIP_RECONCILIATION_PASS")
    }
}
