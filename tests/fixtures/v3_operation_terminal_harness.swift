import Foundation

@main
struct OperationTerminalHarness {
    static func main() async throws {
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

        precondition(V3DeleteCancellationPolicy.callbackCancellationRemainsPending(
            isCancellation: true, cancellationRequested: true) &&
            !V3DeleteCancellationPolicy.callbackCancellationRemainsPending(
                isCancellation: true, cancellationRequested: false) &&
            V3DeleteCancellationPolicy.cancelRequestReturnsBeforeDriverSettlement(
                operation: "delete", driverIsRunning: true) &&
            !V3DeleteCancellationPolicy.cancelRequestReturnsBeforeDriverSettlement(
                operation: "install", driverIsRunning: true) &&
            V3DeleteCancellationPolicy.keepsHostPollMonitor(operation: "delete"),
            "delete cancellation returns an unsettled reply and keeps its session poller and mutation owner")
        let deleteCancelID = UUID().uuidString
        let nextMutationID = UUID().uuidString
        var deleteCancellationOwner = V3OperationMutationRegistry()
        precondition(deleteCancellationOwner.begin(deleteCancelID) == .started)
        precondition(V3DeleteCancellationPolicy.callbackCancellationRemainsPending(
            isCancellation: true, cancellationRequested: true) &&
            deleteCancellationOwner.begin(nextMutationID) == .busy,
            "opStart remains busy after Cancel until Delete's native result is reconciled")
        precondition(deleteCancellationOwner.finish(deleteCancelID) &&
            deleteCancellationOwner.begin(nextMutationID) == .started,
            "a new mutation is admitted only after the Delete owner actually finishes")
        precondition(!V3OperationCancellationResolutionPolicy.requiresReconciliation(
            backendSettled: true, outcomeUnknown: false) &&
            V3OperationCancellationResolutionPolicy.requiresReconciliation(
                backendSettled: false, outcomeUnknown: false),
            "settled cancellation clears the temporary Reconcile UI state")

        let delayedCancelSession = UUID().uuidString
        precondition(V3OperationCancellationOutcomePolicy.isCorrelated(
            expectedSessionID: delayedCancelSession, replySessionID: delayedCancelSession) &&
            !V3OperationCancellationOutcomePolicy.isCorrelated(
                expectedSessionID: delayedCancelSession, replySessionID: UUID().uuidString),
            "cancel replies are bound to the operation session they were requested for")
        var uncertainDeleteSession: String? = delayedCancelSession
        var visibleDeleteState = "completed"
        let delayedCancelAckClearsHandle = V3OperationCancellationOutcomePolicy.shouldClearSessionHandle(
            currentSessionID: uncertainDeleteSession, expectedSessionID: delayedCancelSession,
            replySessionID: delayedCancelSession, state: "cancelling", backendSettled: false,
            stopConfirmed: false, outcomeUnknown: false)
        if delayedCancelAckClearsHandle {
            visibleDeleteState = "cancelling"
            uncertainDeleteSession = nil
        }
        precondition(!delayedCancelAckClearsHandle && visibleDeleteState == "completed" &&
            uncertainDeleteSession == delayedCancelSession &&
            V3OperationCoverDismissalPolicy.mustConfirmBackendStop(isRunning: false,
                hasSession: false, sessionIsTerminal: true,
                hasUncertainSession: uncertainDeleteSession != nil, transitionInFlight: false),
            "a delayed unsettled cancel acknowledgment cannot regress the newer delete result or erase its reconciliation handle")
        let settledDeleteAck = V3OperationCancellationOutcomePolicy.terminalState(
            expectedSessionID: delayedCancelSession, replySessionID: delayedCancelSession,
            state: "completed", backendSettled: true, stopConfirmed: false,
            outcomeUnknown: false)
        precondition(settledDeleteAck == "completed",
            "a correlated settled cancel reply may resolve and release the session handle")
        let settledAckClearsHandle = V3OperationCancellationOutcomePolicy.shouldClearSessionHandle(
            currentSessionID: uncertainDeleteSession, expectedSessionID: delayedCancelSession,
            replySessionID: delayedCancelSession, state: "completed", backendSettled: true,
            stopConfirmed: false, outcomeUnknown: false)
        if settledAckClearsHandle { uncertainDeleteSession = nil }
        precondition(settledAckClearsHandle && uncertainDeleteSession == nil &&
            !V3OperationCancellationOutcomePolicy.shouldClearSessionHandle(
                currentSessionID: UUID().uuidString, expectedSessionID: delayedCancelSession,
                replySessionID: delayedCancelSession, state: "completed", backendSettled: true,
                stopConfirmed: false, outcomeUnknown: false),
            "only settlement for the currently retained session may release its reconciliation handle")
        precondition(!V3OperationCancellationReplyPolicy.shouldApplyPollState(
            userRequestedCancellation: true, nextState: "working") &&
            !V3OperationCancellationReplyPolicy.shouldApplyPollState(
                userRequestedCancellation: true, nextState: "awaitingPrompt") &&
            V3OperationCancellationReplyPolicy.shouldApplyPollState(
                userRequestedCancellation: true, nextState: "reconciling") &&
            V3OperationCancellationReplyPolicy.shouldApplyPollState(
                userRequestedCancellation: true, nextState: "completed"),
            "a pre-cancel working poll is ignored while authoritative reconciliation and terminal results remain visible")

        let failureWins = V3OperationTerminalResponse()
        precondition(failureWins.setIfEmpty(["state": "failed", "stage": "signing"]))
        precondition(!failureWins.requestCancellation())
        precondition(!failureWins.setIfEmpty(["state": "completed"]))
        precondition(failureWins.value?["stage"] as? String == "signing")

        var lateDeleteSuccessContract = V3DeleteCompletionContract()
        precondition(lateDeleteSuccessContract.resolve(backend: .pending,
            nativeUninstallSucceeded: false, appStillInAuthoritativeLibrary: true,
            deadlineExpired: true, progress: 0.01) == .outcomeUnknown &&
            lateDeleteSuccessContract.terminal == nil,
            "a delete timeout remains provisional while its backend callback is pending")
        precondition(lateDeleteSuccessContract.resolve(backend: .succeeded,
            nativeUninstallSucceeded: true, appStillInAuthoritativeLibrary: false,
            deadlineExpired: false, progress: 0.01) == .completed,
            "late callback success plus authoritative absence resolves the provisional result to completed")

        var lateDeleteFailureContract = V3DeleteCompletionContract()
        precondition(lateDeleteFailureContract.resolve(backend: .pending,
            nativeUninstallSucceeded: false, appStillInAuthoritativeLibrary: true,
            deadlineExpired: true, progress: 0.01) == .outcomeUnknown)
        precondition(lateDeleteFailureContract.resolve(backend: .failed,
            nativeUninstallSucceeded: false, appStillInAuthoritativeLibrary: true,
            deadlineExpired: false, progress: 0.01) == .failed,
            "a late typed backend failure resolves the provisional timeout as failure")

        var nativeSuccessWhileCallbackPending = V3DeleteCompletionContract()
        precondition(nativeSuccessWhileCallbackPending.resolve(backend: .pending,
            nativeUninstallSucceeded: true, appStillInAuthoritativeLibrary: false,
            deadlineExpired: true, progress: 0.01) == .completed,
            "native uninstall success plus authoritative absence is verified completion while callback settlement remains pending")
        precondition(V3DeleteReconciliationPolicy.mayPublishVerifiedDeleteCompletion(
            backendPending: true, nativeUninstallSucceeded: true,
            appStillInLibrary: false, reconciliationDeadlineElapsed: true) &&
            !V3DeleteReconciliationPolicy.mayPublishVerifiedDeleteCompletion(
                backendPending: true, nativeUninstallSucceeded: true,
                appStillInLibrary: true, reconciliationDeadlineElapsed: true) &&
            !V3DeleteReconciliationPolicy.mayPublishVerifiedDeleteCompletion(
                backendPending: true, nativeUninstallSucceeded: false,
                appStillInLibrary: false, reconciliationDeadlineElapsed: true) &&
            !V3DeleteReconciliationPolicy.mayPublishVerifiedDeleteCompletion(
                backendPending: true, nativeUninstallSucceeded: true,
                appStillInLibrary: false, reconciliationDeadlineElapsed: false),
            "user-visible completion is bounded and requires native success plus authoritative absence")

        let pendingCallbackTerminal = V3OperationTerminalResponse()
        precondition(pendingCallbackTerminal.finishOrResolve(["state": "reconciling",
            "outcomeUnknown": true], backendSettled: false))
        let verifiedDeleteReply: [String: Any] = [
            "operation": "delete", "state": "completed", "outcomeUnknown": false,
            "verifiedDeleteCompletion": true,
            "sourceStep": "native_uninstall+authoritative_library_absence"
        ]
        let verifiedDeleteWireData = try PropertyListSerialization.data(
            fromPropertyList: verifiedDeleteReply, format: .binary, options: 0)
        let verifiedDeleteWire = try PropertyListSerialization.propertyList(
            from: verifiedDeleteWireData, options: [], format: nil) as! [String: Any]
        let verifiedDeleteProof = V3OperationReplyFieldPolicy.strictBoolean(
            verifiedDeleteWire["verifiedDeleteCompletion"]) == true
        precondition(verifiedDeleteProof &&
            pendingCallbackTerminal.finishOrResolve(verifiedDeleteWire, backendSettled: false),
            "native success plus fresh library absence resolves the visible result without claiming the runner callback settled")
        let pendingCallbackSession = UUID().uuidString
        let pendingCallbackFields = pendingCallbackTerminal.reply(
            sessionID: pendingCallbackSession, backendSettled: false)!
        precondition(pendingCallbackFields["state"] as? String == "completed" &&
            pendingCallbackFields["backendSettled"] as? Bool == false &&
            pendingCallbackFields["verifiedDeleteCompletion"] as? Bool == true &&
            V3OperationCompletionPolicy.disposition(state: "completed", backendSettled: false,
                outcomeUnknown: false) == .completedAwaitingBackendSettlement,
            "verified removal reaches Completed while mutation ownership remains until callback settlement")
        var pendingDeleteOwner = V3OperationMutationRegistry()
        let pendingDeleteOwnerID = UUID().uuidString
        let nextDeleteMutationID = UUID().uuidString
        precondition(pendingDeleteOwner.begin(pendingDeleteOwnerID) == .started &&
            pendingDeleteOwner.begin(nextDeleteMutationID) == .busy &&
            pendingDeleteOwner.finish(pendingDeleteOwnerID) &&
            pendingDeleteOwner.begin(nextDeleteMutationID) == .started,
            "the visible verified completion does not release mutation ownership before the pipeline settles")
        var hostDeleteAttempt = V3OperationAttemptState()
        let hostDeleteGeneration = hostDeleteAttempt.begin()
        let hostDeleteSession = hostDeleteAttempt.sessionID!
        precondition(hostDeleteAttempt.accept(state: "reconciling", generation: hostDeleteGeneration,
            sessionID: hostDeleteSession) && !hostDeleteAttempt.isTerminal)
        precondition(hostDeleteAttempt.accept(state: "completed", generation: hostDeleteGeneration,
            sessionID: hostDeleteSession) && hostDeleteAttempt.isTerminal,
            "the host can present the bounded verified completion response while retaining service ownership")
        precondition(!V3OperationProvisionalOutcomePolicy.canResolve(
            currentState: "reconciling", currentBackendSettled: false,
            currentOutcomeUnknown: true, nextState: "completed", nextBackendSettled: false,
            nextOutcomeUnknown: false, nextOperation: "install", verifiedDeleteCompletion: true) &&
            !V3OperationProvisionalOutcomePolicy.canResolve(
                currentState: "reconciling", currentBackendSettled: false,
                currentOutcomeUnknown: true, nextState: "completed", nextBackendSettled: false,
                nextOutcomeUnknown: false, nextOperation: "delete", verifiedDeleteCompletion: false),
            "only a delete response with explicit backend and library evidence can resolve this provisional result")

        let malformedOutcomeUnknown = try PropertyListSerialization.data(
            fromPropertyList: ["outcomeUnknown": NSNumber(value: 1)], format: .binary, options: 0)
        let malformedOutcomeUnknownReply = try PropertyListSerialization.propertyList(
            from: malformedOutcomeUnknown, options: [], format: nil) as! [String: Any]
        let malformedOutcomeIsUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(
            malformedOutcomeUnknownReply["outcomeUnknown"])
        precondition(malformedOutcomeIsUnknown &&
            !V3OperationTerminalAcceptancePolicy.isSettledTerminal(state: "cancelled",
                backendSettled: true, stopConfirmed: true, outcomeUnknown: malformedOutcomeIsUnknown),
            "a numeric plist value cannot masquerade as the Boolean false needed to accept a terminal")
        let malformedDeleteProof = try PropertyListSerialization.data(
            fromPropertyList: ["verifiedDeleteCompletion": NSNumber(value: 1)],
            format: .binary, options: 0)
        let malformedDeleteProofReply = try PropertyListSerialization.propertyList(
            from: malformedDeleteProof, options: [], format: nil) as! [String: Any]
        let malformedDeleteProofAccepted = V3OperationReplyFieldPolicy.strictBoolean(
            malformedDeleteProofReply["verifiedDeleteCompletion"]) == true
        precondition(!malformedDeleteProofAccepted &&
            !V3OperationProvisionalOutcomePolicy.canResolve(
                currentState: "reconciling", currentBackendSettled: false,
                currentOutcomeUnknown: true, nextState: "completed", nextBackendSettled: false,
                nextOutcomeUnknown: false, nextOperation: "delete",
                verifiedDeleteCompletion: malformedDeleteProofAccepted),
            "a numeric plist value cannot authorize provisional delete completion")
        let validFalseOutcomeUnknown = try PropertyListSerialization.data(
            fromPropertyList: ["outcomeUnknown": false], format: .binary, options: 0)
        let validFalseReply = try PropertyListSerialization.propertyList(
            from: validFalseOutcomeUnknown, options: [], format: nil) as! [String: Any]
        precondition(!V3OperationReplyFieldPolicy.outcomeUnknown(validFalseReply["outcomeUnknown"]) &&
            !V3OperationReplyFieldPolicy.outcomeUnknown(nil),
            "valid Boolean false and a legacy absent field remain compatible")

        let provisionalTerminal = V3OperationTerminalResponse()
        precondition(provisionalTerminal.finishOrResolve(["state": "reconciling",
            "outcomeUnknown": true], backendSettled: false))
        let provisionalSessionID = UUID().uuidString
        let pollRequestID = UUID().uuidString
        let provisionalDeleteFailure = CombinedFailure(operation: "delete", stage: .command,
            code: .timedOut, id: provisionalSessionID)
        var provisionalReply = provisionalTerminal.reply(
            sessionID: provisionalSessionID, backendSettled: false)!
        provisionalReply["failure"] = provisionalDeleteFailure.wire
        let provisionalEnvelope: [String: Any] = ["version": 1, "id": pollRequestID,
            "ok": true, "result": provisionalReply]
        let provisionalEncoded = V3ResponseEncoder.encodeDetailed(provisionalEnvelope,
            operation: "opPoll", limit: V3WireContract.responseLimit)
        precondition(provisionalEncoded.fallbackToken == nil)
        precondition(V3ServiceReadinessReply.decode(provisionalEncoded.data,
            requestID: pollRequestID) == .ready,
            "the service envelope carries a plist-safe reconciling result to the host")
        let provisionalDecoded = try PropertyListSerialization.propertyList(
            from: provisionalEncoded.data, format: nil) as! [String: Any]
        let provisionalFields = provisionalDecoded["result"] as! [String: Any]
        precondition(provisionalFields["state"] as? String == "reconciling" &&
            provisionalFields["outcomeUnknown"] as? Bool == true &&
            provisionalFields["backendSettled"] as? Bool == false &&
            CombinedFailure.decode(provisionalFields["failure"] as? [String: Any] ?? [:],
                expectedID: provisionalSessionID)?.stage == .command,
            "the actual binary plist round trip preserves the provisional state and correlated safe failure")
        precondition(!provisionalTerminal.finishOrResolve(["state": "completed"], backendSettled: false),
            "a provisional unknown result cannot resolve before backend settlement")
        precondition(provisionalTerminal.finishOrResolve(["state": "completed"], backendSettled: true),
            "settled callback success atomically resolves only the provisional result")
        precondition(provisionalTerminal.value?["state"] as? String == "completed" &&
            provisionalTerminal.value?["backendSettled"] as? Bool == true)
        let completedEnvelope: [String: Any] = ["version": 1, "id": pollRequestID,
            "ok": true, "result": provisionalTerminal.reply(
                sessionID: provisionalSessionID, backendSettled: true)!]
        let completedEncoded = V3ResponseEncoder.encodeDetailed(completedEnvelope,
            operation: "opPoll", limit: V3WireContract.responseLimit)
        let completedDecoded = try PropertyListSerialization.propertyList(
            from: completedEncoded.data, format: nil) as! [String: Any]
        let completedFields = completedDecoded["result"] as! [String: Any]
        precondition(completedFields["state"] as? String == "completed" &&
            completedFields["backendSettled"] as? Bool == true &&
            V3ServiceReadinessReply.decode(completedEncoded.data, requestID: pollRequestID) == .ready,
            "the settled callback terminal survives service encoding and host envelope validation")

        let provisionalFailure = V3OperationTerminalResponse()
        precondition(provisionalFailure.finishOrResolve(["state": "reconciling",
            "outcomeUnknown": true], backendSettled: false))
        let typedLateFailure: [String: Any] = ["state": "failed",
            "failure": ["stage": "installation", "safeCause": "installFailed"]]
        precondition(provisionalFailure.finishOrResolve(typedLateFailure, backendSettled: true) &&
            (provisionalFailure.value?["failure"] as? [String: String])?["stage"] == "installation",
            "late failure keeps the callback's typed failure envelope")
        precondition(!provisionalFailure.finishOrResolve(["state": "completed"], backendSettled: true),
            "a resolved terminal result remains write-once")

        let settledCancellation = V3OperationTerminalResponse()
        precondition(settledCancellation.finishOrResolve(["state": "reconciling",
            "outcomeUnknown": true], backendSettled: false))
        precondition(settledCancellation.finishOrResolve(["state": "cancelled",
            "stopConfirmed": true], backendSettled: true) &&
            settledCancellation.value?["state"] as? String == "cancelled" &&
            settledCancellation.value?["backendSettled"] as? Bool == true,
            "a confirmed late cancellation resolves provisional state to a settled terminal")
        precondition(V3StagedIPALeasePolicy.isLeased(hasOperationTask: true,
            preparationFinished: true, ownsMutationRegistry: false) &&
            V3StagedIPALeasePolicy.isLeased(hasOperationTask: false,
                preparationFinished: true, ownsMutationRegistry: true) &&
            !V3StagedIPALeasePolicy.isLeased(hasOperationTask: false,
                preparationFinished: true, ownsMutationRegistry: false),
            "IPA cleanup waits for task, preparation, and mutation ownership to end")

        var lateDeleteAttempt = V3OperationAttemptState()
        let lateDeleteGeneration = lateDeleteAttempt.begin()
        let lateDeleteID = lateDeleteGeneration.uuidString
        precondition(lateDeleteAttempt.bind(sessionID: lateDeleteID, generation: lateDeleteGeneration))
        precondition(lateDeleteAttempt.accept(state: "reconciling", generation: lateDeleteGeneration,
            sessionID: lateDeleteID) && !lateDeleteAttempt.isTerminal,
            "the host keeps polling a provisional result without marking the attempt terminal")
        precondition(lateDeleteAttempt.accept(state: "completed", generation: lateDeleteGeneration,
            sessionID: lateDeleteID) && lateDeleteAttempt.isTerminal,
            "the same session may commit its authoritative late completion")

        let cancellationSession = UUID().uuidString
        precondition(V3OperationCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "failed", backendSettled: false, stopConfirmed: false,
            outcomeUnknown: true) == nil,
            "an unknown cancellation result must preserve the install attempt and staged IPA")
        precondition(V3OperationCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "cancelled", backendSettled: true, stopConfirmed: true,
            outcomeUnknown: false) == "cancelled",
            "confirmed cancellation releases the install attempt")
        precondition(V3OperationCancellationOutcomePolicy.terminalState(
            expectedSessionID: cancellationSession, replySessionID: cancellationSession,
            state: "completed", backendSettled: true, stopConfirmed: false,
            outcomeUnknown: false) == "completed",
            "completion that wins the cancel race is retained as completion")
        precondition(V3OperationCancellationOutcomePolicy.terminalState(
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
        let deleteOwnerID = UUID().uuidString
        var deleteOwner = V3OperationMutationRegistry()
        precondition(deleteOwner.begin(deleteOwnerID) == .started)
        let replacementID = UUID().uuidString
        precondition(V3DeleteCancellationPolicy.callbackCancellationRemainsPending(
            isCancellation: true, cancellationRequested: true) &&
            deleteOwner.begin(replacementID) == .busy,
            "a cancelled PipelineRunner callback cannot release deletion ownership before authoritative reconciliation")
        precondition(deleteOwner.finish(deleteOwnerID) && deleteOwner.begin(replacementID) == .started,
            "a replacement mutation starts only after the confirmed delete owner finishes")
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
        let nativeMarkerID = UUID().uuidString
        let nativeMarker = V3DeleteNativeSuccessRegistry()
        nativeMarker.record(sessionID: nativeMarkerID, now: deletePollNow)
        precondition(nativeMarker.contains(sessionID: nativeMarkerID, now: deletePollNow) &&
            !nativeMarker.contains(sessionID: nativeMarkerID,
                now: deletePollNow.addingTimeInterval(V3DeleteNativeSuccessRegistry.retentionInterval + 1)),
            "late native delete markers expire within a bounded interval")
        precondition(V3DeleteReconciliationPolicy.shouldCheckLibrary(lastCheck: nil, now: deletePollNow))
        precondition(!V3DeleteReconciliationPolicy.shouldCheckLibrary(
            lastCheck: deletePollNow, now: deletePollNow.addingTimeInterval(1)),
            "after a verified absence, callback waiting must not refetch Core Data four times per second")
        precondition(V3DeleteReconciliationPolicy.shouldThrottleLibraryChecks(
            authoritativeAbsenceConfirmed: false, cancellationRequested: true) &&
                     !V3DeleteReconciliationPolicy.shouldThrottleLibraryChecks(
                        authoritativeAbsenceConfirmed: false, cancellationRequested: false),
            "an unresolved cancellation wait rate-limits library rechecks even when deletion is not confirmed")
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
                        nativeUninstallSucceeded: true, appStillInLibrary: true) == 2.0,
            "a settled callback with an app still present keeps bounded reconciliation backoff")
        let settledPresentDelay1 = V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: 0.25, backendPending: false, nativeUninstallSucceeded: true,
            appStillInLibrary: true)
        let settledPresentDelay2 = V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: settledPresentDelay1, backendPending: false,
            nativeUninstallSucceeded: true, appStillInLibrary: true)
        precondition(settledPresentDelay1 == 1.0 && settledPresentDelay2 == 2.0 &&
            V3DeleteReconciliationPolicy.nextCallbackPollDelay(current: 20,
                backendPending: false, nativeUninstallSucceeded: false,
                appStillInLibrary: true) == 15,
            "a settled callback with a still-present app backs off authoritative reads instead of checking every 250 ms")
        precondition(V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: 0.25, backendPending: true, nativeUninstallSucceeded: false,
            appStillInLibrary: true) == 1.0,
            "an unresolved delete polls the callback and library at one second instead of four times per second")
        precondition(V3DeleteReconciliationPolicy.nextCallbackPollDelay(
            current: 0.25, backendPending: true, nativeUninstallSucceeded: false,
            appStillInLibrary: true, cancellationRequested: true) == 0.5,
            "an unresolved cancelled delete also backs off instead of polling four times per second")

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

        let lateDeleteSession = UUID().uuidString
        let lateDeleteTerminal = V3OperationTerminalResponse()
        precondition(lateDeleteTerminal.setIfEmpty(["state": "completed"]))
        precondition(lateDeleteTerminal.reply(sessionID: lateDeleteSession, backendSettled: false)?["state"] as? String == "completed")
        precondition(lateDeleteTerminal.reply(sessionID: lateDeleteSession, backendSettled: true)?["backendSettled"] as? Bool == true,
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
        precondition(V3OperationCompletionPolicy.disposition(state: "reconciling",
            backendSettled: false, outcomeUnknown: true) == .outcomeUnknownAwaitingBackendSettlement &&
            V3OperationCompletionPolicy.shouldContinuePolling(state: "reconciling",
                backendSettled: false, outcomeUnknown: true) &&
            V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(state: "reconciling",
                backendSettled: false, outcomeUnknown: true) &&
            V3OperationCompletionPolicy.requiresDeviceCheck(state: "reconciling",
                backendSettled: false, deviceCheckConfirmed: false, outcomeUnknown: true),
            "an unknown delete result remains visible and monitored until settlement or explicit reconciliation")
        precondition(V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
            state: "completed", backendSettled: false, outcomeUnknown: false) &&
            !V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
                state: "completed", backendSettled: true, outcomeUnknown: false) &&
            V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
                state: "cancelling", backendSettled: false, outcomeUnknown: false,
                cancellationRequested: true) &&
            !V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
                state: "cancelling", backendSettled: false, outcomeUnknown: false,
                cancellationRequested: false),
            "transient poll timeouts preserve unsettled/cancel-requested monitoring, while settled results are absorbing")
        precondition(V3OperationCompletionPolicy.nextSettlementPollRetryDelay(current: 1) == 5 &&
            V3OperationCompletionPolicy.nextSettlementPollRetryDelay(current: 5) == 10 &&
            V3OperationCompletionPolicy.nextSettlementPollRetryDelay(current: 20) == 30 &&
            V3OperationCompletionPolicy.nextSettlementPollRetryDelay(current: 30) == 30,
            "transient settlement poll failures use a bounded capped backoff")
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
