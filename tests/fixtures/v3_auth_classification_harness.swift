import Foundation

enum DeveloperPortalError: Error {
    case incorrectCredentials
    case appSpecificPasswordRequired
    case tooManyAttempts
    case incorrectVerificationCode
    case invalidAnisetteData
    case accountRepairRequired
    case userCancelled
    case unknown
}

enum ServerError: Error {
    case badServerResponse
    case invalidResponseFormat
    case missingKey
    case underlyingError(Int, String)
}

@main
struct AuthClassificationHarness {
    static func main() {
        precondition(v3ClassifyAuthError(DeveloperPortalError.incorrectCredentials) == .invalidCredentials)
        precondition(v3ClassifyAuthError(DeveloperPortalError.appSpecificPasswordRequired) == .appSpecificPasswordRequired)
        precondition(v3ClassifyAuthError(DeveloperPortalError.incorrectVerificationCode) == .invalidCode)
        precondition(v3ClassifyAuthError(DeveloperPortalError.tooManyAttempts) == .rateLimited)
        precondition(v3ClassifyAuthError(DeveloperPortalError.invalidAnisetteData) == .anisette)
        precondition(v3ClassifyAuthError(DeveloperPortalError.accountRepairRequired) == .accountRepairRequired)
        precondition(v3ClassifyAuthError(NSError(domain: NSURLErrorDomain, code: -1009)) == .network)
        precondition(v3ClassifyAuthError(NSError(domain: "SideSignErrorDomain", code: 20)) == .unknown)
        precondition(v3ClassifyAuthError(NSError(domain: "ALTServerErrorDomain", code: 20)) == .unknown)

        let id = UUID().uuidString
        let provisioning = CombinedFailure.capture(
            NSError(domain: "SideSignErrorDomain", code: 20),
            operation: "install", stage: .installation, id: id)
        precondition(provisioning.stage == .installation, "provisioning was mislabeled as authentication")

        let validPPQ = CombinedFailure.capture(
            NSError(domain: "IdeviceGatewayError", code: 0,
                userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed: Failed to verify code signature of /Payload/App.app: 0xE8008024 (The provisioning profile is banned.)"]),
            operation: "install", stage: .installation, id: id)
        precondition(validPPQ.stage == .installation && validPPQ.underlyingCode == 0xE8008024)
        precondition(validPPQ.technicalDetails.contains("installVerdict=profileBanned"))

        let unrelatedText = CombinedFailure.capture(
            NSError(domain: "ExampleDomain", code: 20,
                userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed 0xE8008024"]),
            operation: "refresh", stage: .refreshVerification, id: id)
        precondition(unrelatedText.stage == .refreshVerification)
        precondition(!unrelatedText.technicalDetails.contains("installVerdict="))
        let unrelatedStage = CombinedFailure.capture(
            NSError(domain: "IdeviceGatewayError", code: 0,
                userInfo: [NSLocalizedDescriptionKey: "ApplicationVerificationFailed 0xE8008018"]),
            operation: "install", stage: .command, id: id)
        precondition(unrelatedStage.stage == .command)
        precondition(!unrelatedStage.technicalDetails.contains("installVerdict="))
        print("V3_AUTH_AND_PPQ_CLASSIFICATION_PASS")
    }
}
