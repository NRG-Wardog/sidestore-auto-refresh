import Foundation

@main
struct OperationRetryFailureHarness {
    static func main() {
        precondition(V3OperationRetryButtonPolicy.title(state: "cancelled", retryDisposition: .unknown) == "Retry" &&
                     V3OperationRetryButtonPolicy.title(state: "failed", retryDisposition: .unknown) ==
                        "Retry (retryability unknown)",
            "a confirmed cancellation has a safe Retry label while an unknown failure stays explicit")
        let userCancelled = V3OperationCancellationPresentationPolicy.resolve(userRequested: true)
        precondition(userCancelled.message == "The operation was cancelled." &&
                     userCancelled.whatToDo.contains("Retry when you are ready"),
            "the user's Cancel action must not be described as an accidental backend cancellation")
        let backendCancelled = V3OperationCancellationPresentationPolicy.resolve(userRequested: false)
        precondition(backendCancelled.whatToDo.contains("Retry if you still need to complete"),
            "an independent backend cancellation offers a truthful recovery")
        let firstSession = UUID().uuidString
        let secondSession = UUID().uuidString
        var registry = V3OperationMutationRegistry()
        precondition(registry.begin(firstSession) == .started)

        let signingFailure = CombinedFailure(operation: "install", stage: .signing,
            id: firstSession, underlying: NSError(domain: "redacted", code: -1005),
            safeCause: .unknownSigningCause, sourceStep: .provisioningProfileFetch)
        var context = V3OperationRetryContext()
        context.recordPipelineFailure(signingFailure)
        precondition(context.currentFailure?.stage == "signing")
        precondition(context.retryDisposition == .unknown,
                     "unknown signing retryability must be shown honestly")
        precondition(V3OperationRetrySafetyPolicy.canRetry(backendSettled: true, outcomeUnknown: false))
        precondition(!V3OperationRetrySafetyPolicy.canRetry(backendSettled: false, outcomeUnknown: true),
                     "an uncertain native result must block a new mutation")
        precondition(V3OperationRetrySafetyPolicy.disposition(state: "completed",
            backendSettled: true, outcomeUnknown: false) == .alreadyCompleted,
            "a lost terminal poll must not let Retry repeat an operation that completed")
        precondition(V3OperationRetrySafetyPolicy.disposition(state: "failed",
            backendSettled: true, outcomeUnknown: false) == .retry)
        precondition(V3OperationRetrySafetyPolicy.disposition(state: "working",
            backendSettled: true, outcomeUnknown: false) == .outcomeUnknown)

        precondition(registry.finish(firstSession))
        context.beginRetry()
        precondition(registry.begin(secondSession) == .started,
                     "retry did not acquire a fresh backend mutation session")
        context.operationStarted()
        let secondSigningFailure = CombinedFailure(operation: "install", stage: .signing,
            id: secondSession, underlying: NSError(domain: "redacted", code: -1005),
            safeCause: .unknownSigningCause, sourceStep: .provisioningProfileFetch)
        context.recordPipelineFailure(secondSigningFailure)
        precondition(context.currentFailure?.stage == "signing" &&
                     context.currentFailure?.correlation == secondSession,
                     "the second pipeline attempt lost its signing stage or correlation")
        precondition(context.previousFailure == nil,
                     "a normally started retry retained stale failure presentation")
        precondition(registry.finish(secondSession))

        let portalFailure = CombinedFailure(operation: "install", stage: .signing,
            id: UUID().uuidString, safeCause: .developerPortalRejectedRequest,
            sourceStep: .provisioningProfileFetch)
        let portalDetails = V3OperationFailureDetails(portalFailure)
        precondition(portalDetails.whatHappened.contains("Developer Portal rejected"))
        precondition(portalDetails.recoveryDestination == "certificates",
                     "a provisioning failure must not send the user to credentials")
        precondition(portalDetails.recommendedAction.contains("Certificates"))
        precondition(portalDetails.retryDisposition == .unknown)

        let fileFailure = CombinedFailure(operation: "install", stage: .filePreparation,
            code: .invalidPackage, id: UUID().uuidString, retryable: false)
        let fileDetails = V3OperationFailureDetails(fileFailure)
        precondition(fileDetails.recoveryDestination == "ipa")
        precondition(fileDetails.retryDisposition == .blocked,
                     "an invalid IPA must offer file selection, not blind Retry")

        let signingNetwork = V3OperationFailureDetails(CombinedFailure(
            operation: "install", stage: .signing, id: UUID().uuidString,
            retryable: true, safeCause: .signingNetworkConnectionLost,
            sourceStep: .provisioningProfileFetch))
        precondition(signingNetwork.recoveryDestination == "connection")
        precondition(signingNetwork.retryDisposition == .allowed)

        let encodingFailure = CombinedFailure(operation: "catalog", stage: .catalog,
            id: UUID().uuidString, retryable: false, safeCause: .responseEncodingFailed)
        let encodingDetails = V3OperationFailureDetails(encodingFailure)
        precondition(encodingDetails.retryDisposition == .blocked)
        precondition(encodingDetails.recommendedAction.contains("Copy Diagnostics"))
        precondition(!encodingDetails.recommendedAction.contains("signing"))
        precondition(!encodingFailure.recovery.contains("Reload the request"))
        let sourceEncodingFailure = CombinedFailure(operation: "source", stage: .source,
            code: .invalidResponse, id: UUID().uuidString, retryable: false,
            safeCause: .responseEncodingFailed)
        let sourceEncodingDetails = V3OperationFailureDetails(sourceEncodingFailure)
        precondition(sourceEncodingDetails.retryDisposition == .blocked &&
                     sourceEncodingDetails.recommendedAction.contains("Repeating the same request will not help") &&
                     !sourceEncodingDetails.recommendedAction.contains("review the source request"),
            "source response encoding failure keeps deterministic service-defect recovery copy")
        let sourceTooLargeFailure = CombinedFailure(operation: "source", stage: .source,
            code: .invalidResponse, id: UUID().uuidString, retryable: false,
            safeCause: .responseTooLarge)
        precondition(V3OperationFailureDetails(sourceTooLargeFailure).recommendedAction
            .contains("Repeating the same request will fail again"),
            "source response-size failure does not fall through to generic URL guidance")
        let encodingPromptFailure = V3OperationPromptFailureDetails(encodingFailure)
        precondition(encodingPromptFailure.blocksResubmission &&
                     encodingPromptFailure.failure.whatHappened == encodingFailure.safeMessage &&
                     encodingPromptFailure.failure.technical.contains("responseEncodingFailed") &&
                     encodingPromptFailure.failure.recommendedAction.contains("Repeating the same request will not help"),
                     "an operation prompt must retain typed deterministic failure details and block duplicate submission")
        let retryablePromptFailure = V3OperationPromptFailureDetails(CombinedFailure(
            operation: "install", stage: .network, id: UUID().uuidString,
            retryable: true, safeCause: .networkConnectionLost))
        precondition(!retryablePromptFailure.blocksResubmission &&
                     retryablePromptFailure.failure.whatHappened.contains("connection was lost"),
                     "only a typed retryable response failure leaves the answer available")

        let oversizedFailure = CombinedFailure(operation: "catalog", stage: .catalog,
            id: UUID().uuidString, retryable: false, safeCause: .responseTooLarge)
        let oversizedDetails = V3OperationFailureDetails(oversizedFailure)
        precondition(oversizedDetails.retryDisposition == .blocked)
        precondition(oversizedDetails.recommendedAction.contains("transfer limit"))
        precondition(!oversizedDetails.recommendedAction.contains("signing"))

        let staleRefresh = CombinedFailure(operation: "refresh", stage: .command,
            code: .staleResult, id: UUID().uuidString, retryable: false,
            safeCause: .staleRefreshAttempt)
        let staleRefreshDetails = V3OperationFailureDetails(staleRefresh)
        precondition(staleRefreshDetails.whatHappened.contains("expired scheduler run") &&
                     staleRefreshDetails.whatHappened.contains("was not started"))
        precondition(staleRefreshDetails.whatToDo.contains("start a new refresh") &&
                     staleRefreshDetails.whatToDo.contains("did not reach SideStore or the device"))
        precondition(staleRefreshDetails.retryDisposition == .blocked &&
                     staleRefreshDetails.recoveryDestination == nil,
                     "a stale pre-dispatch request must not suggest device reconciliation or blind retry")
        precondition(staleRefreshDetails.recommendedAction.contains("was not started"))

        let removedCatalogSource = V3OperationFailureDetails(CombinedFailure(
            operation: "catalog", stage: .catalog, code: .unavailable,
            id: UUID().uuidString, retryable: false, safeCause: .catalogSourceUnavailable))
        precondition(removedCatalogSource.whatHappened.contains("no longer in the SideStore source list"))
        precondition(removedCatalogSource.whatToDo.contains("Return to Sources") &&
                     !removedCatalogSource.whatHappened.contains("service is not ready"),
                     "a removed source must not be mislabeled as a starting service")
        let sourceAddFailure = V3OperationFailureDetails(CombinedFailure(
            operation: "source", stage: .source, code: .invalidResponse,
            id: UUID().uuidString, retryable: false, safeCause: .sourceInvalidManifest))
        precondition(sourceAddFailure.whatHappened.contains("valid source") &&
                     sourceAddFailure.recoveryDestination == "sources" &&
                     sourceAddFailure.recoveryActionTitle == "Open Sources" &&
                     sourceAddFailure.recommendedAction.contains("manifest") &&
                     !sourceAddFailure.recommendedAction.contains("signing"),
                     "a failed source add must retain source-specific recovery instead of generic signing advice")

        // Separately model opStart returning busy before the second pipeline begins.
        let blockedSession = UUID().uuidString
        let startFailureSession = UUID().uuidString
        var blockedRegistry = V3OperationMutationRegistry()
        precondition(blockedRegistry.begin(blockedSession) == .started)
        var startContext = V3OperationRetryContext()
        startContext.recordPipelineFailure(signingFailure)
        startContext.beginRetry()
        precondition(blockedRegistry.begin(startFailureSession) == .busy)
        startContext.recordStartFailure(CombinedFailure(operation: "install", stage: .command,
            code: .busy, id: startFailureSession, retryable: true))
        precondition(startContext.retryCouldNotStart)
        precondition(startContext.currentFailure?.stage == "command")
        precondition(startContext.whatHappened.contains("retry could not start"))
        precondition(startContext.whatHappened.contains("sign"))
        precondition(startContext.technicalDetails.contains("retry_start_failure:"))

        var deterministicRetryStart = V3OperationRetryContext()
        deterministicRetryStart.recordStartFailure(encodingFailure)
        precondition(deterministicRetryStart.whatToDo.contains("same request will not help"),
                     "retry-start copy must preserve deterministic response-encoding guidance")
        precondition(!deterministicRetryStart.whatToDo.lowercased().contains("retry could not start") &&
                     deterministicRetryStart.whatToDo.contains("operation could not start"),
                     "a first opStart failure must not be described as a failed retry")
        var busyStart = V3OperationRetryContext()
        busyStart.recordStartFailure(CombinedFailure(operation: "install", stage: .command,
            code: .busy, id: UUID().uuidString, retryable: true,
            safeCause: .operationInProgress))
        precondition(busyStart.whatHappened.contains("another SideStore operation is still active") &&
                     busyStart.whatToDo.contains("Wait for the active SideStore operation") &&
                     busyStart.retryDisposition == .prerequisite,
                     "a backend-rejected start must direct users to contention recovery without offering Retry")
        precondition(startContext.technicalDetails.contains("previous_attempt_failure:"))
        print("V3_RETRY_SIGNING_STAGE_AND_START_FAILURE_PASS")
    }
}
