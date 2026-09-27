import Foundation

@main
struct OperationTerminalHarness {
    static func main() async {
        let cancelThenSuccess = V3OperationTerminalResponse()
        precondition(cancelThenSuccess.requestCancellation())
        precondition(cancelThenSuccess.value == nil,
                     "a cancellation request must not become a terminal result")
        precondition(cancelThenSuccess.isCancellationRequested)
        precondition(cancelThenSuccess.setIfEmpty(["state": "completed"]),
                     "a successful native callback must commit after cancellation was requested")
        precondition(cancelThenSuccess.value?["state"] as? String == "completed")
        precondition(!cancelThenSuccess.setIfEmpty(["state": "cancelled"]),
                     "late cancellation must not replace the backend completion")

        let successThenCancel = V3OperationTerminalResponse()
        precondition(successThenCancel.setIfEmpty(["state": "completed"]))
        precondition(!successThenCancel.requestCancellation(),
                     "cancelling an already terminal operation must not alter it")
        precondition(successThenCancel.value?["state"] as? String == "completed")

        let cancelledByBackend = V3OperationTerminalResponse()
        precondition(cancelledByBackend.requestCancellation())
        precondition(cancelledByBackend.setIfEmpty(["state": "cancelled", "stopConfirmed": true]))
        precondition(cancelledByBackend.value?["stopConfirmed"] as? Bool == true)

        let failureWins = V3OperationTerminalResponse()
        precondition(failureWins.setIfEmpty(["state": "failed", "stage": "signing"]))
        precondition(!failureWins.requestCancellation())
        precondition(!failureWins.setIfEmpty(["state": "completed"]))
        precondition(failureWins.value?["stage"] as? String == "signing")

        let cancellationSession = UUID().uuidString
        precondition(V3InstallCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "failed", backendSettled: false, stopConfirmed: false,
            outcomeUnknown: true) == nil,
            "an unknown cancellation result must preserve the install attempt and staged IPA")
        precondition(V3InstallCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "cancelled", backendSettled: true, stopConfirmed: true,
            outcomeUnknown: false) == "cancelled",
            "confirmed cancellation releases the install attempt")
        precondition(V3InstallCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "completed", backendSettled: true, stopConfirmed: false,
            outcomeUnknown: false) == "completed",
            "completion that wins the cancel race is retained as completion")
        precondition(V3InstallCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: UUID().uuidString,
            state: "cancelled", backendSettled: true, stopConfirmed: true,
            outcomeUnknown: false) == nil,
            "a terminal response for another session cannot reset this install attempt")

        let forgottenSessionID = UUID().uuidString
        let lostSession = V3OperationMissingSessionPolicy.unknownTerminal(
            sessionID: forgottenSessionID, knownStarted: true)
        precondition(lostSession?["state"] as? String == "failed" &&
            lostSession?["outcomeUnknown"] as? Bool == true &&
            lostSession?["backendSettled"] as? Bool == false &&
            lostSession?["stopConfirmed"] as? Bool == false,
            "a previously started but now-missing service session must remain outcome-unknown")
        precondition(V3OperationMissingSessionPolicy.unknownTerminal(
            sessionID: forgottenSessionID, knownStarted: false) == nil,
            "a known pre-start cancellation must use the before-start cancellation registry")
        var delayedStartRegistry = V3OperationMutationRegistry()
        precondition(delayedStartRegistry.cancel(forgottenSessionID) == .recordedBeforeStart)
        precondition(delayedStartRegistry.begin(forgottenSessionID) == .cancelledBeforeStart,
            "opCancel before the delayed start must prevent the mutation from launching")
        precondition(V3OperationStartDispatchPolicy.provesNotDispatched(resultWasReturned: false))
        precondition(!V3OperationStartDispatchPolicy.provesNotDispatched(resultWasReturned: true),
            "cancellation after opStart produced a session must not erase operation ownership")
        precondition(V3OperationSessionCorrelationPolicy.matches(operation: "opStart",
            target: "", requestedStartSession: forgottenSessionID,
            resultSession: forgottenSessionID))
        precondition(!V3OperationSessionCorrelationPolicy.matches(operation: "opPoll",
            target: forgottenSessionID, requestedStartSession: nil,
            resultSession: UUID().uuidString),
            "a terminal reply for another operation session must not release this session's gate")
        precondition(V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "completed", backendSettled: true, stopConfirmed: nil))
        precondition(!V3OperationCoverDismissalPolicy.mustConfirmBackendStop(
            isRunning: false, hasSession: true, sessionIsTerminal: true,
            hasUncertainSession: false, transitionInFlight: false),
            "a swipe after a confirmed terminal result must not issue a redundant opCancel")
        precondition(V3OperationCoverDismissalPolicy.mustConfirmBackendStop(
            isRunning: true, hasSession: true, sessionIsTerminal: false,
            hasUncertainSession: false, transitionInFlight: false),
            "a running operation still requires backend stop confirmation")
        precondition(V3OperationCoverDismissalPolicy.mustConfirmBackendStop(
            isRunning: false, hasSession: true, sessionIsTerminal: true,
            hasUncertainSession: true, transitionInFlight: false),
            "an outcome-unknown session still requires authoritative stop confirmation")
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "failed", backendSettled: true, stopConfirmed: nil, outcomeUnknown: true),
            "a settled callback must not erase an explicitly unknown device outcome")
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "working", backendSettled: true, stopConfirmed: true),
            "a working result cannot authorize staged-file cleanup")
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "completed", backendSettled: nil, stopConfirmed: nil),
            "missing settlement evidence must preserve the staged IPA and mutation owner")

        let deletePollNow = Date(timeIntervalSince1970: 100)
        precondition(V3DeleteReconciliationPolicy.shouldCheckLibrary(lastCheck: nil, now: deletePollNow))
        precondition(!V3DeleteReconciliationPolicy.shouldCheckLibrary(
            lastCheck: deletePollNow, now: deletePollNow.addingTimeInterval(1)),
            "after a verified absence, callback waiting must not refetch Core Data four times per second")
        precondition(V3DeleteReconciliationPolicy.shouldCheckLibrary(
            lastCheck: deletePollNow,
            now: deletePollNow.addingTimeInterval(V3DeleteReconciliationPolicy.libraryRecheckInterval)),
            "an unsettled delete periodically rechecks authoritative library state")
        let callbackDelay1 = V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: 0.25, backendPending: true, nativeUninstallSucceeded: true,
            appStillInLibrary: false)
        let callbackDelay2 = V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: callbackDelay1, backendPending: true, nativeUninstallSucceeded: true,
            appStillInLibrary: false)
        precondition(callbackDelay1 == 0.5 && callbackDelay2 == 1.0 &&
                     V3DeleteReconciliationPolicy.nextCallbackPollDelay(
                        current: callbackDelay2, backendPending: false,
                        nativeUninstallSucceeded: true, appStillInLibrary: false) == 0.25,
            "a verified delete backs off callback checks, then resets when the callback settles")

        let earlyTerminalAt = Date(timeIntervalSince1970: 100)
        let lateCallbackAt = earlyTerminalAt.addingTimeInterval(700)
        precondition(V3OperationSessionRetentionPolicy.shouldRefreshTerminalAt(
            terminalAccepted: true, backendSettled: false))
        precondition(!V3OperationSessionRetentionPolicy.isExpired(backendSettled: false,
            terminalAt: earlyTerminalAt, now: lateCallbackAt),
            "an unsettled backend task cannot be pruned regardless of its visible terminal age")
        precondition(V3OperationSessionRetentionPolicy.shouldRefreshTerminalAt(
            terminalAccepted: false, backendSettled: true))
        precondition(!V3OperationSessionRetentionPolicy.isExpired(backendSettled: true,
            terminalAt: lateCallbackAt, now: lateCallbackAt),
            "late callback settlement restarts terminal retention from the settlement time")
        precondition(V3OperationSessionRetentionPolicy.isExpired(backendSettled: true,
            terminalAt: lateCallbackAt,
            now: lateCallbackAt.addingTimeInterval(V3OperationSessionRetentionPolicy.terminalRetention + 1)),
            "a settled operation session remains bounded after its final settlement timestamp")

        let lateDeleteTerminal = V3OperationTerminalResponse()
        precondition(lateDeleteTerminal.setIfEmpty(["state": "completed"]))
        precondition(lateDeleteTerminal.reply(sessionID: deleteSession, backendSettled: false)?["state"] as? String == "completed")
        precondition(lateDeleteTerminal.reply(sessionID: deleteSession, backendSettled: true)?["backendSettled"] as? Bool == true,
            "the write-once completion state can report dynamic backend settlement after a late callback")

        var deleteAttempt = V3OperationAttemptState()
        let deleteGeneration = deleteAttempt.begin()
        let deleteSession = deleteGeneration.uuidString
        precondition(deleteAttempt.bind(sessionID: deleteSession, generation: deleteGeneration))
        precondition(deleteAttempt.accept(state: "completed", generation: deleteGeneration,
            sessionID: deleteSession))
        precondition(deleteAttempt.owns(generation: deleteGeneration, sessionID: deleteSession) &&
                     !deleteAttempt.matches(generation: deleteGeneration, sessionID: deleteSession),
            "the sheet can consume a settlement update for its own terminal delete session")
        let pendingDeleteDisposition = V3OperationCompletionPolicy.disposition(
            state: "completed", backendSettled: false)
        precondition(pendingDeleteDisposition == .completedAwaitingBackendSettlement &&
                     V3OperationCompletionPolicy.shouldContinuePolling(state: "completed", backendSettled: false) &&
                     !V3OperationCompletionPolicy.mayDismiss(state: "completed", backendSettled: false),
            "native uninstall success plus library absence stays visible while backend callback is pending")
        precondition(V3OperationCompletionPolicy.mayDismiss(state: "completed",
            backendSettled: false, deviceCheckConfirmed: true),
            "dismissal becomes available only after explicit device-check reconciliation or backend settlement")
        precondition(V3OperationCompletionPolicy.disposition(
            state: "completed", backendSettled: true) == .completed &&
                     !V3OperationCompletionPolicy.shouldContinuePolling(state: "completed", backendSettled: true) &&
                     V3OperationCompletionPolicy.mayDismiss(state: "completed", backendSettled: true),
            "a late successful callback unlocks Done without changing the terminal success result")

        let preparation = V3OperationPreparationGate()
        var cancellations = 0
        preparation.installCancellation { cancellations += 1 }
        let waiter = Task { await preparation.wait() }
        while preparation.pendingWaiterCount == 0 { await Task.yield() }
        precondition(preparation.requestCancellation())
        precondition(cancellations == 1,
                     "cancellation must reach the in-flight URLSession download before it is reported stopped")
        precondition(!preparation.isFinished,
                     "preparation cancellation must await its completion callback")
        precondition(preparation.requestCancellation())
        precondition(cancellations == 1, "repeated cancellation must not forward twice")
        preparation.finish()
        await waiter.value
        precondition(preparation.isFinished)

        let cancelBeforeTaskCreation = V3OperationPreparationGate()
        precondition(cancelBeforeTaskCreation.requestCancellation())
        var lateTaskCancellation = 0
        cancelBeforeTaskCreation.installCancellation { lateTaskCancellation += 1 }
        precondition(lateTaskCancellation == 1,
                     "a download created after cancellation must be cancelled before resume")
        cancelBeforeTaskCreation.finish()

        print("V3_OPERATION_CANCELLATION_TERMINAL_PASS")
    }
}
