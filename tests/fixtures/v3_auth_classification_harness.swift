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
    case badServerResponse(reason: String, jsonPayload: String)
    case invalidResponseFormat(rawPayload: String)
    case missingKey(key: String, jsonPayload: String)
    case underlyingError(code: Int, message: String)
}

enum SideSign {
    enum AnisetteError: Error {
        case noServersConfigured
        case allServersFailed
        case badServerResponse(statusCode: Int, payload: String)
    }
}

@main
struct AuthClassificationHarness {
    static func main() {
        precondition(v3ClassifyAuthError(DeveloperPortalError.incorrectCredentials) == .invalidCredentials)
        precondition(v3ClassifyAuthError(DeveloperPortalError.appSpecificPasswordRequired) == .appSpecificPasswordRequired)
        precondition(v3ClassifyAuthError(DeveloperPortalError.incorrectVerificationCode) == .invalidCode)
        precondition(v3ClassifyAuthError(DeveloperPortalError.tooManyAttempts) == .rateLimited)
        precondition(v3ClassifyAuthError(ServerError.underlyingError(
            code: -22411, message: "PRIVATE_RATE_LIMIT_BODY")) == .rateLimited)
        let invalidResponses: [ServerError] = [
            .badServerResponse(reason: "unreadable", jsonPayload: "PRIVATE_SERVER_BODY"),
            .invalidResponseFormat(rawPayload: "PRIVATE_SERVER_BODY"),
            .missingKey(key: "Status", jsonPayload: "PRIVATE_SERVER_BODY")
        ]
        for invalidResponse in invalidResponses {
            precondition(v3ClassifyAuthError(invalidResponse) == .serviceUnavailable)
        }
        precondition(v3ClassifyAuthError(DeveloperPortalError.invalidAnisetteData) == .anisette)
        precondition(v3ClassifyAuthError(SideSign.AnisetteError.noServersConfigured) == .anisette)
        precondition(v3ClassifyAuthError(SideSign.AnisetteError.allServersFailed) == .anisette)
        precondition(v3ClassifyAuthError(SideSign.AnisetteError.badServerResponse(
            statusCode: 503, payload: "PRIVATE_PROVIDER_BODY")) == .anisette)
        precondition(v3ClassifyAuthError(DeveloperPortalError.accountRepairRequired) == .accountRepairRequired)
        precondition(v3AuthFailureStage(.anisette) == .authentication,
            "Anisette failures retain the broad wire authentication stage")
        precondition(v3AuthFailureStage(.network) == .network,
            "network failures during provisioning retry retain the network stage")
        precondition(v3ClassifyAuthError(NSError(domain: NSURLErrorDomain, code: -1009)) == .network)
        precondition(v3ClassifyAuthError(URLError(.networkConnectionLost)) == .network)
        precondition(v3ClassifyAuthError(NSError(domain: "kCFErrorDomainCFNetwork",
            code: URLError.Code.dnsLookupFailed.rawValue)) == .network,
            "the canonical CFNetwork domain keeps known transport guidance")

        let connectionLost = URLError(.networkConnectionLost)
        let connectionLostKind = v3ClassifyAuthError(connectionLost)!
        var networkWire = CombinedFailure.capture(connectionLost,
            operation: "signIn", stage: .authentication, id: UUID().uuidString).wire
        networkWire["kind"] = connectionLostKind.rawValue
        let networkHostMessage = v3HostAuthFailureMessage(from: networkWire)
        precondition(networkWire["stage"] as? String == CombinedFailure.Stage.network.rawValue)
        precondition(networkHostMessage.contains("could not reach the required Apple service"))
        precondition(!networkHostMessage.contains("LocalDevVPN"))

        let badServerResponse = ServerError.invalidResponseFormat(rawPayload: "PRIVATE_BAD_RESPONSE")
        let badServerKind = v3ClassifyAuthError(badServerResponse)!
        var badResponseWire = CombinedFailure.capture(badServerResponse,
            operation: "signIn", stage: .authentication, id: UUID().uuidString).wire
        badResponseWire["kind"] = badServerKind.rawValue
        let badResponseHostMessage = v3HostAuthFailureMessage(from: badResponseWire)
        precondition(badServerKind == .serviceUnavailable)
        precondition(badResponseHostMessage.contains("did not return a valid response"))
        precondition(!badResponseHostMessage.contains("PRIVATE_BAD_RESPONSE"))

        // Cancellation is lifecycle evidence and URLSession uses this domain
        // code for it. It must not become a network retry or a displayed auth error.
        let urlCancellation = URLError(.cancelled)
        precondition(v3IsAuthCancellation(urlCancellation))
        precondition(v3ClassifyAuthError(urlCancellation) == nil)
        precondition(v3ClassifyAuthError(DeveloperPortalError.userCancelled) == nil)
        let cancelledFailure = CombinedFailure.capture(urlCancellation,
            operation: "signIn", stage: .authentication, id: UUID().uuidString)
        precondition(cancelledFailure.code == .cancelled && cancelledFailure.retryable == false)
        let cfNetworkCancellation = NSError(domain: "kCFErrorDomainCFNetwork",
            code: URLError.Code.cancelled.rawValue)
        precondition(v3IsAuthCancellation(cfNetworkCancellation))
        precondition(v3ClassifyAuthError(cfNetworkCancellation) == nil)
        precondition(CombinedFailure.capture(cfNetworkCancellation,
            operation: "signIn", stage: .authentication, id: UUID().uuidString).code == .cancelled)

        // URL-loading file I/O and other unrecognized URL errors remain an
        // honest auth-stage failure. They carry no network or LocalDevVPN advice.
        for localCode in [URLError.Code.cannotCreateFile, .cannotOpenFile,
                          .cannotWriteToFile, .cannotMoveFile,
                          .cannotCloseFile, .cannotRemoveFile] {
            let localFileError = URLError(localCode)
            precondition(v3ClassifyAuthError(localFileError) == .unknown,
                         "local URL file error is not an auth network failure: \(localCode)")
            let failure = CombinedFailure.capture(localFileError,
                operation: "signIn", stage: .authentication, id: UUID().uuidString)
            precondition(failure.stage == .authentication && failure.safeCause == nil)
            precondition(!failure.recovery.contains("LocalDevVPN"))
            var localWire = failure.wire
            localWire["kind"] = v3ClassifyAuthError(localFileError)!.rawValue
            let localHostMessage = v3HostAuthFailureMessage(from: localWire)
            precondition(localWire["stage"] as? String == CombinedFailure.Stage.authentication.rawValue)
            precondition(localHostMessage.contains("could not be safely classified"))
            precondition(!localHostMessage.contains("required Apple service"))
            precondition(!localHostMessage.contains("LocalDevVPN"))
        }
        let unknownURL = NSError(domain: NSURLErrorDomain, code: -9999)
        precondition(v3ClassifyAuthError(unknownURL) == .unknown)
        let unknownURLFailure = CombinedFailure.capture(unknownURL,
            operation: "signIn", stage: .authentication, id: UUID().uuidString)
        precondition(unknownURLFailure.stage == .authentication && unknownURLFailure.safeCause == nil)
        var unknownWire = unknownURLFailure.wire
        unknownWire["kind"] = V3AuthFailureKind.unknown.rawValue
        precondition(v3HostAuthFailureMessage(from: unknownWire).contains("could not be safely classified"))

        precondition(v3ClassifyAuthError(NSError(domain: "SideSignErrorDomain", code: 20)) == .unknown)
        precondition(v3ClassifyAuthError(NSError(domain: "ALTServerErrorDomain", code: 20)) == .unknown)

        let id = UUID().uuidString
        let provisioning = CombinedFailure.capture(
            NSError(domain: "SideSignErrorDomain", code: 20),
            operation: "install", stage: .installation, id: id)
        precondition(provisioning.stage == .installation, "provisioning was mislabeled as authentication")

        precondition(V3SignInFailureRoutingPolicy.shouldOpenSignIn(
            stage: .authentication, safeCause: nil),
            "an authentication failure should return the user to credentials")
        precondition(!V3SignInFailureRoutingPolicy.shouldOpenSignIn(
            stage: .authentication, safeCause: .keychainSignOutFailed),
            "a failed sign-out must surface its error without reopening sign-in")
        precondition(!V3SignInFailureRoutingPolicy.shouldOpenSignIn(
            stage: .command, safeCause: nil),
            "non-auth failures must not route to credentials")

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
