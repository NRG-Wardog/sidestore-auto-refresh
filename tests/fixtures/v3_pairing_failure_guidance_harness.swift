import Foundation

enum OperationError: Error {
    case invalidPairingFile(String?)
    case unrelated
}

enum MinimuxerError: Error {
    case invalidPairing(protocol: String, reason: String)
    case unrelated
}

struct MinimuxerServiceError: Error { let error: Error }
struct ALTWrappedError: Error { let wrappedError: Error }

@main
struct PairingFailureGuidanceHarness {
    static func main() {
        let id = UUID().uuidString
        let privateReason = "PAIRING_PRIVATE_DETAIL"
        let inputs: [Error] = [
            OperationError.invalidPairingFile(privateReason),
            ALTWrappedError(wrappedError: OperationError.invalidPairingFile(privateReason)),
            MinimuxerServiceError(error: MinimuxerError.invalidPairing(
                protocol: "lockdown", reason: privateReason)),
            NSError(domain: "ALTWrappedError", code: 9, userInfo: [
                NSUnderlyingErrorKey: OperationError.invalidPairingFile(privateReason)
            ])
        ]

        for input in inputs {
            let tagged = V3HeadlessPairingFailure.tagIfInvalidPairing(input)
            let failure = CombinedFailure.capture(tagged, operation: "refresh",
                stage: .command, id: id)
            precondition(failure.stage == .pairing && failure.safeCause == .invalidPairingFile &&
                         failure.retryable == false && failure.correlationID == id,
                         "typed operation, service, and NSError-wrapped pairing failures must retain their pairing semantics")
            precondition(failure.message == "The existing pairing file was rejected by the device." &&
                         failure.recovery.contains("Open Pairing File"),
                         "pairing guidance must direct users to replace the saved pairing file")
            precondition(!failure.message.contains(privateReason) &&
                         !failure.technicalDetails.contains(privateReason),
                         "pairing parse details must not enter user-copyable diagnostics")
            let issue = V3OperationFailureDetails(failure)
            precondition(issue.recoveryDestination == "pairing" &&
                         issue.recoveryActionTitle == "Open Pairing File" &&
                         issue.recommendedAction.contains("replace the saved pairing record"),
                         "the user-facing failure action must be the Pairing File repair")
        }

        let unrelated = OperationError.unrelated
        let generic = CombinedFailure.capture(
            V3HeadlessPairingFailure.tagIfInvalidPairing(unrelated),
            operation: "refresh", stage: .command, id: id)
        precondition(generic.safeCause != .invalidPairingFile && generic.stage == .command,
                     "unrelated errors must not be mislabeled as pairing failures")
        print("V3_PAIRING_FAILURE_GUIDANCE_PASS")
    }
}
