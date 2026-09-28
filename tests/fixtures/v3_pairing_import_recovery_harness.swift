@main
struct PairingImportRecoveryHarness {
    static func main() throws {
        let id = UUID().uuidString
        let invalidFile = CombinedFailure(operation: "pairingImportData", stage: .pairing,
            code: .failed, id: id, retryable: false, safeCause: .invalidPairingFile)
        precondition(invalidFile.operation == "pairingImportData",
            "pairing-import must retain its typed operation through normalization")
        let plistData = try PropertyListSerialization.data(fromPropertyList: invalidFile.wire,
            format: .binary, options: 0)
        let plistReply = try PropertyListSerialization.propertyList(from: plistData,
            format: nil) as! [String: Any]
        let decodedFileFailure = CombinedFailure.decode(plistReply, expectedID: id)!
        precondition(V3PairingImportFailurePolicy.shouldOfferFileRetry(
            operation: decodedFileFailure.operation,
            stage: decodedFileFailure.stage.rawValue,
            safeCause: decodedFileFailure.safeCause?.rawValue),
            "only a typed file validation failure should offer Choose Pairing File Again")

        let busyService = CombinedFailure(operation: "pairingImportData", stage: .command,
            code: .busy, id: id, retryable: true, safeCause: .operationInProgress)
        let startupFailure = CombinedFailure(operation: "pairingImportData", stage: .serviceReadiness,
            code: .notReady, id: id, retryable: true)
        precondition(!V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: busyService.operation,
                        stage: busyService.stage.rawValue, safeCause: busyService.safeCause?.rawValue) &&
                     !V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: startupFailure.operation,
                        stage: startupFailure.stage.rawValue, safeCause: startupFailure.safeCause?.rawValue),
            "busy and service readiness failures must route through shared error recovery, not blame the selected file")
        print("V3_PAIRING_IMPORT_RECOVERY_PASS")
    }
}
