import Foundation

@main
struct AuthOwnershipReconciliationHarness {
    static func main() throws {
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

        ownership.register(sessionID: current, deadline: deadline, now: now)
        ownership.reconcile(sessionID: current, authenticationActive: true)
        precondition(ownership.hasActiveSession(now: now),
                     "a snapshot with an active backend session cannot release host ownership")
        ownership.reconcile(sessionID: current, authenticationActive: false)
        precondition(!ownership.hasActiveSession(now: now),
                     "a validated inactive snapshot retires the correlated host owner after a lost terminal poll")

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
        for terminal in ["timedOut", "cancelled", "resultUnknown", "failed", "promptExpired"] {
            let incompleteAccount = V3AuthReconciliationPresentationPolicy.resolve(
                reportedState: terminal, authenticated: true, provisioningIncomplete: true,
                previousFailureMessage: terminal == "failed" ? "Apple rejected credentials." : nil)
            precondition(incompleteAccount.state == terminal &&
                         incompleteAccount.message.contains("provisioning is incomplete") &&
                         !incompleteAccount.message.contains("signed in successfully"),
                "an incomplete account snapshot cannot replace the terminal \(terminal) attempt result")
        }
        for terminal in ["timedOut", "cancelled", "resultUnknown", "failed", "promptExpired"] {
            let completeAccount = V3AuthReconciliationPresentationPolicy.resolve(
                reportedState: terminal, authenticated: true, provisioningIncomplete: false)
            precondition(completeAccount.state == terminal &&
                         !completeAccount.message.contains("signed in successfully"),
                "a complete account snapshot cannot rewrite the terminal \(terminal) attempt result")
        }
        let successfulIncompleteAccount = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "working", authenticated: true, provisioningIncomplete: true)
        precondition(successfulIncompleteAccount.state == "authenticatedProvisioningIncomplete" &&
                     successfulIncompleteAccount.message == "Apple ID signed in successfully.",
                     "only a nonterminal attempt can reconcile to the authenticated provisioning state")
        let authenticatedWhileProvisioningRuns = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "working", authenticated: true, provisioningIncomplete: false,
            authenticationActive: true)
        precondition(authenticatedWhileProvisioningRuns.state == "authenticatedProvisioningIncomplete" &&
                     authenticatedWhileProvisioningRuns.message.contains("still finishing provisioning"),
                     "an active backend session cannot be rendered as a completed sign-in")
        let signedOutTimeout = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "timedOut", authenticated: false, provisioningIncomplete: false)
        precondition(signedOutTimeout.state == "timedOut",
                     "a signed-out snapshot does not turn an unconfirmed timeout into a different result")
        precondition(signedOutTimeout.message.contains("no account is currently signed in") &&
                     !signedOutTimeout.message.contains("Checking the current SideStore account"),
                     "a confirmed signed-out snapshot must end the transient checking copy")
        let inactivePromptSession = V3AuthInactiveSessionResolutionPolicy.resolve(
            reportedState: "awaitingPrompt", authenticated: false, authenticationActive: false)
        precondition(inactivePromptSession?.state == "failed" &&
                     inactivePromptSession?.message.contains("You can start a new sign-in") == true &&
                     V3AuthInactiveSessionResolutionPolicy.resolve(
                        reportedState: "awaitingPrompt", authenticated: false,
                        authenticationActive: true) == nil,
                     "a confirmed inactive signed-out session exits the prompt state, while a live backend session remains owned")
        let unrelatedSessionPresentation = V3AuthInactiveSessionResolutionPolicy.resolve(
            reportedState: "awaitingPrompt", authenticated: false, authenticationActive: false,
            anotherSessionActive: true)
        precondition(unrelatedSessionPresentation?.state == "resultUnknown" &&
                     unrelatedSessionPresentation?.message.contains("Another Apple sign-in session is active") == true,
                     "a lost reply for this session is not misreported as signed out while another session owns authentication")
        precondition(V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
            snapshotConfirmed: true, authenticated: false, hasSession: false,
            cancellationConfirmed: true, state: "failed") &&
            !V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                snapshotConfirmed: false, authenticated: false, hasSession: false,
                cancellationConfirmed: true, state: "failed") &&
            !V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                snapshotConfirmed: true, authenticated: false, hasSession: true,
                cancellationConfirmed: true, state: "failed"),
            "only the correlated confirmed inactive session can commit signed-out failure details")
        precondition(V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
            reportedState: "awaitingPrompt", hasPrompt: true,
            activeSessionMatches: true, cancellationInProgress: false),
            "an authenticated-but-not-yet-provisioned account snapshot cannot hide the active Team/2FA prompt")
        let accountLagPresentation = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "awaitingPrompt", authenticated: true, provisioningIncomplete: true)
        let accountLagFacts = V3AuthSnapshotAuthorityPolicy.facts(V3AuthServiceSnapshot(
            authenticated: true, provisioningIncomplete: true,
            provisioningRetryAvailable: false, authenticationActive: true,
            authenticationSessionID: current))
        precondition(accountLagPresentation.state == "authenticatedProvisioningIncomplete" &&
                     accountLagFacts.authenticated && accountLagFacts.provisioningIncomplete &&
                     accountLagFacts.authenticationActive &&
                     V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
                        reportedState: "awaitingPrompt", hasPrompt: true,
                        activeSessionMatches: true, cancellationInProgress: false),
                     "an incomplete account row cannot replace the still-owned Team prompt")
        precondition(!V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
            reportedState: "awaitingPrompt", hasPrompt: true,
            activeSessionMatches: false, cancellationInProgress: false),
            "a stale session snapshot cannot preserve another session's prompt")
        precondition(!V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
            reportedState: "awaitingPrompt", hasPrompt: true,
            activeSessionMatches: true, cancellationInProgress: true),
            "confirmed cancellation retires an active prompt instead of resuming it")

        let persistedAccountButNoSession = ["authenticated": false,
            "provisioningIncomplete": false, "provisioningRetryAvailable": false,
            "authenticationActive": false]
        precondition(!V3AuthSnapshotAuthorityPolicy.isAuthenticated(persistedAccountButNoSession),
            "a persisted account label cannot override SideStore's explicit unauthenticated session fact")
        precondition(V3AuthSnapshotAuthorityPolicy.isAuthenticated([
            "authenticated": true, "provisioningIncomplete": true,
            "provisioningRetryAvailable": false, "authenticationActive": false]),
            "an authenticated session remains signed in while provisioning is incomplete")
        precondition(V3AuthSnapshotAuthorityPolicy.needsSignIn(authenticated: false) &&
                     !V3AuthSnapshotAuthorityPolicy.needsSignIn(authenticated: true),
            "a persisted email is display metadata and cannot override the session's authenticated fact")

        var reconciliationGate = V3AuthReconciliationGate()
        let oldSnapshotTicket = reconciliationGate.begin(sessionID: prior,
            state: "failed", revision: 4)
        precondition(reconciliationGate.mayApply(oldSnapshotTicket, sessionID: prior,
            state: "failed", revision: 4), "the current snapshot request owns its result")
        reconciliationGate.invalidate() // begin() installs a new auth attempt
        precondition(!reconciliationGate.mayApply(oldSnapshotTicket, sessionID: current,
            state: "working", revision: 0),
            "a delayed snapshot from the previous attempt cannot overwrite a newer sign-in")
        let promptTicket = reconciliationGate.begin(sessionID: current,
            state: "awaitingPrompt", revision: 8)
        precondition(!reconciliationGate.mayApply(promptTicket, sessionID: current,
            state: "awaitingPrompt", revision: 9),
            "a snapshot cannot overwrite a newer prompt revision in the same session")
        let newestSnapshotTicket = reconciliationGate.begin(sessionID: current,
            state: "failed", revision: 9)
        precondition(!reconciliationGate.mayApply(promptTicket, sessionID: current,
            state: "failed", revision: 9) &&
                     reconciliationGate.mayApply(newestSnapshotTicket, sessionID: current,
                        state: "failed", revision: 9),
            "the most recent reconciliation wins when account snapshots return out of order")

        precondition(V3AuthReconciliationSessionPolicy.mayStart(
            expectedSessionID: prior, currentSessionID: prior))
        precondition(!V3AuthReconciliationSessionPolicy.mayStart(
            expectedSessionID: prior, currentSessionID: current),
            "a stale start task cannot mint a new reconciliation ticket for its replacement attempt")

        precondition(V3AuthPollFailureRacePolicy.shouldIgnore(
            requestedSessionID: current, currentSessionID: current,
            requestedRevision: 8, currentRevision: 9,
            requestedPromptResponseGeneration: 3, currentPromptResponseGeneration: 4,
            promptSubmissionInProgress: false),
            "a poll failure from before a completed or failed 2FA response cannot replace that response")
        precondition(V3AuthPollFailureRacePolicy.shouldIgnore(
            requestedSessionID: current, currentSessionID: current,
            requestedRevision: 9, currentRevision: 9,
            requestedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            promptSubmissionInProgress: true),
            "poll failures cannot overwrite an answer that is still being submitted")
        precondition(!V3AuthPollFailureRacePolicy.shouldIgnore(
            requestedSessionID: current, currentSessionID: current,
            requestedRevision: 9, currentRevision: 9,
            requestedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            promptSubmissionInProgress: false),
            "a current poll failure remains reportable when no newer answer exists")
        precondition(!V3AuthAttemptFailureCommitPolicy.mayCommit(
            requestedSessionID: current, currentSessionID: current,
            capturedPromptResponseGeneration: 4, currentPromptResponseGeneration: 5,
            reconciliationGenerationBefore: 12, currentReconciliationGeneration: 13,
            cancellationInProgress: false, taskCancelled: false),
            "a poll failure cannot commit resultUnknown after a newer 2FA response begins")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 9,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 5,
            state: "awaitingPrompt", promptSubmissionInProgress: true,
            cancellationInProgress: false, taskCancelled: false,
            now: now, sessionDeadline: deadline),
            "a poll error reconciled after a newer answer must restart monitoring that same session")
        precondition(!V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 8,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            state: "awaitingPrompt", promptSubmissionInProgress: false,
            cancellationInProgress: false, taskCancelled: false,
            now: now, sessionDeadline: deadline),
            "a current poll error with no newer user response follows its failure path")
        precondition(!V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 8,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            state: "awaitingPrompt", promptSubmissionInProgress: false,
            pollFailureIsTransient: false, cancellationInProgress: false,
            taskCancelled: false, now: now, sessionDeadline: deadline),
            "a deterministic poll failure with no response progress does not retry just because a prompt is visible")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 8,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            state: "awaitingPrompt", promptSubmissionInProgress: false,
            pollFailureIsTransient: true, cancellationInProgress: false,
            taskCancelled: false, now: now, sessionDeadline: deadline),
            "a transient poll transport error retains the current prompt and backs off")
        precondition(!V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 9,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 5,
            state: "awaitingPrompt", promptSubmissionInProgress: true,
            cancellationInProgress: true, taskCancelled: false,
            now: now, sessionDeadline: deadline),
            "authoritative cancellation never restarts polling")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 9,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 5,
            state: "awaitingPrompt", promptSubmissionInProgress: true,
            cancellationInProgress: false, taskCancelled: false,
            now: deadline, sessionDeadline: deadline),
            "the replacement monitor owns deadline handling instead of abandoning a superseded prompt")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 8,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            state: "authenticatedProvisioningIncomplete", promptSubmissionInProgress: false,
            activeSessionID: current, pollFailureIsTransient: false,
            cancellationInProgress: false, taskCancelled: false,
            now: now, sessionDeadline: deadline) &&
            !V3AuthPollMonitorRecoveryPolicy.shouldResume(
                requestedSessionID: current, currentSessionID: current,
                failedPromptRevision: 8, currentPromptRevision: 8,
                failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
                state: "authenticatedProvisioningIncomplete", promptSubmissionInProgress: false,
                activeSessionID: nil, pollFailureIsTransient: false,
                cancellationInProgress: false, taskCancelled: false,
                now: now, sessionDeadline: deadline),
            "a confirmed active provisioning session keeps a poller after XPC loss, but an inactive one does not")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 8,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            state: "working", promptSubmissionInProgress: false,
            activeSessionID: current, pollFailureIsTransient: false,
            cancellationInProgress: false, taskCancelled: false,
            now: now, sessionDeadline: deadline),
            "an unauthenticated but active SideSign session retains its host poller after a deterministic XPC read error")
        precondition(!V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: current, currentSessionID: current,
            failedPromptRevision: 8, currentPromptRevision: 9,
            failedPromptResponseGeneration: 4, currentPromptResponseGeneration: 5,
            state: "awaitingPrompt", promptSubmissionInProgress: true,
            activeSessionID: prior, pollFailureIsTransient: true,
            cancellationInProgress: false, taskCancelled: false,
            now: now, sessionDeadline: deadline),
            "even a transient read error cannot poll this request when a different backend auth session is active")
        precondition(V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
            requestedSessionID: current, currentSessionID: current,
            activeSessionID: current, cancellationInProgress: false, taskCancelled: false) &&
            !V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                requestedSessionID: prior, currentSessionID: current,
                activeSessionID: current, cancellationInProgress: false, taskCancelled: false) &&
            !V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                requestedSessionID: current, currentSessionID: current,
                activeSessionID: nil, cancellationInProgress: false, taskCancelled: false) &&
            !V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                requestedSessionID: current, currentSessionID: current,
                activeSessionID: prior, cancellationInProgress: false, taskCancelled: false) &&
            V3AuthSessionCorrelationPolicy.hasOtherActiveSession(sessionID: current,
                authenticationActive: true, activeSessionID: prior),
            "an authBegin reply lost after dispatch restarts polling only for its own active session")

        let authRequestID = UUID().uuidString
        let missingSession = CombinedFailure(operation: "signIn", stage: .authentication,
            code: .invalidResponse, id: current, retryable: false,
            safeCause: .authSessionUnavailable)
        precondition(!V3AuthPollRecoveryPolicy.isTransientTransportFailure(missingSession))
        let unavailableEnvelope: [String: Any] = [
            "version": 1, "id": authRequestID,
            "failure": missingSession.correlating(to: authRequestID).wire
        ]
        let unavailableBytes = try PropertyListSerialization.data(fromPropertyList: unavailableEnvelope,
            format: .binary, options: 0)
        let unavailableReply = try PropertyListSerialization.propertyList(from: unavailableBytes,
            format: nil) as! [String: Any]
        let decodedUnavailable = CombinedFailure.decode(
            unavailableReply["failure"] as! [String: Any], expectedID: authRequestID)
        precondition(decodedUnavailable?.safeCause == .authSessionUnavailable,
            "a retired auth session keeps its typed cause when rebound to the poll request UUID")
        precondition(decodedUnavailable?.correlationID == authRequestID &&
                     decodedUnavailable?.stage == .authentication &&
                     decodedUnavailable?.retryable == false &&
                     CombinedFailure.decode(missingSession.wire, expectedID: authRequestID) == nil,
            "the failure uses request correlation while retaining the separate session failure semantics")
        let sourceFailureForCorrelation = CombinedFailure(operation: "source", stage: .source,
            code: .failed, id: current,
            underlying: NSError(domain: "NSURLErrorDomain", code: -1005), retryable: true,
            safeCause: .sourceNetworkFailure, sourceStep: .sourceDownload)
        let reboundSourceFailure = sourceFailureForCorrelation.correlating(to: authRequestID)
        let reboundBytes = try PropertyListSerialization.data(
            fromPropertyList: reboundSourceFailure.wire, format: .binary, options: 0)
        let reboundPropertyList = try PropertyListSerialization.propertyList(
            from: reboundBytes, format: nil) as! [String: Any]
        let decodedReboundSource = CombinedFailure.decode(reboundPropertyList,
            expectedID: authRequestID)
        precondition(decodedReboundSource?.underlyingDomain == "NSURLErrorDomain" &&
                     decodedReboundSource?.underlyingCode == -1005 &&
                     decodedReboundSource?.sourceStep == .sourceDownload &&
                     decodedReboundSource?.safeCause == .sourceNetworkFailure,
            "rebinding to request correlation preserves the safe underlying transport evidence")
        var unavailableOwnership = V3AuthSessionOwnership()
        unavailableOwnership.register(sessionID: current, deadline: deadline, now: now)
        precondition(V3AuthSessionUnavailablePolicy.shouldRetireOwnership(
            sessionID: current, currentSessionID: current, failure: decodedUnavailable!))
        unavailableOwnership.clear(sessionID: current)
        precondition(!unavailableOwnership.hasActiveSession(now: now),
            "a validated missing-session cause clears only its host-side mutation owner")
        unavailableOwnership.register(sessionID: prior, deadline: deadline, now: now)
        precondition(!V3AuthSessionUnavailablePolicy.shouldRetireOwnership(
            sessionID: current, currentSessionID: prior, failure: decodedUnavailable!) &&
                     unavailableOwnership.owns(prior, now: now),
            "a late failure cannot clear ownership for a newer sign-in session")
        let missingSessionUI = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: false, provisioningIncomplete: false, snapshotConfirmed: true,
            safeMessage: missingSession.safeMessage, recovery: missingSession.recovery)
        precondition(missingSessionUI.state == "failed" &&
                     missingSessionUI.cancellationConfirmed &&
                     missingSessionUI.message.contains("start a new sign-in"),
                     "a retired service session resolves promptly to actionable state, not a ten-minute connection retry")
        let unrelatedActiveSessionUI = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: false, provisioningIncomplete: false, snapshotConfirmed: true,
            safeMessage: missingSession.safeMessage, recovery: missingSession.recovery,
            anotherSessionActive: true)
        precondition(unrelatedActiveSessionUI.state == "resultUnknown" &&
                     unrelatedActiveSessionUI.message.contains("could not be matched") &&
                     unrelatedActiveSessionUI.cancellationConfirmed,
                     "the requested session is retired without claiming another active session failed")
        let authenticatedButIncomplete = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: true, provisioningIncomplete: true, snapshotConfirmed: true,
            safeMessage: missingSession.safeMessage, recovery: missingSession.recovery)
        precondition(authenticatedButIncomplete.state == "authenticatedProvisioningIncomplete" &&
                     authenticatedButIncomplete.provisioningMessage?.contains("saved provisioning session") == true,
                     "session loss after Apple authentication is shown as provisioning recovery")
        let unconfirmedSignedIn = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: true, provisioningIncomplete: false, snapshotConfirmed: false,
            safeMessage: missingSession.safeMessage, recovery: missingSession.recovery)
        precondition(unconfirmedSignedIn.state == "resultUnknown" &&
                     unconfirmedSignedIn.message.contains("current account and provisioning state could not be confirmed") &&
                     !unconfirmedSignedIn.message.contains("confirmed that the account is signed in"),
            "a stale signed-in flag cannot claim success when the confirming snapshot failed")
        let unconfirmedIncomplete = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: true, provisioningIncomplete: true, snapshotConfirmed: false,
            safeMessage: missingSession.safeMessage, recovery: missingSession.recovery)
        precondition(unconfirmedIncomplete.state == "resultUnknown" &&
                     unconfirmedIncomplete.provisioningMessage == nil,
            "a stale provisioning-incomplete flag cannot be rendered as confirmed sign-in")
        precondition(V3AuthStatusTextPolicy.accountLabel(state: "resultUnknown", isSignedIn: true) ==
                     "Last confirmed account status: signed in",
            "unknown current status identifies an older signed-in fact as last confirmed")
        precondition(!V3AuthAttemptFailureCommitPolicy.mayCommit(
            requestedSessionID: prior, currentSessionID: current,
            capturedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            reconciliationGenerationBefore: 12, currentReconciliationGeneration: 13,
            cancellationInProgress: false, taskCancelled: false),
            "an old attempt's catch cannot write into its replacement session")
        precondition(!V3AuthAttemptFailureCommitPolicy.mayCommit(
            requestedSessionID: current, currentSessionID: current,
            capturedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            reconciliationGenerationBefore: 12, currentReconciliationGeneration: 13,
            cancellationInProgress: true, taskCancelled: false),
            "authoritative cancellation prevents a late failure write")
        precondition(V3AuthAttemptFailureCommitPolicy.mayCommit(
            requestedSessionID: current, currentSessionID: current,
            capturedPromptResponseGeneration: 4, currentPromptResponseGeneration: 4,
            reconciliationGenerationBefore: 12, currentReconciliationGeneration: 13,
            cancellationInProgress: false, taskCancelled: false),
            "the current attempt may commit its own failure when no newer response exists")
        let reconciledAuthSuccess = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "working", authenticated: true, provisioningIncomplete: false,
            previousFailureMessage: nil)
        let reconciledProvisioningIncomplete = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: "working", authenticated: true, provisioningIncomplete: true,
            previousFailureMessage: nil)
        precondition(reconciledAuthSuccess.state == "completed" &&
            reconciledProvisioningIncomplete.state == "authenticatedProvisioningIncomplete" &&
            V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: true, authenticated: true, state: reconciledAuthSuccess.state) &&
            V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: true, authenticated: true, state: reconciledProvisioningIncomplete.state) &&
            !V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: false, authenticated: true, state: "completed") &&
            !V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: true, authenticated: false, state: "resultUnknown"),
            "a later poll failure cannot downgrade an account state confirmed by reconciliation")

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
        let authStartNotDispatched = V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(
            CombinedFailure(operation: "authBegin", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true))
        precondition(V3AuthAttemptStartFailurePolicy.isConfirmedNotDispatched(authStartNotDispatched) &&
                     authStartNotDispatched.safeCause == .authAttemptNotDispatched &&
                     authStartNotDispatched.safeMessage.contains("was not submitted"),
                     "a correlated not-dispatched auth start must not be treated as an unknown active session")
        let provisioningNotDispatched = V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(
            CombinedFailure(operation: "authRetryProvisioning", stage: .command,
                code: .busy, id: UUID().uuidString, retryable: true),
            operation: "authRetryProvisioning")
        precondition(provisioningNotDispatched.safeCause == .authProvisioningRetryNotDispatched &&
                     provisioningNotDispatched.safeMessage.contains("provisioning retry"),
                     "a rejected provisioning retry keeps its own non-dispatch meaning")
        let authCapacityFailure = V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(
            CombinedFailure(operation: "authBegin", stage: .command, code: .busy,
                id: authRequestID, retryable: true,
                safeCause: .responseCapacityUnavailable))
        let authCapacityDetails = V3OperationFailureDetails(authCapacityFailure)
        precondition(V3AuthAttemptStartFailurePolicy.isConfirmedNotDispatched(authCapacityFailure) &&
                     authCapacityFailure.safeCause == .authResponseCapacityUnavailable &&
                     authCapacityFailure.recovery.contains("release earlier request results") &&
                     authCapacityDetails.retryDisposition == .prerequisite &&
                     authCapacityDetails.recommendedAction.contains("reload status"),
            "a reply-capacity rejection remains distinct from an active operation and submits no Apple credentials")
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
            "Last confirmed account status: signed in")
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

        let missingSessionFailure = CombinedFailure(operation: "signIn", stage: .authentication,
            code: .notReady, id: current, safeCause: .authSessionUnavailable)
        let unconfirmedSession = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: false, provisioningIncomplete: false, snapshotConfirmed: false,
            safeMessage: missingSessionFailure.safeMessage, recovery: missingSessionFailure.recovery)
        precondition(unconfirmedSession.state == "resultUnknown" &&
            !unconfirmedSession.cancellationConfirmed,
            "a failed account snapshot cannot be treated as confirmed sign-in cancellation")
        precondition(V3AuthUnknownResultRecoveryPolicy.action(isCancelling: false,
            cancellationConfirmed: false, hasSession: false) == .reloadStatus &&
            V3AuthUnknownResultRecoveryPolicy.action(isCancelling: false,
                cancellationConfirmed: false, hasSession: true) == .cancelSession &&
            V3AuthUnknownResultRecoveryPolicy.action(isCancelling: false,
                cancellationConfirmed: true, hasSession: false) == .reloadStatus,
            "an unknown result without a live session offers status reload, not a fake local cancellation")
        precondition(V3AuthUnknownResultReconciliationPolicy.reportedState(
            originalState: "resultUnknown", hasSession: false, authenticated: false) == "working" &&
            V3AuthUnknownResultReconciliationPolicy.reportedState(
                originalState: "resultUnknown", hasSession: false, authenticated: true) == "resultUnknown" &&
            V3AuthUnknownResultReconciliationPolicy.reportedState(
                originalState: "resultUnknown", hasSession: true, authenticated: true) == "resultUnknown",
            "an authenticated account snapshot cannot turn an uncorrelated attempt into success")
        let signedInButUnknownAttempt = V3AuthReconciliationPresentationPolicy.resolve(
            reportedState: V3AuthUnknownResultReconciliationPolicy.reportedState(
                originalState: "resultUnknown", hasSession: false, authenticated: true),
            authenticated: true, provisioningIncomplete: false)
        precondition(signedInButUnknownAttempt.state == "resultUnknown" &&
            !signedInButUnknownAttempt.message.contains("signed in successfully"),
            "current account state and the latest sign-in attempt remain separate facts")
        let unknownProvisioningRecovery = V3AuthProvisioningRecoveryPolicy.resolve(
            state: "resultUnknown", hasSession: false, signedIn: true,
            provisioningRetryAvailable: true, isCancelling: false,
            cancellationConfirmed: true)
        precondition(unknownProvisioningRecovery.showRetryProvisioning &&
            unknownProvisioningRecovery.showFinishLater &&
            !unknownProvisioningRecovery.showCancellationInstruction,
            "an authenticated account with incomplete provisioning and no active auth session gets usable recovery without claiming the old attempt succeeded")
        let unconfirmedProvisioningRecovery = V3AuthProvisioningRecoveryPolicy.resolve(
            state: "resultUnknown", hasSession: false, signedIn: true,
            provisioningRetryAvailable: true, isCancelling: false,
            cancellationConfirmed: false)
        precondition(!unconfirmedProvisioningRecovery.showRetryProvisioning,
            "a provisioning retry is hidden until an authoritative account snapshot confirms the previous session is absent")
        let backendBusyProvisioningRecovery = V3AuthProvisioningRecoveryPolicy.resolve(
            state: "resultUnknown", hasSession: false, signedIn: true,
            provisioningRetryAvailable: true, isCancelling: false,
            cancellationConfirmed: true, authenticationActive: true)
        precondition(!backendBusyProvisioningRecovery.showRetryProvisioning &&
            backendBusyProvisioningRecovery.blockedByActiveSession,
            "an independently active backend authentication session blocks a stale retry action")
        let activeUnknownProvisioningRecovery = V3AuthProvisioningRecoveryPolicy.resolve(
            state: "resultUnknown", hasSession: true, signedIn: true,
            provisioningRetryAvailable: true, isCancelling: false,
            cancellationConfirmed: false)
        precondition(!activeUnknownProvisioningRecovery.showRetryProvisioning &&
            !activeUnknownProvisioningRecovery.showFinishLater &&
            activeUnknownProvisioningRecovery.showCancellationInstruction,
            "an unconfirmed live auth session must be cancelled before provisioning can be retried")
        let unavailableProvisioningRecovery = V3AuthProvisioningRecoveryPolicy.resolve(
            state: "resultUnknown", hasSession: false, signedIn: true,
            provisioningRetryAvailable: false, isCancelling: false,
            cancellationConfirmed: true, authenticationActive: false)
        precondition(!unavailableProvisioningRecovery.showRetryProvisioning &&
            unavailableProvisioningRecovery.showFinishLater &&
            !unavailableProvisioningRecovery.showCancellationInstruction,
            "when the saved retry session is gone, a signed-in user gets Finish Later instead of an impossible cancellation instruction")
        precondition(V3AuthCancellationFeedbackPolicy.statusLabel(isCancelling: true,
            normalLabel: "Result not confirmed") == "Cancelling..." &&
            V3AuthCancellationFeedbackPolicy.message(isCancelling: true) != nil,
            "cancellation remains visibly acknowledged while the backend confirms it")

        let capacityRejection = CombinedFailure(operation: "authRetryProvisioning",
            stage: .serviceReadiness, code: .busy, id: UUID().uuidString,
            retryable: true, safeCause: .responseCapacityUnavailable)
        let capacityNotDispatched = V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(
            capacityRejection, operation: "authRetryProvisioning")
        precondition(capacityNotDispatched.safeCause == .authResponseCapacityUnavailable &&
            V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched(capacityNotDispatched) &&
            V3AuthProvisioningRetryDispatchPolicy.whatHappened(capacityNotDispatched)
                .contains("provisioning retry") &&
            !V3AuthProvisioningRetryDispatchPolicy.whatHappened(capacityNotDispatched)
                .contains("start sign-in"),
            "provisioning response-capacity rejection keeps its known pre-dispatch cause")
        precondition(!V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched(
            CombinedFailure(operation: "signIn", stage: .network, id: UUID().uuidString,
                retryable: true, safeCause: .networkConnectionLost)),
            "an unrelated network failure does not enter confirmed-not-dispatched recovery")
        let authSessionBusy = CombinedFailure(operation: "authRetryProvisioning",
            stage: .serviceReadiness, code: .busy, id: UUID().uuidString,
            retryable: true, safeCause: .operationInProgress)
        let authSessionBusyStart = V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(
            authSessionBusy, operation: "authRetryProvisioning")
        precondition(authSessionBusyStart.safeCause == .operationInProgress &&
            V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched(authSessionBusyStart) &&
            V3AuthProvisioningRetryDispatchPolicy.whatHappened(authSessionBusyStart)
                .contains("Another sign-in or provisioning attempt is already active"),
            "a backend auth-session conflict remains a typed, no-dispatch prerequisite instead of superseding that session")

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

        let lowercaseID = UUID().uuidString.lowercased()
        let lowercaseInvalidRequest = try PropertyListSerialization.data(fromPropertyList: [
            "id": lowercaseID, "operation": "authPoll", "unexpected": true
        ] as [String: Any], format: .binary, options: 0)
        let lowercaseIdentity = V3WireContract.invalidRequestIdentity(from: lowercaseInvalidRequest)
        precondition(lowercaseIdentity.id == lowercaseID,
            "invalid-request replies retain a valid lowercase UUID for exact caller correlation")

        print("V3_AUTH_OWNERSHIP_RECONCILIATION_PASS")
    }
}
