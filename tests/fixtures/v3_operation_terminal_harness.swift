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
