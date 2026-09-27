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
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "failed", backendSettled: true, stopConfirmed: nil, outcomeUnknown: true),
            "a settled callback must not erase an explicitly unknown device outcome")
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "working", backendSettled: true, stopConfirmed: true),
            "a working result cannot authorize staged-file cleanup")
        precondition(!V3OperationTerminalAcceptancePolicy.isSettledTerminal(
            state: "completed", backendSettled: nil, stopConfirmed: nil),
            "missing settlement evidence must preserve the staged IPA and mutation owner")

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
