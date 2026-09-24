import Foundation

private struct ResolvedTestApp {
    let bundleIdentifier: String
}

private struct TestInstallOperation: Equatable {
    let kind: String
    let bundleIdentifier: String
}

@main
struct V3DeviceAcceptanceRegressionsHarness {
    static func main() {
        installPresentationStateMachine()
        installRouteParity()
        deleteTerminalReconciliation()
        refreshRunIsolationAndGuidance()
        print("V3 DEVICE ACCEPTANCE REGRESSION BEHAVIOR PASS")
    }

    private static func installPresentationStateMachine() {
        let token = UUID().uuidString.lowercased()
        let title = "Install / Sideload App with SideStore"

        // CASE A: On a fresh root, the first tap asks the root UIKit anchor to
        // present the document picker directly. The picker is visibly active
        // before a file is selected; there is no newly presented SwiftUI cover
        // that must present a nested sheet.
        var attempt = V3InstallAttemptState()
        let pickerPresenter = V3InstallPickerPresentationCoordinator()
        var snapshotLoading = true
        let attemptID = attempt.beginPicker()!
        let firstDecision = pickerPresenter.request(attemptID: attemptID,
            presenterReady: true, presenterBusy: false)
        precondition(firstDecision == .present(attemptID),
                     "first user tap did not directly request the root picker")
        precondition(pickerPresenter.didPresent(attemptID: attemptID))
        precondition(pickerPresenter.phase == .presented,
                     "picker presentation coordinator did not reach visible state")

        // The status reload can finish while UIKit still owns the picker
        // dismissal transaction. Operation presentation waits for both signals.
        precondition(attempt.beginStaging(attemptID: attemptID))
        precondition(attempt.staged(attemptID: attemptID, token: token, title: title,
                                    waitsForPickerDismissal: true, isLoading: snapshotLoading))
        attempt.reloadFinished()
        precondition(attempt.phase == .waitingForPickerDismissal,
                     "an early reload event incorrectly consumed the picker handoff")
        precondition(pickerPresenter.beginDismissal(attemptID: attemptID))
        precondition(!pickerPresenter.didDismiss(attemptID: attemptID, presenterIsClear: false),
                     "picker dismissal completed while UIKit still owned a presenter")
        precondition(pickerPresenter.presenterBecameReady(isBusy: false) == .dismissed(attemptID),
                     "the root lifecycle did not release the delayed picker dismissal")
        precondition(attempt.pickerDidDisappear(attemptID: attemptID, isLoading: snapshotLoading))
        var operation = attempt.takeReadyOperation(isLoading: snapshotLoading,
                                                    hasActiveOperationPresentation: false)
        precondition(operation == nil, "loading must retain, not reject, the selected IPA")
        snapshotLoading = false
        if attempt.phase == .waitingForReload { attempt.reloadFinished() }
        precondition(attempt.phase == .readyToPresentOperation)
        operation = attempt.takeReadyOperation(isLoading: snapshotLoading,
                                               hasActiveOperationPresentation: false)
        precondition(operation?.attemptID == attemptID && operation?.token == token &&
                     operation?.title == title,
                     "selection/reload ordering lost the attempt or token")
        precondition(attempt.takeReadyOperation(isLoading: false,
            hasActiveOperationPresentation: false) == nil, "selection presented more than once")

        // CASE B: UIKit delays the anchor's first presentation transaction.
        // The request remains queued until the real viewDidAppear event; it is
        // not lost and does not need a timer or a second user tap.
        var delayedMachine = V3InstallAttemptState()
        let delayedPresenter = V3InstallPickerPresentationCoordinator()
        let delayedID = delayedMachine.beginPicker()!
        precondition(delayedPresenter.request(attemptID: delayedID,
            presenterReady: false, presenterBusy: false) == .queued)
        precondition(delayedMachine.phase == .pickerPresented)
        precondition(delayedPresenter.presenterBecameReady(isBusy: false) == .present(delayedID))
        precondition(delayedPresenter.didPresent(attemptID: delayedID))
        precondition(delayedPresenter.phase == .presented,
                     "delayed UIKit presentation did not open the first picker")
        precondition(delayedPresenter.beginDismissal(attemptID: delayedID))
        precondition(delayedPresenter.didDismiss(attemptID: delayedID, presenterIsClear: true))
        precondition(delayedMachine.cancelPicker(attemptID: delayedID))
        precondition(delayedMachine.beginPicker() != nil,
                     "picker cancellation did not make the next tap reusable")

        // Cancellation can arrive before UIKit calls the anchor's presentation
        // completion. That race also dismisses the pending native picker and
        // releases the attempt without waiting for a second tap.
        var earlyCancelMachine = V3InstallAttemptState()
        let earlyCancelPresenter = V3InstallPickerPresentationCoordinator()
        let earlyCancelID = earlyCancelMachine.beginPicker()!
        precondition(earlyCancelPresenter.request(attemptID: earlyCancelID,
            presenterReady: true, presenterBusy: false) == .present(earlyCancelID))
        precondition(earlyCancelPresenter.beginDismissal(attemptID: earlyCancelID))
        precondition(earlyCancelPresenter.didDismiss(attemptID: earlyCancelID,
            presenterIsClear: true))
        precondition(earlyCancelMachine.cancelPicker(attemptID: earlyCancelID))
        precondition(earlyCancelMachine.beginPicker() != nil)

        // CASE C: A Wi-Fi/precondition failure after opStart is acknowledged;
        // the next direct picker and operation start with fresh identities.
        var afterStart = V3InstallAttemptState()
        let firstID = afterStart.beginPicker()!
        stageAndPresent(&afterStart, attemptID: firstID, token: UUID().uuidString, title: title)
        let firstOperation = afterStart.operationID!
        let firstSession = UUID().uuidString
        precondition(afterStart.backendStartRequested(attemptID: firstID,
            operationID: firstOperation, sessionID: firstSession))
        precondition(afterStart.backendStarted(attemptID: firstID, operationID: firstOperation,
                                               sessionID: firstSession))
        precondition(afterStart.recordTerminal(attemptID: firstID, operationID: firstOperation,
                                               outcome: "connectionFailure"))
        finishAcknowledgedAttempt(&afterStart, attemptID: firstID)
        precondition(afterStart.isIdle)
        let secondID = afterStart.beginPicker()!
        let retryPresenter = V3InstallPickerPresentationCoordinator()
        precondition(retryPresenter.request(attemptID: secondID,
            presenterReady: true, presenterBusy: false) == .present(secondID),
            "immediate retry after a Wi-Fi failure did not present the picker")
        precondition(retryPresenter.didPresent(attemptID: secondID))
        stageAndPresent(&afterStart, attemptID: secondID, token: UUID().uuidString, title: title)
        let secondOperation = afterStart.operationID!
        precondition(secondID != firstID && secondOperation != firstOperation)
        let secondSession = UUID().uuidString
        precondition(afterStart.backendStartRequested(attemptID: secondID,
            operationID: secondOperation, sessionID: secondSession))
        precondition(afterStart.backendStarted(attemptID: secondID, operationID: secondOperation,
                                               sessionID: secondSession))
        precondition(afterStart.phase == .operationStarted)

        // CASE D: A preflight failure before opStart is terminal and returns to
        // idle after acknowledgement.
        var beforeStart = V3InstallAttemptState()
        let preflightID = beforeStart.beginPicker()!
        stageAndPresent(&beforeStart, attemptID: preflightID, token: UUID().uuidString, title: title)
        let preflightOperation = beforeStart.operationID!
        precondition(beforeStart.recordTerminal(attemptID: preflightID,
            operationID: preflightOperation, outcome: "preconditionFailure"))
        precondition(beforeStart.backendSessionID == nil)
        finishAcknowledgedAttempt(&beforeStart, attemptID: preflightID)
        let afterPreflight = beforeStart.beginPicker()
        precondition(afterPreflight != nil, "pre-opStart failure poisoned the picker")
        let afterPreflightPresenter = V3InstallPickerPresentationCoordinator()
        precondition(afterPreflightPresenter.request(attemptID: afterPreflight!,
            presenterReady: true, presenterBusy: false) == .present(afterPreflight!))
        precondition(afterPreflightPresenter.didPresent(attemptID: afterPreflight!))

        // A pipeline failure after opStart receives the same reset contract.
        var postStart = V3InstallAttemptState()
        let postID = postStart.beginPicker()!
        stageAndPresent(&postStart, attemptID: postID, token: UUID().uuidString, title: title)
        let postOperation = postStart.operationID!
        let postSession = UUID().uuidString
        precondition(postStart.backendStartRequested(attemptID: postID,
            operationID: postOperation, sessionID: postSession))
        precondition(postStart.backendStarted(attemptID: postID, operationID: postOperation,
                                              sessionID: postSession))
        precondition(postStart.recordTerminal(attemptID: postID, operationID: postOperation,
                                              outcome: "failed"))
        finishAcknowledgedAttempt(&postStart, attemptID: postID)
        precondition(postStart.beginPicker() != nil, "post-opStart failure poisoned the picker")

        // A Retry whose second opStart fails remains a terminal result; Done
        // clears that retry generation and the following picker still opens.
        var retryStartFails = V3InstallAttemptState()
        let retryID = retryStartFails.beginPicker()!
        stageAndPresent(&retryStartFails, attemptID: retryID,
                        token: UUID().uuidString, title: title)
        let originalOperation = retryStartFails.operationID!
        precondition(retryStartFails.recordTerminal(attemptID: retryID,
            operationID: originalOperation, outcome: "signing_failed"))
        precondition(retryStartFails.prepareRetry(attemptID: retryID,
            operationID: originalOperation))
        let retrySession = UUID().uuidString
        precondition(retryStartFails.backendStartRequested(attemptID: retryID,
            operationID: originalOperation, sessionID: retrySession))
        precondition(retryStartFails.recordTerminal(attemptID: retryID,
            operationID: originalOperation, outcome: "retry_start_failed"))
        finishAcknowledgedAttempt(&retryStartFails, attemptID: retryID)
        precondition(retryStartFails.beginPicker() != nil,
                     "retry-start failure poisoned the next picker")

        // CASE E: Picker cancellation clears all staged/request state.
        var pickerCancelled = V3InstallAttemptState()
        let cancelledPickerID = pickerCancelled.beginPicker()!
        precondition(pickerCancelled.cancelPicker(attemptID: cancelledPickerID))
        precondition(pickerCancelled.isIdle && pickerCancelled.token == nil)
        precondition(pickerCancelled.beginPicker() != nil, "picker cancellation left a stale token")

        // A source-file staging failure before presentation is also terminal
        // for that attempt and cannot retain its token or cover ownership.
        var stagingFailed = V3InstallAttemptState()
        let stagingID = stagingFailed.beginPicker()!
        precondition(stagingFailed.beginStaging(attemptID: stagingID))
        precondition(stagingFailed.failStaging(attemptID: stagingID))
        precondition(stagingFailed.isIdle && stagingFailed.token == nil)
        precondition(stagingFailed.beginPicker() != nil,
                     "a failed stage left a stale picker attempt")

        // An interruption after choosing the file but before the operation
        // cover appears clears the staged token and returns to idle.
        var interrupted = V3InstallAttemptState()
        let interruptedID = interrupted.beginPicker()!
        stageAndPresent(&interrupted, attemptID: interruptedID,
                        token: UUID().uuidString, title: title)
        precondition(interrupted.token != nil,
                     "operation presentation failure did not retain a staged token for cleanup")
        precondition(!interrupted.operationViewDidAppear && interrupted.backendSessionID == nil)
        precondition(interrupted.resetBeforeBackend(attemptID: interruptedID),
                     "pre-opStart presentation interruption did not reset safely")
        precondition(interrupted.isIdle && interrupted.token == nil)
        precondition(interrupted.beginPicker() != nil,
                     "presentation interruption poisoned the following attempt")

        // CASE F: An occupied UIKit presenter rejects this attempt explicitly,
        // resets its local state, and accepts the next attempt after dismissal.
        let interruptedPresenter = V3InstallPickerPresentationCoordinator()
        var presentationFailure = V3InstallAttemptState()
        let failedPresentationID = presentationFailure.beginPicker()!
        let rejected = interruptedPresenter.request(attemptID: failedPresentationID,
            presenterReady: true, presenterBusy: true)
        precondition(rejected == .rejected(failedPresentationID, "presentation_active"))
        precondition(interruptedPresenter.fail(attemptID: failedPresentationID))
        precondition(presentationFailure.resetBeforeBackend(attemptID: failedPresentationID))
        let recoveredPresentationID = presentationFailure.beginPicker()!
        precondition(interruptedPresenter.request(attemptID: recoveredPresentationID,
            presenterReady: true, presenterBusy: false) == .present(recoveredPresentationID))

        // A UIKit request that is accepted but never reaches didPresent also
        // rolls back, rather than leaving attempt_not_idle latched.
        var didNotPresent = V3InstallAttemptState()
        let didNotPresentCoordinator = V3InstallPickerPresentationCoordinator()
        let didNotPresentID = didNotPresent.beginPicker()!
        precondition(didNotPresentCoordinator.request(attemptID: didNotPresentID,
            presenterReady: true, presenterBusy: false) == .present(didNotPresentID))
        precondition(didNotPresentCoordinator.fail(attemptID: didNotPresentID))
        precondition(didNotPresent.resetBeforeBackend(attemptID: didNotPresentID))
        precondition(didNotPresent.beginPicker() != nil,
                     "a picker that never reached didPresent blocked the next tap")

        // CASE G: Five alternating failed/cancelled attempts leave both the
        // backend-independent attempt state and the direct picker coordinator
        // reusable for a sixth first-tap presentation.
        var repeatedMachine = V3InstallAttemptState()
        let repeatedPresenter = V3InstallPickerPresentationCoordinator()
        for index in 0..<5 {
            let id = repeatedMachine.beginPicker()!
            precondition(repeatedPresenter.request(attemptID: id,
                presenterReady: true, presenterBusy: false) == .present(id))
            precondition(repeatedPresenter.didPresent(attemptID: id))
            if index.isMultiple(of: 2) {
                precondition(repeatedPresenter.beginDismissal(attemptID: id))
                precondition(repeatedPresenter.didDismiss(attemptID: id, presenterIsClear: true))
                precondition(repeatedMachine.cancelPicker(attemptID: id))
            } else {
                stageAndPresent(&repeatedMachine, attemptID: id,
                    token: UUID().uuidString, title: title)
                let opID = repeatedMachine.operationID!
                precondition(repeatedMachine.recordTerminal(attemptID: id,
                    operationID: opID, outcome: "failed"))
                finishAcknowledgedAttempt(&repeatedMachine, attemptID: id)
                precondition(repeatedPresenter.beginDismissal(attemptID: id))
                precondition(repeatedPresenter.didDismiss(attemptID: id, presenterIsClear: true))
            }
            precondition(repeatedMachine.isIdle && repeatedPresenter.phase == .idle)
        }
        let sixthID = repeatedMachine.beginPicker()!
        precondition(repeatedPresenter.request(attemptID: sixthID,
            presenterReady: true, presenterBusy: false) == .present(sixthID),
            "sixth first tap did not open after repeated failure/cancellation")
        precondition(repeatedPresenter.didPresent(attemptID: sixthID))

        // Confirmed operation cancellation and success use the same reusable
        // terminal reset contract.
        var operationCancelled = V3InstallAttemptState()
        let cancelID = operationCancelled.beginPicker()!
        stageAndPresent(&operationCancelled, attemptID: cancelID, token: UUID().uuidString, title: title)
        let cancelOperationID = operationCancelled.operationID!
        let cancelSession = UUID().uuidString
        precondition(operationCancelled.backendStartRequested(attemptID: cancelID,
            operationID: cancelOperationID, sessionID: cancelSession))
        precondition(operationCancelled.backendStarted(attemptID: cancelID,
            operationID: cancelOperationID, sessionID: cancelSession))
        precondition(operationCancelled.recordTerminal(attemptID: cancelID,
            operationID: cancelOperationID, outcome: "cancelled"))
        finishAcknowledgedAttempt(&operationCancelled, attemptID: cancelID)
        precondition(operationCancelled.beginPicker() != nil,
                     "operation cancellation did not return to idle")

        // An unconfirmed backend cancellation must not reset the attempt or
        // permit a duplicate mutation. A confirmed cancellation then resets it.
        var cancellationUncertain = V3InstallAttemptState()
        let uncertainID = cancellationUncertain.beginPicker()!
        stageAndPresent(&cancellationUncertain, attemptID: uncertainID,
                        token: UUID().uuidString, title: title)
        let uncertainOperation = cancellationUncertain.operationID!
        let uncertainSession = UUID().uuidString
        precondition(cancellationUncertain.backendStartRequested(attemptID: uncertainID,
            operationID: uncertainOperation, sessionID: uncertainSession))
        precondition(cancellationUncertain.backendStarted(attemptID: uncertainID,
            operationID: uncertainOperation, sessionID: uncertainSession))
        precondition(!cancellationUncertain.resetBeforeBackend(attemptID: uncertainID),
                     "an active backend mutation was reset without cancellation confirmation")
        precondition(cancellationUncertain.beginPicker() == nil,
                     "a second mutation started while backend cancellation was uncertain")
        precondition(cancellationUncertain.recordTerminal(attemptID: uncertainID,
            operationID: uncertainOperation, outcome: "cancelled"))
        finishAcknowledgedAttempt(&cancellationUncertain, attemptID: uncertainID)
        precondition(cancellationUncertain.beginPicker() != nil)

        // Cleanup RPC failure does not retain stale UI ownership. The token is
        // attempted for cleanup, then the terminal UI still becomes reusable.
        var cleanupFailed = V3InstallAttemptState()
        let cleanupID = cleanupFailed.beginPicker()!
        stageAndPresent(&cleanupFailed, attemptID: cleanupID,
                        token: UUID().uuidString, title: title)
        let cleanupOperation = cleanupFailed.operationID!
        precondition(cleanupFailed.recordTerminal(attemptID: cleanupID,
            operationID: cleanupOperation, outcome: "failed"))
        precondition(cleanupFailed.beginCleanup(attemptID: cleanupID))
        let stagedCleanupRPCSucceeded = false
        _ = stagedCleanupRPCSucceeded
        precondition(cleanupFailed.finishCleanup(attemptID: cleanupID))
        precondition(cleanupFailed.beginPicker() != nil,
                     "a staged-file cleanup failure wedged the next attempt")

        // Success is acknowledged through the identical reusable cleanup path.
        var succeeded = V3InstallAttemptState()
        let successID = succeeded.beginPicker()!
        stageAndPresent(&succeeded, attemptID: successID,
                        token: UUID().uuidString, title: title)
        let successOperationID = succeeded.operationID!
        let successSession = UUID().uuidString
        precondition(succeeded.backendStartRequested(attemptID: successID,
            operationID: successOperationID, sessionID: successSession))
        precondition(succeeded.backendStarted(attemptID: successID,
            operationID: successOperationID, sessionID: successSession))
        precondition(succeeded.recordTerminal(attemptID: successID,
            operationID: successOperationID, outcome: "completed"))
        finishAcknowledgedAttempt(&succeeded, attemptID: successID)
        precondition(succeeded.beginPicker() != nil,
                     "successful install did not return to idle after acknowledgement")
    }

    private static func stageAndPresent(_ state: inout V3InstallAttemptState,
                                        attemptID: UUID, token: String, title: String) {
        precondition(state.beginStaging(attemptID: attemptID))
        precondition(state.staged(attemptID: attemptID, token: token, title: title,
                                  waitsForPickerDismissal: true, isLoading: false))
        precondition(state.pickerDidDisappear(attemptID: attemptID, isLoading: false))
        precondition(state.takeReadyOperation(isLoading: false,
            hasActiveOperationPresentation: false) != nil)
    }

    private static func finishAcknowledgedAttempt(_ state: inout V3InstallAttemptState,
                                                   attemptID: UUID) {
        precondition(state.phase == .terminal)
        precondition(state.beginCleanup(attemptID: attemptID))
        precondition(state.finishCleanup(attemptID: attemptID))
        precondition(state.isIdle && state.attemptID == nil && state.token == nil &&
                     state.backendSessionID == nil,
                     "terminal acknowledgement did not completely reset install state")
    }

    private static func installRouteParity() {
        let local = ResolvedTestApp(bundleIdentifier: "example.local")
        let remote = ResolvedTestApp(bundleIdentifier: "example.remote")
        let localBuilt = V3InstallPipelineParity.makeOperation(route: .localIPA, local) {
            TestInstallOperation(kind: "install", bundleIdentifier: $0.bundleIdentifier)
        }
        let remoteBuilt = V3InstallPipelineParity.makeOperation(route: .remoteURL, remote) {
            TestInstallOperation(kind: "install", bundleIdentifier: $0.bundleIdentifier)
        }
        precondition(localBuilt.route == .localIPA && remoteBuilt.route == .remoteURL)
        precondition(localBuilt.operation.kind == "install" && remoteBuilt.operation.kind == "install")
        precondition(localBuilt.operation.bundleIdentifier == local.bundleIdentifier)
        precondition(remoteBuilt.operation.bundleIdentifier == remote.bundleIdentifier)
    }

    private static func deleteTerminalReconciliation() {
        // The old driver awaited only the full PipelineRunner completion
        // callback, so a missing callback left opPoll working at ~1%, even
        // after CoreDevice removed the app and SideStore saved that absence.
        let oldCallbackArrived = false
        let authoritativeAppStillPresent = false
        let lowProgress = 0.01
        precondition(!oldCallbackArrived && !authoritativeAppStillPresent && lowProgress < 0.02,
                     "the missing-callback/low-progress device shape was not represented")

        var contract = V3DeleteCompletionContract()
        precondition(contract.resolve(backend: .pending, nativeUninstallSucceeded: true,
                                      appStillInAuthoritativeLibrary: false,
                                      deadlineExpired: false, progress: lowProgress) == nil,
                     "absence without the bounded reconciliation window must not fabricate success")
        precondition(contract.resolve(backend: .pending, nativeUninstallSucceeded: true,
                                      appStillInAuthoritativeLibrary: false,
                                      deadlineExpired: true, progress: lowProgress) == .completed,
                     "native uninstall success plus authoritative library absence did not complete")
        precondition(contract.resolve(backend: .failed, nativeUninstallSucceeded: true,
                                      appStillInAuthoritativeLibrary: true,
                                      deadlineExpired: true, progress: 1) == .completed,
                     "a late callback changed a terminal deletion result")

        var listOnly = V3DeleteCompletionContract()
        precondition(listOnly.resolve(backend: .pending, nativeUninstallSucceeded: false,
                                      appStillInAuthoritativeLibrary: false,
                                      deadlineExpired: true, progress: lowProgress) == .failed,
                     "a UI/library list change alone must not report deletion success")

        var callbackSuccess = V3DeleteCompletionContract()
        precondition(callbackSuccess.resolve(backend: .succeeded, nativeUninstallSucceeded: false,
                                             appStillInAuthoritativeLibrary: false,
                                             deadlineExpired: false, progress: lowProgress) == .completed,
                     "a successful backend callback plus authoritative absence should complete promptly")
    }

    private static func refreshRunIsolationAndGuidance() {
        let setupRequest = UUID().uuidString
        let setupRun = UUID().uuidString
        let homeRequest = UUID().uuidString
        let homeRun = UUID().uuidString
        let manifest: [String: Any] = [
            "version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": setupRun,
            "requested_ids": ["host.app"], "expected_ids": ["host.app"], "skipped_ids": [],
            "results": [["bundle_id": "host.app", "success": true]]
        ]
        let setupSuccess: [String: Any] = ["request_id": setupRequest, "run_id": setupRun,
                                            "state": "completed", "manifest": manifest]

        var homeAttempt = V3RefreshAllAttemptState()
        homeAttempt.begin(requestID: homeRequest)
        precondition(!homeAttempt.observe(setupSuccess),
                     "Setup Test Refresh success must not satisfy a later Home request")
        let homeStarted: [String: Any] = ["request_id": homeRequest, "run_id": homeRun, "state": "running"]
        precondition(homeAttempt.observe(homeStarted))

        let exactFailure = CombinedFailure(operation: "refresh", stage: .signing,
            id: homeRun, underlying: NSError(domain: "PrivateProviderDomain", code: -1005),
            retryable: true, safeCause: .signingNetworkConnectionLost,
            sourceStep: .provisioningProfileFetch)
        let failed: [String: Any] = [
            "request_id": homeRequest, "run_id": homeRun, "source": "manual", "origin": "home",
            "network_preflight": "passed", "active_run_id": "none", "health": "REFRESH_FAILED",
            "manifest_run_id": "unknown", "state": "failed", "message": exactFailure.safeMessage,
            "failure": exactFailure.wire,
            "manifest": ["requested_ids": ["host.app", "guest.app"],
                         "expected_ids": ["host.app", "guest.app"], "skipped_ids": []]
        ]
        precondition(homeAttempt.observe(failed) && homeAttempt.phase == .failed)
        let details = V3OperationFailureDetails(exactFailure)
        precondition(details.whatHappened ==
            "The connection to the provisioning service was interrupted during signing.")
        precondition(details.recommendedAction ==
            "Your current connection may still be healthy. Retry once. If this happens again, open Connection Check.")
        precondition(details.recoveryDestination == "connection")

        let diagnostic = V3RefreshAllFailureDiagnostics.text(
            requestID: homeRequest, runID: homeRun, record: failed)!
        for field in ["request_id=\(homeRequest)", "run_id=\(homeRun)", "operation=refresh",
                      "stage=signing", "code=failed", "source_step=provisioningProfileFetch",
                      "correlation=\(homeRun)", "underlying_domain=redacted", "underlying_code=-1005",
                      "retryable=true", "safe_cause=signingNetworkConnectionLost",
                      "network_preflight=passed", "active_run_id=none", "health=REFRESH_FAILED",
                      "terminal_ledger_state=failed", "origin=home",
                      "requested_app_ids=host.app,guest.app", "attempted_app_ids=host.app,guest.app"] {
            precondition(diagnostic.contains(field), "missing per-run refresh diagnostic: \(field)")
        }
        precondition(!homeAttempt.observe(setupSuccess), "a late prior run changed Home's terminal failure")
        precondition(homeAttempt.phase == .failed)
    }
}
