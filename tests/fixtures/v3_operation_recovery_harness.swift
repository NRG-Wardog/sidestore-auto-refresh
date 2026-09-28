import Foundation

@main
struct OperationRecoveryHarness {
    static func main() {
        let session = UUID().uuidString
        let ipa = UUID().uuidString.lowercased()
        let deleteTarget = "x-coredata://\(UUID().uuidString)/InstalledApp/p1"

        let deleteRecord = V3OperationRecoveryRecord(sessionID: session, kind: "delete",
            phase: .prepared)
        precondition(deleteRecord != nil)
        let deletePlist = deleteRecord!.propertyListRepresentation
        precondition(!deletePlist.keys.contains("ipa"), "nil IPA markers are omitted from the plist")
        let deleteBytes = try! PropertyListSerialization.data(fromPropertyList: deletePlist,
            format: .binary, options: 0)
        let decodedDelete = try! PropertyListSerialization.propertyList(from: deleteBytes, format: nil)
        precondition(V3OperationRecoveryRecord.decodePropertyList(decodedDelete) == deleteRecord)

        let installRecord = V3OperationRecoveryRecord(sessionID: session, kind: "installSharedIPA",
            phase: .dispatched, stagedIPAToken: ipa)
        precondition(installRecord != nil)
        let installBytes = try! PropertyListSerialization.data(
            fromPropertyList: installRecord!.propertyListRepresentation, format: .binary, options: 0)
        let decodedInstall = try! PropertyListSerialization.propertyList(from: installBytes, format: nil)
        precondition(V3OperationRecoveryRecord.decodePropertyList(decodedInstall) == installRecord)
        precondition(V3OperationRecoveryRecord.decodePropertyList([
            "version": 1, "session": session, "kind": "delete", "phase": "prepared", "ipa": NSNull()
        ]) == nil, "NSNull is not accepted as a persisted IPA value")

        let deadline = Date().addingTimeInterval(30)
        let prepareRequest: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "opRecoveryPrepare", "target": "", "deadline": deadline,
            "payload": ["kind": "delete", "target": deleteTarget, "session": session]]
        guard let prepareBytes = V3WireContract.encodeRequest(prepareRequest),
              V3WireContract.decodeRequest(prepareBytes) != nil else {
            fatalError("opRecoveryPrepare must satisfy the production XPC schema")
        }
        let reconcileRequest: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "opRecoveryReconcile", "target": session, "deadline": deadline,
            "payload": ["userConfirmed": true]]
        precondition(V3WireContract.decodeRequest(V3WireContract.encodeRequest(reconcileRequest)!) != nil)
        var invalidReconcile = reconcileRequest
        invalidReconcile["payload"] = ["userConfirmed": false]
        precondition(V3WireContract.encodeRequest(invalidReconcile) == nil,
            "reconciliation requires the explicit true marker")
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
        precondition(!recreatedService.clearPreparedAfterNotDispatched(sessionID: session,
            expectedRequestID: UUID().uuidString, replyRequestID: UUID().uuidString,
            operationNotDispatched: true), "not-dispatched evidence cannot clear a dispatched lease")

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

        let preparedRequestID = UUID().uuidString
        var preflightRejected = V3OperationRecoveryLease()
        precondition(preflightRejected.reserve(sessionID: preparedID, kind: "delete") == .reserved)
        precondition(!preflightRejected.clearPreparedAfterNotDispatched(sessionID: preparedID,
            expectedRequestID: preparedRequestID, replyRequestID: UUID().uuidString,
            operationNotDispatched: true), "an uncorrelated failure cannot clear the prepared record")
        precondition(preflightRejected.clearPreparedAfterNotDispatched(sessionID: preparedID,
            expectedRequestID: preparedRequestID, replyRequestID: preparedRequestID,
            operationNotDispatched: true), "a correlated service rejection proves dispatch never occurred")

        var longRunningRefresh = V3RefreshAdmissionLease()
        let refreshID = UUID().uuidString
        precondition(longRunningRefresh.acquire(runID: refreshID, requestID: UUID().uuidString,
            authenticationActive: false, anotherMutationActive: false, now: Date(timeIntervalSince1970: 10)))
        precondition(longRunningRefresh.expire(now: Date(timeIntervalSince1970: 10 + 661)))
        precondition(longRunningRefresh.isActive && longRunningRefresh.ownerLost,
            "elapsed time marks refresh ownership lost without releasing admission")
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: true, refreshActive: longRunningRefresh.isActive),
            "install/update remains blocked beyond the former 660-second expiry")
        precondition(longRunningRefresh.release(runID: refreshID) && !longRunningRefresh.isActive)
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: true, refreshActive: longRunningRefresh.isActive),
            "correlated terminal or retirement releases the refresh admission lease")

        var reconciledRefresh = V3RefreshAdmissionLease()
        let reconcileRunID = UUID().uuidString
        precondition(reconciledRefresh.acquire(runID: reconcileRunID, requestID: UUID().uuidString,
            authenticationActive: false, anotherMutationActive: false, now: Date(timeIntervalSince1970: 1)))
        precondition(reconciledRefresh.expire(now: Date(timeIntervalSince1970: 662)))
        precondition(!reconciledRefresh.reconcileAfterDeviceCheck(runID: reconcileRunID, userConfirmed: false))
        precondition(reconciledRefresh.reconcileAfterDeviceCheck(runID: reconcileRunID, userConfirmed: true))
        precondition(!reconciledRefresh.isActive)
        print("V3_OPERATION_RECOVERY_PASS")
    }
}
