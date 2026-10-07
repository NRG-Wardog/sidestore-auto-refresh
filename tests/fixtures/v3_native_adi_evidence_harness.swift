// Compiled with the production typed adapter, phase/terminal capture, wire
// model and final Copy Details renderer by test_v3_account_diagnostics.py.
// Native producer strings are from AnisetteKit 1f5a7e36553cc865b873f222b87a6486c0bcc7bf.
@main struct NativeADIEvidenceHarness {
    static func main() async {
        typealias Phase = V3AnisetteNativeEvidence.Phase
        let id = UUID().uuidString
        let secret = "PRIVATE_PASSWORD PRIVATE_IDENTIFIER PRIVATE_ADI_BLOB /private/account/path"
        var cases: [(Int32, String, Phase, Int32?)] = [
            (-45061, "ADIOTPRequest failed (Device not provisioned (-45061)): -45061", .nativeOTP, nil),
            (-45063, "ADIProvisioningStart failed (Pending ADI session (-45063)): -45063", .provisionStart, nil),
            (-45006, "ADIProvisioningEnd failed (PTM and TK mismatch (-45006)): -45006", .provisionEnd, nil),
            (-3, "Symbol ADIOTPRequest missing", .nativeOTP, nil),
            (-3, "Symbol ADIProvisioningStart missing", .provisionStart, nil),
            (-3, "Symbol ADIProvisioningEnd missing", .provisionEnd, nil),
            (-4, "Failed to read generated adi.pb", .readProvisioningData, nil),
            (-2, "Library directory path is null.", .setupLibraries, nil),
            (-2, "Failed to load libraries into VM", .setupLibraries, nil),
            (-2, "Required ADI setup symbol missing in VM", .setupLoadLibrary, nil),
            (-2, "Symbol ADILoadLibraryWithPath (kq56gsgHG6) missing from libraries", .setupLoadLibrary, nil),
            (-2, "Symbol ADISetProvisioningPath missing in VM", .setupProvisioningPath, nil),
            (-2, "Symbol ADISetProvisioningPath (nf92ngaK92) missing", .setupProvisioningPath, nil),
            (-2, "Symbol ADISetAndroidID missing in VM", .setupAndroidID, nil),
            (-2, "Symbol ADISetAndroidID (Sph98paBcz) missing", .setupAndroidID, nil),
            (-2, "ADILoadLibraryWithPath failed: -45075", .setupLoadLibrary, -45075),
            (-2, "ADILoadLibraryWithPath (kq56gsgHG6) failed (Library loading failed (-45075)): -45075", .setupLoadLibrary, -45075),
            (-2, "ADISetProvisioningPath failed: -45054", .setupProvisioningPath, -45054),
            (-2, "ADISetProvisioningPath failed (ADI filesystem error (-45054)): -45054", .setupProvisioningPath, -45054),
            (-2, "ADISetAndroidID failed: -45001", .setupAndroidID, -45001),
            (-2, "ADISetAndroidID failed (Invalid ADI parameters (-45001)): -45001", .setupAndroidID, -45001),
            (-2, "ADISetAndroidID failed: -77777", .setupAndroidID, -77777),
            (-2, "ADISetAndroidID failed (Unknown ADI error): -77777", .setupAndroidID, -77777),
            (-77777, "ADIOTPRequest failed (Unknown ADI error): -77777", .nativeOTP, nil),
            (Int32.min, "ADIProvisioningStart failed (Unknown ADI error): -2147483648", .provisionStart, nil),
            (Int32.max, "ADIProvisioningEnd failed (Unknown ADI error): 2147483647", .provisionEnd, nil),
            (-2, "ADISetAndroidID failed: -2147483648", .setupAndroidID, Int32.min),
            (-2, "ADISetAndroidID failed: 2147483647", .setupAndroidID, Int32.max)
        ]
        let hostileDescriptions = [secret, "ADIOTPRequest " + secret,
            "ADIOTPRequest failed (" + secret + "): -45061",
            "ADIOTPRequest failed (Device not provisioned (-45061)): -45061 " + secret,
            "ADIOTPRequest failed (Unknown ADI error): -45061",
            "ADISetAndroidID failed (" + secret + "): -45001",
            "Provided library path is not a valid directory: " + secret,
            "Failed to load libCoreADI.so at: " + secret,
            "Failed to load libstoreservicescore.so at: " + secret,
            String(repeating: "X", count: 257), "Symbol ADIOTPRequest missing\n" + secret]
        for description in hostileDescriptions {
            for code in [Int32(-2), -45061, -77777, Int32.min, Int32.max, 0] {
                cases.append((code, description, .unknown, nil))
            }
        }
        for tail in ["2147483648", "-2147483649", "+1", "01", "-01", " 1", "1 ", "1\n", "0", "-0", "1.0", "1e2", secret] {
            cases.append((-2, "ADISetAndroidID failed: " + tail, .unknown, nil))
        }
        // A recognizable description paired with the wrong scalar is not a
        // pinned producer, even though each field is independently harmless.
        cases.append((-2, "Symbol ADIOTPRequest missing", .unknown, nil))
        cases.append((-3, "ADISetAndroidID failed: -45001", .unknown, nil))
        cases.append((-3, "Failed to read generated adi.pb", .unknown, nil))
        cases.append((-45001, "ADIOTPRequest failed (Device not provisioned (-45061)): -45061", .unknown, nil))

        for (code, description, phase, subcode) in cases {
            let error = AnisetteKit.AnisetteError.adiError(code: code, description: description)
            let typed = v3AccountOperationFailure(error, step: .anisetteFetch)
            precondition(typed.kind == .anisetteKitADIError && typed.serverCode == nil && typed.httpStatus == nil)
            precondition(typed.nativeEvidence?.code == code && typed.nativeEvidence?.phase == phase)
            precondition(typed.nativeEvidence?.subcode == subcode)
            precondition(v3ClassifyAuthError(typed) == .anisette && !v3IsAuthCancellation(typed))
            precondition(!typed.requiresReconciliation && !typed.portalSessionRejected && !typed.credentialCommit)
            // Interactive typed failures and terminal cached/resume failures
            // use the same production capture and final Copy Details render.
            for stage in [CombinedFailure.Stage.authentication, .provisioning] {
                do {
                    let _: Int = try await v3AuthenticationPhase(.anisetteFetch) { throw error }
                    preconditionFailure("native failure swallowed")
                } catch {
                    for routeError in [error, typed] as [Error] {
                        let failure = v3CaptureAuthFailure(routeError, operation: "signIn", stage: stage, id: id)
                        precondition(failure.stage == .authentication && failure.sourceStep == .anisetteFetch)
                        precondition(failure.safeCause == nil && failure.retryable == nil)
                        let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
                        precondition(decoded.signingContext == failure.signingContext)
                        precondition(decoded.signingContext["native_code"] == String(code))
                        precondition(decoded.signingContext["native_phase"] == phase.rawValue)
                        precondition(decoded.signingContext["native_subcode"] == (subcode.map(String.init) ?? "unknown"))
                        precondition(decoded.signingContext["server_code"] == "unknown")
                        precondition(decoded.signingContext["http_status"] == "unavailable")
                        var wire = decoded.wire
                        wire["kind"] = "anisetteFailure"
                        let copy = V3AuthFailureDiagnosticsPolicy.render(wire,
                            underlyingCode: decoded.underlyingCode, retryableValue: decoded.retryable)
                        precondition(V3AuthFailureDiagnosticsPolicy.diagnosticCode(for: wire) == "SS-AUTH-C11-S02-T19-A06")
                        precondition(hostFailureMessage(from: wire).contains("Error ID: SS-AUTH-C11-S02-T19-A06"))
                        precondition(copy.contains("native_code=\(code) native_phase=\(phase.rawValue) native_subcode=\(subcode.map(String.init) ?? "unknown")"))
                        let prompt = CombinedFailure.provisioningRetryTechnicalDetails(for: typed, correlationID: id)
                        for rendered in [copy, decoded.technicalDetails, String(describing: wire), prompt] {
                            precondition(!rendered.contains("PRIVATE_") && !rendered.contains("/private/account/path"))
                            precondition(!rendered.contains("ADIOTPRequest") && !rendered.contains("ADISetAndroidID"))
                            precondition(rendered.contains("native_code=\(code)") || rendered.contains("native_code"))
                        }
                        let recaptured = v3CaptureAuthFailure(decoded, operation: "signIn", stage: .provisioning, id: id)
                        precondition(recaptured.signingContext == decoded.signingContext)
                    }
                }
            }
        }

        let legacy = V3AccountOperationError(step: .anisetteFetch, kind: .anisetteKitADIError,
            underlying: AnisetteKit.AnisetteError.adiError(code: -45061, description: secret), serverCode: nil)
        precondition(legacy.nativeEvidence == nil)
        let legacyFailure = legacy.failure(operation: "signIn", id: id)
        precondition(legacyFailure.signingContext["native_code"] == nil)
        let legacyCopy = V3AuthFailureDiagnosticsPolicy.render(legacyFailure.wire,
            underlyingCode: legacyFailure.underlyingCode, retryableValue: nil)
        precondition(legacyCopy.contains("native_code=unknown native_phase=unknown native_subcode=unknown"))
        for field in ["native_code", "native_subcode"] {
            for text in ["-2147483648", "2147483647", "-1", "0", "1", "unknown"] {
                precondition(CombinedFailure.validatedSigningContext([field: text]) != nil)
            }
            for text in ["2147483648", "-2147483649", "+1", "01", "-01", "-0", " 1", "1 ", "1\n", "1.0", "1e2", secret] {
                precondition(CombinedFailure.validatedSigningContext([field: text]) == nil)
                var wire = legacyFailure.wire
                wire["signingContext"] = ["typed_error": "anisetteKitADIError", field: text]
                precondition(CombinedFailure.decode(wire, expectedID: id) == nil)
                let copy = V3AuthFailureDiagnosticsPolicy.render(wire, underlyingCode: nil, retryableValue: nil)
                precondition(!copy.contains("PRIVATE_") && !copy.contains("/private/account/path"))
            }
        }
        precondition(CombinedFailure.validatedSigningContext(["native_phase": secret]) == nil)
        // NSError lookalikes cannot invoke the typed producer decoder.
        let lookalike = v3AccountOperationFailure(NSError(domain: "AnisetteKit.AnisetteError", code: -45061,
            userInfo: [NSLocalizedDescriptionKey: "ADIOTPRequest failed (Device not provisioned (-45061)): -45061"]), step: .anisetteFetch)
        precondition(lookalike.nativeEvidence == nil && lookalike.kind == .unknownAccountFailure)
        for cancellation in [CancellationError(), URLError(.cancelled), DeveloperPortalError.userCancelled] as [Error] {
            do {
                let _: Int = try await v3AuthenticationPhase(.anisetteFetch) { throw cancellation }
                preconditionFailure("cancellation swallowed")
            } catch {
                precondition(v3IsAuthCancellation(error) && v3ClassifyAuthError(error) == nil)
                precondition(v3AccountOperationFailure(error, step: .anisetteFetch).nativeEvidence == nil)
            }
        }
        print("Native ADI evidence PASS")
    }
}
