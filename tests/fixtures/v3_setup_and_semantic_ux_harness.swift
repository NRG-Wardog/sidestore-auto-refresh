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

private final class V3AuthReadinessHostObserverProbe {
    private var ledger = V3AuthReadinessRefreshEventLedger()
    private(set) var readinessRefreshSessionIDs: [String] = []
    private(set) var readinessRefreshAttemptSequences: [UInt64] = []
    private(set) var authoritativeHealthReadCount = 0
    var sharedReadiness: V3JITLessReadiness?

    func receive(_ notification: Notification) {
        guard let sessionID = V3AuthReadinessRefreshEvent.sessionID(from: notification),
              let attemptSequence = V3AuthReadinessRefreshEvent.attemptSequence(from: notification),
              ledger.claim(attemptSequence: attemptSequence) else { return }
        readinessRefreshSessionIDs.append(sessionID)
        readinessRefreshAttemptSequences.append(attemptSequence)
        authoritativeHealthReadCount += 1
        sharedReadiness = .ready
    }
}

@main
struct SetupAndSemanticUXHarness {
    static func main() {
        func plistRoundTrip(_ failure: CombinedFailure) -> CombinedFailure? {
            guard let data = try? PropertyListSerialization.data(
                    fromPropertyList: failure.wire, format: .binary, options: 0),
                  let object = try? PropertyListSerialization.propertyList(from: data, format: nil),
                  let dictionary = object as? [String: Any] else { return nil }
            return CombinedFailure.decode(dictionary, expectedID: failure.correlationID)
        }
        precondition(V3SharedKeychainAccessGroupPolicy.sharedGroup(in: [
            "TEAMID.com.kdt.livecontainer", "TEAMID.com.kdt.livecontainer.shared"
        ]) == "TEAMID.com.kdt.livecontainer.shared",
            "canonical JIT-Less import must prefer the explicit shared SideStore keychain group")
        precondition(V3SharedKeychainAccessGroupPolicy.sharedGroup(in: [
            "TEAMID.com.kdt.livecontainer", "TEAMID.other.shared"
        ]) == nil,
            "a default or unrelated keychain group cannot stand in for SideStore's shared group")
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
        let sourceCapacity = issue("source", "command",
            CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue, retryable: true)
        precondition(sourceCapacity.primaryAction == .dismiss &&
                     sourceCapacity.retryDisposition == .prerequisite,
            "a source request blocked by response capacity must wait instead of immediately repeating the request")
        let signInCapacity = V3UserFacingIssue.make(
            CombinedFailure(operation: "signIn", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true,
                safeCause: .authResponseCapacityUnavailable))
        precondition(signInCapacity.recoveryDestination == "signIn" &&
                     signInCapacity.primaryAction == .openAccount &&
                     signInCapacity.retryDisposition == .prerequisite,
            "a rejected auth start preserves the response-capacity prerequisite without blaming a connection")
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
        // Only genuine connection evidence opens connection settings.
        let networkFailure = issue("refresh", "network", retryable: true)
        precondition(networkFailure.primaryAction == .openConnectionCheck &&
                     networkFailure.primaryAction.title == "Open Connection Settings")
        precondition(networkFailure.recoveryDestination == "connection")
        let serviceFailure = issue("catalog", "serviceReadiness", retryable: true)
        precondition(serviceFailure.primaryAction != .openConnectionCheck &&
                     serviceFailure.recoveryDestination == nil,
                     "embedded SideStore readiness is not proof that Connection Settings will help")
        let serviceXPCFailure = issue("status", "xpcConnection", retryable: true)
        precondition(serviceXPCFailure.primaryAction != .openConnectionCheck &&
                     serviceXPCFailure.recoveryDestination == nil,
                     "an interrupted SideStore XPC session does not route to VPN settings")
        let sourceNotReady = CombinedFailure(operation: "source", stage: .serviceReadiness,
            code: .notReady, id: UUID().uuidString, retryable: true)
        let sourceNotReadyIssue = V3UserFacingIssue.make(sourceNotReady)
        precondition(sourceNotReadyIssue.primaryAction == .openSources &&
            sourceNotReadyIssue.primaryAction.title == "Open Sources",
            "source service-readiness failures must not claim Retry Source re-fetches the source")
        let knownSourceInvalid = issue("source", "source",
            CombinedFailure.SafeCause.knownSourcePolicyInvalidResponse.rawValue, retryable: false)
        precondition(knownSourceInvalid.primaryAction == .openSources &&
                     knownSourceInvalid.recoveryDestination == "sources",
                     "an unreadable SideStore safety list must point to Sources, not blame the entered URL")
        let knownSourceNetworkFailure = CombinedFailure(operation: "source", stage: .source,
            code: .failed, id: UUID().uuidString, retryable: true,
            safeCause: .knownSourcePolicyNetworkFailure, sourceStep: .knownSourcePolicyFetch)
        precondition(knownSourceNetworkFailure.message.contains("known-source safety list") &&
                     knownSourceNetworkFailure.recovery.contains("not the URL you entered"),
                     "a known-source endpoint failure must name SideStore's safety-list fetch")
        let deterministicSourceReply = issue("source", "replyEncoding",
            CombinedFailure.SafeCause.responseEncodingFailed.rawValue, retryable: false)
        precondition(deterministicSourceReply.primaryAction == .dismiss &&
                     deterministicSourceReply.retryDisposition == .blocked,
                     "a deterministic source reply defect must not offer Retry Source")
        precondition(!V3SourceSubmissionPolicy.mayResubmit(retryable: false,
            safeCause: CombinedFailure.SafeCause.responseTooLarge.rawValue,
            failedInput: "https://example.invalid/source.json",
            currentInput: "https://example.invalid/source.json"),
            "the source form must not repeat the same deterministic oversized response request")
        precondition(V3SourceSubmissionPolicy.mayResubmit(retryable: false,
            safeCause: CombinedFailure.SafeCause.responseTooLarge.rawValue,
            failedInput: "https://example.invalid/source.json",
            currentInput: "https://example.invalid/changed-source.json"),
            "editing the request permits a new source submission after a deterministic failure")
        precondition(V3SourceSubmissionPolicy.mayResubmit(retryable: true,
            safeCause: CombinedFailure.SafeCause.sourceNetworkFailure.rawValue,
            failedInput: "https://example.invalid/source.json",
            currentInput: "https://example.invalid/source.json"),
            "a network source failure may be retried explicitly")
        for sourceCause in [CombinedFailure.SafeCause.sourceInvalidManifest.rawValue,
                            CombinedFailure.SafeCause.sourcePersistenceUnverified.rawValue] {
            precondition(V3SourceSubmissionPolicy.mayResubmit(retryable: false,
                safeCause: sourceCause, failedInput: "https://example.invalid/source.json",
                currentInput: "https://example.invalid/source.json"),
                "correcting a source manifest or reconciling uncertain persistence can retry the same URL")
        }
        let contention = issue("refresh", "command",
            CombinedFailure.SafeCause.operationInProgress.rawValue, retryable: true)
        precondition(contention.primaryAction == .dismiss && contention.recoveryDestination == nil,
                     "mutation contention must not be routed as a connection failure")
        let activeMutationFailure = CombinedFailure(operation: "refresh", stage: .command,
            code: .busy, id: UUID().uuidString, retryable: true, safeCause: .operationInProgress)
        precondition(activeMutationFailure.message.contains("Another SideStore operation") &&
                     activeMutationFailure.recovery.contains("check the action's current state") &&
                     !activeMutationFailure.recovery.contains("start Refresh again"),
                     "busy recovery must stay with the requested action rather than misroute other mutations to Refresh")
        let capacityFailure = CombinedFailure(operation: "signOut", stage: .command,
            code: .busy, id: UUID().uuidString, retryable: true,
            safeCause: .responseCapacityUnavailable)
        precondition(capacityFailure.message.contains("cannot safely accept another") &&
                     capacityFailure.recovery.contains("release earlier request results"),
                     "request-cache capacity has distinct wait guidance from an active device mutation")
        let deleteTimeout = CombinedFailure(operation: "delete", stage: .command,
            code: .timedOut, id: UUID().uuidString)
        precondition(V3OperationFailureDetails(deleteTimeout).recommendedAction == deleteTimeout.recovery &&
                     deleteTimeout.recovery.contains("verify the deletion"),
            "unknown retryability must preserve the operation-specific delete reconciliation guidance")
        precondition(V3CatalogRetryPresentationPolicy.action(for: .allowed) == .retry &&
                     V3CatalogRetryPresentationPolicy.action(for: .unknown) == .retryWithUnknownDisposition &&
                     V3CatalogRetryPresentationPolicy.action(for: .prerequisite) == .noRetry &&
                     V3CatalogRetryPresentationPolicy.action(for: .blocked) == .noRetry,
                     "catalog retry copy is explicit about uncertainty and never encourages retry before prerequisites")
        let catalogUnavailable = CombinedFailure(operation: "catalog", stage: .catalog,
            code: .failed, id: UUID().uuidString, retryable: false,
            safeCause: .catalogUnavailable)
        let catalogUnavailableDetails = V3OperationFailureDetails(catalogUnavailable)
        precondition(V3CatalogRetryPresentationPolicy.action(for: catalogUnavailableDetails.retryDisposition,
            safeCause: catalogUnavailableDetails.safeCause) == .reloadCatalog &&
                     catalogUnavailableDetails.recommendedAction.contains("Reload this source's catalog") &&
                     !catalogUnavailableDetails.recommendedAction.contains("signing status"),
            "local catalog read failure offers a catalog reload and retains its specific recovery guidance")
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: true,
            isAuthContinuation: false, responseCapacityAvailable: true),
            "mutations must wait until the live authentication/provisioning session terminates")
        precondition(V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authBegin", ownsActiveSession: false, authenticationActive: false),
            "a new authentication attempt is admitted only after the current backend auth session is idle")
        precondition(!V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authBegin", ownsActiveSession: false, authenticationActive: true),
            "a stale auth screen cannot supersede another active authentication session")
        precondition(V3AuthSessionAdmissionPolicy.mayStartNewSession(hasActiveSession: false) &&
            !V3AuthSessionAdmissionPolicy.mayStartNewSession(hasActiveSession: true) &&
            V3ServiceMutationBusyCausePolicy.safeCause(operation: "authRetryProvisioning",
                anotherMutationActive: false, responseCapacityAvailable: true,
                refreshActive: false, refreshRelease: false, authenticationActive: true,
                isAuthContinuation: false) == .operationInProgress,
            "active authentication ownership blocks retry admission with an actionable typed conflict")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: true,
            isAuthContinuation: V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
                "authBegin", ownsActiveSession: false, authenticationActive: true), responseCapacityAvailable: true) == false,
            "a stale auth begin cannot cancel and replace an active session through mutation admission")
        precondition(V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authRetryProvisioning", ownsActiveSession: false, authenticationActive: false) &&
            !V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
                "authRetryProvisioning", ownsActiveSession: false, authenticationActive: true),
            "provisioning resume is unavailable while another auth session owns the backend")
        precondition(V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authRespond", ownsActiveSession: true, authenticationActive: true),
            "the active session must accept its own prompt response")
        precondition(!V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authRespond", ownsActiveSession: false, authenticationActive: true),
            "another session must not answer a prompt it does not own")
        precondition(V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authCancel", ownsActiveSession: true, authenticationActive: true),
            "the active session must have a scoped cancellation path")
        precondition(!V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            "authCancel", ownsActiveSession: false, authenticationActive: true),
            "cancellation must not target another live auth session")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: true,
            isAuthContinuation: true, responseCapacityAvailable: true),
            "the active auth session must still accept its own prompt continuation")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: false,
            anotherMutationActive: true, authenticationActive: true,
            isAuthContinuation: false, responseCapacityAvailable: true),
            "read-only status remains available while mutation admission is gated")

        precondition(V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("authBegin"))
        precondition(V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("authRetryProvisioning"))
        precondition(V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("refreshAdmissionBegin"))
        precondition(!V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("authPoll"))
        precondition(V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("authCancel"),
                     "an unconfirmed cancellation must retire the service after its bounded recovery grace")
        precondition(!V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("opPoll"))
        precondition(!V3IdleReadRetirementPolicy.shouldRetireService(
            operation: "authPoll", hostMutationActive: false, refreshAttemptActive: false),
            "one timed-out auth poll must not retire a still-running SignInOperation")
        precondition(!V3IdleReadRetirementPolicy.shouldRetireService(
            operation: "snapshot", hostMutationActive: true, refreshAttemptActive: false),
            "an idle-read timeout must not retire a service while a host mutation is active")
        precondition(V3IdleReadRetirementPolicy.shouldRetireService(
            operation: "snapshot", hostMutationActive: false, refreshAttemptActive: false),
            "an unresponsive idle service can still be retired")
        let unauthenticatedExpiry = V3AuthSessionExpiryPolicy.response(authenticated: false)
        precondition(unauthenticatedExpiry["state"] as? String == "timedOut" &&
                     unauthenticatedExpiry["authenticated"] as? Bool == false,
                     "expired authentication must be distinct from user cancellation")
        let postAuthExpiry = V3AuthSessionExpiryPolicy.response(authenticated: true)
        precondition(postAuthExpiry["state"] as? String == "authenticatedProvisioningIncomplete" &&
                     postAuthExpiry["authenticated"] as? Bool == true,
                     "a provisioning timeout after Apple authentication must preserve signed-in state")
        let resumableExpiry = V3AuthSessionExpiryPolicy.response(authenticated: true, resumable: true)
        precondition(resumableExpiry["resumable"] as? Bool == true,
                     "a provisioning timeout must retain an already-authenticated resumable session")

        // V3_REFRESH_ADMISSION_LEASE_V1: the scheduler's direct XPC refresh
        // path acquires this same service-owned lease before dispatch. Its
        // MainActor serialization closes the gap between a readiness snapshot
        // and the native refresh call.
        let runA = UUID().uuidString
        let runB = UUID().uuidString
        let refreshRequestA = UUID().uuidString
        let refreshRequestB = UUID().uuidString
        precondition(V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionBegin", target: runA, activeRunID: runA,
            refreshAttemptActive: true),
            "the current refresh attempt can enter the service admission handshake")
        precondition(V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionEnd", target: runA, activeRunID: runA,
            refreshAttemptActive: true),
            "the same refresh attempt can release while its operation token is still held")
        precondition(!V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionBegin", target: runA, activeRunID: runA,
            refreshAttemptActive: true, anotherHostMutationActive: true),
            "refresh controls must not bypass another in-flight host mutation")
        precondition(!V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionEnd", target: runB, activeRunID: runA,
            refreshAttemptActive: true),
            "a different run cannot use the host bridge's refresh-control exception")
        precondition(!V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionBegin", target: runA, activeRunID: runA,
            refreshAttemptActive: false),
            "a stale owner identity without a live refresh token is not sufficient")
        precondition(!V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "settingsSet", target: runA, activeRunID: runA,
            refreshAttemptActive: true),
            "the refresh exception must not authorize unrelated host mutations")
        precondition(V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionReconcile", target: runA, activeRunID: nil,
            refreshAttemptActive: true, anotherHostMutationActive: true,
            userConfirmedReconciliation: true),
            "an explicit exact-run device confirmation must reach the service even while the stale host owner is busy")
        precondition(!V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: "refreshAdmissionReconcile", target: runA, activeRunID: nil,
            refreshAttemptActive: true, anotherHostMutationActive: true,
            userConfirmedReconciliation: false) &&
            !V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
                operation: "refreshAdmissionReconcile", target: "not-a-run-id", activeRunID: runA,
                refreshAttemptActive: true, anotherHostMutationActive: false,
                userConfirmedReconciliation: true),
            "host reconciliation requires confirmation and a canonical target; the service checks exact run ownership")
        var refreshLease = V3RefreshAdmissionLease()
        precondition(!refreshLease.acquire(runID: runA, requestID: refreshRequestA, authenticationActive: true,
            anotherMutationActive: false),
            "refresh cannot acquire ownership while authentication is active")
        precondition(refreshLease.acquire(runID: runA, requestID: refreshRequestA, authenticationActive: false,
            anotherMutationActive: false),
            "a ready scheduler run acquires refresh ownership")
        precondition(!refreshLease.acquire(runID: runB, requestID: refreshRequestB, authenticationActive: false,
            anotherMutationActive: false),
            "a second scheduler run cannot overlap the current refresh")
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false,
            isAuthContinuation: V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
                "authBegin", ownsActiveSession: false, authenticationActive: false), responseCapacityAvailable: true,
            refreshActive: refreshLease.isActive),
            "authentication cannot begin after refresh has atomically acquired ownership")
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false,
            isAuthContinuation: false, responseCapacityAvailable: true,
            refreshActive: refreshLease.isActive),
            "other mutations cannot overlap the scheduler refresh")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false,
            isAuthContinuation: false, responseCapacityAvailable: true,
            refreshActive: refreshLease.isActive, isRefreshRelease: refreshLease.owns(runA)),
            "only the owner can release the refresh gate")
        precondition(!refreshLease.release(runID: runB),
            "a stale run cannot release another run's refresh lease")
        precondition(!refreshLease.release(requestID: refreshRequestB),
            "a cancelled unrelated request cannot release another run's refresh lease")
        precondition(refreshLease.release(runID: runA) && !refreshLease.isActive,
            "terminal refresh releases admission for the next request")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false,
            isAuthContinuation: false, responseCapacityAvailable: true,
            refreshActive: refreshLease.isActive),
            "the next mutation is admitted after the run-scoped lease is released")
        var expiringLease = V3RefreshAdmissionLease()
        let leaseStart = Date(timeIntervalSince1970: 900)
        let expiry = leaseStart.addingTimeInterval(V3RefreshAdmissionLease.lifetime)
        precondition(expiringLease.acquire(runID: runB, requestID: refreshRequestB, authenticationActive: false,
            anotherMutationActive: false, now: leaseStart))
        precondition(!expiringLease.expire(now: leaseStart.addingTimeInterval(90)) && expiringLease.isActive,
            "the short begin-request deadline cannot expire the 600-second native refresh lease")
        precondition(expiringLease.expire(now: expiry) && expiringLease.isActive && expiringLease.ownerLost,
            "a lost host owner is recorded after 660 seconds without releasing native refresh admission")
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: true, refreshActive: expiringLease.isActive),
            "time alone cannot admit an install/update while native refresh may still be active")
        var cancelledLease = V3RefreshAdmissionLease()
        precondition(cancelledLease.acquire(runID: runA, requestID: refreshRequestA,
            authenticationActive: false, anotherMutationActive: false))
        precondition(cancelledLease.release(requestID: refreshRequestA) && !cancelledLease.isActive,
            "a request-scoped cancel releases only its own pre-dispatch refresh lease")

        // V3_KNOWN_SOURCE_PREFLIGHT_V1: the headless source path refreshes the
        // canonical source blocklist on first use and then reuses it for six
        // hours, avoiding both an empty-cache safety bypass and a network fetch
        // for every Preview/Confirm interaction.
        let knownNow = Date(timeIntervalSince1970: 100_000)
        precondition(V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: false, lastSuccessfulUpdate: nil, now: knownNow),
            "a fresh headless service must load SideStore's blocklist before source fetch")
        precondition(V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: true,
            lastSuccessfulUpdate: knownNow.addingTimeInterval(-7 * 60 * 60), now: knownNow),
            "an expired blocklist must be refreshed before a new source is fetched")
        precondition(!V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: true,
            lastSuccessfulUpdate: knownNow.addingTimeInterval(-5 * 60 * 60), now: knownNow),
            "a fresh SideStore blocklist is reused across preview and confirmation")
        precondition(V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: true,
            lastSuccessfulUpdate: knownNow.addingTimeInterval(60), now: knownNow),
            "a future cache timestamp is treated as invalid and refreshed")
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
        let signInReady = V3SignInJITLessGuidancePolicy.resolve(osMajor: 26, readiness: .ready)!
        precondition(signInReady.presentation.title == "Configured / Ready" &&
                     !signInReady.presentation.isOutstandingSetupTask &&
                     signInReady.action == .none,
                     "a signed-in account with ready JIT-Less is not prompted to set it up again")
        let signInMissing = V3SignInJITLessGuidancePolicy.resolve(osMajor: 26, readiness: .setupRequired)!
        precondition(signInMissing.presentation.title == "JIT-Less certificate not configured" &&
                     signInMissing.action == .setUp,
                     "a confirmed missing JIT-Less copy goes to canonical setup")
        for stale: V3JITLessReadiness in [.needsCertificateRefresh, .certificateMismatch, .revoked] {
            let guidance = V3SignInJITLessGuidancePolicy.resolve(osMajor: 26, readiness: stale)!
            precondition(guidance.presentation.isOutstandingSetupTask &&
                         guidance.action == .refreshCertificate,
                         "\(stale.rawValue) routes to canonical certificate refresh")
        }
        for activeCertificateIssue: V3JITLessReadiness in [
            .activeCertificateMissing, .activeCertificateRevoked, .activeCertificateExpired
        ] {
            let guidance = V3SignInJITLessGuidancePolicy.resolve(
                osMajor: 26, readiness: activeCertificateIssue)!
            precondition(guidance.action == .openCertificates,
                         "\(activeCertificateIssue.rawValue) routes to SideStore Certificates")
        }
        let signInUnknown = V3SignInJITLessGuidancePolicy.resolve(osMajor: 26, readiness: nil)!
        precondition(signInUnknown.readiness == .unknown &&
                     signInUnknown.presentation.title == "Validation unknown" &&
                     signInUnknown.action == .openSetup,
                     "unobserved readiness is shown as unknown, never as confirmed missing")
        // V3_AUTH_READINESS_REFRESH_EVENT_V1: the root observer outlives each
        // Sign In presentation and consumes an event after that view disappears.
        let hostReadinessObserver = V3AuthReadinessHostObserverProbe()
        func parsedReadinessSequence(_ value: Any) -> UInt64? {
            let notification = Notification(name: V3AuthReadinessRefreshEvent.notificationName,
                object: nil, userInfo: [V3AuthReadinessRefreshEvent.attemptSequenceKey: value])
            return V3AuthReadinessRefreshEvent.attemptSequence(from: notification)
        }
        precondition(parsedReadinessSequence(NSNumber(value: UInt64(0))) == 0,
                     "zero is parsed exactly, then rejected by the event ledger")
        precondition(parsedReadinessSequence(NSNumber(value: UInt64(7))) == 7,
                     "positive integer attempt sequences parse exactly")
        precondition(parsedReadinessSequence(NSNumber(value: UInt64.max)) == UInt64.max,
                     "the UInt64 maximum parses without wrapping")
        precondition(parsedReadinessSequence(NSNumber(value: -1)) == nil,
                     "negative NSNumber values cannot poison the high-water ledger")
        precondition(parsedReadinessSequence(NSNumber(value: 1.5)) == nil,
                     "fractional floating-point sequences are rejected")
        precondition(parsedReadinessSequence(NSNumber(value: true)) == nil,
                     "CFBoolean is not accepted as an integer sequence")
        precondition(parsedReadinessSequence(NSDecimalNumber(string: "7")) == nil,
                     "Decimal NSNumber encodings are rejected even when integral")
        precondition(parsedReadinessSequence(NSDecimalNumber(string: "18446744073709551616")) == nil,
                     "an overflowing Decimal cannot wrap to a valid sequence")
        precondition(parsedReadinessSequence("malformed") == nil,
                     "non-numeric payloads are rejected")
        let observerToken = NotificationCenter.default.addObserver(
            forName: V3AuthReadinessRefreshEvent.notificationName,
            object: nil, queue: nil) { hostReadinessObserver.receive($0) }
        defer { NotificationCenter.default.removeObserver(observerToken) }
        var signInViewPresent = true
        var accountReloadCompletedEarlier = false
        let signInSessionID = UUID().uuidString
        signInViewPresent = false
        accountReloadCompletedEarlier = true // onDisappear's account snapshot finished
        let signInAttemptSequence: UInt64 = 1
        V3AuthReadinessRefreshEvent.post(sessionID: signInSessionID, attemptSequence: signInAttemptSequence)
        precondition(!signInViewPresent && accountReloadCompletedEarlier &&
                     hostReadinessObserver.readinessRefreshSessionIDs == [signInSessionID],
                     "the root observes a reconciled auth completion after the view and its reload are gone")
        V3AuthReadinessRefreshEvent.post(sessionID: signInSessionID, attemptSequence: signInAttemptSequence) // same attempt
        precondition(hostReadinessObserver.readinessRefreshSessionIDs == [signInSessionID],
                     "duplicate delivery of one auth session cannot start another readiness read")
        var setupLocalHealthReadCount = 0
        if V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
            hostReadinessObserver.sharedReadiness) {
            setupLocalHealthReadCount += 1
        }
        precondition(hostReadinessObserver.authoritativeHealthReadCount == 1 &&
                     setupLocalHealthReadCount == 0,
                     "Quick Setup reuses the root-published JIT-Less fact instead of fetching it again")

        let provisioningRetrySession = signInSessionID
        let retryAttemptSequence: UInt64 = 2
        var retryOwner = V3ProvisioningRetryReadinessOwnership()
        retryOwner.begin(sessionID: provisioningRetrySession)
        precondition(retryOwner.owns(sessionID: provisioningRetrySession))
        precondition(retryOwner.allowsPromptResponse(sessionID: provisioningRetrySession),
                     "the retry owns its own verification prompt response")
        precondition(retryOwner.owns(sessionID: provisioningRetrySession),
                     "submitting a prompt response retains retry ownership")
        let replacementMonitorAllowed = V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: provisioningRetrySession,
            currentSessionID: provisioningRetrySession,
            failedPromptRevision: 1, currentPromptRevision: 2,
            failedPromptResponseGeneration: 1, currentPromptResponseGeneration: 2,
            state: "awaitingPrompt", promptSubmissionInProgress: true,
            activeSessionID: provisioningRetrySession, pollFailureIsTransient: true,
            cancellationInProgress: false, taskCancelled: false,
            reconciliationWasSuperseded: true,
            sessionDeadline: Date().addingTimeInterval(60))
        precondition(replacementMonitorAllowed,
                     "a transient poll failure after a prompt response transfers monitoring")
        precondition(retryOwner.handoffAfterSupersededPollFailure(sessionID: provisioningRetrySession) &&
                     retryOwner.owns(sessionID: provisioningRetrySession),
                     "a transient poll failure hands the same retry to its monitor without dropping ownership")
        let continuedTerminalRefresh = retryOwner.settle(
            currentSessionID: provisioningRetrySession, replySessionID: provisioningRetrySession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: false, taskCancelled: false)
        if continuedTerminalRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: provisioningRetrySession, attemptSequence: retryAttemptSequence)
        }
        precondition(continuedTerminalRefresh == .committed &&
                     !retryOwner.owns(sessionID: provisioningRetrySession) &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession],
                     "the monitor's terminal result reaches the root after sign-in view lifetime")
        if V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
            hostReadinessObserver.sharedReadiness) {
            setupLocalHealthReadCount += 1
        }
        precondition(hostReadinessObserver.authoritativeHealthReadCount == 2 &&
                     setupLocalHealthReadCount == 0,
                     "Setup Assistant coalesces with the retry terminal's single shared health read")
        let duplicateTerminalRefresh = retryOwner.settle(
            currentSessionID: provisioningRetrySession, replySessionID: provisioningRetrySession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: false, taskCancelled: false)
        if duplicateTerminalRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: provisioningRetrySession, attemptSequence: retryAttemptSequence)
        }
        precondition(duplicateTerminalRefresh == .notRetry &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession],
                     "a duplicate retry terminal cannot trigger another authoritative read")
        precondition(hostReadinessObserver.readinessRefreshAttemptSequences == [1, 2],
                     "initial sign-in and later retry remain distinct despite sharing a backend session ID")

        let retryReconcileSession = UUID().uuidString
        let retryReconcileSequence: UInt64 = 3
        precondition(V3AuthRetryReadinessReconciliationPolicy.shouldPublish(
            sessionID: retryReconcileSession, expectedSessionID: retryReconcileSession,
            ownsRetry: true, authenticated: true, authenticationActive: false,
            provisioningIncomplete: false, attemptSequence: retryReconcileSequence),
            "a correlated committed retry reconciliation publishes readiness")
        V3AuthReadinessRefreshEvent.post(sessionID: retryReconcileSession,
            attemptSequence: retryReconcileSequence)
        V3AuthReadinessRefreshEvent.post(sessionID: retryReconcileSession,
            attemptSequence: retryReconcileSequence) // the later terminal is the same attempt
        precondition(hostReadinessObserver.readinessRefreshAttemptSequences == [1, 2, 3] &&
                     hostReadinessObserver.authoritativeHealthReadCount == 3,
                     "retry reconciliation and terminal delivery coalesce to one shared read")
        V3AuthReadinessRefreshEvent.post(sessionID: signInSessionID,
            attemptSequence: signInAttemptSequence) // old auth event after newer retry fact
        precondition(hostReadinessObserver.readinessRefreshAttemptSequences == [1, 2, 3],
                     "bounded high-water dedupe rejects old attempt events after a newer fact")
        let reconciliationControls: [(Bool, Bool, Bool, String)] = [
            (false, false, false, retryReconcileSession),
            (true, true, false, retryReconcileSession),
            (true, false, true, retryReconcileSession),
            (true, false, false, UUID().uuidString)
        ]
        for (authenticated, active, incomplete, expectedSession) in reconciliationControls {
            precondition(!V3AuthRetryReadinessReconciliationPolicy.shouldPublish(
                sessionID: retryReconcileSession, expectedSessionID: expectedSession,
                ownsRetry: true, authenticated: authenticated, authenticationActive: active,
                provisioningIncomplete: incomplete, attemptSequence: 4),
                "failed, active, incomplete or replaced reconciliation cannot publish")
        }

        // Cancel can race a commit already accepted by the backend. The
        // authCancel reply carrying the correlated completed result wins.
        let cancelRaceSession = UUID().uuidString
        retryOwner.begin(sessionID: cancelRaceSession)
        precondition(retryOwner.handoffAfterSupersededPollFailure(sessionID: cancelRaceSession))
        let completedDuringCancel = retryOwner.settle(
            currentSessionID: cancelRaceSession, replySessionID: cancelRaceSession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: true, taskCancelled: false)
        if completedDuringCancel == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: cancelRaceSession, attemptSequence: 5)
        }
        precondition(completedDuringCancel == .committed &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                     "a committed successful retry returned by authCancel reaches the root observer")

        let trueCancelSession = UUID().uuidString
        retryOwner.begin(sessionID: trueCancelSession)
        let cancelledRetryRefresh = retryOwner.settle(
            currentSessionID: trueCancelSession, replySessionID: trueCancelSession,
            replyState: "cancelled", authenticated: false,
            cancellationInProgress: true, taskCancelled: false)
        if cancelledRetryRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: trueCancelSession, attemptSequence: 6)
        }
        precondition(cancelledRetryRefresh == .finishedWithoutCommit &&
                     !retryOwner.owns(sessionID: trueCancelSession),
                     "a true cancelled terminal releases ownership without refreshing readiness")
        precondition(hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                     "a true cancellation does not trigger another readiness read")
        let lateAfterCancelSuccess = retryOwner.settle(
            currentSessionID: trueCancelSession, replySessionID: trueCancelSession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: false, taskCancelled: false)
        precondition(lateAfterCancelSuccess == .notRetry,
            "a late success cannot revive an already cancelled retry")

        let cancelledTaskSession = UUID().uuidString
        retryOwner.begin(sessionID: cancelledTaskSession)
        let cancelledTaskRefresh = retryOwner.settle(
            currentSessionID: cancelledTaskSession, replySessionID: cancelledTaskSession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: false, taskCancelled: true)
        if cancelledTaskRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: cancelledTaskSession, attemptSequence: 7)
        }
        precondition(cancelledTaskRefresh == .finishedWithoutCommit &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                     "a task-cancelled terminal cannot publish a retry success")

        let unauthenticatedSession = UUID().uuidString
        retryOwner.begin(sessionID: unauthenticatedSession)
        let unauthenticatedRefresh = retryOwner.settle(
            currentSessionID: unauthenticatedSession, replySessionID: unauthenticatedSession,
            replyState: "completed", authenticated: false,
            cancellationInProgress: false, taskCancelled: false)
        if unauthenticatedRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: unauthenticatedSession, attemptSequence: 8)
        }
        precondition(unauthenticatedRefresh == .finishedWithoutCommit &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                     "a completed reply without authenticated=true is not retry success")

        let replacedSession = UUID().uuidString
        let replacementSession = UUID().uuidString
        retryOwner.begin(sessionID: replacedSession)
        retryOwner.begin(sessionID: replacementSession)
        let staleCompletedRefresh = retryOwner.settle(
            currentSessionID: replacementSession, replySessionID: replacedSession,
            replyState: "completed", authenticated: true,
            cancellationInProgress: false, taskCancelled: false)
        if staleCompletedRefresh == .committed {
            V3AuthReadinessRefreshEvent.post(sessionID: replacedSession, attemptSequence: 9)
        }
        precondition(staleCompletedRefresh == .unmatched &&
                     retryOwner.owns(sessionID: replacementSession) &&
                     hostReadinessObserver.readinessRefreshSessionIDs ==
                        [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                     "a replaced session's late terminal cannot consume or signal the new owner")
        for terminal in ["failed", "authenticatedProvisioningIncomplete", "timedOut", "promptExpired"] {
            let failedSession = UUID().uuidString
            retryOwner.begin(sessionID: failedSession)
            let failureRefresh = retryOwner.settle(
                currentSessionID: failedSession, replySessionID: failedSession,
                replyState: terminal, authenticated: true,
                cancellationInProgress: false, taskCancelled: false)
            if failureRefresh == .committed {
                V3AuthReadinessRefreshEvent.post(sessionID: failedSession, attemptSequence: 10)
            }
            precondition(failureRefresh == .finishedWithoutCommit &&
                         !retryOwner.owns(sessionID: failedSession) &&
                         hostReadinessObserver.readinessRefreshSessionIDs ==
                            [signInSessionID, provisioningRetrySession, retryReconcileSession, cancelRaceSession],
                         "a \(terminal) terminal releases ownership without refreshing readiness")
        }
        let contradictoryNotRequired = V3SignInJITLessGuidancePolicy.resolve(
            osMajor: 26, readiness: .notRequired)!
        precondition(contradictoryNotRequired.readiness == .unknown,
                     "an iOS 26+ view cannot accept a stale not-required fact as ready")
        precondition(V3SignInJITLessGuidancePolicy.resolve(osMajor: 25, readiness: .setupRequired) == nil,
                     "older iOS keeps the legacy sign-in presentation with no JIT-Less stage")
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
        precondition(V3JITLessSetupActionPolicy.action(for: .activeCertificateMissing) == .openCertificates &&
                     V3JITLessSetupActionPolicy.action(for: .activeCertificateRevoked) == .openCertificates,
                     "missing or revoked SideStore active certificates must go to Certificates, not JIT-Less import")
        precondition(V3JITLessSetupActionPolicy.action(for: .setupRequired) == .setUp &&
                     V3JITLessSetupActionPolicy.action(for: .certificateMismatch) == .refreshCertificate,
                     "a valid active certificate routes setup and stale-copy repair to the canonical import flow")
        precondition(V3JITLessHealthRecoveryPolicy.shouldOfferCanonicalSetup(
            for: .unknown, activeCertificateAvailable: false) &&
            !V3JITLessHealthRecoveryPolicy.shouldOfferCanonicalSetup(
                for: .certificateImported, activeCertificateAvailable: false),
            "a failed Health status read preserves the canonical setup route without claiming an active certificate")

        // V3_SNAPSHOT_GATE_V1: the activity, not a shared busy flag, decides
        // what a snapshot request does. The previous gate took a single `loading`
        // boolean that a mutation also set, so a caller awaiting authoritative
        // status could join a mutation; the deep interleavings are executed in
        // v3_snapshot_ownership_harness.swift.
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                          manual: true, requiresConnectionRetry: false) == .performSnapshot)
        precondition(V3SnapshotGate.decide(activity: .snapshot, presentationActive: false,
                                          manual: true, requiresConnectionRetry: false) == .joinSnapshot)
        precondition(V3SnapshotGate.decide(activity: .mutation, presentationActive: false,
                                          manual: true, requiresConnectionRetry: false) == .awaitMutationThenSnapshot,
                     "a mutation must never be mistaken for a snapshot")
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: true,
                                          manual: true, requiresConnectionRetry: false) == .deferForPresentation)
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                          manual: false, requiresConnectionRetry: true) == .doNotObserve)
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                          manual: true, requiresConnectionRetry: true) == .performSnapshot,
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

        // A visible Add Source Cancel closes the form with keyboard either
        // shown or hidden, restores the pre-open value, discards only the local
        // preview, and emits no preview/validate/persist effect.
        for focused in [false, true] {
            let beforeCancel = V3SourceFormState(isPresented: true,
                url: "https://example.invalid/new.json",
                originalURL: "https://example.invalid/saved.json",
                isFocused: focused, hasPreview: true)
            let cancelled = V3SourceEditingPolicy.closeForm(beforeCancel)
            precondition(!cancelled.state.isPresented)
            precondition(cancelled.state.url == beforeCancel.originalURL)
            precondition(!cancelled.state.isFocused)
            precondition(!cancelled.state.hasPreview)
            precondition(cancelled.effects.isEmpty,
                "Cancel must not preview, validate, or persist a source")
        }

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
        let factsTime = Date(timeIntervalSince1970: 100)
        precondition(V3SetupFactRevisionPolicy.mayApply(captured: 4, current: 4))
        precondition(!V3SetupFactRevisionPolicy.mayApply(captured: 4, current: 5),
            "an older health response cannot overwrite facts after invalidation")
        precondition(V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: nil, now: factsTime))
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: false,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: nil, now: factsTime))
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: true, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: nil, now: factsTime),
            "an open Setup Assistant owns its probes without a duplicate root probe")
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: true, loading: false,
            returnToSetupPending: false, lastAttemptAt: nil, now: factsTime))
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: true,
            returnToSetupPending: false, lastAttemptAt: nil, now: factsTime))
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: factsTime, now: factsTime.addingTimeInterval(30)))
        precondition(V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: factsTime,
            now: factsTime.addingTimeInterval(V3SetupFactObservationPolicy.maximumAge)),
            "setup facts refresh after the bounded cache age")
        let recentlyObservedAfterSignIn = factsTime.addingTimeInterval(30)
        precondition(!V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: factsTime,
            now: recentlyObservedAfterSignIn),
            "a recent readiness result would otherwise remain cached after authentication")
        let invalidatedAfterSignIn: Date? = nil
        precondition(V3SetupFactObservationPolicy.shouldObserve(connected: true,
            setupPresented: false, operationPresented: false, loading: false,
            returnToSetupPending: false, lastAttemptAt: invalidatedAfterSignIn,
            now: recentlyObservedAfterSignIn),
            "sign-in invalidation makes the newly provisioned active certificate observable immediately")
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

        let sourceRemoval = CombinedFailure(operation: "source", stage: .source, code: .failed,
            id: UUID().uuidString, safeCause: .sourceRemoveFailed, sourceStep: .catalogRead)
        let sourceRemovalIssue = V3UserFacingIssue.make(sourceRemoval)
        precondition(sourceRemovalIssue.primaryAction == .reloadSources,
                     "a remove failure must reconcile Sources rather than repeat source downloading")
        precondition(sourceRemovalIssue.primaryAction.title == "Reload Sources")
        precondition(sourceRemovalIssue.whatToDo.contains("confirm whether the source is gone"))

        let sourceRemovalBusy = CombinedFailure(operation: "source", stage: .source, code: .busy,
            id: UUID().uuidString, retryable: true, safeCause: .sourceRemoveBusy)
        let sourceRemovalBusyIssue = V3UserFacingIssue.make(sourceRemovalBusy)
        precondition(sourceRemovalBusyIssue.primaryAction == .reloadSources,
                     "a rejected remove must reload the source state rather than fetch manifests")
        precondition(sourceRemovalBusyIssue.whatToDo.contains("Wait for the current SideStore request"))

        // A networking failure opens connection settings, routed by typed stage.
        let networkIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "status", stage: .network, code: .failed,
                            id: UUID().uuidString, safeCause: .networkConnectionLost))
        precondition(networkIssue.recoveryDestination == "connection")
        precondition(networkIssue.primaryAction == .openConnectionCheck,
                     "a connection failure opens settings instead of claiming the mutation was retried")
        let signingNetworkFailure = CombinedFailure(operation: "install", stage: .signing,
            code: .failed, id: UUID().uuidString, retryable: true,
            safeCause: .signingNetworkConnectionLost)
        let signingNetworkIssue = V3UserFacingIssue.make(signingNetworkFailure)
        precondition(signingNetworkIssue.recoveryDestination == "connection" &&
                     signingNetworkIssue.primaryAction == .openConnectionCheck &&
                     signingNetworkIssue.whatToDo.contains("current connection may still be healthy"),
            "a typed provisioning network timeout directs to connection recovery, not Certificates")
        precondition(V3OperationFailureDetails(signingNetworkFailure).recoveryDestination == "connection",
            "the global issue router and operation sheet must agree on the typed network cause")
        for stage: CombinedFailure.Stage in [.serviceReadiness, .xpcConnection, .rsdDiscovery,
                                               .refreshVerification, .replyEncoding] {
            let timeout = CombinedFailure(operation: "install", stage: stage,
                code: .timedOut, id: UUID().uuidString)
            precondition(!timeout.safeMessage.contains(stage.rawValue),
                "user-facing timeout copy must not expose internal stage \(stage.rawValue)")
        }
        let signingTimeout = CombinedFailure(operation: "install", stage: .signing,
            code: .timedOut, id: UUID().uuidString, retryable: true,
            safeCause: .signingNetworkTimedOut)
        precondition(signingTimeout.safeMessage.contains("install the app") &&
                     signingTimeout.safeMessage.contains("signing the app"),
            "timeout copy uses plain-language context rather than the stage token")
        let anisetteRequestID = UUID().uuidString
        let anisetteNetworkFailure = V3AnisetteSyncFailurePolicy.failure(
            URLError(.networkConnectionLost), id: anisetteRequestID)
        precondition(anisetteNetworkFailure.operation == "anisetteSync" &&
                     anisetteNetworkFailure.stage == .network &&
                     anisetteNetworkFailure.safeCause == .networkConnectionLost,
                     "typed URL transport loss retains the Anisette operation and network cause")
        let decodedAnisetteNetworkFailure = plistRoundTrip(anisetteNetworkFailure)
        let anisetteGuidance = decodedAnisetteNetworkFailure.flatMap(V3AnisetteFailureGuidance.message)
        precondition(anisetteGuidance?.contains("configured Anisette server") == true &&
                     decodedAnisetteNetworkFailure?.operation == "anisetteSync" &&
                     decodedAnisetteNetworkFailure?.stage == .network &&
                     decodedAnisetteNetworkFailure?.code == .failed &&
                     decodedAnisetteNetworkFailure?.retryable == true &&
                     decodedAnisetteNetworkFailure?.safeCause == .networkConnectionLost &&
                     anisetteGuidance?.localizedCaseInsensitiveContains("LocalDevVPN") == false,
                     "the typed Anisette network failure survives the actual structured wire contract")
        let anisetteUnavailable = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "AnisetteServersManager", code: 503), id: UUID().uuidString)
        let unavailableRoundTrip = plistRoundTrip(anisetteUnavailable)
        precondition(anisetteUnavailable.operation == "anisetteSync" &&
                     anisetteUnavailable.stage == .command &&
                     anisetteUnavailable.code == .failed &&
                     anisetteUnavailable.safeCause == .anisetteServerUnavailable &&
                     anisetteUnavailable.retryable == true &&
                     unavailableRoundTrip?.operation == "anisetteSync" &&
                     unavailableRoundTrip?.stage == .command &&
                     unavailableRoundTrip?.code == .failed &&
                     unavailableRoundTrip?.safeCause == .anisetteServerUnavailable &&
                     unavailableRoundTrip?.retryable == true &&
                     anisetteUnavailable.safeMessage.contains("temporarily unavailable") &&
                     anisetteUnavailable.recovery.contains("choose another configured Anisette server") &&
                     V3AnisetteFailureGuidance.message(anisetteUnavailable) == nil,
                     "an explicit HTTP 5xx is a server outage, not a network-path or LocalDevVPN failure")
        let anisetteUnavailableIssue = V3UserFacingIssue.make(anisetteUnavailable)
        precondition(anisetteUnavailableIssue.recoveryDestination == nil &&
                     anisetteUnavailableIssue.whatToDo.contains("Anisette server") &&
                     !anisetteUnavailableIssue.whatToDo.localizedCaseInsensitiveContains("LocalDevVPN"),
                     "a server outage does not route to or blame the device tunnel")
        let anisetteRejected = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "AnisetteServersManager", code: 404), id: UUID().uuidString)
        let rejectedRoundTrip = plistRoundTrip(anisetteRejected)
        precondition(anisetteRejected.stage == .command &&
                     anisetteRejected.safeCause == .anisetteServerRejected &&
                     anisetteRejected.retryable == false &&
                     rejectedRoundTrip?.operation == "anisetteSync" &&
                     rejectedRoundTrip?.stage == .command &&
                     rejectedRoundTrip?.code == .failed &&
                     rejectedRoundTrip?.safeCause == .anisetteServerRejected &&
                     rejectedRoundTrip?.retryable == false &&
                     anisetteRejected.safeMessage.contains("unsuccessful response") &&
                     anisetteRejected.recovery.contains("server address") &&
                     V3AnisetteFailureGuidance.message(anisetteRejected) == nil,
                     "an HTTP client response remains distinct from transport failure")
        let anisetteRequestTimeout = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "AnisetteServersManager", code: 408), id: UUID().uuidString)
        let timeoutRoundTrip = plistRoundTrip(anisetteRequestTimeout)
        precondition(anisetteRequestTimeout.stage == .command &&
                     anisetteRequestTimeout.code == .failed &&
                     anisetteRequestTimeout.safeCause == .anisetteRequestTimedOut &&
                     anisetteRequestTimeout.retryable == true &&
                     timeoutRoundTrip?.operation == "anisetteSync" &&
                     timeoutRoundTrip?.stage == .command &&
                     timeoutRoundTrip?.code == .failed &&
                     timeoutRoundTrip?.safeCause == .anisetteRequestTimedOut &&
                     timeoutRoundTrip?.retryable == true &&
                     anisetteRequestTimeout.recovery.contains("Retry once") &&
                     !anisetteRequestTimeout.recovery.localizedCaseInsensitiveContains("LocalDevVPN"),
                     "HTTP 408 survives a real plist round-trip with bounded retry guidance")
        let anisetteRateLimit = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "AnisetteServersManager", code: 429), id: UUID().uuidString)
        let rateLimitRoundTrip = plistRoundTrip(anisetteRateLimit)
        precondition(anisetteRateLimit.stage == .command &&
                     anisetteRateLimit.code == .busy &&
                     anisetteRateLimit.safeCause == .anisetteRateLimited &&
                     anisetteRateLimit.retryable == true &&
                     rateLimitRoundTrip?.operation == "anisetteSync" &&
                     rateLimitRoundTrip?.stage == .command &&
                     rateLimitRoundTrip?.code == .busy &&
                     rateLimitRoundTrip?.safeCause == .anisetteRateLimited &&
                     rateLimitRoundTrip?.retryable == true &&
                     anisetteRateLimit.safeMessage.contains("rate-limiting") &&
                     anisetteRateLimit.recovery.contains("Wait before retrying once") &&
                     !anisetteRateLimit.recovery.localizedCaseInsensitiveContains("LocalDevVPN"),
                     "HTTP 429 survives a real plist round-trip and asks the user to wait before one retry")
        let timeoutIssue = V3UserFacingIssue.make(anisetteRequestTimeout)
        let rateLimitIssue = V3UserFacingIssue.make(anisetteRateLimit)
        precondition(timeoutIssue.recoveryDestination == nil &&
                     timeoutIssue.retryDisposition == .allowed &&
                     timeoutIssue.whatToDo.contains("Retry once") &&
                     rateLimitIssue.recoveryDestination == nil &&
                     rateLimitIssue.retryDisposition == .allowed &&
                     rateLimitIssue.whatToDo.contains("Wait before retrying once"),
                     "Anisette transient statuses offer user-directed, bounded retry without connection routing")
        let anisetteInvalidResponse = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "AnisetteServersManager", code: -1), id: UUID().uuidString)
        let invalidResponseRoundTrip = plistRoundTrip(anisetteInvalidResponse)
        precondition(anisetteInvalidResponse.stage == .command &&
                     anisetteInvalidResponse.code == .invalidResponse &&
                     anisetteInvalidResponse.safeCause == .anisetteInvalidResponse &&
                     anisetteInvalidResponse.retryable == nil &&
                     invalidResponseRoundTrip?.operation == "anisetteSync" &&
                     invalidResponseRoundTrip?.stage == .command &&
                     invalidResponseRoundTrip?.code == .invalidResponse &&
                     invalidResponseRoundTrip?.safeCause == .anisetteInvalidResponse &&
                     invalidResponseRoundTrip?.retryable == nil &&
                     anisetteInvalidResponse.safeMessage.contains("could not read") &&
                     anisetteInvalidResponse.recovery.contains("could not be read") &&
                     V3AnisetteFailureGuidance.message(anisetteInvalidResponse) == nil,
                     "the pinned Anisette invalid-response error stays separate from network failure")
        let anisetteInvalidResponseIssue = V3UserFacingIssue.make(anisetteInvalidResponse)
        precondition(anisetteInvalidResponseIssue.recoveryDestination == nil &&
                     anisetteInvalidResponseIssue.whatHappened.contains("could not read") &&
                     !anisetteInvalidResponseIssue.whatToDo.localizedCaseInsensitiveContains("LocalDevVPN"),
                     "an invalid server response keeps its own guidance rather than device connection advice")
        let anisetteUnknown = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "PrivateUnknownDomain", code: 77), id: UUID().uuidString)
        let unknownRoundTrip = plistRoundTrip(anisetteUnknown)
        precondition(anisetteUnknown.stage == .command &&
                     anisetteUnknown.safeCause == .anisetteUnknownFailure &&
                     anisetteUnknown.retryable == nil &&
                     unknownRoundTrip?.operation == "anisetteSync" &&
                     unknownRoundTrip?.stage == .command &&
                     unknownRoundTrip?.code == .failed &&
                     unknownRoundTrip?.safeCause == .anisetteUnknownFailure &&
                     unknownRoundTrip?.retryable == nil &&
                     anisetteUnknown.safeMessage.contains("unknown reason") &&
                     anisetteUnknown.recovery.contains("exact Anisette synchronization cause") &&
                     V3AnisetteFailureGuidance.message(anisetteUnknown) == nil,
                     "unclassified Anisette errors retain honest unknown wording")
        let anisetteNonTransportURLError = V3AnisetteSyncFailurePolicy.failure(
            URLError(.badURL), id: UUID().uuidString)
        precondition(anisetteNonTransportURLError.stage == .command &&
                     anisetteNonTransportURLError.safeCause == .anisetteUnknownFailure,
                     "a URL error that is not transport evidence is not labeled a network outage")
        let anisetteUnrelatedNumericCode = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: "PrivateUnknownDomain", code: NSURLErrorNetworkConnectionLost),
            id: UUID().uuidString)
        precondition(anisetteUnrelatedNumericCode.safeCause == .anisetteUnknownFailure,
                     "a familiar numeric code in an unrelated domain does not prove network loss")
        let anisetteUnknownURLCode = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: NSURLErrorDomain, code: -9999), id: UUID().uuidString)
        let unknownURLCodeRoundTrip = plistRoundTrip(anisetteUnknownURLCode)
        precondition(anisetteUnknownURLCode.operation == "anisetteSync" &&
                     anisetteUnknownURLCode.stage == .command &&
                     anisetteUnknownURLCode.code == .failed &&
                     anisetteUnknownURLCode.safeCause == .anisetteUnknownFailure &&
                     anisetteUnknownURLCode.retryable == nil &&
                     unknownURLCodeRoundTrip?.operation == "anisetteSync" &&
                     unknownURLCodeRoundTrip?.stage == .command &&
                     unknownURLCodeRoundTrip?.code == .failed &&
                     unknownURLCodeRoundTrip?.safeCause == .anisetteUnknownFailure &&
                     unknownURLCodeRoundTrip?.retryable == nil &&
                     unknownURLCodeRoundTrip?.underlyingDomain == NSURLErrorDomain &&
                     unknownURLCodeRoundTrip?.underlyingCode == -9999,
                     "unknown NSURLErrorDomain codes remain unknown through the production classifier and plist wire")
        let anisetteCancelled = V3AnisetteSyncFailurePolicy.failure(
            CancellationError(), id: UUID().uuidString)
        precondition(anisetteCancelled.code == .cancelled && anisetteCancelled.safeCause == nil &&
                     V3AnisetteFailureGuidance.message(anisetteCancelled) == nil,
                     "cancellation is not mislabeled as Anisette networking failure")
        let anisetteNSErrorCancelled = V3AnisetteSyncFailurePolicy.failure(
            NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled), id: UUID().uuidString)
        let cancelledRoundTrip = plistRoundTrip(anisetteNSErrorCancelled)
        precondition(anisetteNSErrorCancelled.operation == "anisetteSync" &&
                     anisetteNSErrorCancelled.stage == .command &&
                     anisetteNSErrorCancelled.code == .cancelled &&
                     anisetteNSErrorCancelled.retryable == false &&
                     anisetteNSErrorCancelled.safeCause == nil &&
                     cancelledRoundTrip?.operation == "anisetteSync" &&
                     cancelledRoundTrip?.stage == .command &&
                     cancelledRoundTrip?.code == .cancelled &&
                     cancelledRoundTrip?.retryable == false &&
                     cancelledRoundTrip?.safeCause == nil &&
                     cancelledRoundTrip?.underlyingDomain == "NSURLErrorDomain" &&
                     cancelledRoundTrip?.underlyingCode == NSURLErrorCancelled &&
                     V3AnisetteFailureGuidance.message(anisetteNSErrorCancelled) == nil,
                     "raw NSError URL cancellation remains cancellation through the real plist boundary")
        let anisetteURLErrorCancelled = V3AnisetteSyncFailurePolicy.failure(
            URLError(.cancelled), id: UUID().uuidString)
        let urlErrorCancelledRoundTrip = plistRoundTrip(anisetteURLErrorCancelled)
        precondition(anisetteURLErrorCancelled.operation == "anisetteSync" &&
                     anisetteURLErrorCancelled.stage == .command &&
                     anisetteURLErrorCancelled.code == .cancelled &&
                     anisetteURLErrorCancelled.retryable == false &&
                     anisetteURLErrorCancelled.safeCause == nil &&
                     anisetteURLErrorCancelled.underlyingDomain == "NSURLErrorDomain" &&
                     anisetteURLErrorCancelled.underlyingCode == NSURLErrorCancelled &&
                     urlErrorCancelledRoundTrip?.operation == "anisetteSync" &&
                     urlErrorCancelledRoundTrip?.stage == .command &&
                     urlErrorCancelledRoundTrip?.code == .cancelled &&
                     urlErrorCancelledRoundTrip?.retryable == false &&
                     urlErrorCancelledRoundTrip?.safeCause == nil &&
                     urlErrorCancelledRoundTrip?.underlyingDomain == "NSURLErrorDomain" &&
                     urlErrorCancelledRoundTrip?.underlyingCode == NSURLErrorCancelled,
                     "typed URLError cancellation preserves the same terminal metadata through plist")
        let ordinaryRefreshFailure = CombinedFailure(operation: "refresh", stage: .network,
            code: .failed, id: UUID().uuidString, retryable: true, safeCause: .networkConnectionLost)
        precondition(V3AnisetteFailureGuidance.message(ordinaryRefreshFailure) == nil,
            "ordinary device refresh failures retain their separate connection guidance")
        let anisetteNetworkIssue = V3UserFacingIssue.make(
            anisetteNetworkFailure)
        precondition(anisetteNetworkIssue.recoveryDestination == nil &&
                     anisetteNetworkIssue.whatToDo.contains("Anisette server") &&
                     !anisetteNetworkIssue.whatToDo.localizedCaseInsensitiveContains("LocalDevVPN"),
            "remote Anisette failures do not blame the device tunnel")
        // A connection-stage failure that is provably not retryable is inspected
        // rather than blindly retried.
        let blockedNetworkIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "status", stage: .network, code: .failed,
                            id: UUID().uuidString, retryable: false))
        precondition(blockedNetworkIssue.recoveryDestination == "connection")
        precondition(blockedNetworkIssue.primaryAction == .openConnectionCheck)
        precondition(blockedNetworkIssue.retryDisposition == .blocked)

        for stage: CombinedFailure.Stage in [.hostContainer, .storagePreparation, .bookmarkCreation] {
            let startupFailure = CombinedFailure(operation: "snapshot", stage: stage,
                id: UUID().uuidString, retryable: false)
            precondition(!startupFailure.recovery.localizedCaseInsensitiveContains("retry connection"),
                         "\(stage.rawValue) recovery must not offer a connection retry without a matching action")
            precondition(startupFailure.recovery.lowercased().contains("copy diagnostics"),
                         "\(stage.rawValue) recovery must provide a useful diagnostics route")
        }
        let bookmarkFailure = CombinedFailure(operation: "snapshot", stage: .bookmarkCreation,
            id: UUID().uuidString, retryable: false)
        precondition(bookmarkFailure.recovery.contains("internal shared SideStore folder") &&
                     !bookmarkFailure.recovery.contains("Choose the file or folder"),
                     "internal App Group bookmark failure must not direct users to an unrelated picker")
        let extensionFailure = CombinedFailure(operation: "snapshot", stage: .extensionDiscovery,
            id: UUID().uuidString, retryable: false)
        let extensionIssue = V3UserFacingIssue.make(extensionFailure)
        precondition(extensionIssue.recoveryDestination != "connection" &&
                     extensionFailure.recovery.contains("embedded LiveProcess extension"),
                     "a missing LiveProcess extension is a package issue, not a connection failure")

        // A certificate failure must never be described as a connection problem.
        let certificateIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "refresh", stage: .signing, code: .failed,
                            id: UUID().uuidString, safeCause: .certificateUnavailable))
        precondition(certificateIssue.recoveryDestination == "certificates")
        precondition(certificateIssue.primaryAction == .openCertificates)
        let keychainSignOutIssue = V3UserFacingIssue.make(
            CombinedFailure(operation: "signOut", stage: .authentication, code: .failed,
                id: UUID().uuidString, retryable: true, safeCause: .keychainSignOutFailed))
        precondition(keychainSignOutIssue.recoveryDestination == "signIn" &&
                     keychainSignOutIssue.whatHappened.contains("could not confirm removal") &&
                     keychainSignOutIssue.whatToDo.contains("Unlock the iPhone"),
            "a failed Keychain deletion cannot be reported as a successful sign-out")

        let untyped = NSError(domain: "LiveContainer.Service", code: 4865)
        precondition(!V3FailureGuidance.message(untyped).contains("4865"),
                     "a numeric error code must never be shown as guidance")
        precondition(!V3FailureGuidance.message(untyped).contains("LiveContainer.Service"),
                     "the raw error domain must not be shown as guidance")
        precondition(V3FailureGuidance.diagnostics(untyped).contains("underlying_domain=redacted") &&
                     V3FailureGuidance.diagnostics(untyped).contains("underlying_code=unknown") &&
                     !V3FailureGuidance.diagnostics(untyped).contains("4865"),
                     "an unrecognized NSError domain redacts its numeric code from diagnostics")
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
