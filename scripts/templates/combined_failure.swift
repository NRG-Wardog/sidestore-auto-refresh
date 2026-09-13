import Foundation
import CoreFoundation

// LC_STRUCTURED_FAILURE_V1: fixed vocabulary, no arbitrary userInfo/descriptions on the wire.
public struct CombinedFailure: Error, LocalizedError {
    public enum Stage: String, CaseIterable {
        case hostContainer, storagePreparation, bookmarkCreation, extensionDiscovery, extensionLaunch
        case xpcConnection, serviceReadiness, command, authentication, signing, installation, refreshVerification
        case endpointSelection, heartbeat, coreDevice, cdTunnel, rsdDiscovery, rsdService, lockdownConnection, uniqueDeviceID, pairing
    }
    public enum Code: String, CaseIterable {
        case unavailable, invalidConfiguration, permissionDenied, timedOut, cancelled, interrupted
        case notReady, busy, invalidResponse, unsupported, failed, missingResult, staleResult
    }
    public let operation: String
    public let stage: Stage
    public let code: Code
    public let correlationID: String
    public let underlyingDomain: String
    public let underlyingCode: Int
    public let retryable: Bool?
    public init(operation: String, stage: Stage, code: Code = .failed, id: String,
                underlying: Error? = nil, retryable: Bool? = nil) {
        let normalized = ["snapshot": "status", "refreshApp": "refresh", "installURL": "install", "installSharedIPA": "install",
                          "addSource": "source", "removeSource": "source", "refreshSources": "source", "syncAppIDs": "signIn"][operation] ?? operation
        self.operation = Self.operations.contains(normalized) ? normalized : "command"
        self.stage = stage; self.code = code
        correlationID = UUID(uuidString: id) != nil ? id : UUID().uuidString
        let error = underlying as NSError?
        let domain = error?.domain ?? "none"
        underlyingDomain = Self.domains.contains(domain) ? domain : "redacted"
        underlyingCode = error?.code ?? 0
        self.retryable = retryable
    }
    private static let operations: Set<String> = ["connect", "status", "command", "refresh", "install", "update", "signIn", "signOut", "catalog", "source", "sign", "activate", "deactivate", "delete", "remove", "backup", "restore", "jit"]
    private static let domains: Set<String> = ["none", "NSCocoaErrorDomain", "NSPOSIXErrorDomain", "NSURLErrorDomain", "NSOSStatusErrorDomain", "ALTServerErrorDomain", "ALTAppleAPIErrorDomain", "ALTErrorDomain", "MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError"]
    public var message: String {
        if code == .cancelled { return "The \(operation) request was cancelled. Its result may need reconciliation." }
        if code == .timedOut { return "The \(operation) request timed out during \(stage.rawValue)." }
        switch stage {
        case .hostContainer: return "SideStore could not start because the authoritative host container is unavailable."
        case .storagePreparation: return "SideStore could not start because its existing data storage could not be prepared."
        case .bookmarkCreation: return "SideStore could not start because its data bookmark could not be created."
        case .extensionDiscovery: return "The embedded LiveProcess extension is missing or unavailable."
        case .extensionLaunch: return "The embedded SideStore process could not be launched."
        case .xpcConnection: return "The connection to the embedded SideStore service was interrupted or unavailable."
        case .serviceReadiness: return "The SideStore process has not finished preparing its service."
        case .endpointSelection: return "No usable device transport endpoint was selected."
        case .heartbeat: return "The device transport heartbeat is inactive."
        case .coreDevice: return "Could not connect to the device through CoreDevice."
        case .cdTunnel: return "The CoreDevice tunnel could not be established."
        case .rsdDiscovery: return "Device service discovery through RSD failed."
        case .rsdService: return "The requested RSD device service could not be connected."
        case .lockdownConnection: return "The device transport opened, but the lockdownd connection failed."
        case .uniqueDeviceID: return "The device connection opened, but the UniqueDeviceID request failed."
        case .pairing: return "Pairing parsing, validation, or a concrete device trust check failed."
        case .authentication: return "SideStore could not complete account authentication."
        case .signing: return "SideStore could not sign the application."
        case .installation: return "SideStore could not complete the application installation."
        case .refreshVerification: return "Refresh completion could not be verified from the installation results."
        case .command: return "SideStore could not complete the requested \(operation) command (\(code.rawValue))."
        }
    }
    public var recovery: String {
        switch stage {
        case .hostContainer, .storagePreparation, .bookmarkCreation:
            return "Keep existing data intact. Return to the host, check available storage, and use Retry Connection. Copy these diagnostics if it fails again."
        case .extensionDiscovery:
            return "Check that the installed combined package retains LiveProcess and its extension registration. Do not reset SideStore or guest data."
        case .authentication, .signing: return "Review Account and Signing, then explicitly retry. Never share credentials or private keys."
        case .installation, .refreshVerification: return "Reload authoritative app status and expiration before retrying. Completion may be uncertain."
        case .endpointSelection, .heartbeat, .coreDevice, .cdTunnel, .rsdDiscovery, .rsdService, .lockdownConnection, .uniqueDeviceID:
            return "Check LocalDevVPN and the device connection, then retry explicitly. This failure alone does not prove invalid pairing."
        default: return "Reconnect explicitly and reload authoritative status before repeating a mutation."
        }
    }
    public var technicalDetails: String {
        "schema=1 operation=\(operation) stage=\(stage.rawValue) code=\(code.rawValue) correlation=\(correlationID) underlying_domain=\(underlyingDomain) underlying_code=\(underlyingCode) retryable=\(retryable.map(String.init) ?? "unknown")"
    }
    public var errorDescription: String? { message + "\n" + recovery + "\n" + technicalDetails }
    public var wire: [String: Any] {
        var result: [String: Any] = ["version": 1, "operation": operation, "stage": stage.rawValue, "code": code.rawValue,
            "correlationID": correlationID, "underlyingDomain": underlyingDomain, "underlyingCode": underlyingCode]
        if let retryable { result["retryable"] = retryable }
        return result
    }
    public var encodedString: String {
        guard let data = try? PropertyListSerialization.data(fromPropertyList: wire, format: .binary, options: 0), data.count <= 4096 else { return "LCFAILURE1:invalid" }
        return "LCFAILURE1:" + data.base64EncodedString()
    }
    public static func fromEncodedString(_ text: String, expectedID: String) -> CombinedFailure? {
        guard text.hasPrefix("LCFAILURE1:"), text.utf8.count <= 6000,
              let data = Data(base64Encoded: String(text.dropFirst(11))), data.count <= 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else { return nil }
        return decode(value, expectedID: expectedID)
    }
    public static func decode(_ value: [String: Any], expectedID: String) -> CombinedFailure? {
        guard Set(value.keys).isSubset(of: ["version", "operation", "stage", "code", "correlationID", "underlyingDomain", "underlyingCode", "retryable"]),
              let version = value["version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
              value["version"] as? Int == 1, value["correlationID"] as? String == expectedID,
              let operation = value["operation"] as? String, operations.contains(operation),
              let stageName = value["stage"] as? String, let stage = Stage(rawValue: stageName),
              let codeName = value["code"] as? String, let code = Code(rawValue: codeName),
              let domain = value["underlyingDomain"] as? String, domains.contains(domain) || domain == "redacted",
              let numberValue = value["underlyingCode"] as? NSNumber, CFGetTypeID(numberValue) != CFBooleanGetTypeID(),
              let number = value["underlyingCode"] as? Int else { return nil }
        if let retry = value["retryable"] {
            guard let bool = retry as? NSNumber, CFGetTypeID(bool) == CFBooleanGetTypeID() else { return nil }
        }
        return CombinedFailure(operation: operation, stage: stage, code: code, id: expectedID,
            underlying: NSError(domain: domain, code: number), retryable: value["retryable"] as? Bool)
    }
    public static func capture(_ error: Error, operation: String, stage: Stage, id: String) -> CombinedFailure {
        if let known = error as? CombinedFailure { return known }
        var cause = error as NSError
        var resolved = stage
        var nativeCode: Int?
        // Only an allowlisted stage is inspected locally. No arbitrary userInfo is serialized.
        for _ in 0..<5 {
            if let name = cause.userInfo["LCFailureStage"] as? String, let found = Stage(rawValue: name) { resolved = found }
            // Upstream gateway/Minimuxer typed errors carry a reason string. Inspect only
            // our fixed machine tokens locally; never forward the reason itself.
            let tokens = cause.localizedDescription.split(whereSeparator: { $0.isWhitespace })
            for token in tokens {
                if token.hasPrefix("lc_stage="), let found = Stage(rawValue: String(token.dropFirst(9))) { resolved = found }
                if token.hasPrefix("lc_native_code=") { nativeCode = Int(token.dropFirst(15)) }
            }
            if let next = cause.userInfo[NSUnderlyingErrorKey] as? NSError { cause = next } else { break }
        }
        return CombinedFailure(operation: operation, stage: resolved,
            code: error is CancellationError ? .cancelled : .failed, id: id,
            underlying: nativeCode.map { NSError(domain: "DeviceGatewayError", code: $0) } ?? cause)
    }
}
