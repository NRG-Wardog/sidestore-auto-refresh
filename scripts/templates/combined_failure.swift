import Foundation
import CoreFoundation

// LC_REFRESH_METADATA_SANITIZED_V1: never forward arbitrary saved result dictionaries.
public enum CombinedVerification {
    static let uncertainMutationKey = "liveContainerAutoRefreshUncertainMutationRunID"
    static func clearUncertainty(_ defaults: UserDefaults, runID: String) {
        guard defaults.string(forKey: uncertainMutationKey) == runID else { return }
        defaults.removeObject(forKey: uncertainMutationKey)
    }
    // Complete terminal results establish completion, not verified refresh success.
    // Empty, duplicated or omitted app results leave mutation completion uncertain.
    // The Setup Assistant reuses this exact contract: a partial manifest (for
    // example two expected apps but only one result) never verifies.
    public static func hasCompleteTerminalResults(_ manifest: [String: Any], runID: String) -> Bool {
        guard UUID(uuidString: runID) != nil, manifest["run_id"] as? String == runID,
              manifest["version"] as? Int == 2, manifest["schema"] as? String == "LiveContainerRefreshManifestV2",
              let expected = manifest["expected_ids"] as? [String], !expected.isEmpty, expected.count <= 1024,
              expected.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }), Set(expected).count == expected.count,
              let entries = manifest["results"] as? [[String: Any]], entries.count == expected.count else { return false }
        var received = Set<String>()
        for entry in entries {
            guard let identifier = entry["bundle_id"] as? String, expected.contains(identifier),
                  received.insert(identifier).inserted,
                  let success = entry["success"] as? NSNumber, CFGetTypeID(success) == CFBooleanGetTypeID() else { return false }
        }
        return received == Set(expected)
    }
    static func sanitized(_ payload: [String: Any], runID: String) -> [String: Any] {
        guard let manifest = payload["liveContainerAutoRefreshVerification"] as? [String: Any],
              manifest["run_id"] as? String == runID,
              let expected = manifest["expected_ids"] as? [String], expected.count <= 1024,
              expected.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }),
              let entries = manifest["results"] as? [[String: Any]], entries.count <= 1024 else { return [:] }
        var result: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": runID, "expected_ids": expected]
        if let date = manifest["date"] as? Date { result["date"] = date }
        if let handoff = manifest["host_handoff"] as? Bool { result["host_handoff"] = handoff }
        result["results"] = entries.compactMap { entry -> [String: Any]? in
            guard let identifier = entry["bundle_id"] as? String, expected.contains(identifier),
                  let success = entry["success"] as? Bool else { return nil }
            var item: [String: Any] = ["bundle_id": identifier, "success": success]
            for key in ["refreshed_date", "expiration_date"] { if let value = entry[key] as? Date { item[key] = value } }
            if !success {
                let native = NSError(domain: entry["error_domain"] as? String ?? "redacted", code: entry["error_code"] as? Int ?? 0,
                    userInfo: [NSLocalizedDescriptionKey: entry["error"] as? String ?? ""])
                let preserved = (entry["failure"] as? [String: Any]).flatMap { CombinedFailure.decode($0, expectedID: runID) }
                let failure = preserved ?? CombinedFailure.capture(native, operation: "refresh", stage: .refreshVerification, id: runID)
                item["error"] = failure.localizedDescription
                item["error_code"] = failure.underlyingCode; item["error_domain"] = failure.underlyingDomain
                item["failure"] = failure.wire
            }
            return item
        }
        var safe: [String: Any] = ["liveContainerAutoRefreshVerification": result]
        if payload["liveContainerAutoRefreshHostHandoffRunID"] as? String == runID {
            safe["liveContainerAutoRefreshHostHandoffRunID"] = runID
            safe["liveContainerAutoRefreshHostHandoff"] = payload["liveContainerAutoRefreshHostHandoff"] as? Bool ?? false
            for key in ["liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
                if let value = payload[key] as? Date { safe[key] = value }
            }
        }
        return safe
    }
}


// LC_STRUCTURED_FAILURE_V1: fixed vocabulary, no arbitrary userInfo/descriptions on the wire.
public struct CombinedFailure: Error, LocalizedError {
    public enum Stage: String, CaseIterable {
        case hostContainer, storagePreparation, bookmarkCreation, extensionDiscovery, extensionLaunch
        case xpcConnection, serviceReadiness, command, authentication, signing, installation, refreshVerification
        case endpointSelection, heartbeat, coreDevice, cdTunnel, rsdDiscovery, rsdService, lockdownConnection, uniqueDeviceID, pairing
        case network
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
                          "addSource": "source", "removeSource": "source", "refreshSources": "source", "syncAppIDs": "signIn",
                          "authBegin": "signIn", "authPoll": "signIn", "authRespond": "signIn", "authCancel": "signIn",
                          "opStart": "command", "opPoll": "command", "opAnswer": "command", "opCancel": "command",
                          "sourcePreview": "source", "sourceAddConfirmed": "source", "sourceRemoveConfirmed": "source"][operation] ?? operation
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
    private static let domains: Set<String> = ["none", "NSCocoaErrorDomain", "NSPOSIXErrorDomain", "NSURLErrorDomain", "NSOSStatusErrorDomain", "ALTServerErrorDomain", "ALTAppleAPIErrorDomain", "ALTErrorDomain", "MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError", "Foundation", "CoreData", "CoreFoundation", "IOKit", "Security", "CFNetwork", "HTTPStatus"]
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
        case .installation:
            // Apple-side application verification rejections carry fixed installd
            // codes. These describe profile/identity rejection, never an account
            // ban, and they do not imply a pairing or LocalDevVPN problem.
            if underlyingCode == 0xE8008024 {
                return "iOS reports that the provisioning profile is banned during application verification. Recreating pairing or changing LocalDevVPN settings is unlikely to address this specific error."
            }
            if underlyingCode == 0xE8008018 {
                return "iOS reports that the identity used to sign the executable is no longer valid. The app must be re-signed with a current signing identity."
            }
            return "SideStore could not complete the application installation."
        case .refreshVerification: return "Refresh completion could not be verified from the installation results."
        case .network: return "Network error during the \(operation) operation."
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
        case .endpointSelection, .heartbeat, .coreDevice, .cdTunnel, .rsdDiscovery, .rsdService, .lockdownConnection, .uniqueDeviceID, .network:
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
    public static func preserving(_ error: Error?, operation: String, stage: Stage, code: Code = .failed, id: String, retryable: Bool? = nil) -> CombinedFailure {
        if let known = error as? CombinedFailure {
            return known
        }
        return CombinedFailure(operation: operation, stage: stage, code: code, id: id, underlying: error, retryable: retryable)
    }
    public static func capture(_ error: Error, operation: String, stage: Stage, id: String) -> CombinedFailure {
        if let known = error as? CombinedFailure { return known }
        var cause = error as NSError
        var resolved = stage
        var nativeCode: Int?
        var nativeDomain: String?
        var ppqLocked = false
        // Only an allowlisted stage is inspected locally. No arbitrary userInfo is serialized.
        for _ in 0..<5 {
            // Domain-specific classification. Only map a numeric code to a
            // stage when the (domain, code) pair has an established meaning.
            // Otherwise preserve the caller stage and keep the underlying
            // domain/code for diagnostics. Unknown stays unknown.
            // Application-verification evidence below is more specific than
            // these generic domain mappings, so once found it locks them out;
            // explicit upstream stage markers still win.
            if !ppqLocked {
                switch cause.domain {
                case "com.SideStore.Authentication":
                    resolved = .authentication
                case "ALTAppleAPIErrorDomain", "ALTServerErrorDomain", "GrandSlamErrorDomain", "SideSignErrorDomain":
                    resolved = .authentication
                case "NSPOSIXErrorDomain":
                    // POSIX error domains carry standard errno values.
                    resolved = .network
                case "NSURLErrorDomain":
                    resolved = .network
                default:
                    break
                }
            }
            // Apple-side installation rejection (InstallationProxy/installd
            // application verification). The hex installer codes are matched
            // case-insensitively alongside the verification marker; the stage
            // is installation and the numeric code is preserved with the
            // cause's own allowlisted domain (never a fabricated one).
            // 0xE8008024: provisioning profile banned. 0xE8008018: signing
            // identity no longer valid. Neither implies pairing, network,
            // CoreDevice, or account-ban conditions.
            let fingerprint = cause.localizedDescription.lowercased()
            if fingerprint.contains("applicationverificationfailed") {
                if fingerprint.contains("e8008024") {
                    resolved = .installation
                    nativeCode = 0xE8008024
                    if nativeDomain == nil, domains.contains(cause.domain) { nativeDomain = cause.domain }
                    ppqLocked = true
                } else if fingerprint.contains("e8008018") {
                    resolved = .installation
                    nativeCode = 0xE8008018
                    if nativeDomain == nil, domains.contains(cause.domain) { nativeDomain = cause.domain }
                    ppqLocked = true
                }
            }
            // Explicit stage marker from upstream
            if let name = cause.userInfo["LCStructuredFailureStageV1"] as? String, let found = Stage(rawValue: name) { resolved = found }
            // Upstream gateway/Minimuxer typed errors carry a reason string. Inspect only
            // our fixed machine tokens locally; never forward the reason itself.
            // A preserved numeric code keeps the domain it was actually observed
            // in: gateway tokens stay in their gateway domain, HTTP statuses use
            // the fixed HTTPStatus domain, and POSIX errnos stay in
            // NSPOSIXErrorDomain. No unrelated code is ever relabelled as a
            // gateway error.
            let tokens = cause.localizedDescription.split(whereSeparator: { $0.isWhitespace })
            for (index, token) in tokens.enumerated() {
                if token.hasPrefix("lc_stage="), let found = Stage(rawValue: String(token.dropFirst(9))) { resolved = found }
                guard !ppqLocked else { continue }
                if token.hasPrefix("lc_native_code="), let code = Int(token.dropFirst(15)) {
                    nativeCode = code
                    if ["MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError"].contains(cause.domain) {
                        nativeDomain = cause.domain
                    }
                }
                // HTTP status in "HTTP 503" form (tokens are whitespace-split).
                if (token == "HTTP" || token == "http"), index + 1 < tokens.count,
                   let code = Int(tokens[index + 1]) {
                    nativeCode = code
                    nativeDomain = "HTTPStatus"
                }
                // POSIX errno in "errno=20" / "errno:20" form.
                if token.hasPrefix("errno=") || token.hasPrefix("errno:") {
                    if let code = Int(token.dropFirst(6)) {
                        nativeCode = code
                        nativeDomain = "NSPOSIXErrorDomain"
                    }
                }
            }
            if let next = cause.userInfo[NSUnderlyingErrorKey] as? NSError { cause = next } else { break }
        }
        let underlying: NSError
        if let code = nativeCode {
            if let domain = nativeDomain {
                underlying = NSError(domain: domain, code: code)
            } else if domains.contains(cause.domain) {
                underlying = NSError(domain: cause.domain, code: code)
            } else {
                underlying = NSError(domain: "redacted", code: code)
            }
        } else {
            underlying = cause
        }
        return CombinedFailure(operation: operation, stage: resolved,
            code: error is CancellationError ? .cancelled : .failed, id: id,
            underlying: underlying)
    }
}
