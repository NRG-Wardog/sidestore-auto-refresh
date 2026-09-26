import Foundation

@main
struct V3AuthJITLessErrorBehaviorHarness {
    static func main() {
        var previous: [String: Any]?
        let invalidCredentials: [String: Any] = ["kind": "invalidCredentials", "code": "invalidCredentials"]
        let firstPrompt: [String: Any] = [
            "state": "awaitingPrompt",
            "prompt": ["kind": "credentials"],
            "previousFailure": invalidCredentials
        ]
        previous = V3AuthPromptFailurePolicy.applying(reply: firstPrompt, current: previous)
        precondition(V3AuthPromptFailurePolicy.isVisible(previous, promptKind: "credentials"))
        let nextPrompt: [String: Any] = ["state": "awaitingPrompt", "prompt": ["kind": "credentials"]]
        previous = V3AuthPromptFailurePolicy.applying(reply: nextPrompt, current: previous)
        precondition(previous?["kind"] as? String == "invalidCredentials")
        precondition(V3AuthPromptFailurePolicy.clearingAfterSubmission(previous, promptKind: "credentials") == nil)
        precondition(V3AuthPromptFailurePolicy.clearingOnDismiss(previous) == nil)
        precondition(!V3AuthPromptFailurePolicy.isVisible(previous, promptKind: "twoFactor"))

        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: true,
            authoritativeAccountMatches: false, provisioningFailed: true, cancelled: false)
            == "authenticatedProvisioningIncomplete")
        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: false,
            authoritativeAccountMatches: true, provisioningFailed: false, cancelled: true)
            == "authenticatedProvisioningIncomplete")
        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: false,
            authoritativeAccountMatches: false, provisioningFailed: false, cancelled: true) == "cancelled")

        precondition(V3TwoFactorStep.afterDeliveryChoice("trustedDevice", phoneCount: 0) == .deliveryRequested)
        precondition(V3TwoFactorStep.afterDelivery("trustedDevice") == .enterVerificationCode)
        precondition(V3TwoFactorStep.afterDeliveryChoice("sms", phoneCount: 2) == .choosePhoneNumber)
        precondition(V3TwoFactorStep.afterDeliveryChoice("voice", phoneCount: 1) == .deliveryRequested)
        precondition(V3TwoFactorStep.afterVerification(accepted: false) == .enterVerificationCode)
        precondition(V3TwoFactorStep.afterVerification(accepted: true) == .completed)
        precondition(V3TwoFactorStep.afterChangeMethod == .chooseDeliveryMethod)

        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 25, hasCopy: false,
            activeCertificateExists: false, identitiesMatch: nil, validationStatus: nil,
            validationFailed: false) == .notRequired)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: false,
            activeCertificateExists: true, activeCertificateStatus: "valid", identitiesMatch: nil,
            validationStatus: nil, validationFailed: false) == .setupRequired)
        // V3_JITLESS_CERT_DISTINCTION_V1: "no active SideStore certificate" is
        // its own state, no longer collapsed into an undifferentiated unknown.
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: false,
            activeCertificateExists: false, identitiesMatch: nil, validationStatus: nil,
            validationFailed: false) == .activeCertificateMissing)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "valid", identitiesMatch: false,
            validationStatus: 0, validationFailed: false) == .certificateMismatch)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "valid", identitiesMatch: true,
            validationStatus: 0, validationFailed: false) == .ready)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "valid", identitiesMatch: false,
            validationStatus: 1, validationFailed: false) == .revoked)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "revoked", identitiesMatch: false,
            validationStatus: 0, validationFailed: false) == .activeCertificateRevoked)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "expired", identitiesMatch: false,
            validationStatus: 0, validationFailed: false) == .activeCertificateExpired)
        precondition(V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: true,
            activeCertificateExists: true, activeCertificateStatus: "unknown", identitiesMatch: nil,
            validationStatus: 0, validationFailed: false) == .unknown)
        precondition(!V3JITLessReadinessPolicy.evaluate(osMajor: 26, hasCopy: false,
            activeCertificateExists: true, activeCertificateStatus: "valid", identitiesMatch: nil,
            validationStatus: nil, validationFailed: false).isReady)
        // A stale copy is outstanding work, and a ready state is not.
        precondition(!V3JITLessPresentation.present(.certificateMismatch).isOutstandingSetupTask == false)
        precondition(!V3JITLessPresentation.present(.ready).isOutstandingSetupTask)
        precondition(V3JITLessPresentation.present(.ready).severity == .completed)

        let sourceNetwork = CombinedFailure(operation: "source", stage: .source, id: UUID().uuidString,
            retryable: true, safeCause: .sourceNetworkFailure, sourceStep: .sourceDownload)
        let badManifest = CombinedFailure(operation: "source", stage: .source, id: UUID().uuidString,
            retryable: false, safeCause: .sourceInvalidManifest, sourceStep: .manifestParsing)
        let catalog = CombinedFailure(operation: "catalog", stage: .catalog, id: UUID().uuidString,
            retryable: false, safeCause: .catalogUnavailable, sourceStep: .catalogRead)
        let pairing = CombinedFailure(operation: "refresh", stage: .pairing, code: .notReady,
            id: UUID().uuidString, retryable: false, safeCause: .pairingRequired)
        precondition(sourceNetwork.safeMessage.contains("source could not be downloaded"))
        precondition(badManifest.safeMessage.contains("valid source"))
        precondition(catalog.safeMessage.contains("saved catalog"))
        precondition(pairing.safeMessage == "A pairing file is required before this device can be refreshed.")
        precondition(pairing.recovery == "Add the pairing file, then retry the refresh.")

        var attempt = V3RefreshAllAttemptState()
        let requestID = UUID().uuidString
        attempt.begin(requestID: requestID)
        attempt.failBeforeStart(message: pairing.safeMessage)
        precondition(attempt.phase == .failed && attempt.runID.isEmpty && attempt.isTerminal)
        print("V3_AUTH_2FA_JITLESS_AND_ERROR_BEHAVIOR_PASS")
    }
}
