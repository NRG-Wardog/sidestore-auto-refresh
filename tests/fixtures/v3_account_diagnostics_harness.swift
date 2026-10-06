
@main struct AccountDiagnosticsHarness {
    static func main() async {
        let id = UUID().uuidString
        let secret = "PRIVATE_ACCOUNT_EMAIL PRIVATE_TOKEN HTTP 503 lc_stage=network errno=13"
        let phaseErrors: [(Error, V3AccountOperationError.Kind)] = [
            (AnisetteKit.AnisetteError.invalidArgument, .anisetteKitInvalidArgument),
            (AnisetteKit.AnisetteError.loaderFailed(reason: secret), .anisetteKitLoaderFailed),
            (AnisetteKit.AnisetteError.symbolMissing(name: secret), .anisetteKitSymbolMissing),
            (AnisetteKit.AnisetteError.readFailure, .anisetteKitReadFailure),
            (AnisetteKit.AnisetteError.invalidResponse(reason: secret), .anisetteKitInvalidResponse),
            (AnisetteKit.AnisetteError.adiError(code: -1, description: secret), .anisetteKitADIError),
            (AnisetteKit.AnisetteError.librariesNotFound(reason: secret), .anisetteKitLibrariesNotFound),
            (AnisetteKit.AnisetteError.httpError(statusCode: 503, message: secret), .anisetteKitHTTPError),
            (SideSign.Archive.Error.corruptArchive(URL(fileURLWithPath: "/PRIVATE_PATH")), .archiveCorrupt),
            (DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: secret)), .decodingDataCorrupted)
        ]
        for (error, kind) in phaseErrors {
            do {
                let _: Int = try await v3AuthenticationPhase(.anisetteFetch) { throw error }
                preconditionFailure("expected injected phase failure")
            } catch {
                precondition(!(error is V3AccountOperationError), "phase decoration must not suppress silent fallback")
                let typed = v3AccountOperationFailure(error, step: .authenticate)
                precondition(typed.step == .anisetteFetch && typed.kind == kind)
                let failure = typed.failure(operation: "signIn", id: id)
                let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
                precondition(decoded.sourceStep == .anisetteFetch && decoded.stage == .authentication)
                precondition(decoded.signingContext["typed_error"] == kind.rawValue)
                precondition(decoded.signingContext["http_status"] == (kind == .anisetteKitHTTPError ? "503" : "unavailable"))
                precondition(!decoded.technicalDetails.contains("PRIVATE_"))
                precondition(!String(describing: decoded.wire).contains("PRIVATE_"))
                if kind.rawValue.hasPrefix("anisetteKit") { precondition(v3ClassifyAuthError(error) == .anisette) }
            }
        }
        // Execute the production terminal capture shared by cached-session and
        // forced-provisioning failures, outside the interactive result handler.
        for terminalStage in [CombinedFailure.Stage.authentication, .provisioning] {
            do {
                let _: Int = try await v3AuthenticationPhase(.anisetteFetch) {
                    throw AnisetteKit.AnisetteError.httpError(statusCode: 503, message: secret)
                }
            } catch {
                let terminal = v3CaptureAuthFailure(error, operation: "signIn", stage: terminalStage, id: id)
                precondition(terminal.sourceStep == .anisetteFetch && terminal.stage == .authentication)
                precondition(terminal.signingContext["typed_error"] == "anisetteKitHTTPError")
                precondition(terminal.signingContext["http_status"] == "503")
                precondition(!terminal.technicalDetails.contains("PRIVATE_"))
            }
            let network = V3AuthenticationPhaseError(step: .anisetteFetch, underlying: URLError(.networkConnectionLost))
            let terminal = v3CaptureAuthFailure(network, operation: "signIn", stage: terminalStage, id: id)
            precondition(terminal.sourceStep == .anisetteFetch && terminal.signingContext["typed_error"] == "transportFailure")
            precondition(terminal.underlyingDomain == NSURLErrorDomain && terminal.underlyingCode == NSURLErrorNetworkConnectionLost)
            let generic = CombinedFailure.capture(network, operation: "refresh", stage: terminalStage, id: id)
            precondition(generic.underlyingDomain == NSURLErrorDomain && generic.underlyingCode == NSURLErrorNetworkConnectionLost)
        }
        let timeout = V3AuthenticationPhaseError(step: .anisetteFetch, underlying: URLError(.timedOut))
        let timedOut = v3CaptureAuthFailure(timeout, operation: "signIn", stage: .provisioning, id: id)
        precondition(timedOut.sourceStep == .anisetteFetch && timedOut.underlyingDomain == NSURLErrorDomain)
        precondition(timedOut.underlyingCode == NSURLErrorTimedOut && timedOut.signingContext["typed_error"] == "transportFailure")
        do {
            let _: Int = try await v3AuthenticationPhase(.anisetteFetch) { throw CancellationError() }
            preconditionFailure("cancellation swallowed")
        } catch { precondition(error is CancellationError && v3IsAuthCancellation(error)) }
        let portalCancellation = V3AuthenticationPhaseError(step: .appleAuthentication, underlying: DeveloperPortalError.userCancelled)
        precondition(v3IsAuthCancellation(portalCancellation) && v3ClassifyAuthError(portalCancellation) == nil)
        let invalidHTTP = v3AccountOperationFailure(
            AnisetteKit.AnisetteError.httpError(statusCode: 99999, message: secret), step: .anisetteFetch)
        precondition(invalidHTTP.httpStatus == nil)
        let cancellation = V3AuthenticationPhaseError(step: .anisetteFetch, underlying: CancellationError())
        precondition(v3IsAuthCancellation(cancellation) && v3ClassifyAuthError(cancellation) == nil)
        let nested = V3AuthenticationPhaseError(step: .accountLookup, underlying: ServerError.underlyingError(code: 1100, message: secret))
        let nestedTyped = v3AccountOperationFailure(nested, step: .authenticate)
        precondition(nestedTyped.step == .accountLookup && nestedTyped.serverCode == 1100 && !nestedTyped.portalSessionRejected)
        let rejected = v3AccountOperationFailure(
            ServerError.underlyingError(code: 1100, message: secret), step: .fetchTeams)
        precondition(rejected.portalSessionRejected)
        precondition(rejected.failure(operation: "signIn", id: id).retryable == false)
        precondition(rejected.failure(operation: "signIn", id: id).technicalDetails.contains("server_code=1100"))
        precondition(!rejected.failure(operation: "signIn", id: id).technicalDetails.contains(secret))
        for otherStep in [CombinedFailure.SourceStep.authenticate, .fetchCertificate, .registerDevice] {
            precondition(!v3AccountOperationFailure(
                ServerError.underlyingError(code: 1100, message: secret), step: otherStep).portalSessionRejected)
        }
        precondition(!v3AccountOperationFailure(
            ServerError.underlyingError(code: 1101, message: secret), step: .fetchTeams).portalSessionRejected)
        precondition(!v3AccountOperationFailure(
            NSError(domain: "SideSign.ServerError", code: 1100), step: .fetchTeams).portalSessionRejected)
        let stages: [CombinedFailure.SourceStep] = [.fetchTeams, .saveAccount, .fetchCertificate,
            .activateCertificate, .registerDevice, .activateAccount, .credentialCommit]
        let errors: [Error] = [
            NSError(domain: "com.SideStore.Keychain", code: -34018,
                    userInfo: [NSLocalizedDescriptionKey: secret]),
            URLError(.networkConnectionLost),
            ServerError.underlyingError(code: 9120, message: secret),
            ServerError.invalidResponseFormat(rawPayload: secret),
            NSError(domain: "PRIVATE_ENDPOINT", code: 503,
                    userInfo: [NSLocalizedDescriptionKey: secret,
                        NSUnderlyingErrorKey: NSError(domain: NSURLErrorDomain, code: -1009)])
        ]
        for step in stages {
            for (index, error) in errors.enumerated() {
                let typed = v3AccountOperationFailure(error, step: step)
                let failure = CombinedFailure.capture(typed, operation: "signIn", stage: .authentication, id: id)
                precondition(failure.sourceStep == step)
                precondition(failure.stage == typed.failureStage)
                precondition(failure.signingContext["http_status"] == "unavailable")
                precondition(failure.signingContext["server_code"] == (index == 2 ? "9120" : "unknown"))
                let decoded = CombinedFailure.fromEncodedString(failure.encodedString, expectedID: id)!
                precondition(decoded.sourceStep == step && decoded.signingContext == failure.signingContext)
                let copy = V3AuthFailureDiagnosticsPolicy.render(decoded.wire,
                    underlyingCode: decoded.underlyingCode, retryableValue: decoded.retryable)
                let prompt = CombinedFailure.provisioningRetryTechnicalDetails(for: typed, correlationID: id)
                for rendered in [decoded.technicalDetails, copy, prompt, String(describing: decoded.wire)] {
                    precondition(!rendered.contains("PRIVATE_"), "private error content escaped")
                    precondition(!rendered.contains("HTTP 503") && !rendered.contains("lc_stage=network"))
                    precondition(rendered.contains(step.rawValue))
                }
                if index == 4 {
                    precondition(decoded.underlyingDomain == "redacted" && decoded.underlyingCode == 0)
                    precondition(typed.kind != .transportFailure, "untrusted underlying chain must not classify")
                }
                if index == 2 { precondition(typed.kind == .sideSignServerReportedError) }
            }
        }
        for nativeCode in [1009, 1010, -34018] {
            let typed = v3AccountOperationFailure(NSError(domain: "com.SideStore.Keychain", code: nativeCode), step: .credentialCommit)
            let expected: V3AuthFailureKind = nativeCode == 1010 ? .credentialStorageUncertain : .credentialStorage
            precondition(v3ClassifyAuthError(typed) == expected)
            let failure = typed.failure(operation: "signIn", id: id)
            var wire = failure.wire
            wire["kind"] = expected.rawValue
            let message = hostFailureMessage(from: wire)
            precondition(message.contains("Apple authentication succeeded"))
            precondition(!message.contains("password"))
            precondition(typed.requiresReconciliation == (nativeCode == 1010) && failure.retryable == false)
            precondition(V3AuthTerminalFailureActionPolicy.resolve(kind: expected.rawValue, retryable: false) == .blocked)
        }
        let conflict = v3AccountOperationFailure(NSError(domain: "LiveContainerRefresh.Configuration", code: 1008), step: .credentialCommit)
        precondition(conflict.kind == .legacyMigrationConflict)
        precondition(v3ClassifyAuthError(DeveloperPortalError.incorrectCredentials) == .invalidCredentials)
        precondition(CombinedFailure.validatedSigningContext(["typed_error": secret]) == nil)
        precondition(CombinedFailure.validatedSigningContext(["server_code": "9120 HTTP 503"]) == nil)
        let suite = "V3AccountDiagnostics-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let prior = ["account:old", "team:old"], intended = ["account:new", "team:new"]
        for observed in [prior, intended] {
            try! V3AccountDatabaseRecovery.begin(previous: prior, intended: intended, defaults: defaults)
            precondition(defaults.object(forKey: V3AccountDatabaseRecovery.key) != nil)
            // A new defaults object represents reloading the durable journal.
            let reloaded = UserDefaults(suiteName: suite)!
            try! V3AccountDatabaseRecovery.reconcile(observed: observed, defaults: reloaded)
            precondition(reloaded.object(forKey: V3AccountDatabaseRecovery.key) == nil)
        }
        try! V3AccountDatabaseRecovery.begin(previous: prior, intended: intended, defaults: defaults)
        do {
            try V3AccountDatabaseRecovery.reconcile(observed: ["account:mixed", "team:new"], defaults: defaults)
            preconditionFailure("mixed activation was accepted")
        } catch { precondition(error is V3AccountDatabaseOutcomeUnknownError) }
        precondition(defaults.object(forKey: V3AccountDatabaseRecovery.key) != nil)
        defaults.set(["unknown": true], forKey: V3AccountDatabaseRecovery.key)
        do {
            try V3AccountDatabaseRecovery.reconcile(observed: intended, defaults: defaults)
            preconditionFailure("malformed journal was cleared")
        } catch { precondition(error is V3AccountDatabaseOutcomeUnknownError) }
        let uncertain = v3AccountOperationFailure(V3AccountDatabaseOutcomeUnknownError(), step: .activateAccount)
        precondition(uncertain.requiresReconciliation && uncertain.kind == .persistenceOutcomeUnknown)
        print("Typed account diagnostics PASS")
    }
}
