import Foundation

// V3_PROVISIONING_TYPED_GUIDANCE_HARNESS_V1
// A local mirror of the pinned SideStore.OperationError declaration, so the
// production guidance function can be executed in isolation. The case names and
// associated-value shapes must match
// SideStore/Core/Operations/Errors/OperationError.swift at the pinned revision.
//
// CustomNSError is part of the mirror on purpose. The pinned type conforms to it
// without implementing errorCode or errorDomain, so the bridged NSError integer
// carries no stable meaning and cannot be used as a semantic API. Conforming the
// mirror keeps the harness honest about the shape of that bridge.
enum OperationError: Error, CustomNSError {
    case unknown(failureReason: String? = nil)
    case unknownResult
    case timedOut
    case notAuthenticated
    case appNotFound(name: String? = nil)
    case unknownUDID
    case invalidApp(reason: String? = nil)
    case invalidParameters(String? = nil)
    case invalidOperationContext(String? = nil)
    case maximumAppIDLimitReached(appName: String, requiredAppIDs: Int, availableAppIDs: Int, expirationDate: Date)
    case noSources
    case noInstalledApps
    case openAppFailed(name: String? = nil)
    case missingAppGroup
    case forbidden(failureReason: String? = nil)
    case sourceNotAdded(name: String)
    case serverNotFound
    case connectionFailed
    case pledgeInactive(appName: String)
    case unableToConnectSideJIT
    case unableToRespondSideJITDevice
    case SideJITIssue(error: String?)
    case provisioningError(result: String, message: String? = nil)
    case certificateRevoked(appName: String)
    case customCertificateRevoked(appName: String, activeTeam: String)
    case customCertificateExpired(appName: String, activeTeam: String)
    case certificateExpired(appName: String)
    case certificateChanged(appName: String)
    case cacheClearError(errors: [String])
    case noConnection(reason: String? = nil)
    case noVPN(reason: String? = nil)
    case invalidVPN(reason: String? = nil)
    case noDevice(reason: String? = nil)
    case notReachable(reason: String)
    case invalidPairingFile(reason: String? = nil)
    case minimuxerNotStarted(reason: String? = nil)
    case pairingNotComplete(reason: String? = nil)
    case missingAppBundle
    case missingInfoPlist
    case missingProvisioningProfile
}

@main
struct ProvisioningTypedGuidanceHarness {
    static func main() {
        // The transport and pairing prerequisites a device needs to finish
        // provisioning after Apple authentication already succeeded.
        // A case that declares a default for its associated value is referenced
        // as a function outside a switch, so the payload is always supplied
        // explicitly here.
        precondition(v3OperationErrorGuidance(OperationError.noConnection(reason: nil)).message
            == "SideStore could not reach this device to finish provisioning.")
        precondition(v3OperationErrorGuidance(OperationError.noVPN(reason: nil)).hint.contains("LocalDevVPN"))
        precondition(v3OperationErrorGuidance(OperationError.invalidVPN(reason: nil)).hint.contains("LocalDevVPN"))
        precondition(v3OperationErrorGuidance(OperationError.noDevice(reason: nil)).hint.contains("endpoint"))
        precondition(v3OperationErrorGuidance(OperationError.notReachable(reason: "")).hint.contains("Connection"))
        precondition(v3OperationErrorGuidance(OperationError.invalidPairingFile(reason: nil)).hint.contains("pairing file"))
        precondition(v3OperationErrorGuidance(OperationError.minimuxerNotStarted(reason: nil)).hint.contains("pairing"))
        precondition(v3OperationErrorGuidance(OperationError.pairingNotComplete(reason: nil)).hint.contains("pairing file"))
        precondition(v3OperationErrorGuidance(OperationError.unknownUDID).message
            == "SideStore could not identify this device for registration.")

        // The account and certificate families.
        precondition(v3OperationErrorGuidance(.notAuthenticated).hint.contains("Sign in again"))
        precondition(v3OperationErrorGuidance(.certificateRevoked(appName: "X")).hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.customCertificateRevoked(appName: "X", activeTeam: "Y"))
            .hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.customCertificateExpired(appName: "X", activeTeam: "Y"))
            .hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.certificateExpired(appName: "X")).hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.certificateChanged(appName: "X")).hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.missingProvisioningProfile).hint.contains("Certificates"))
        precondition(v3OperationErrorGuidance(.maximumAppIDLimitReached(
            appName: "X", requiredAppIDs: 1, availableAppIDs: 0, expirationDate: Date())
            ).hint.contains("App ID"))
        precondition(v3OperationErrorGuidance(.timedOut).hint.contains("Retry"))
        precondition(v3OperationErrorGuidance(.connectionFailed).hint.contains("connection"))

        // No associated value is ever forwarded. The identical message and hint
        // for a populated and an empty payload prove the reason string from the
        // transport layer never reaches the user or the diagnostics.
        let secret = "SECRET-TRANSPORT-REASON-9f2c"
        let payloadPairs: [(OperationError, OperationError)] = [
            (OperationError.noConnection(reason: secret), OperationError.noConnection(reason: nil)),
            (OperationError.noVPN(reason: secret), OperationError.noVPN(reason: nil)),
            (OperationError.invalidVPN(reason: secret), OperationError.invalidVPN(reason: nil)),
            (OperationError.noDevice(reason: secret), OperationError.noDevice(reason: nil)),
            (OperationError.invalidPairingFile(reason: secret), OperationError.invalidPairingFile(reason: nil)),
            (OperationError.minimuxerNotStarted(reason: secret), OperationError.minimuxerNotStarted(reason: nil)),
            (OperationError.pairingNotComplete(reason: secret), OperationError.pairingNotComplete(reason: nil)),
            (OperationError.unknown(failureReason: secret), OperationError.unknown(failureReason: nil)),
            (OperationError.forbidden(failureReason: secret), OperationError.forbidden(failureReason: nil)),
            (OperationError.SideJITIssue(error: secret), OperationError.SideJITIssue(error: nil)),
            (OperationError.provisioningError(result: secret, message: secret),
             OperationError.provisioningError(result: secret, message: nil))
        ]
        for pair in payloadPairs {
            precondition(v3OperationErrorGuidance(pair.0).message
                == v3OperationErrorGuidance(pair.1).message,
                "an associated value changed the user-facing message")
            precondition(v3OperationErrorGuidance(pair.0).hint
                == v3OperationErrorGuidance(pair.1).hint,
                "an associated value changed the recovery hint")
            precondition(!v3OperationErrorGuidance(pair.0).message.contains(secret))
            precondition(!v3OperationErrorGuidance(pair.0).hint.contains(secret))
        }

        // An unclassified case stays honestly unclassified; it is never
        // relabelled as a credential, pairing, or manifest problem.
        let unclassified = v3OperationErrorGuidance(OperationError.serverNotFound)
        precondition(unclassified.message.contains("does not classify"))
        precondition(!unclassified.message.lowercased().contains("password"))
        precondition(!unclassified.message.lowercased().contains("manifest"))
        precondition(!unclassified.message.lowercased().contains("pairing"))

        // The bridged NSError integer is not a semantic API. Whether the
        // runtime synthesises a per-case ordinal or reports the CustomNSError
        // default, the value is an implementation detail, so it is recorded as
        // evidence and never asserted. The property that matters is that two
        // distinct typed cases still receive different guidance, so nothing can
        // be classified from that integer.
        let bridgedCodes = [
            (OperationError.noConnection(reason: secret) as NSError).code,
            (OperationError.unknownUDID as NSError).code,
            (OperationError.serverNotFound as NSError).code
        ]
        print("V3_PROVISIONING_BRIDGE_CODES \(bridgedCodes)")
        precondition(v3OperationErrorGuidance(OperationError.noConnection(reason: nil)).message
            != v3OperationErrorGuidance(OperationError.unknownUDID).message,
            "typed cases must not collapse when the bridged code is identical")
        precondition(v3OperationErrorGuidance(OperationError.unknownUDID).message
            != v3OperationErrorGuidance(OperationError.serverNotFound).message,
            "typed cases must not collapse when the bridged code is identical")

        // A successful Apple sign-in with a failed provisioning step is a
        // distinct terminal from a failed sign-in.
        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: true,
            authoritativeAccountMatches: false, provisioningFailed: true, cancelled: false)
            == "authenticatedProvisioningIncomplete")
        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: false,
            authoritativeAccountMatches: false, provisioningFailed: true, cancelled: false)
            == "failed")
        precondition(V3AuthTerminalPolicy.resolve(authenticationSucceeded: true,
            authoritativeAccountMatches: true, provisioningFailed: false, cancelled: false)
            == "completed")

        print("V3_PROVISIONING_TYPED_GUIDANCE_PASS")
    }
}
