// Executes the production trace, error capture, wire and visible/copy rendering.
@main struct TemporaryAnisetteTraceHarness {
    static func main() async throws {
        typealias Trace = V3TemporaryAnisetteTrace
        let id = UUID().uuidString
        let otherID = UUID().uuidString
        let key = Trace.contextKey
        let nativeDescription = "ADIOTPRequest failed (Device not provisioned (-45061)): -45061"
        let marker = " [DEBUG_TEMPORARY_NATIVE_TRACE:"
        let suffix = marker + "arguments.ok,file.open.ok,file.write.ok,setup.ok,native.otp.failed]"
        let canaries = ["PRIVATE_PASSWORD", "PRIVATE_OTP", "PRIVATE_TOKEN", "PRIVATE_IDENTIFIER",
                        "PRIVATE_BLOB", "/private/account/path", String(repeating: "ab", count: 32)]
        var trace = Trace()
        trace.record(step: .keychainRead, outcome: .started)
        trace.record(step: .keychainRead, outcome: .succeeded)
        trace.record(step: .primaryProvider, outcome: .started)
        trace.appendNative(errorDescription: canaries.joined(separator: " ") + suffix, scope: .primary)
        trace.record(step: .primaryProvider, outcome: .failed)
        let native = AnisetteKit.AnisetteError.adiError(code: -45061, description: nativeDescription + suffix)
        let context = V3AnisetteAttemptContext(blobState: .existing, recovery: .currentProbeRejected,
            probeEvidence: .capture(code: -45054,
                description: "ADIOTPRequest failed (ADI filesystem error (-45054)): -45054"), trace: trace)
        let attempt = V3AnisetteAttemptError(underlying: native, context: context)
        let failure = v3CaptureAuthFailure(V3AuthenticationPhaseError(step: .anisetteFetch, underlying: attempt),
            operation: "signIn", stage: .authentication, id: id)
        let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
        precondition(CombinedFailure.fromEncodedString(failure.encodedString, expectedID: otherID) == nil)
        precondition(decoded.signingContext["native_code"] == "-45061")
        precondition(decoded.signingContext["native_phase"] == "nativeOTP")
        precondition(decoded.signingContext["probe_native_code"] == "-45054")
        precondition(decoded.signingContext["probe_native_phase"] == "nativeOTP")
        precondition(decoded.signingContext["anisette_recovery"] == "currentProbeRejected")
        precondition(decoded.correlationID == id && decoded.retryable == nil)
        var wire = decoded.wire
        wire["kind"] = "anisette"
        let visible = hostFailureMessage(from: wire)
        let rendered = V3AuthFailureDiagnosticsPolicy.render(wire,
            underlyingCode: decoded.underlyingCode, retryableValue: decoded.retryable)
        let copied = V3DiagnosticCopy.details(visibleMessage: visible, technical: rendered)
        precondition(copied.contains("SS-AUTH-C11-S02-T19-A06"))
        for secret in canaries {
            precondition(!copied.contains(secret) && !decoded.technicalDetails.contains(secret))
            precondition(!String(describing: decoded.wire).contains(secret))
        }
        if !Trace.temporaryAnisetteTraceEnabled {
            precondition(trace.snapshot == nil && trace.failedStep == nil && trace.technicalDetails.isEmpty)
            precondition(decoded.signingContext[key] == nil)
            precondition(!copied.contains("DEBUG TEMPORARY") && !decoded.technicalDetails.contains("DEBUG TEMPORARY"))
            precondition(Trace(encoded: "v1;swift.keychainRead.failed") == nil)
            let stripped = CombinedFailure.validatedSigningContext([
                "typed_error": "anisetteKitADIError", key: canaries.joined(separator: " ")])!
            precondition(stripped == ["typed_error": "anisetteKitADIError"])
            precondition(V3AuthFailureDiagnosticsPolicy.display(visible + "\nDEBUG TEMPORARY failed step: primary.native.otp", failure: wire) == visible)
            print("TEMPORARY_ANISETTE_TRACE_DISABLED_PASS")
            return
        }
        precondition(decoded.temporaryAnisetteTrace == trace)
        precondition(copied.contains("DEBUG TEMPORARY") && decoded.technicalDetails.contains("DEBUG TEMPORARY"))
        precondition(visible.contains("DEBUG TEMPORARY failed step: primary.native.otp"))
        precondition(V3AuthFailureDiagnosticsPolicy.display(visible, failure: wire) == visible)
        precondition(!decoded.technicalDetails.contains(key + "="), "trace must use the explicit temporary label")
        precondition(Trace(encoded: trace.snapshot!) == trace)
        var wrappedFailure = Trace()
        wrappedFailure.record(step: .currentProbe, outcome: .failed)
        wrappedFailure.appendNative(errorDescription: marker + "library.load.failed,setup.failed,cleanup.failed]", scope: .current)
        precondition(wrappedFailure.failedStep == "current.library.load")
        precondition(V3AnisetteAttemptContext(blobState: .unknown, recovery: .notAttempted,
            trace: trace).diagnosticFields["anisette_blob_state"] == "unknown")

        // Snapshot/error ownership is value-based across separate asynchronous attempts.
        let originalSnapshot = trace.snapshot!
        let retainedContext = context
        var copiedTrace = trace
        copiedTrace.record(step: .identityCommit, outcome: .failed)
        precondition(trace.snapshot == originalSnapshot && retainedContext.trace?.snapshot == originalSnapshot)
        precondition(copiedTrace.failedStep == "identityCommit")
        let first = Task { () -> Trace in
            var own = Trace()
            own.record(step: .keychainRead, outcome: .started)
            await Task.yield()
            own.record(step: .keychainRead, outcome: .failed)
            return own
        }
        let second = Task { () -> Trace in
            var own = Trace()
            own.record(step: .legacyProbe, outcome: .started)
            await Task.yield()
            own.record(step: .legacyProbe, outcome: .failed)
            return own
        }
        let firstTrace = await first.value
        let secondTrace = await second.value
        precondition(firstTrace.failedStep == "keychainRead" && secondTrace.failedStep == "legacyProbe")
        precondition(!firstTrace.snapshot!.contains("legacyProbe") && !secondTrace.snapshot!.contains("keychainRead"))
        let cancelled = V3AnisetteAttemptError(underlying: CancellationError(), context: retainedContext)
        precondition(v3IsAuthCancellation(cancelled) && v3ClassifyAuthError(cancelled) == nil)
        precondition(retainedContext.trace?.snapshot == originalSnapshot)
        precondition(v3AccountOperationFailure(attempt, step: .authenticate).anisetteAttempt?.trace?.snapshot == originalSnapshot)

        // Both event-count and byte caps retain the newest failed step and mark loss.
        var long = Trace()
        for _ in 0..<300 { long.record(step: .currentSnapshot, outcome: .succeeded) }
        long.record(step: .identityCommit, outcome: .failed)
        precondition(long.snapshot!.hasPrefix("v1;trace.truncated;"))
        precondition(long.snapshot!.split(separator: ";").count - 1 <= 64)
        precondition(long.snapshot!.utf8.count <= 2048 && long.failedStep == "identityCommit")
        var longNative = Trace()
        for _ in 0..<100 {
            longNative.appendNative(errorDescription: marker + "file.readback.not_checked]", scope: .current)
        }
        longNative.record(step: .freshBlobCommit, outcome: .failed)
        precondition(longNative.snapshot!.hasPrefix("v1;trace.truncated;"))
        precondition(longNative.snapshot!.utf8.count > 512 && longNative.snapshot!.utf8.count <= 2048)
        precondition(longNative.failedStep == "freshBlobCommit")
        // Three native calls can exceed the shared budget. Truncation is
        // explicit, terminal legacy evidence survives, and the original native
        // code/phase remain in their existing independent structured fields.
        var threeCalls = Trace()
        let nativeBatch = Array(repeating: "file.readback.not_checked", count: 31) + ["native.otp.failed"]
        for scope in [Trace.Scope.primary, .current, .legacy] {
            threeCalls.appendNative(errorDescription: marker + nativeBatch.joined(separator: ",") + "]", scope: scope)
        }
        threeCalls.record(step: .legacyProbe, outcome: .failed)
        let truncatedSnapshot = threeCalls.snapshot!
        precondition(truncatedSnapshot.hasPrefix("v1;trace.truncated;"))
        precondition(truncatedSnapshot.utf8.count <= 2048)
        precondition(!truncatedSnapshot.contains("native.primary."))
        precondition(truncatedSnapshot.contains("native.legacy.native.otp.failed"))
        precondition(truncatedSnapshot.hasSuffix("swift.legacyProbe.failed"))
        precondition(threeCalls.failedStep == "legacy.native.otp")
        let truncatedAttempt = V3AnisetteAttemptError(underlying: native,
            context: .init(blobState: .existing, recovery: .probeRejected, trace: threeCalls))
        let truncatedFailure = v3CaptureAuthFailure(truncatedAttempt,
            operation: "signIn", stage: .authentication, id: id)
        let truncatedDecoded = CombinedFailure.fromEncodedString(truncatedFailure.encodedString, expectedID: id)!
        precondition(truncatedDecoded.temporaryAnisetteTrace == threeCalls)
        precondition(truncatedDecoded.signingContext["native_code"] == "-45061")
        precondition(truncatedDecoded.signingContext["native_phase"] == "nativeOTP")
        let bounded = CombinedFailure(operation: "signIn", stage: .authentication, code: .failed, id: id,
            signingContext: [key: longNative.snapshot!, "typed_error": "anisetteKitADIError"])
        precondition(CombinedFailure.fromEncodedString(bounded.encodedString, expectedID: id)?.temporaryAnisetteTrace == longNative)
        let reply = try PropertyListSerialization.data(fromPropertyList: ["version": 1, "id": id,
            "error": "failed", "failure": bounded.wire], format: .binary, options: 0)
        guard case .failed(let readiness) = V3ServiceReadinessReply.decode(reply, requestID: id) else {
            preconditionFailure("bounded trace was rejected by transport length gate")
        }
        precondition(readiness.signingContext[key] == longNative.snapshot!)

        // Malformed input fails closed. No raw or partial token is serialized.
        let malformed = ["", "v1;", "v2;swift.keychainRead.failed", "v1;swift.unknown.failed",
            "v1;swift.keychainRead.unknown", "v1;swift.keychainRead.failed;", "v1;swift.keychainRead.failed\n",
            "v1;native.unknown.native.otp.failed", "v1;native.primary.unknown.failed",
            "v1;swift.keychainRead.failed;trace.truncated", "v1;trace.truncated;trace.truncated",
            "v1;" + Array(repeating: "swift.keychainRead.failed", count: 65).joined(separator: ";"),
            "v1;" + String(repeating: "X", count: 2048)] + canaries.map { "v1;native.primary." + $0 }
        for value in malformed {
            precondition(Trace(encoded: value) == nil)
            precondition(CombinedFailure.validatedSigningContext([key: value]) == nil)
            var poisoned = bounded.wire
            poisoned["signingContext"] = [key: value]
            precondition(CombinedFailure.decode(poisoned, expectedID: id) == nil)
            let display = V3AuthFailureDiagnosticsPolicy.render(poisoned, underlyingCode: 0, retryableValue: nil)
            precondition(!display.contains("DEBUG TEMPORARY"))
            for secret in canaries { precondition(!display.contains(secret)) }
        }
        let invalidSuffixes = [marker + "]", marker + "native.otp.failed,]", marker + "native.otp.failed]tail",
            marker + "native.otp.failed]\n", marker + "trace.truncated,native.otp.failed]",
            marker + Array(repeating: "arguments.ok", count: 33).joined(separator: ",") + "]",
            marker + String(repeating: "X", count: 1025) + "]",
            marker + "arguments.ok]" + marker + "native.otp.failed]"] + canaries.map { marker + $0 + "]" }
        for value in invalidSuffixes {
            var rejected = Trace()
            rejected.appendNative(errorDescription: nativeDescription + value, scope: .primary)
            precondition(rejected.snapshot == nil)
            precondition(V3AnisetteNativeEvidence.capture(code: -45061,
                description: nativeDescription + value).phase == .unknown)
        }
        precondition(CombinedFailure.validatedSigningContext(["native_phase": String(repeating: "X", count: 513)]) == nil)
        precondition(CombinedFailure.validatedSigningContext(["other_trace": originalSnapshot]) == nil)
        precondition(Trace().snapshot == nil)
        let ordinary = CombinedFailure(operation: "signIn", stage: .authentication, code: .failed, id: id)
        precondition(!ordinary.technicalDetails.contains("DEBUG TEMPORARY"))
        print("TEMPORARY_ANISETTE_TRACE_PASS")
    }
}
