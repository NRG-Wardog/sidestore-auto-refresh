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
        pickerHandoff()
        installRouteParity()
        deleteTerminalReconciliation()
        refreshRunIsolationAndGuidance()
        print("V3 DEVICE ACCEPTANCE REGRESSION BEHAVIOR PASS")
    }

    private static func pickerHandoff() {
        let token = UUID().uuidString.lowercased()
        var snapshotLoading = true

        // Reproduce the old first-attempt failure: sheet dismissal called
        // perform() while a foreground snapshot still owned loading=true.
        let oldImmediatePresenterAccepted = !snapshotLoading
        precondition(!oldImmediatePresenterAccepted,
                     "the reported first-selection race was not represented")

        var handoff = V3InstallPresentationHandoff()
        precondition(handoff.stage(token: token, title: "Install / Sideload App",
                                   waitsForPickerDismissal: true))
        handoff.pickerDidDismiss()
        precondition(handoff.takeIfReady(isLoading: snapshotLoading,
                                         hasActivePresentation: false) == nil,
                     "picker dismissal must retain the staged request while reload owns presentation")
        snapshotLoading = false
        let queued = handoff.takeIfReady(isLoading: snapshotLoading,
                                         hasActivePresentation: false)
        precondition(queued?.token == token && queued?.title == "Install / Sideload App",
                     "the first IPA selection was lost instead of queued")
        precondition(handoff.takeIfReady(isLoading: false, hasActivePresentation: false) == nil,
                     "one selection presented more than one operation")
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
