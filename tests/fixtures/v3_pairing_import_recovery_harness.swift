@main
struct PairingImportRecoveryHarness {
    static func main() {
        let id = UUID().uuidString
        let invalidFile = CombinedFailure(operation: "pairingImportData", stage: .pairing,
            code: .failed, id: id, retryable: false, safeCause: .invalidPairingFile)
        precondition(invalidFile.operation == "pairingImportData",
            "pairing-import must retain its typed operation through normalization")
        precondition(V3PairingImportFailurePolicy.shouldOfferFileRetry(invalidFile),
            "only a typed file validation failure should offer Choose Pairing File Again")

        let busyService = CombinedFailure(operation: "pairingImportData", stage: .command,
            code: .busy, id: id, retryable: true, safeCause: .operationInProgress)
        let startupFailure = CombinedFailure(operation: "pairingImportData", stage: .serviceReadiness,
            code: .notReady, id: id, retryable: true)
        precondition(!V3PairingImportFailurePolicy.shouldOfferFileRetry(busyService) &&
                     !V3PairingImportFailurePolicy.shouldOfferFileRetry(startupFailure),
            "busy and service readiness failures must route through shared error recovery, not blame the selected file")
        print("V3_PAIRING_IMPORT_RECOVERY_PASS")
    }
}
