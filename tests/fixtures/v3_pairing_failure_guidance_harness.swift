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
            precondition(failure.message == "SideStore could not read or validate the pairing file." &&
                         failure.recovery.contains("Open Pairing File"),
                         "pairing guidance must direct users to replace the saved pairing file")
            precondition(!failure.message.contains(privateReason) &&
                         !failure.technicalDetails.contains(privateReason),
                         "pairing parse details must not enter user-copyable diagnostics")

            let reply: [String: Any] = ["version": 1, "id": id, "ok": false,
                "error": "failed", "failure": failure.wire]
            let bytes = try! PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
            do {
                _ = try V3CatalogRequestContext.classifyReply(bytes, operation: "refresh", id: id)
                preconditionFailure("the host classifier accepted a failed pairing response")
            } catch let received as CombinedFailure {
                precondition(received.stage == .pairing && received.safeCause == .invalidPairingFile &&
                             received.retryable == false && received.correlationID == id,
                             "the typed pairing cause survives the XPC property-list reply and host classification")
                precondition(!received.technicalDetails.contains(privateReason),
                             "the reply boundary must not expose private pairing parse details")
            } catch {
                preconditionFailure("the typed pairing failure changed at the host reply boundary: \(error)")
            }
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
