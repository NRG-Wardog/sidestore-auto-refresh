import Foundation

@main
struct PairingFailureGuidanceHarness {
    static func main() {
        let correlationID = UUID().uuidString
        let underlying = NSError(domain: "MinimuxerError", code: 7)
        guard let failure = V3PairingFailureClassificationPolicy.classify(
            typedInvalidPairing: true, operation: "refresh", id: correlationID,
            underlying: underlying) else {
            fatalError("typed invalid pairing was not classified")
        }
        precondition(failure.stage == .pairing && failure.safeCause == .invalidPairingFile &&
                     failure.retryable == false && failure.correlationID == correlationID &&
                     failure.underlyingDomain == "MinimuxerError" && failure.underlyingCode == 7,
                     "invalid pairing classification must preserve its typed category and safe native identity")
        precondition(failure.message == "The existing pairing file was rejected by the device." &&
                     failure.recovery.contains("Open Pairing File"),
                     "invalid pairing guidance must direct the user to replace the pairing file")
        let issue = V3OperationFailureDetails(failure)
        precondition(issue.recoveryDestination == "pairing" &&
                     issue.recoveryActionTitle == "Open Pairing File" &&
                     issue.recommendedAction.contains("replace the saved pairing record"),
                     "operation failure presentation must expose the correct pairing repair action")
        precondition(V3PairingFailureClassificationPolicy.classify(
            typedInvalidPairing: false, operation: "refresh", id: correlationID,
            underlying: underlying) == nil,
            "an untyped Minimuxer error must not be mislabeled as invalid pairing")
        let unrelated = CombinedFailure.capture(underlying, operation: "refresh",
            stage: .command, id: correlationID)
        precondition(unrelated.safeCause != .invalidPairingFile && unrelated.stage == .command,
                     "domain and numeric code alone must not classify a pairing failure")
        print("V3_PAIRING_FAILURE_GUIDANCE_PASS")
    }
}
