import Foundation

// V3_SETUP_AND_SEMANTIC_UX_HARNESS_V1
// Executes the REAL shared policies for the UX correctness findings:
//   - one setup-completion decision that Home and the Setup Assistant share
//   - a reload status model where loading wins over connected
//   - recovery routing that does not blame networking for unrelated failures
//   - one semantic status model for success, warning and failure
//   - JIT-Less states that never confuse the active certificate with the copy
//   - the reload gate, so a caller can never start a second concurrent snapshot
//   - Add Source keyboard dismissal and cancel semantics
//   - one JIT-Less readiness fact, so Home and the assistant cannot disagree
//   - failure guidance that never shows a numeric error code as advice

@main
struct SetupAndSemanticUXHarness {
    static func main() {
        // V3_SETUP_COMPLETION_POLICY_V1
        var all = V3SetupCompletionInputs()
        all.accountComplete = true
        all.pairingSatisfied = true
        all.jitlessRequired = true
        all.jitlessComplete = true
        all.networkComplete = true
        all.tunnelComplete = true
        all.backgroundRefreshAvailable = true
        all.scheduleEnabled = true
        all.verifiedRefreshPresent = true
        precondition(all.isComplete)
        precondition(all.outstanding().isEmpty)

        // Everything complete except JIT-Less on iOS 26 -> outstanding, and the
        // outstanding item is named rather than being a bare boolean.
        var missingJITLess = all
        missingJITLess.jitlessComplete = false
        precondition(!missingJITLess.isComplete, "an unverified JIT-Less must block on iOS 26")
        precondition(missingJITLess.outstanding() == [.jitless])

        // The same inputs with JIT-Less not required are complete.
        var notRequired = missingJITLess
        notRequired.jitlessRequired = false
        precondition(notRequired.isComplete, "JIT-Less must not block where it is not required")

        // Each prerequisite is individually required.
        precondition(!V3SetupCompletionInputs(accountComplete: true).isComplete)
        var noPairing = all
        noPairing.pairingSatisfied = false
        precondition(noPairing.outstanding() == [.pairing])
        var provisioning = all
        provisioning.provisioningIncomplete = true
        precondition(provisioning.outstanding() == [.provisioning],
                     "incomplete provisioning is distinct from being signed out")
        var noTunnel = all
        noTunnel.tunnelComplete = false
        precondition(noTunnel.outstanding() == [.tunnel])
        var noBackground = all
        noBackground.backgroundRefreshAvailable = false
        precondition(noBackground.outstanding() == [.backgroundRefresh])
        var noSchedule = all
        noSchedule.scheduleEnabled = false
        precondition(noSchedule.outstanding() == [.schedule])
        var noVerified = all
        noVerified.verifiedRefreshPresent = false
        precondition(noVerified.outstanding() == [.verifiedRefresh])
        var noNetwork = all
        noNetwork.networkComplete = false
        precondition(noNetwork.outstanding() == [.network])

        // Every outstanding item carries a user-facing title.
        for item in V3SetupOutstandingItem.allCases {
            precondition(!item.title.isEmpty, "an outstanding item must be nameable")
        }

        // V3_RELOAD_STATUS_VISIBILITY_V1: loading wins over connected. The old
        // ordering rendered a green "Active & Connected" during a reload, which
        // is why Reload Status looked like it did nothing.
        let reloading = V3StatusPresentation.connectionState(connected: true, loading: true)
        precondition(reloading.severity == .working, "loading must not render as connected")
        precondition(reloading.title == "Reloading Status...")
        let connected = V3StatusPresentation.connectionState(connected: true, loading: false)
        precondition(connected.severity == .completed, "a healthy connection must render as success")
        precondition(connected.title == "Connected")
        let disconnected = V3StatusPresentation.connectionState(connected: false, loading: false)
        precondition(disconnected.severity == .failed, "a lost connection must render as failure")
        precondition(disconnected.isFailure)
        // Meaning never depends on colour alone.
        for presentation in [reloading, connected, disconnected] {
            precondition(!presentation.icon.isEmpty && !presentation.title.isEmpty)
        }

        // V3_USER_FACING_ISSUE_V1: recovery routing must follow the typed cause,
        // not the fact that something failed.
        func issue(_ operation: String, _ stage: String, _ safeCause: String? = nil,
                   retryable: Bool? = nil) -> V3UserFacingIssue {
            V3UserFacingIssue.make(operation: operation, stage: stage, code: "failed",
                                    safeCause: safeCause, sourceStep: nil, retryable: retryable,
                                    whatHappened: "what happened", whatToDo: "what to do",
                                    technicalDetails: "operation=\(operation) stage=\(stage)")
        }
        let sourceFailure = issue("source", "source")
        precondition(sourceFailure.primaryAction == .retrySource,
                     "a source failure must offer Retry Source, not Retry Connection")
        precondition(sourceFailure.recoveryDestination == "sources")
        // A signing failure is a certificate problem, so it offers Certificates.
        let certFailure = issue("command", "signing")
        precondition(certFailure.primaryAction == .openCertificates,
                     "a signing failure must offer Certificates")
        precondition(certFailure.recoveryDestination == "certificates")
        let authFailure = issue("signIn", "authentication")
        precondition(authFailure.primaryAction == .openAccount,
                     "an authentication failure must offer Account & Signing")
        let pairingFailure = issue("refresh", "pairing", CombinedFailure.SafeCause.pairingRequired.rawValue,
                                   retryable: false)
        precondition(pairingFailure.primaryAction == .showPairingSetup,
                     "a pairing failure must offer Pairing Setup")
        precondition(pairingFailure.retryDisposition == .blocked,
                     "a non-retryable prerequisite must not offer Retry")
        // Only genuine connection evidence offers a connection action.
        let networkFailure = issue("refresh", "network", retryable: true)
        precondition(networkFailure.primaryAction == .retryConnection)
        precondition(networkFailure.recoveryDestination == "connection")
        let serviceFailure = issue("catalog", "serviceReadiness", retryable: true)
        precondition(serviceFailure.primaryAction == .retryConnection,
                     "service readiness is a connection-class failure")
        let ipaFailure = issue("install", "filePreparation")
        precondition(ipaFailure.primaryAction == .chooseIPA)
        // A failure with no specific evidence must not assume networking.
        let unclassified = issue("command", "command")
        precondition(unclassified.primaryAction == .dismiss,
                     "an unclassified failure must not claim a connection problem")
        precondition(unclassified.recoveryDestination == nil)
        // Every action's destination agrees with the issue's own destination.
        for candidate in [sourceFailure, certFailure, authFailure, pairingFailure, networkFailure, ipaFailure] {
            precondition(candidate.primaryAction.destination == candidate.recoveryDestination)
        }
        // Diagnostics are preserved for copying.
        precondition(!sourceFailure.technicalDetails.isEmpty)

        // V3_STATUS_PRESENTATION_V1: one semantic model.
        precondition(V3StatusPresentation.severity(forState: "complete") == .completed)
        precondition(V3StatusPresentation.severity(forState: "failed") == .failed)
        precondition(V3StatusPresentation.severity(forState: "actionRequired") == .warning)
        precondition(V3StatusPresentation.severity(forState: "running") == .working)
        precondition(V3StatusPresentation.severity(forState: "cancelled") == .cancelled)
        precondition(V3StatusPresentation.severity(forState: "mystery") == .unknown)
        // Only a genuine success shows a tick; a failure never does.
        precondition(V3StatusSeverity.completed.showsCheckmark)
        precondition(!V3StatusSeverity.failed.showsCheckmark)
        precondition(!V3StatusSeverity.warning.showsCheckmark)
        precondition(V3StatusSeverity.failed.icon == "xmark.circle.fill")
        precondition(V3StatusSeverity.completed.icon == "checkmark.circle.fill")
        precondition(V3StatusSeverity.warning.icon == "exclamationmark.triangle.fill")
        precondition(V3StatusSeverity.failed.isFailure && V3StatusSeverity.completed.isSuccess)
        // The severities are distinct, so one cannot be mistaken for another.
        precondition(Set(V3StatusSeverity.allCases.map(\.icon)).count == V3StatusSeverity.allCases.count)

        // V3_JITLESS_CERT_DISTINCTION_V1: the active SideStore certificate and
        // the LiveContainer copy are never conflated.
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "valid", identitiesMatch: true,
            validationStatus: 0, validationFailed: false) == .ready)
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 25, hasCopy: false, activeCertificateExists: false,
            identitiesMatch: nil, validationStatus: nil, validationFailed: false) == .notRequired)
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: false, activeCertificateExists: true,
            identitiesMatch: nil, validationStatus: nil, validationFailed: false) == .setupRequired)
        // A valid copy that differs from the active certificate is a stale COPY,
        // not a broken SideStore certificate.
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "valid", identitiesMatch: false,
            validationStatus: 0, validationFailed: false) == .certificateMismatch)
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "revoked", identitiesMatch: true,
            validationStatus: 0, validationFailed: false) == .activeCertificateRevoked)
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "expired", identitiesMatch: true,
            validationStatus: 0, validationFailed: false) == .activeCertificateExpired)
        // No active certificate is its own state, not "unknown".
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: false,
            identitiesMatch: nil, validationStatus: 0, validationFailed: false) == .activeCertificateMissing)
        // The local copy is revoked. When the active certificate is the very same
        // revoked identity, the active certificate is what is reported; a local
        // copy that is revoked on its own is reported as such.
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "valid", identitiesMatch: true,
            validationStatus: 1, validationFailed: false) == .activeCertificateRevoked)
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "valid", identitiesMatch: false,
            validationStatus: 1, validationFailed: false) == .revoked,
            "a revoked local copy that differs from the active certificate is a copy problem")
        precondition(V3JITLessReadinessPolicy.evaluate(
            osMajor: 26, hasCopy: true, activeCertificateExists: true,
            activeCertificateStatus: "valid", identitiesMatch: nil,
            validationStatus: nil, validationFailed: false) == .certificateImported)

        // A ready JIT-Less is a completed result, never an outstanding task.
        let ready = V3JITLessPresentation.present(.ready)
        precondition(ready.severity == .completed && !ready.isOutstandingSetupTask)
        precondition(ready.title == "Configured / Ready")
        precondition(V3JITLessPresentation.present(.notRequired).severity == .completed)
        // Everything that still needs work is flagged as an outstanding task.
        for state: V3JITLessReadiness in [.setupRequired, .certificateMismatch, .certificateImported,
                                          .needsCertificateRefresh, .revoked, .activeCertificateMissing,
                                          .activeCertificateRevoked, .activeCertificateExpired, .unknown] {
            precondition(V3JITLessPresentation.present(state).isOutstandingSetupTask,
                         "\(state.rawValue) must be presented as outstanding work")
            precondition(!V3JITLessPresentation.present(state).title.isEmpty)
        }
        // The mismatch message must name the copy, and must not blame SideStore.
        let mismatch = V3JITLessPresentation.present(.certificateMismatch)
        precondition(mismatch.detail.contains("LiveContainer"))
        precondition(mismatch.detail.lowercased().contains("refresh the jit-less certificate copy"))
        precondition(!mismatch.detail.lowercased().contains("broken"))
        precondition(mismatch.severity == .warning)

        // V3_RELOAD_GATE_V1: a caller can never start a second concurrent
        // snapshot, which is what made the recalculate race possible.
        precondition(V3ReloadGate.begin(loading: false, presentationActive: false,
                                        manual: true, requiresConnectionRetry: false) == .startSnapshot)
        precondition(V3ReloadGate.begin(loading: true, presentationActive: false,
                                        manual: true, requiresConnectionRetry: false) == .joinInFlight)
        precondition(V3ReloadGate.begin(loading: false, presentationActive: true,
                                        manual: true, requiresConnectionRetry: false) == .deferUntilIdle)
        precondition(V3ReloadGate.begin(loading: false, presentationActive: false,
                                        manual: false, requiresConnectionRetry: true) == .skip)
        precondition(V3ReloadGate.begin(loading: false, presentationActive: false,
                                        manual: true, requiresConnectionRetry: true) == .startSnapshot,
                     "an explicit manual reload must always be allowed")

        // V3_SOURCE_EDITING_POLICY_V1 (issue #40): Done keeps the typed value,
        // Cancel restores the pre-edit value, and neither implies any action.
        precondition(V3SourceEditingPolicy.done(typed: "https://x") == .dismissed)
        precondition(V3SourceEditingPolicy.cancel(typed: "https://x",
                                                 beforeEditing: "https://y") == .restored("https://y"))
        precondition(V3SourceEditingPolicy.resolved(.dismissed, typed: "https://x") == "https://x")
        precondition(V3SourceEditingPolicy.resolved(.restored("https://y"), typed: "https://x") == "https://y")
        // Cancelling a pristine field is a no-op.
        precondition(V3SourceEditingPolicy.resolved(
            V3SourceEditingPolicy.cancel(typed: "same", beforeEditing: "same"), typed: "same") == "same")

        // V3_SHARED_JITLESS_FACT_V1: Home and the Setup Assistant must be able to
        // reach the same completion answer from the same observed readiness.
        // A verified copy completes the item on a platform that requires it; an
        // unobserved fact is outstanding rather than assumed fine, which is the
        // answer that used to differ per surface.
        precondition(V3JITLessCompletionPolicy.isRequired(osMajor: 26))
        precondition(V3JITLessCompletionPolicy.isRequired(osMajor: 27))
        precondition(!V3JITLessCompletionPolicy.isRequired(osMajor: 18))
        precondition(V3JITLessCompletionPolicy.isComplete(.ready))
        precondition(V3JITLessCompletionPolicy.isComplete(.notRequired))
        precondition(!V3JITLessCompletionPolicy.isComplete(nil),
                     "an unobserved JIT-Less state must stay outstanding")
        precondition(!V3JITLessCompletionPolicy.isComplete(.unknown))
        for state: V3JITLessReadiness in [.setupRequired, .certificateImported,
                                         .needsCertificateRefresh, .revoked,
                                         .activeCertificateMissing, .activeCertificateRevoked,
                                         .activeCertificateExpired, .certificateMismatch,
                                         .unknown] {
            precondition(!V3JITLessCompletionPolicy.isComplete(state),
                         "\(state) is outstanding setup work")
        }

        // V3_FAILURE_GUIDANCE_V1: a typed failure keeps its own recovery copy, an
        // untyped one never publishes a numeric domain and code as guidance, and
        // neither claims a network cause that was not proven.
        let typed = CombinedFailure(operation: "source", stage: .source, code: .failed,
                                    id: UUID().uuidString, retryable: true,
                                    safeCause: .sourceNetworkFailure, sourceStep: .sourceDownload)
        precondition(V3FailureGuidance.message(typed) == typed.recovery,
                     "a typed failure shows its own product recovery copy")
        precondition(V3FailureGuidance.diagnostics(typed) == typed.technicalDetails)
        precondition(!V3FailureGuidance.message(typed).contains("LiveContainer"),
                     "guidance must not leak the underlying error domain")

        // A source failure must earn the source action, and only that action, so
        // the button the user presses re-requests the sources instead of claiming
        // a retry while only reloading status.
        let sourceIssue = V3UserFacingIssue.make(typed)
        precondition(sourceIssue.recoveryDestination == "sources",
                     "a source failure routes to Sources")
        precondition(sourceIssue.primaryAction == .retrySource)
        precondition(sourceIssue.primaryAction.title == "Retry Source")
        precondition(sourceIssue.retryDisposition == .allowed)

        // A networking failure is the only case that may offer a connection retry,
        // and it must still be routed by the typed stage rather than by a guess.
        let networkIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "status", stage: .network, code: .failed,
                            id: UUID().uuidString, safeCause: .networkConnectionLost))
        precondition(networkIssue.recoveryDestination == "connection")
        precondition(networkIssue.primaryAction == .retryConnection,
                     "a retryable connection failure is the one case that offers a retry")
        // A connection-stage failure that is provably not retryable is inspected
        // rather than blindly retried.
        let blockedNetworkIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "status", stage: .network, code: .failed,
                            id: UUID().uuidString, retryable: false))
        precondition(blockedNetworkIssue.recoveryDestination == "connection")
        precondition(blockedNetworkIssue.primaryAction == .openConnectionCheck)
        precondition(blockedNetworkIssue.retryDisposition == .blocked)

        // A certificate failure must never be described as a connection problem.
        let certificateIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "refresh", stage: .signing, code: .failed,
                            id: UUID().uuidString, safeCause: .certificateUnavailable))
        precondition(certificateIssue.recoveryDestination == "certificates")
        precondition(certificateIssue.primaryAction == .openCertificates)

        let untyped = NSError(domain: "LiveContainer.Service", code: 4865)
        precondition(!V3FailureGuidance.message(untyped).contains("4865"),
                     "a numeric error code must never be shown as guidance")
        precondition(!V3FailureGuidance.message(untyped).contains("LiveContainer.Service"),
                     "the raw error domain must not be shown as guidance")
        precondition(V3FailureGuidance.diagnostics(untyped).contains("4865"),
                     "the code stays available through diagnostics")
        // An untyped failure has no proven cause, so it must not claim one.
        let untypedIssue = V3UserFacingIssue.make(
            operation: "command", stage: CombinedFailure.Stage.command.rawValue,
            code: CombinedFailure.Code.failed.rawValue, safeCause: nil, sourceStep: nil,
            retryable: nil, whatHappened: "That action did not complete.",
            whatToDo: V3FailureGuidance.message(untyped),
            technicalDetails: V3FailureGuidance.diagnostics(untyped))
        precondition(untypedIssue.primaryAction == .dismiss,
                     "with no evidence, no action is invented")
        precondition(untypedIssue.recoveryDestination == nil)

        print("V3_SETUP_AND_SEMANTIC_UX_PASS")
    }
}
