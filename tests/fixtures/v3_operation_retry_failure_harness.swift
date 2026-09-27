import Foundation

@main
struct OperationRetryFailureHarness {
    static func main() {
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
        precondition(startContext.technicalDetails.contains("previous_attempt_failure:"))
        print("V3_RETRY_SIGNING_STAGE_AND_START_FAILURE_PASS")
    }
}
