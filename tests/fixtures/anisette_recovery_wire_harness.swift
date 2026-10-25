@main struct RecoveryWire {
    static func main() async throws {
        let id = UUID().uuidString
        let secret = "PRIVATE_IDENTIFIER PRIVATE_BLOB PRIVATE_PATH"
        let original = AnisetteKit.AnisetteError.adiError(code: -45061,
            description: "ADIOTPRequest failed (Device not provisioned (-45061)): -45061")
        for status in [V3AnisetteAttemptContext.Recovery.noLegacyCandidate, .legacyReadFailed,
            .ambiguousLegacyIdentity, .legacyBlobMismatch, .invalidLegacyPair,
            .probeRejected, .invalidNativeProof, .restoreFailed, .temporaryStorageUnavailable, .currentProbeRejected] {
            let attempt = V3AnisetteAttemptError(underlying: original,
                context: .init(blobState: .existing, recovery: status))
            let error = V3AuthenticationPhaseError(step: .anisetteFetch, underlying: attempt)
            let typed = v3AccountOperationFailure(error, step: .authenticate)
            precondition(typed.kind == .anisetteKitADIError)
            precondition(typed.nativeEvidence?.code == -45061 && typed.nativeEvidence?.phase == .nativeOTP)
            precondition(v3ClassifyAuthError(error) == .anisette)
            let failure = v3CaptureAuthFailure(error, operation: "signIn", stage: .authentication, id: id)
            let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
            var wire = decoded.wire
            wire["kind"] = "anisette"
            let copy = V3AuthFailureDiagnosticsPolicy.render(wire,
                underlyingCode: decoded.underlyingCode, retryableValue: decoded.retryable)
            precondition(copy.contains("native_code=-45061 native_phase=nativeOTP native_subcode=unknown"))
            precondition(copy.contains("anisette_blob_state=existing anisette_recovery=\(status.rawValue)"))
            precondition(copy.contains("diagnostic_code=SS-AUTH-C11-S02-T19-A06"))
            precondition(!copy.contains(secret) && decoded.retryable == nil)
        }
        let probe = V3AnisetteAttemptError(underlying: original,
            context: .init(blobState: .existing, recovery: .currentProbeRejected,
                probeEvidence: .capture(code: -45054,
                    description: "ADIOTPRequest failed (ADI filesystem error (-45054)): -45054")))
        let probeFailure = v3CaptureAuthFailure(V3AuthenticationPhaseError(step: .anisetteFetch, underlying: probe),
            operation: "signIn", stage: .authentication, id: id)
        let probeDecoded = CombinedFailure.fromEncodedString(probeFailure.encodedString, expectedID: id)!
        let probeCopy = V3AuthFailureDiagnosticsPolicy.render(probeDecoded.wire,
            underlyingCode: probeDecoded.underlyingCode, retryableValue: probeDecoded.retryable)
        precondition(probeCopy.contains("native_code=-45061 native_phase=nativeOTP"))
        precondition(probeCopy.contains("probe_native_code=-45054 probe_native_phase=nativeOTP probe_native_subcode=unknown"))
        for field in ["probe_native_code", "probe_native_subcode"] {
            for value in ["2147483648", "-2147483649", "+1", "01", "1\n", secret] {
                precondition(CombinedFailure.validatedSigningContext([field: value]) == nil)
            }
        }
        precondition(CombinedFailure.validatedSigningContext(["probe_native_phase": secret]) == nil)
        let blocked = V3AnisetteAttemptError(underlying: LCAnisettePairError.stateChanged,
            context: .init(blobState: .existing, recovery: .stateChanged))
        for error in [blocked, V3AuthenticationPhaseError(step: .anisetteFetch, underlying: blocked)] as [Error] {
            let generic = CombinedFailure.capture(error, operation: "refresh", stage: .network, id: id)
            precondition(generic.retryable == false && generic.stage == .authentication)
            precondition(generic.signingContext["typed_error"] == "anisetteIdentityStateInvalid")
            precondition(generic.signingContext["anisette_recovery"] == "stateChanged")
            precondition(v3ClassifyAuthError(error) == .anisetteIdentityStateInvalid)
        }
        let cancelled = V3AnisetteAttemptError(underlying: CancellationError(),
            context: .init(blobState: .existing, recovery: .probeRejected))
        precondition(v3IsAuthCancellation(cancelled) && v3ClassifyAuthError(cancelled) == nil)
        for field in ["anisette_blob_state", "anisette_recovery"] {
            precondition(CombinedFailure.validatedSigningContext([field: secret]) == nil)
        }
        print("RECOVERY_WIRE_PASS")
    }
}
