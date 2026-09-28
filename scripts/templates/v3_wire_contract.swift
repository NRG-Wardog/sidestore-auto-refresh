import Foundation
import CoreFoundation
import CryptoKit

public struct V3AuthServiceSnapshot: Equatable {
    public let authenticated: Bool
    public let provisioningIncomplete: Bool
    public let provisioningRetryAvailable: Bool
    public let authenticationActive: Bool
    public let authenticationSessionID: String?

    public init(authenticated: Bool, provisioningIncomplete: Bool,
                provisioningRetryAvailable: Bool, authenticationActive: Bool,
                authenticationSessionID: String?) {
        self.authenticated = authenticated
        self.provisioningIncomplete = provisioningIncomplete
        self.provisioningRetryAvailable = provisioningRetryAvailable
        self.authenticationActive = authenticationActive
        self.authenticationSessionID = authenticationSessionID
    }
}

// V3_WIRE_CONTRACT_V1: shared source, compiled independently in each process.
// V3_HEADLESS_CONTRACT_V2: SideStore is a headless backend. All presentation
// decisions cross as data (prompts/confirmations); no remote UI is addressed.
enum V3WireContract {
    static let requestLimit = 16_384
    static let responseLimit = 4_194_304
    static let authSessionLifetime: TimeInterval = 600
    static let cancellationScopes: Set<String> = ["auth", "operation", "request"]

    static func strictBool(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    static func strictInt(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let type = String(cString: number.objCType)
        guard ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) else {
            return nil
        }
        return number.intValue
    }

    static func authSnapshot(_ reply: [String: Any]) -> V3AuthServiceSnapshot? {
        guard let authenticated = strictBool(reply["authenticated"]),
              let provisioningIncomplete = strictBool(reply["provisioningIncomplete"]),
              let provisioningRetryAvailable = strictBool(reply["provisioningRetryAvailable"]),
              let authenticationActive = strictBool(reply["authenticationActive"]) else {
            return nil
        }
        let authenticationSessionID: String?
        if let rawAuthenticationSessionID = reply["authenticationSessionID"] {
            guard let value = rawAuthenticationSessionID as? String else { return nil }
            authenticationSessionID = value
        } else {
            authenticationSessionID = nil
        }
        if authenticationActive {
            guard let authenticationSessionID,
                  UUID(uuidString: authenticationSessionID)?.uuidString == authenticationSessionID else { return nil }
        } else if authenticationSessionID != nil {
            return nil
        }
        return V3AuthServiceSnapshot(authenticated: authenticated,
            provisioningIncomplete: provisioningIncomplete,
            provisioningRetryAvailable: provisioningRetryAvailable,
            authenticationActive: authenticationActive,
            authenticationSessionID: authenticationSessionID)
    }

    static func invalidRequestIdentity(from data: Data) -> (id: String?, operation: String?) {
        guard data.count <= requestLimit,
              let envelope = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
            return (nil, nil)
        }
        let rawID = envelope["id"] as? String
        // Preserve the caller's spelling so reply correlation remains exact;
        // UUID(uuidString:) accepts lowercase forms as valid UUIDs too.
        let id = rawID.flatMap { UUID(uuidString: $0) != nil ? $0 : nil }
        let rawOperation = envelope["operation"] as? String
        let operation = rawOperation.flatMap { operations.contains($0) ? $0 : nil } ?? "command"
        return (id, operation)
    }

    static let operations: Set<String> = ["snapshot", "catalog", "appIcon", "cancel", "refreshSources",
        "refreshAdmissionBegin", "refreshAdmissionEnd",
        "signOut", "syncAppIDs", "clearCache", "jit", "backupResult",
        "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning",
        "opStart", "opPoll", "opAnswer", "opCancel", "opRecoveryPrepare", "opRecoveryReconcile",
        "refreshAdmissionReconcile", "ipaCleanup", "ipaActiveTokens",
        "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "settingsGet", "settingsSet",
        "anisetteList", "anisetteReset", "anisetteSync",
        "sidesignGet", "sidesignSet", "sidesignReset", "sidesignImport", "sidesignExport",
        "logTail", "healthSnapshot", "accountExport", "accountImport"]
    static let readOperations: Set<String> = ["snapshot", "catalog", "appIcon",
        "authPoll", "opPoll", "opCancel", "ipaCleanup", "ipaActiveTokens", "authCancel", "certList", "certPortalList",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "settingsGet",
        "anisetteList", "sidesignGet", "sidesignExport", "logTail", "healthSnapshot"]

    static func decodeRequest(_ data: Data, now: Date = Date()) -> [String: Any]? {
        guard data.count <= requestLimit,
              let request = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(request.keys).isSubset(of: ["version", "id", "operation", "target", "deadline", "cursor", "payload"]),
              strictInt(request["version"]) == 1,
              let id = request["id"] as? String, UUID(uuidString: id) != nil,
              let operation = request["operation"] as? String, operations.contains(operation),
              let target = request["target"] as? String, target.utf8.count <= 4096,
              let deadline = request["deadline"] as? Date,
              deadline > now, deadline.timeIntervalSince(now) <= 610 else { return nil }
        if request["value"] != nil { return nil }
        let emptyTargetOperations: Set<String> = [
            "snapshot", "opStart", "accountExport", "ipaActiveTokens", "refreshSources", "signOut",
            "syncAppIDs", "clearCache", "settingsGet", "settingsSet", "sidesignGet", "sidesignSet",
            "sidesignReset", "sidesignExport", "anisetteList", "anisetteReset", "anisetteSync",
            "healthSnapshot", "logTail", "certList", "certPortalList", "certCreate", "opRecoveryPrepare",
            "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles"
        ]
        if emptyTargetOperations.contains(operation) && !target.isEmpty { return nil }
        if ["authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning",
            "opPoll", "opAnswer", "opCancel", "pairingImportData", "sidesignImport", "accountImport",
            "refreshAdmissionBegin", "refreshAdmissionEnd", "refreshAdmissionReconcile",
            "opRecoveryReconcile", "cancel"].contains(operation),
           !canonicalSecretToken(target) { return nil }
        if operation == "ipaCleanup", !canonicalLowercaseFileToken(target) { return nil }
        if ["appIcon", "jit"].contains(operation),
           !acceptsCoreDataTarget(target, entity: "InstalledApp") { return nil }
        if ["sourcePreview", "sourceAddConfirmed"].contains(operation), !isHTTPURL(target) { return nil }
        if operation == "backupResult", !["success", "failure"].contains(target) { return nil }
        if let cursor = request["cursor"] {
            guard operation == "catalog", let value = strictInt(cursor),
                  value >= 0, value <= 1_000_000 else { return nil }
        }
        if let rawPayload = request["payload"] {
            guard let payload = rawPayload as? [String: Any],
                  acceptsPayload(operation: operation, target: target, payload: payload, now: now) else { return nil }
        } else if requiredPayloadOperations.contains(operation) {
            return nil
        }
        return request
    }

    // Apply the service's exact request schema before a host message can cross
    // XPC. Decoding remains mandatory in the service; this outbound pass keeps
    // raw secrets and unsupported fields from being transmitted at all.
    static func encodeRequest(_ request: [String: Any], now: Date = Date()) -> Data? {
        guard let data = try? PropertyListSerialization.data(
                fromPropertyList: request, format: .binary, options: 0),
              decodeRequest(data, now: now) != nil else { return nil }
        return data
    }

    private static let requiredPayloadOperations: Set<String> = [
        "authBegin", "authRetryProvisioning", "authRespond", "opAnswer", "opStart", "opRecoveryPrepare",
        "cancel", "accountExport", "accountImport", "settingsSet", "sidesignSet"
    ]

    private static func acceptsPayload(operation: String, target: String,
                                       payload: [String: Any], now: Date) -> Bool {
        guard !containsRawSecretField(payload) else { return false }
        switch operation {
        case "snapshot":
            return Set(payload.keys) == Set(["readinessOnly"]) &&
                strictBool(payload["readinessOnly"]) == true
        case "authBegin", "authRetryProvisioning":
            guard Set(payload.keys) == Set(["session", "sessionDeadline"]),
                  let session = payload["session"] as? String,
                  canonicalSecretToken(session), session == target,
                  let sessionDeadline = payload["sessionDeadline"] as? Date,
                  sessionDeadline > now,
                  sessionDeadline.timeIntervalSince(now) <= authSessionLifetime + 10 else { return false }
            return true
        case "cancel":
            guard Set(payload.keys) == Set(["scope"]),
                  let scope = payload["scope"] as? String else { return false }
            return cancellationScopes.contains(scope)
        case "authRespond", "opAnswer":
            guard Set(payload.keys) == Set(["prompt", "secretToken"]),
                  let prompt = payload["prompt"] as? String, !prompt.isEmpty, prompt.utf8.count <= 256,
                  canonicalSecretToken(payload["secretToken"]) else { return false }
            return true
        case "accountExport":
            guard Set(payload.keys) == Set(["secretToken", "includeApple"]),
                  canonicalSecretToken(payload["secretToken"]),
                  strictBool(payload["includeApple"]) != nil else { return false }
            return true
        case "accountImport":
            return Set(payload.keys) == Set(["secretToken"]) && canonicalSecretToken(payload["secretToken"])
        case "opStart":
            guard Set(payload.keys) == Set(["kind", "target", "session"]),
                  let kind = payload["kind"] as? String, !kind.isEmpty, kind.utf8.count <= 128,
                  let operationTarget = payload["target"] as? String, operationTarget.utf8.count <= 4096,
                  let session = payload["session"] as? String, canonicalSecretToken(session) else { return false }
            return acceptsOperationTarget(kind: kind, target: operationTarget)
        case "opRecoveryPrepare":
            guard Set(payload.keys) == Set(["kind", "target", "session"]),
                  let kind = payload["kind"] as? String,
                  let operationTarget = payload["target"] as? String, operationTarget.utf8.count <= 4096,
                  let session = payload["session"] as? String, canonicalSecretToken(session) else { return false }
            return acceptsOperationTarget(kind: kind, target: operationTarget)
        case "opRecoveryReconcile", "refreshAdmissionReconcile":
            return Set(payload.keys) == Set(["userConfirmed"]) && strictBool(payload["userConfirmed"]) == true
        case "opCancel":
            return Set(payload.keys) == Set(["knownStarted"]) &&
                strictBool(payload["knownStarted"]) != nil
        case "settingsSet":
            guard let key = payload["key"] as? String, !key.isEmpty, key.utf8.count <= 256,
                  let type = payload["type"] as? String else { return false }
            switch type {
            case "bool":
                return Set(payload.keys) == Set(["key", "type", "bool"]) && strictBool(payload["bool"]) != nil
            case "string":
                guard Set(payload.keys) == Set(["key", "type", "string"]),
                      let value = payload["string"] as? String else { return false }
                return value.utf8.count <= 8192
            case "int":
                return Set(payload.keys) == Set(["key", "type", "int"]) && strictInt(payload["int"]) != nil
            default: return false
            }
        case "sidesignSet":
            return Set(payload.keys) == Set(["secretToken"]) &&
                canonicalSecretToken(payload["secretToken"])
        default:
            // Every unlisted operation is payloadless. New payload-bearing
            // commands must add an explicit schema before crossing XPC.
            return false
        }
    }

    private static func canonicalSecretToken(_ value: Any?) -> Bool {
        guard let token = value as? String, let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString == token
    }

    private static func canonicalLowercaseFileToken(_ token: String) -> Bool {
        guard token.utf8.count == 36, let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString.lowercased() == token
    }

    private static func acceptsOperationTarget(kind: String, target: String) -> Bool {
        switch kind {
        case "installSharedIPA":
            return canonicalLowercaseFileToken(target)
        case "installURL":
            return isHTTPURL(target)
        case "install":
            return acceptsCoreDataTarget(target, entity: "StoreApp")
        case "update", "refreshApp", "activate", "deactivate", "remove", "delete", "backup", "restore":
            return acceptsCoreDataTarget(target, entity: "InstalledApp")
        default:
            return false
        }
    }

    private static func acceptsCoreDataTarget(_ target: String, entity expectedEntity: String) -> Bool {
        guard let components = URLComponents(string: target),
              components.scheme?.lowercased() == "x-coredata",
              let host = components.host, UUID(uuidString: host) != nil,
              components.user == nil, components.password == nil,
              components.port == nil, components.query == nil, components.fragment == nil else { return false }
        let path = components.percentEncodedPath.split(separator: "/")
        return path.count == 2 && String(path[0]) == expectedEntity &&
            path[1].first == "p" && Int(path[1].dropFirst()) != nil
    }

    private static func isHTTPURL(_ value: String) -> Bool {
        guard let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil else { return false }
        if let port = components.port, !(1...65535).contains(port) { return false }
        return true
    }

    private static let sensitiveSecretFieldFragments: [String] = [
        "appleid", "password", "passphrase", "answer", "verificationcode", "securitycode", "otp",
        "privatekey", "p12", "credential", "auth", "accesstoken", "refreshtoken",
        "authorization", "cookie", "dsid", "phoneid", "phonenumber", "secret", "token", "udid"
    ]

    private static func containsRawSecretField(_ value: Any) -> Bool {
        var pending: [Any] = [value]
        while let current = pending.popLast() {
            if let dictionary = current as? [String: Any] {
                for (key, nested) in dictionary {
                    let normalized = key.lowercased().filter { $0.isLetter || $0.isNumber }
                    // This UUID is a non-secret, one-time capability allowed
                    // only by the exact schemas validated below.
                    let opaqueHandoffToken = normalized == "secrettoken"
                    if !opaqueHandoffToken && sensitiveSecretFieldFragments.contains(where: { normalized.contains($0) }) {
                        return true
                    }
                    pending.append(nested)
                }
            } else if let array = current as? [Any] {
                pending.append(contentsOf: array)
            }
        }
        return false
    }

    // V3_PROPERTY_LIST_VALUE_V1
    // Property lists cannot encode a Swift Optional that has been boxed into
    // `Any`. Assigning `someOptional` to an `[String: Any]` value stores
    // `Optional<T>.none` as a live object, and serialization then fails for the
    // whole response, long after the value was read correctly from its owner.
    //
    // V3_PLIST_LEAF_CONTRACT_V1: the accepted leaf set is Foundation's, not a
    // hand-written list, so it cannot drift from CoreFoundation. The previous
    // list accepted `URL`, which CoreFoundation rejects for every property-list
    // format except OpenStep: a `URL` object is not a property-list leaf and a
    // URL must be sent as `url.absoluteString`. It also rejected `Float` and the
    // narrow integer types, which do serialize. `NSNumber` is used because every
    // Swift numeric type bridges to it, including Bool, so one case covers the
    // whole numeric family without a remembered list.
    enum V3PropertyListValue {
        /// Returns the unwrapped value, or nil when it is absent.
        ///
        /// Only the Optional case is unwrapped. A value that is present but not
        /// representable is returned unchanged so the encoder can report a real
        /// encoding failure instead of silently dropping data.
        static func unwrapOptional(_ value: Any?) -> Any? {
            guard let value else { return nil }
            let mirror = Mirror(reflecting: value)
            guard mirror.displayStyle == .optional else { return value }
            return mirror.children.first?.value
        }

        /// Builds a property-list-safe dictionary, omitting keys whose value is
        /// an absent Optional. A key whose value is present but unrepresentable
        /// is preserved so serialization fails loudly rather than quietly.
        static func dictionary(_ entries: [String: Any?]) -> [String: Any] {
            var result: [String: Any] = [:]
            result.reserveCapacity(entries.count)
            for (key, value) in entries {
                if let unwrapped = unwrapOptional(value) { result[key] = unwrapped }
            }
            return result
        }

        /// True when a value can be encoded by PropertyListSerialization.
        ///
        /// `URL` is deliberately absent and unknown types are rejected rather
        /// than stringified: silently coercing an arbitrary object would put
        /// unreviewable text on the wire, and dropping it would lose data without
        /// reporting anything.
        static func isEncodable(_ value: Any) -> Bool {
            // A still-boxed Optional is never encodable, so an absent value
            // reports false rather than being silently accepted.
            guard let unwrapped = unwrapOptional(value) else { return false }
            if unwrapped is String || unwrapped is NSNumber
                || unwrapped is Date || unwrapped is Data { return true }
            if let array = unwrapped as? [Any] { return array.allSatisfy { isEncodable($0) } }
            if let dictionary = unwrapped as? [String: Any] {
                return dictionary.values.allSatisfy { isEncodable($0) }
            }
            return false
        }
    }
}

enum V3RequestReplayPolicy {
    static let cancellationOperations: Set<String> = ["cancel", "authCancel", "opCancel"]

    static func requiresCompletedReply(operation: String) -> Bool {
        cancellationOperations.contains(operation)
    }

    static func fingerprint(_ requestData: Data) -> Data {
        Data(SHA256.hash(data: requestData))
    }

    static func matches(cachedFingerprint: Data?, incomingRequestData: Data) -> Bool {
        guard let cachedFingerprint else { return false }
        return cachedFingerprint == fingerprint(incomingRequestData)
    }

    static func matchesInFlight(cachedFingerprint: Data?, incomingRequestData: Data) -> Bool {
        matches(cachedFingerprint: cachedFingerprint, incomingRequestData: incomingRequestData)
    }

    static func isIdentifierCollision(cachedFingerprint: Data?, incomingRequestData: Data) -> Bool {
        guard let cachedFingerprint else { return false }
        return !matches(cachedFingerprint: cachedFingerprint, incomingRequestData: incomingRequestData)
    }

    static func mayClaimNotDispatched(operation: String, identifierCollision: Bool) -> Bool {
        !identifierCollision && ["opStart", "authBegin", "authRetryProvisioning"].contains(operation)
    }
}

enum V3RefreshAdmissionCancellationAckPolicy {
    static func accepts(_ data: Data, cancellationID: String) -> Bool {
        guard !data.isEmpty, data.count <= V3WireContract.responseLimit,
              let reply = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              V3WireContract.strictInt(reply["version"]) == 1,
              reply["id"] as? String == cancellationID,
              V3WireContract.strictBool(reply["ok"]) == true,
              V3WireContract.strictBool(reply["refreshAdmissionReleased"]) == true else { return false }
        return true
    }
}

struct V3MutationReplyCacheBudget {
    static let maximumStoredBytes = 64 * 1024 * 1024
    static let maximumStoredReplies = 512
    static let reservedControlBytes = V3WireContract.responseLimit * 2
    // Prompt acknowledgements are not stored in the completed-request cache;
    // the session's accepted-prompt ledger makes them idempotent. Keep a small
    // reserve for starts and refresh admission release replies.
    static let authenticationLifecycleReplyBudget = 2
    static let provisioningRetryReplyBudget = 1
    static let operationPromptReplyBudget = 1
    static let reservedControlReplies = 8
    private(set) var storedBytes = 0

    static func isControlReply(operation: String) -> Bool {
        ["refreshAdmissionEnd", "refreshAdmissionReconcile", "opRecoveryReconcile",
         "authBegin", "authRetryProvisioning", "opStart"]
            .contains(operation) || V3RequestReplayPolicy.requiresCompletedReply(operation: operation)
    }

    static func shouldCacheResponse(operation: String) -> Bool {
        !["authRespond", "opAnswer"].contains(operation)
    }

    static func minimumAvailableRepliesToAdmit(operation: String) -> Int {
        switch operation {
        case "authBegin": return authenticationLifecycleReplyBudget
        case "authRetryProvisioning": return provisioningRetryReplyBudget
        case "opStart": return operationPromptReplyBudget
        default: return 1
        }
    }

    static func minimumReplyBytesToAdmit(operation: String) -> Int {
        operation == "authBegin"
            ? V3WireContract.responseLimit * 2
            : V3WireContract.responseLimit
    }

    static func canAdmit(operation: String, completedReplyCount: Int) -> Bool {
        let required = minimumAvailableRepliesToAdmit(operation: operation)
        return completedReplyCount >= 0 && completedReplyCount <= maximumStoredReplies - required
    }

    func canReserve(maximumResponseBytes: Int = V3WireContract.responseLimit,
                    preservingControlCapacity: Bool = true) -> Bool {
        let limit = Self.maximumStoredBytes - (preservingControlCapacity ? Self.reservedControlBytes : 0)
        return maximumResponseBytes >= 0 && maximumResponseBytes <= limit &&
            storedBytes <= limit - maximumResponseBytes
    }

    mutating func record(_ byteCount: Int, controlResponse: Bool = false) -> Bool {
        guard byteCount >= 0,
              canReserve(maximumResponseBytes: byteCount, preservingControlCapacity: !controlResponse) else { return false }
        storedBytes += byteCount
        return true
    }

    static func responseCountLimit(isControlResponse: Bool) -> Int {
        isControlResponse ? maximumStoredReplies : maximumStoredReplies - reservedControlReplies
    }

    mutating func remove(_ byteCount: Int) {
        storedBytes = max(0, storedBytes - max(0, byteCount))
    }
}

enum V3ServiceReadinessReply: Equatable {
    case invalid
    case notReady
    case failed(V3ServiceReadinessFailure)
    case ready

    static func decode(_ data: Data, requestID: String) -> V3ServiceReadinessReply {
        guard !data.isEmpty, data.count <= V3WireContract.responseLimit,
              let reply = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              V3WireContract.strictInt(reply["version"]) == 1,
              reply["id"] as? String == requestID else { return .invalid }
        // A structured failure is authoritative even when a malformed peer
        // omits the legacy error token. Fail closed on success-shaped replies
        // that also carry an invalid structured failure envelope.
        if reply["error"] != nil || reply["failure"] != nil {
            guard let envelope = reply["failure"] as? [String: Any],
                  Set(envelope.keys).isSubset(of: Set(["version", "operation", "stage", "code", "correlationID",
                      "underlyingDomain", "underlyingCode", "retryable", "safeCause", "sourceStep"])),
                  V3WireContract.strictInt(envelope["version"]) == 1,
                  envelope["correlationID"] as? String == requestID,
                  let operation = envelope["operation"] as? String,
                  let stage = envelope["stage"] as? String,
                  let code = envelope["code"] as? String,
                  let domain = envelope["underlyingDomain"] as? String,
                  let underlyingCode = V3WireContract.strictInt(envelope["underlyingCode"]) else { return .invalid }
            guard !operation.isEmpty, operation.utf8.count <= 64,
                  !stage.isEmpty, stage.utf8.count <= 64,
                  !code.isEmpty, code.utf8.count <= 64,
                  domain.utf8.count <= 128 else { return .invalid }
            let retryable: Bool?
            if let raw = envelope["retryable"] {
                guard let value = V3WireContract.strictBool(raw) else { return .invalid }
                retryable = value
            } else { retryable = nil }
            guard envelope["safeCause"] == nil || envelope["safeCause"] as? String != nil,
                  envelope["sourceStep"] == nil || envelope["sourceStep"] as? String != nil else { return .invalid }
            guard ((envelope["safeCause"] as? String)?.utf8.count ?? 0) <= 128,
                  ((envelope["sourceStep"] as? String)?.utf8.count ?? 0) <= 64 else { return .invalid }
            let failure = V3ServiceReadinessFailure(operation: operation, stage: stage, code: code,
                correlationID: requestID, underlyingDomain: domain, underlyingCode: underlyingCode,
                safeCause: envelope["safeCause"] as? String, sourceStep: envelope["sourceStep"] as? String,
                retryable: retryable)
            if ["snapshot", "status"].contains(failure.operation) && failure.stage == "serviceReadiness" &&
               failure.code == "notReady" && failure.retryable == true {
                return .notReady
            }
            return .failed(failure)
        }
        guard V3WireContract.strictBool(reply["ok"]) == true,
              let result = reply["result"] as? [String: Any],
              let ready = V3WireContract.strictBool(result["ready"]) else { return .invalid }
        return ready ? .ready : .notReady
    }
}

struct V3ServiceReadinessFailure: Equatable {
    let operation: String
    let stage: String
    let code: String
    let correlationID: String
    let underlyingDomain: String
    let underlyingCode: Int
    let safeCause: String?
    let sourceStep: String?
    let retryable: Bool?
}
