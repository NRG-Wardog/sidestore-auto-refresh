import Foundation

@main
struct OperationRecoveryHarness {
    static func main() {
        let session = UUID().uuidString
        let ipa = UUID().uuidString.lowercased()
        let deleteTarget = "x-coredata://\(UUID().uuidString)/InstalledApp/p1"

        precondition(V3OperationSessionCorrelationPolicy.requestSessionID(
            operation: "opStart", target: "", payload: ["session": session]) == session)
        for operation in ["opPoll", "opAnswer", "opCancel"] {
            precondition(V3OperationSessionCorrelationPolicy.requestSessionID(
                operation: operation, target: session, payload: [:]) == session)
        }
        precondition(V3OperationSessionCorrelationPolicy.requestSessionID(
            operation: "snapshot", target: session, payload: [:]) == nil)
        precondition(V3OperationSessionCorrelationPolicy.requestSessionID(
            operation: "opStart", target: "", payload: [:]) == nil)

        let refreshRequest = ["version": 1, "id": UUID().uuidString,
            "operation": "refreshAdmissionEnd", "target": UUID().uuidString,
            "deadline": Date().addingTimeInterval(30), "payload": ["state": "failed"]] as [String: Any]
        precondition(V3WireContract.decodeRequest(V3WireContract.encodeRequest(refreshRequest)!) != nil,
            "terminal refresh release carries a typed state")
        var invalidRefreshRequest = refreshRequest
        invalidRefreshRequest["payload"] = ["state": "retry"]
        precondition(V3WireContract.encodeRequest(invalidRefreshRequest) == nil,
            "arbitrary text cannot clear refresh ownership")
        let unreadableRepairRequest: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "recoveryDiscardUnreadable", "target": "",
            "deadline": Date().addingTimeInterval(30), "payload": ["userConfirmed": true]]
        precondition(V3WireContract.decodeRequest(V3WireContract.encodeRequest(unreadableRepairRequest)!) != nil,
            "unreadable journal repair is an explicit empty-target control request")
        var unconfirmedUnreadableRepair = unreadableRepairRequest
        unconfirmedUnreadableRepair["payload"] = ["userConfirmed": false]
        precondition(V3WireContract.encodeRequest(unconfirmedUnreadableRepair) == nil,
            "unreadable journal repair cannot be requested without true device confirmation")

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

        let preparedOperation = V3OperationRecoveryRecord(sessionID: session, kind: "delete", phase: .prepared)!
        let matchingStart = V3ServiceRecoveryAdmissionPolicy.decide(operation: "opStart", target: "",
            payload: ["kind": "delete"], operationSessionID: session, recovery: preparedOperation,
            recoveryReadFailed: false, refreshOwnerLost: false)
        precondition(matchingStart.matchingPreparedStart && !matchingStart.blocksMutation)
        let wrongKindStart = V3ServiceRecoveryAdmissionPolicy.decide(operation: "opStart", target: "",
            payload: ["kind": "install"], operationSessionID: session, recovery: preparedOperation,
            recoveryReadFailed: false, refreshOwnerLost: false)
        precondition(wrongKindStart.blocksMutation,
            "a new operation kind cannot claim an unrelated prepared recovery")
        let matchingPoll = V3ServiceRecoveryAdmissionPolicy.decide(operation: "opPoll", target: session,
            payload: [:], operationSessionID: session, recovery: preparedOperation,
            recoveryReadFailed: false, refreshOwnerLost: false)
        precondition(matchingPoll.recoveryControl && !matchingPoll.blocksMutation)
        precondition(!V3OperationCancelKnownStartedPolicy.resolve(sessionID: session,
            hostReportedKnownStarted: false, recovery: preparedOperation),
            "prepared recovery remains cancelable before opStart")

        let refreshRecord = V3OperationRecoveryRecord(sessionID: session, kind: "refreshAll", phase: .prepared)!
        let dispatchedOperation = V3OperationRecoveryRecord(sessionID: session,
            kind: "delete", phase: .dispatched)!
        precondition(V3OperationCancelKnownStartedPolicy.resolve(sessionID: session,
            hostReportedKnownStarted: false, recovery: dispatchedOperation),
            "after relaunch, the durable dispatched phase overrides a lost host-local session set")
        let coldCancelReply = V3OperationMissingSessionPolicy.unknownTerminal(sessionID: session,
            knownStarted: V3OperationCancelKnownStartedPolicy.resolve(sessionID: session,
                hostReportedKnownStarted: false, recovery: dispatchedOperation))
        precondition(coldCancelReply?["state"] as? String == "failed" &&
            V3WireContract.strictBool(coldCancelReply?["backendSettled"]) == false &&
            V3OperationReplyFieldPolicy.outcomeUnknown(coldCancelReply?["outcomeUnknown"]) == true,
            "a missing in-memory session after cold relaunch cannot be reported as a confirmed cancellation")
        precondition(!V3OperationCancelKnownStartedPolicy.resolve(sessionID: UUID().uuidString,
            hostReportedKnownStarted: false, recovery: dispatchedOperation),
            "a different session cannot inherit dispatched evidence")
        let overlappingStart = V3ServiceRecoveryAdmissionPolicy.decide(operation: "opStart", target: "",
            payload: ["kind": "delete"], operationSessionID: UUID().uuidString, recovery: refreshRecord,
            recoveryReadFailed: false, refreshOwnerLost: true)
        precondition(overlappingStart.blocksMutation,
            "an unknown refresh run blocks all new backend mutations")
        let refreshTerminal = V3ServiceRecoveryAdmissionPolicy.decide(operation: "refreshAdmissionEnd",
            target: session, payload: ["state": "completed"], operationSessionID: nil,
            recovery: refreshRecord, recoveryReadFailed: false, refreshOwnerLost: true)
        precondition(refreshTerminal.recoveryControl && refreshTerminal.refreshRelease &&
            !refreshTerminal.blocksMutation)
        let wrongRefreshTerminal = V3ServiceRecoveryAdmissionPolicy.decide(operation: "refreshAdmissionEnd",
            target: UUID().uuidString, payload: ["state": "completed"], operationSessionID: nil,
            recovery: refreshRecord, recoveryReadFailed: false, refreshOwnerLost: true)
        precondition(wrongRefreshTerminal.blocksMutation && !wrongRefreshTerminal.refreshRelease)
        let refreshReconcile = V3ServiceRecoveryAdmissionPolicy.decide(operation: "refreshAdmissionReconcile",
            target: session, payload: ["userConfirmed": true], operationSessionID: nil,
            recovery: refreshRecord, recoveryReadFailed: false, refreshOwnerLost: true)
        precondition(refreshReconcile.recoveryControl && refreshReconcile.refreshRelease)
        let unconfirmedRefreshReconcile = V3ServiceRecoveryAdmissionPolicy.decide(operation: "refreshAdmissionReconcile",
            target: session, payload: ["userConfirmed": true], operationSessionID: nil,
            recovery: refreshRecord, recoveryReadFailed: false, refreshOwnerLost: false)
        precondition(unconfirmedRefreshReconcile.recoveryControl && !unconfirmedRefreshReconcile.refreshRelease)
        let readableRecordCannotBeDiscarded = V3ServiceRecoveryAdmissionPolicy.decide(
            operation: "recoveryDiscardUnreadable", target: "", payload: ["userConfirmed": true],
            operationSessionID: nil, recovery: preparedOperation,
            recoveryReadFailed: false, refreshOwnerLost: false)
        precondition(readableRecordCannotBeDiscarded.blocksMutation,
            "device check cannot delete a valid operation journal")
        let unreadableConfirmed = V3ServiceRecoveryAdmissionPolicy.decide(
            operation: "recoveryDiscardUnreadable", target: "", payload: ["userConfirmed": true],
            operationSessionID: nil, recovery: nil, recoveryReadFailed: true, refreshOwnerLost: false)
        precondition(unreadableConfirmed.recoveryControl && !unreadableConfirmed.blocksMutation)
        let unreadableUnconfirmed = V3ServiceRecoveryAdmissionPolicy.decide(
            operation: "recoveryDiscardUnreadable", target: "", payload: ["userConfirmed": false],
            operationSessionID: nil, recovery: nil, recoveryReadFailed: true, refreshOwnerLost: false)
        precondition(!unreadableUnconfirmed.recoveryControl && unreadableUnconfirmed.blocksMutation)

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

        var cancelledBeforeDispatch = V3OperationRecoveryLease()
        let cancelledSessionID = UUID().uuidString
        precondition(cancelledBeforeDispatch.reserve(sessionID: cancelledSessionID, kind: "delete") == .reserved)
        precondition(!cancelledBeforeDispatch.clearPreparedAfterConfirmedCancellation(
            sessionID: cancelledSessionID, replySessionID: cancelledSessionID,
            state: "cancelled", backendSettled: true, stopConfirmed: true, knownStarted: true),
            "the knownStarted=false path cannot be inferred from a started request")
        precondition(!cancelledBeforeDispatch.clearPreparedAfterConfirmedCancellation(
            sessionID: cancelledSessionID, replySessionID: UUID().uuidString,
            state: "cancelled", backendSettled: true, stopConfirmed: true, knownStarted: false),
            "a mismatched cancel reply cannot clear a prepared session")
        precondition(cancelledBeforeDispatch.clearPreparedAfterConfirmedCancellation(
            sessionID: cancelledSessionID, replySessionID: cancelledSessionID,
            state: "cancelled", backendSettled: true, stopConfirmed: true, knownStarted: false),
            "a correlated settled opCancel before opStart clears the prepared lease")
        precondition(!cancelledBeforeDispatch.blocksMutation)

        var dispatchedCancel = V3OperationRecoveryLease()
        precondition(dispatchedCancel.reserve(sessionID: cancelledSessionID, kind: "delete") == .reserved)
        precondition(dispatchedCancel.beginDispatch(sessionID: cancelledSessionID, kind: "delete"))
        precondition(!dispatchedCancel.clearPreparedAfterConfirmedCancellation(
            sessionID: cancelledSessionID, replySessionID: cancelledSessionID,
            state: "cancelled", backendSettled: true, stopConfirmed: true, knownStarted: false),
            "knownStarted=false never clears a dispatched lease")
        precondition(dispatchedCancel.blocksMutation)

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

        var refreshRecoveryLease = V3OperationRecoveryLease()
        let recoveredRefreshID = UUID().uuidString
        precondition(refreshRecoveryLease.reserve(sessionID: recoveredRefreshID, kind: "refreshAll") == .reserved)
        var restartedRefreshService = V3RefreshAdmissionLease()
        precondition(restartedRefreshService.restoreLost(runID: recoveredRefreshID))
        precondition(restartedRefreshService.isActive && restartedRefreshService.ownerLost)
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: true, refreshActive: restartedRefreshService.isActive),
            "a recreated service must keep conflicting mutations closed while refresh result is unknown")
        precondition(!refreshRecoveryLease.reconcileRefreshAdmissionAfterDeviceCheck(
            runID: recoveredRefreshID, userConfirmed: false))
        precondition(refreshRecoveryLease.record != nil)
        precondition(refreshRecoveryLease.settleRefreshAdmission(runID: recoveredRefreshID,
            terminalState: "failed", terminalConfirmed: true))
        precondition(refreshRecoveryLease.record == nil,
            "only a matching terminal result releases a durable refresh hold")

        var operationRecord = V3OperationRecoveryLease()
        let operationRecoveryID = UUID().uuidString
        precondition(operationRecord.reserve(sessionID: operationRecoveryID, kind: "delete") == .reserved)
        precondition(!operationRecord.settleRefreshAdmission(runID: operationRecoveryID,
            terminalState: "completed", terminalConfirmed: true))
        precondition(operationRecord.record?.kind == "delete",
            "refresh controls cannot clear an operation recovery lease")

        var userCheckedRefresh = V3OperationRecoveryLease()
        let checkedRefreshID = UUID().uuidString
        precondition(userCheckedRefresh.reserve(sessionID: checkedRefreshID, kind: "refreshAll") == .reserved)
        precondition(userCheckedRefresh.reconcileRefreshAdmissionAfterDeviceCheck(
            runID: checkedRefreshID, userConfirmed: true))
        precondition(userCheckedRefresh.record == nil)
        print("V3_OPERATION_RECOVERY_PASS")
    }
}
