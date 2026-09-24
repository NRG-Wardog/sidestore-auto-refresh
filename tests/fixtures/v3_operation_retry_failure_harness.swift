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
        precondition(signingNetwork.recoveryDestination == "setup")
        precondition(signingNetwork.retryDisposition == .allowed)

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
        precondition(startContext.technicalDetails.contains("previous_attempt_failure:"))
        print("V3_RETRY_SIGNING_STAGE_AND_START_FAILURE_PASS")
    }
}
