import Foundation

@main
struct OperationRecoveryHarness {
    static func main() {
        let session = UUID().uuidString
        let ipa = UUID().uuidString.lowercased()
        var firstProcess = V3OperationRecoveryLease()
        precondition(firstProcess.reserve(sessionID: session, kind: "installSharedIPA",
            stagedIPAToken: ipa) == .reserved)
        precondition(firstProcess.beginDispatch(sessionID: session, kind: "installSharedIPA",
            stagedIPAToken: ipa), "dispatch must persist before the service call")

        // Process recreation restores the durable record; no in-memory task or
        // registry is carried over, and the staged token remains protected.
        var recreatedService = V3OperationRecoveryLease(record: firstProcess.record)
        precondition(recreatedService.blocksMutation)
        precondition(recreatedService.protectedStagedIPAToken == ipa)
        precondition(recreatedService.reserve(sessionID: UUID().uuidString, kind: "delete") == .blocked,
            "a fresh process must not admit a conflicting mutation")
        precondition(!recreatedService.beginDispatch(sessionID: session, kind: "installSharedIPA",
            stagedIPAToken: ipa), "relaunch must never replay an already dispatched operation")

        precondition(!recreatedService.settle(sessionID: session, replySessionID: UUID().uuidString,
            state: "completed", backendSettled: true), "a mismatched terminal cannot release the lease")
        precondition(!recreatedService.settle(sessionID: session, replySessionID: session,
            state: "completed", backendSettled: false), "unknown device outcome cannot release the lease")
        precondition(recreatedService.protectedStagedIPAToken == ipa)
        precondition(recreatedService.settle(sessionID: session, replySessionID: session,
            state: "completed", backendSettled: true), "a correlated settled terminal releases the lease")
        precondition(!recreatedService.blocksMutation && recreatedService.protectedStagedIPAToken == nil)

        // A crash between host reservation and service dispatch also stays
        // blocked until the user explicitly checks and reconciles the device.
        let preparedID = UUID().uuidString
        var prepared = V3OperationRecoveryLease()
        precondition(prepared.reserve(sessionID: preparedID, kind: "delete") == .reserved)
        var recreatedHost = V3OperationRecoveryLease(record: prepared.record)
        precondition(recreatedHost.blocksMutation)
        precondition(!recreatedHost.reconcileAfterDeviceCheck(sessionID: preparedID, userConfirmed: false))
        precondition(recreatedHost.blocksMutation)
        precondition(recreatedHost.reconcileAfterDeviceCheck(sessionID: preparedID, userConfirmed: true))
        precondition(!recreatedHost.blocksMutation)
        print("V3_OPERATION_RECOVERY_PASS")
    }
}
