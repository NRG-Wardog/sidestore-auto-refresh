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

        // CASE A: A foreground snapshot, child document-picker dismissal, and
        // its onDismiss callback may interleave in either order. The install
        // remains owned by the same full-screen host cover until one request is
        // materialized, and it is presented exactly once.
        var attempt = V3InstallAttemptState()
        var snapshotLoading = true
        let hostCoverID = attempt.beginPicker()!
        precondition(attempt.phase == .pickerPresented)
        precondition(attempt.beginStaging(attemptID: hostCoverID))
        precondition(attempt.staged(attemptID: hostCoverID, token: token, title: title,
                                    waitsForPickerDismissal: true, isLoading: snapshotLoading))
        // Reload completes just before UIKit reports sheet dismissal.
        attempt.reloadFinished()
        precondition(attempt.phase == .waitingForPickerDismissal)
        precondition(attempt.pickerDidDisappear(attemptID: hostCoverID, isLoading: snapshotLoading))
        var operation = attempt.takeReadyOperation(isLoading: snapshotLoading,
                                                    hasActiveOperationPresentation: false)
        precondition(operation == nil, "loading must retain, not reject, the selected IPA")
        snapshotLoading = false
        if attempt.phase == .waitingForReload { attempt.reloadFinished() }
        precondition(attempt.phase == .readyToPresentOperation)
        operation = attempt.takeReadyOperation(isLoading: snapshotLoading,
                                               hasActiveOperationPresentation: false)
        precondition(operation?.attemptID == hostCoverID && operation?.token == token &&
                     operation?.title == title,
                     "selection/reload ordering lost the attempt or token")
        precondition(attempt.takeReadyOperation(isLoading: false,
            hasActiveOperationPresentation: false) == nil, "selection presented more than once")
        precondition(attempt.attemptID == hostCoverID,
                     "operation handoff replaced the root host cover identity")

        // Also reproduce the opposite race: picker dismissal happens while the
        // status reload is still active, and reload completion is delayed.
        var reverse = V3InstallAttemptState()
        let reverseID = reverse.beginPicker()!
        precondition(reverse.beginStaging(attemptID: reverseID))
        precondition(reverse.staged(attemptID: reverseID, token: UUID().uuidString,
            title: title, waitsForPickerDismissal: true, isLoading: true))
        precondition(reverse.pickerDidDisappear(attemptID: reverseID, isLoading: true))
        precondition(reverse.phase == .waitingForReload)
        precondition(reverse.takeReadyOperation(isLoading: true,
            hasActiveOperationPresentation: false) == nil)
        reverse.reloadFinished()
        precondition(reverse.takeReadyOperation(isLoading: false,
            hasActiveOperationPresentation: false) != nil,
            "late snapshot completion did not resume the queued first attempt")

        // CASE B: Failure after opStart -> acknowledge -> cleanup -> immediate
        // second picker/operation works without an app restart.
        var afterStart = V3InstallAttemptState()
        let firstID = afterStart.beginPicker()!
        stageAndPresent(&afterStart, attemptID: firstID, token: UUID().uuidString, title: title)
        let firstOperation = afterStart.operationID!
        precondition(afterStart.backendStarted(attemptID: firstID, operationID: firstOperation,
                                               sessionID: UUID().uuidString))
        precondition(afterStart.recordTerminal(attemptID: firstID, operationID: firstOperation,
                                               outcome: "connectionFailure"))
        finishAcknowledgedAttempt(&afterStart, attemptID: firstID)
        precondition(afterStart.isIdle)
        let secondID = afterStart.beginPicker()!
        stageAndPresent(&afterStart, attemptID: secondID, token: UUID().uuidString, title: title)
        let secondOperation = afterStart.operationID!
        precondition(secondID != firstID && secondOperation != firstOperation)
        precondition(afterStart.backendStarted(attemptID: secondID, operationID: secondOperation,
                                               sessionID: UUID().uuidString))
        precondition(afterStart.phase == .operationStarted)

        // CASE C: A prerequisite failure before opStart is terminal and still
        // returns to reusable idle after acknowledgement.
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

        // CASE D is covered above; additionally ensure an opStart success with
        // a later failure receives the same cleanup/reuse transition.
        var postStart = V3InstallAttemptState()
        let postID = postStart.beginPicker()!
        stageAndPresent(&postStart, attemptID: postID, token: UUID().uuidString, title: title)
        let postOperation = postStart.operationID!
        precondition(postStart.backendStarted(attemptID: postID, operationID: postOperation,
                                              sessionID: UUID().uuidString))
        precondition(postStart.recordTerminal(attemptID: postID, operationID: postOperation,
                                              outcome: "failed"))
        finishAcknowledgedAttempt(&postStart, attemptID: postID)
        precondition(postStart.beginPicker() != nil, "post-opStart failure poisoned the picker")

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

        // CASE F: Operation cancellation has a terminal outcome and the same
        // cleanup transition as failure and success.
        var operationCancelled = V3InstallAttemptState()
        let cancelID = operationCancelled.beginPicker()!
        stageAndPresent(&operationCancelled, attemptID: cancelID, token: UUID().uuidString, title: title)
        let cancelOperationID = operationCancelled.operationID!
        precondition(operationCancelled.backendStarted(attemptID: cancelID,
            operationID: cancelOperationID, sessionID: UUID().uuidString))
        precondition(operationCancelled.recordTerminal(attemptID: cancelID,
            operationID: cancelOperationID, outcome: "cancelled"))
        finishAcknowledgedAttempt(&operationCancelled, attemptID: cancelID)
        precondition(operationCancelled.beginPicker() != nil,
                     "operation cancellation did not return to idle")

        // Success is acknowledged through the identical reusable cleanup path.
        var succeeded = V3InstallAttemptState()
        let successID = succeeded.beginPicker()!
        stageAndPresent(&succeeded, attemptID: successID,
                        token: UUID().uuidString, title: title)
        let successOperationID = succeeded.operationID!
        precondition(succeeded.backendStarted(attemptID: successID,
            operationID: successOperationID, sessionID: UUID().uuidString))
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
