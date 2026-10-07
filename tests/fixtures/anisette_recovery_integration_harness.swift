@main struct RecoveryIntegration {
    static func main() async throws {
        let mode = CommandLine.arguments[1]
        let a = PairTest.originalID
        let b = PairTest.otherID
        let client = LCEmbeddedSharedKeychain.makeClient()
        Keychain.shared = Keychain(client)
        let selected = Store.keychainGroup
        let legacy = Store.processGroup
        Store.data[selected] = ["identifier": Data(b.uuidString.utf8), "adiPb": PairTest.encodedBlob]
        Store.data[legacy] = ["identifier": Data(a.uuidString.utf8)]
        let original = Store.data
        let otp = Data("synthetic-otp".utf8).base64EncodedString()
        let mid = Data("synthetic-mid".utf8).base64EncodedString()
        let native = AnisetteKit.AnisetteError.adiError(code: -45061,
            description: "ADIOTPRequest failed (Device not provisioned (-45061)): -45061")
        PairTest.beforeReturn = { throw native }
        LCAnisetteIsolatedProbe.action = { identifier, blob in
            precondition(blob == PairTest.originalBlob)
            if ["current_success", "current_changed", "current_cancel"].contains(mode) {
                precondition(identifier == b)
                if mode == "current_changed" { Store.data[selected]?["identifier"] = Data(a.uuidString.utf8) }
                if mode == "current_cancel" { RecoveryIntegrationQueries.cancelOnSnapshotRead = true }
                return (ALTAnisetteData(), otp, mid)
            }
            if mode == "control_wrong_code" {
                throw AnisetteKit.AnisetteError.adiError(code: -45054,
                    description: "ADIOTPRequest failed (ADI filesystem error (-45054)): -45054")
            }
            if mode == "control_wrong_phase" {
                throw AnisetteKit.AnisetteError.adiError(code: -45061,
                    description: "ADIProvisioningStart failed (Device not provisioned (-45061)): -45061")
            }
            if mode == "malformed" { return (ALTAnisetteData(), "", mid) }
            if mode == "temporary" { throw LCAnisetteIsolatedProbe.LocalFailure.temporaryStorage }
            if mode == "cancelled" { throw CancellationError() }
            if identifier == b { throw native }
            precondition(identifier == a)
            if mode == "rejected" { throw native }
            if mode == "changed" { Store.data[selected]?["identifier"] = Data(a.uuidString.utf8) }
            return (ALTAnisetteData(), otp, mid)
        }
        if mode == "no_candidate" { Store.data[legacy] = [:] }
        if mode == "fresh" { Store.data[selected]?.removeValue(forKey: "adiPb") }
        if mode == "wrong_operation" {
            PairTest.beforeReturn = { throw AnisetteKit.AnisetteError.adiError(code: -45061,
                description: "ADIProvisioningStart failed (Device not provisioned (-45061)): -45061") }
        }
        if mode == "wrong_code" {
            PairTest.beforeReturn = { throw AnisetteKit.AnisetteError.adiError(code: -45063,
                description: "ADIOTPRequest failed (Pending ADI session (-45063)): -45063") }
        }
        let before = Store.data
        do {
            _ = try await OnDeviceAnisetteManager.shared.fetchAnisetteData()
            precondition(mode == "success" || mode == "current_success")
            precondition(Store.data[selected]?["identifier"] == Data((mode == "success" ? a : b).uuidString.utf8))
            if mode == "current_success" {
                precondition(Store.data == before && RecoveryIntegrationQueries.count == 0 && Store.writes == 0)
                precondition(Store.logs.contains { $0.contains("outcome=isolated_current_pair") })
            }
            precondition(Store.data[selected]?["adiPb"] == original[selected]?["adiPb"])
            precondition(Store.data[legacy] == original[legacy])
            precondition(Store.data[selected]?.count == 2)
        } catch let error as V3AnisetteAttemptError {
            let expected: V3AnisetteAttemptContext.Recovery
            switch mode {
            case "no_candidate": expected = .noLegacyCandidate
            case "fresh", "wrong_operation", "wrong_code": expected = .notAttempted
            case "rejected": expected = .probeRejected
            case "malformed": expected = .invalidNativeProof
            case "temporary": expected = .temporaryStorageUnavailable
            case "changed", "current_changed": expected = .stateChanged
            case "control_wrong_code", "control_wrong_phase": expected = .currentProbeRejected
            default: preconditionFailure("unexpected recovery failure")
            }
            precondition(error.context.recovery == expected)
            precondition(error.context.blobState == (mode == "fresh" ? .fresh : .existing))
            if !["changed", "current_changed"].contains(mode) { precondition(Store.data == before) }
            else { precondition(error.underlying is LCAnisettePairError) }
            if ["current_changed", "control_wrong_code", "control_wrong_phase"].contains(mode) {
                precondition(RecoveryIntegrationQueries.count == 0 && Store.writes == 0)
            }
        } catch is CancellationError {
            precondition(["cancelled", "current_cancel"].contains(mode) && Store.data == before)
            precondition(Store.writes == 0 && RecoveryIntegrationQueries.count == 0)
        }
        let expectedCalls = ["fresh", "wrong_operation", "wrong_code"].contains(mode) ? 0 :
            (["success", "rejected", "changed"].contains(mode) ? 2 : 1)
        precondition(LCAnisetteIsolatedProbe.calls == expectedCalls)
        precondition(PairTest.providerCalls == 1)
        precondition(!Store.logs.joined().contains(otp) && !Store.logs.joined().contains(mid))
        print("RECOVERY_INTEGRATION_PASS " + mode)
    }
}
