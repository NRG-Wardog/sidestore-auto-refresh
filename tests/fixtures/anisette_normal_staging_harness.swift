// Actual generated ODA boundary; only the native/provider I/O is doubled.
@main struct NormalAnisetteStagingHarness {
    static func main() async throws {
        precondition(!LCAnisetteRecoveryPolicy.automaticRecoveryEnabled)
        let mode = CommandLine.arguments[1]
        let client = LCEmbeddedSharedKeychain.makeClient()
        Keychain.shared = Keychain(client)
        let selected = Store.keychainGroup, legacy = Store.processGroup
        let identifier = PairTest.otherID
        let fresh = ["fresh_failure", "fresh_success", "empty_failure"].contains(mode)
        Store.data[selected] = ["identifier": Data(identifier.uuidString.utf8)]
        if mode == "empty_failure" { Store.data[selected] = [:] }
        if !fresh { Store.data[selected]?["adiPb"] = PairTest.encodedBlob }
        // A preserved alternative must not be queried or used automatically.
        Store.data[legacy] = ["identifier": PairTest.originalIDData]
        if mode == "journal" {
            Store.data[selected]?["LCAnisetteIdentityRecoveryV1"] = Data("preserve-pending-journal".utf8)
        }
        if mode == "keychain" { Store.failure = -25308 }
        let original = Store.data
        LCAnisetteIsolatedProbe.action = { _, _ in
            preconditionFailure("normal authentication invoked dormant isolation")
        }
        let storage = mode == "storage_failure"
        let nativeCode: Int32 = storage ? -6 : -45061
        let nativeDescription = storage ? "Checked OTP staging failed" :
            "ADIOTPRequest failed (Device not provisioned (-45061)): -45061"
        PairTest.beforeReturn = {
            if mode == "cancel" { throw CancellationError() }
            if !["saved_success", "fresh_success"].contains(mode) {
                throw AnisetteKit.AnisetteError.adiError(code: nativeCode, description: nativeDescription)
            }
        }
        if mode == "fresh_success" { PairTest.returnedBlob = PairTest.originalBlob }
        do {
            _ = try await OnDeviceAnisetteManager.shared.fetchAnisetteData()
            precondition(["saved_success", "fresh_success"].contains(mode))
            precondition(Store.data[selected]?["identifier"] == original[selected]?["identifier"])
            if mode == "fresh_success" {
                precondition(Store.data[selected]?["adiPb"] == PairTest.encodedBlob && Store.writes == 1)
            } else { precondition(Store.data == original && Store.writes == 0) }
        } catch is CancellationError {
            precondition(mode == "cancel" && Store.data == original && Store.writes == 0)
        } catch let failure as V3AnisetteAttemptError {
            if mode == "empty_failure" {
                // Ordinary first-use identity creation remains part of the
                // original authentication flow, never a diagnostic probe.
                let created = Store.data[selected]?["identifier"].flatMap { String(data: $0, encoding: .utf8) }
                precondition(created.flatMap(UUID.init(uuidString:)) == PairTest.providerIdentifier)
                precondition(Store.data[selected]?.count == 1 && Store.data[selected]?["adiPb"] == nil)
                precondition(Store.writes == 1 && Store.removeCalls == 0)
            } else {
                precondition(Store.data == original && Store.writes == 0 && Store.removeCalls == 0)
            }
            if ["keychain", "journal"].contains(mode) {
                precondition(failure.context.blobState == .unknown && failure.context.recovery == .notAttempted)
            } else {
                precondition(failure.context.blobState == (fresh ? .fresh : .existing))
                precondition(failure.context.recovery == .automaticRecoveryDisabled)
                let trace = failure.context.trace?.snapshot ?? ""
                precondition(trace.contains("swift.currentProbe.skipped") && trace.contains("swift.identityCommit.skipped"))
                guard let native = failure.underlying as? AnisetteKit.AnisetteError,
                      case .adiError(let code, let description) = native else { preconditionFailure("typed error lost") }
                precondition(code == nativeCode)
                precondition(V3AnisetteNativeEvidence.capture(code: code, description: description).phase ==
                    (storage ? .nativeStorage : .nativeOTP))
            }
        }
        precondition(LCAnisetteIsolatedProbe.calls == 0 && RecoveryIntegrationQueries.count == 0)
        precondition(PairTest.providerCalls == (["keychain", "journal"].contains(mode) ? 0 : 1))
        precondition(Store.data[legacy] == original[legacy])
        print("NORMAL_ANISETTE_STAGING_PASS " + mode)
    }
}
