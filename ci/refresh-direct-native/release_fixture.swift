import Foundation
import CoreFoundation

public struct V3AuthServiceSnapshot: Equatable {
    public let authenticated: Bool
    public let credentialRoutePresent: Bool
    public let provisioningIncomplete: Bool
    public let provisioningRetryAvailable: Bool
    public let authenticationActive: Bool
    public let authenticationSessionID: String?
    public let identityStamp: String?
    public let identityStable: Bool

    public init(authenticated: Bool, provisioningIncomplete: Bool,
                provisioningRetryAvailable: Bool, authenticationActive: Bool,
                authenticationSessionID: String?, credentialRoutePresent: Bool = false,
                identityStamp: String? = nil, identityStable: Bool = true) {
        self.authenticated = authenticated
        self.credentialRoutePresent = credentialRoutePresent
        self.provisioningIncomplete = provisioningIncomplete
        self.provisioningRetryAvailable = provisioningRetryAvailable
        self.authenticationActive = authenticationActive
        self.authenticationSessionID = authenticationSessionID
        self.identityStamp = identityStamp
        self.identityStable = identityStable
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
        if ["C", "S", "I", "L", "Q"].contains(type) {
            return Int(exactly: number.uint64Value)
        }
        return Int(exactly: number.int64Value)
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
        guard let identityStamp = reply["identityStamp"] as? String,
              !identityStamp.isEmpty, identityStamp.utf8.count <= 128,
              let identityStable = strictBool(reply["identityStable"]) else { return nil }
        return V3AuthServiceSnapshot(authenticated: authenticated,
            provisioningIncomplete: provisioningIncomplete,
            provisioningRetryAvailable: provisioningRetryAvailable,
            authenticationActive: authenticationActive,
            authenticationSessionID: authenticationSessionID,
            credentialRoutePresent: strictBool(reply["credentialRoutePresent"]) ?? false,
            identityStamp: identityStamp, identityStable: identityStable)
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

    static let operations: Set<String> = ["legacyReplacementPrepare", "snapshot", "catalog", "appIcon", "cancel", "refreshSources",
        "refreshAdmissionBegin", "refreshAdmissionEnd",
        "signOut", "syncAppIDs", "clearCache", "jit", "backupResult",
        "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning", "authReconcileStorage",
        "opStart", "opPoll", "opAnswer", "opCancel", "opRecoveryPrepare", "opRecoveryReconcile",
        "refreshAdmissionReconcile", "recoveryDiscardUnreadable", "directRecoveryInspect",
        "directRecoveryReconcile", "ipaCleanup", "ipaActiveTokens",
        "certList", "certExportActive", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "pairingUsePlaced", "settingsGet", "settingsSet",
        "anisetteList", "anisetteReset", "anisetteSync",
        "sidesignGet", "sidesignSet", "sidesignReset", "sidesignImport", "sidesignExport",
        "logTail", "healthSnapshot", "accountExport", "accountImport"]
    static let readOperations: Set<String> = ["legacyReplacementPrepare", "snapshot", "catalog", "appIcon",
        "authPoll", "opPoll", "opCancel", "ipaCleanup", "ipaActiveTokens", "authCancel", "certList", "certExportActive", "certPortalList",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "settingsGet",
        "anisetteList", "sidesignGet", "sidesignExport", "logTail", "healthSnapshot",
        "directRecoveryInspect"]

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
            "snapshot", "opStart", "accountExport", "ipaActiveTokens", "refreshSources", "signOut", "pairingUsePlaced",
            "syncAppIDs", "clearCache", "settingsGet", "settingsSet", "sidesignGet", "sidesignSet",
            "sidesignReset", "sidesignExport", "anisetteList", "anisetteReset", "anisetteSync",
            "logTail", "certList", "certExportActive", "certPortalList", "certCreate", "opRecoveryPrepare",
            "recoveryDiscardUnreadable", "authReconcileStorage",
            "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles"
        ]
        if emptyTargetOperations.contains(operation) && !target.isEmpty { return nil }
        if operation == "healthSnapshot", !["", "hostSigningOnly"].contains(target) { return nil }
        if ["authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning",
            "opPoll", "opAnswer", "opCancel", "pairingImportData", "sidesignImport", "accountImport",
            "refreshAdmissionBegin", "refreshAdmissionEnd", "refreshAdmissionReconcile",
            "opRecoveryReconcile", "directRecoveryInspect", "directRecoveryReconcile", "cancel"].contains(operation),
           !canonicalSecretToken(target) { return nil }
        if operation == "ipaCleanup", !canonicalLowercaseFileToken(target) { return nil }
        if ["appIcon", "jit", "legacyReplacementPrepare"].contains(operation),
           !acceptsCoreDataTarget(target, entity: "InstalledApp") { return nil }
        if ["sourcePreview", "sourceAddConfirmed"].contains(operation), !isHTTPURL(target) { return nil }
        if operation == "backupResult", !canonicalSecretToken(target) { return nil }
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
        "legacyReplacementPrepare",
        "authBegin", "authRetryProvisioning", "authRespond", "opAnswer", "opStart", "opRecoveryPrepare",
        "cancel", "accountExport", "accountImport", "settingsSet", "sidesignSet", "refreshAdmissionEnd",
        "recoveryDiscardUnreadable", "directRecoveryReconcile", "backupResult"
    ]

    /// The one request key that may carry a credential, and only for the
    /// operations that declare it below.
    ///
    /// The answer used to cross as an opaque Keychain token in a shared access
    /// group. Re-signing can leave the service extension without that group,
    /// making the token inaccessible even when the host can write it. The
    /// answer now travels in the request itself, over the peer-validated channel,
    /// so there is no shared-storage copy of the credential to protect at all.
    ///
    /// This key is the ONLY exemption from the raw-secret sweep. Rejecting any
    /// nested value is what keeps the exemption from becoming a hole.
    private static let credentialAnswerKey = "answer"

    private static let credentialAnswerOperations: Set<String> = [
        "authRespond", "opAnswer", "accountExport", "accountImport"
    ]

    /// A flat, strictly bounded string map. Nothing can nest inside it, so the
    /// sweep's exemption can never carry a subtree past `containsRawSecretField`.
    private static func credentialAnswerIsBounded(_ value: Any?) -> Bool {
        guard let map = value as? [String: String], !map.isEmpty, map.count <= 32 else { return false }
        return map.allSatisfy { key, text in
            !key.isEmpty && key.utf8.count <= 64 && text.utf8.count <= 4096
        }
    }

    private static func acceptsPayload(operation: String, target: String,
                                       payload: [String: Any], now: Date) -> Bool {
        // Skipped by exact key, and only where an operation declares it. Every
        // other key is still swept, at every depth.
        let skipping: Set<String> = credentialAnswerOperations.contains(operation)
            ? [credentialAnswerKey] : []
        guard !containsRawSecretField(payload, skipping: skipping) else { return false }
        switch operation {
        case "backupResult":
            return Set(payload.keys) == Set(["nonce", "action", "result"]) &&
                canonicalSecretToken(payload["nonce"]) &&
                ["backup", "restore"].contains(payload["action"] as? String ?? "") &&
                ["success", "failure"].contains(payload["result"] as? String ?? "")
        case "legacyReplacementPrepare":
            return Set(payload.keys) == Set(["runID", "requestID"]) &&
                canonicalSecretToken(payload["runID"]) && canonicalSecretToken(payload["requestID"])
        case "snapshot":
            return Set(payload.keys) == Set(["readinessOnly"]) &&
                strictBool(payload["readinessOnly"]) == true
        case "authBegin", "authRetryProvisioning":
            let baseKeys: Set<String> = ["session", "sessionDeadline"]
            // A control flag, not an authentication secret. Keep its name out
            // of the raw-secret vocabulary; do not exempt it from that sweep.
            let allowedKeys = operation == "authBegin" ? baseKeys.union(["provisioningLogin", "finishPendingCertificateActivation"]) : baseKeys
            guard baseKeys.isSubset(of: Set(payload.keys)), Set(payload.keys).isSubset(of: allowedKeys),
                  payload["provisioningLogin"] == nil || strictBool(payload["provisioningLogin"]) != nil,
                  payload["finishPendingCertificateActivation"] == nil || strictBool(payload["finishPendingCertificateActivation"]) != nil,
                  strictBool(payload["finishPendingCertificateActivation"]) != true || strictBool(payload["provisioningLogin"]) == true,
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
            // The prompt id is what makes this one-shot: the service accepts an
            // answer only for the prompt it currently holds, and only once. The
            // token that used to sit here added no property the service did not
            // already enforce, and could not be read back under any signer.
            guard Set(payload.keys) == Set(["prompt", credentialAnswerKey]),
                  let prompt = payload["prompt"] as? String, !prompt.isEmpty, prompt.utf8.count <= 256,
                  credentialAnswerIsBounded(payload[credentialAnswerKey]) else { return false }
            return true
        case "accountExport":
            return Set(payload.keys) == Set(["includeApple", credentialAnswerKey]) &&
                strictBool(payload["includeApple"]) != nil &&
                credentialAnswerIsBounded(payload[credentialAnswerKey])
        case "accountImport":
            return Set(payload.keys) == Set([credentialAnswerKey]) &&
                credentialAnswerIsBounded(payload[credentialAnswerKey])
        case "opStart":
            let replacement = payload["kind"] as? String == "legacyReplacement"
            let keys: Set<String> = replacement ? ["kind", "target", "session", "riskAcknowledgmentVersion", "userConfirmed"] : ["kind", "target", "session"]
            guard Set(payload.keys) == keys,
                  !replacement || (strictInt(payload["riskAcknowledgmentVersion"]) == 1 && strictBool(payload["userConfirmed"]) == true),
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
        case "directRecoveryReconcile":
            if Set(payload.keys) == Set(["ackTerminal"]) {
                return strictBool(payload["ackTerminal"]) == true
            }
            return Set(payload.keys) == Set(["userConfirmed"]) && strictBool(payload["userConfirmed"]) == true
        case "recoveryDiscardUnreadable":
            return Set(payload.keys) == Set(["userConfirmed"]) && strictBool(payload["userConfirmed"]) == true
        case "refreshAdmissionEnd":
            return Set(payload.keys) == Set(["state", "admissionID"]) &&
                canonicalSecretToken(payload["admissionID"]) &&
                ["completed", "failed", "notDispatched"].contains(payload["state"] as? String ?? "")
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
            // SideSign headers are user configuration and legitimately contain an
            // Authorization header, so their text is not content-scanned. The
            // bound that matters is the size cap.
            guard Set(payload.keys) == Set(["config"]),
                  let config = payload["config"] as? String, config.utf8.count <= 8192 else {
                return false
            }
            return true
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
        case "legacyReplacement": return canonicalSecretToken(target)
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

    private static func containsRawSecretField(_ value: Any, skipping: Set<String> = []) -> Bool {
        var pending: [Any] = [value]
        while let current = pending.popLast() {
            if let dictionary = current as? [String: Any] {
                for (key, nested) in dictionary {
                    let normalized = key.lowercased().filter { $0.isLetter || $0.isNumber }
                    // Matched before the fragment sweep, and the subtree is not
                    // traversed. Its shape is bounded separately, in
                    // `credentialAnswerIsBounded`.
                    if skipping.contains(normalized) { continue }
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


struct V3RefreshAdmissionLease {
    static let nativeRefreshTimeout: TimeInterval = 600
    static let retirementGrace: TimeInterval = 60
    static let lifetime: TimeInterval = nativeRefreshTimeout + retirementGrace
    static let nativeRefreshTimeoutNanoseconds: UInt64 = 600_000_000_000

    private(set) var runID: String?
    private(set) var requestID: String?
    private(set) var expiresAt: Date?
    private(set) var ownerLost = false

    // A retained run still fences new mutations after its owner is lost.
    var isActive: Bool { runID != nil }
    // Snapshot activity must describe a live owner, not a durable recovery hold.
    // Otherwise a restarted host cannot accept the very snapshot needed to
    // present and explicitly reconcile an interrupted refresh.
    var hasLiveOwner: Bool { isActive && !ownerLost }

    mutating func expire(now: Date = Date()) -> Bool {
        guard let expiresAt, expiresAt <= now, !ownerLost else { return false }
        ownerLost = true
        self.expiresAt = nil
        return true
    }

    mutating func acquire(runID: String, requestID: String,
                          authenticationActive: Bool,
                          anotherMutationActive: Bool,
                          now: Date = Date()) -> Bool {
        _ = expire(now: now)
        guard let parsed = UUID(uuidString: runID), parsed.uuidString == runID,
              let parsedRequest = UUID(uuidString: requestID), parsedRequest.uuidString == requestID,
              self.runID == nil, !authenticationActive, !anotherMutationActive,
              Self.lifetime > 0 else { return false }
        self.runID = runID
        self.requestID = requestID
        expiresAt = now.addingTimeInterval(Self.lifetime)
        ownerLost = false
        return true
    }

    func owns(_ candidate: String) -> Bool { runID == candidate }

    mutating func restoreLost(runID: String) -> Bool {
        guard self.runID == nil,
              let parsed = UUID(uuidString: runID), parsed.uuidString == runID else { return false }
        self.runID = runID
        requestID = nil
        expiresAt = nil
        ownerLost = true
        return true
    }

    @discardableResult
    mutating func release(runID: String) -> Bool {
        guard self.runID == runID else { return false }
        self.runID = nil
        requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func release(requestID: String) -> Bool {
        guard self.requestID == requestID else { return false }
        runID = nil
        self.requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func reconcileAfterDeviceCheck(runID: String, userConfirmed: Bool) -> Bool {
        guard ownerLost, userConfirmed, owns(runID) else { return false }
        return release(runID: runID)
    }
}

// V3_REFRESH_EXECUTION_GATE_V1: one in-process execution owner across the
// external AppIntent and the admitted host XPC path. MainActor owns this value.
struct V3RefreshExecutionTicket: Equatable, Sendable {
    let id: UUID
    let hostRunID: String?
    let hostAdmissionID: String?
}

struct V3RefreshExecutionGate {
    private(set) var current: V3RefreshExecutionTicket?
    private var consumedHostAdmissionID: String?
    private var consumedHostRunID: String?
    var isActive: Bool { current != nil }
    var isExternalActive: Bool { current != nil && current?.hostRunID == nil }

    mutating func acquire(hostRunID: String?, hostAdmissionID: String? = nil, hostLeaseActive: Bool) -> V3RefreshExecutionTicket? {
        guard current == nil else { return nil }
        if let hostRunID {
            guard hostLeaseActive, UUID(uuidString: hostRunID)?.uuidString == hostRunID,
                  let hostAdmissionID, UUID(uuidString: hostAdmissionID)?.uuidString == hostAdmissionID,
                  consumedHostAdmissionID != hostAdmissionID else { return nil }
        } else {
            guard !hostLeaseActive, hostAdmissionID == nil else { return nil }
        }
        let ticket = V3RefreshExecutionTicket(id: UUID(), hostRunID: hostRunID, hostAdmissionID: hostAdmissionID)
        current = ticket
        if let hostAdmissionID {
            consumedHostAdmissionID = hostAdmissionID
            consumedHostRunID = hostRunID
        }
        return ticket
    }

    func hasDispatched(hostRunID: String, hostAdmissionID: String) -> Bool {
        consumedHostAdmissionID == hostAdmissionID && consumedHostRunID == hostRunID
    }

    func owns(_ ticket: V3RefreshExecutionTicket) -> Bool { current == ticket }

    mutating func release(_ ticket: V3RefreshExecutionTicket) {
        guard current == ticket else { return }
        current = nil
    }
}

enum ServiceError: Error, Equatable { case busy, invalidRequest, notFound }
struct V3OperationRecoveryRecord { let kind: String; let sessionID: String }
enum V3OperationRecoveryJournal {
    static var record: V3OperationRecoveryRecord?
    static var failRead = false
    static var canSettle = true
    static var settleCalls = 0
    static var reconcileCalls = 0
    static func current() throws -> V3OperationRecoveryRecord? {
        if failRead { throw ServiceError.busy }
        return record
    }
    static func settleRefreshAdmission(runID: String, terminalState: String, terminalConfirmed: Bool) throws -> Bool {
        settleCalls += 1
        guard canSettle, terminalConfirmed, record?.sessionID == runID else { return false }
        record = nil
        return true
    }
    static func reconcileRefreshAdmissionAfterDeviceCheck(runID: String, userConfirmed: Bool) throws -> Bool {
        reconcileCalls += 1
        guard canSettle, userConfirmed, record?.sessionID == runID else { return false }
        record = nil
        return true
    }
    static func reset() { record = nil; failRead = false; canSettle = true; settleCalls = 0; reconcileCalls = 0 }
}

final class ReleaseService {
var refreshAdmission = V3RefreshAdmissionLease()
var refreshExecutionGate = V3RefreshExecutionGate()
func dispatch(_ operation: String, target: String, payload: [String: Any]) throws -> [String: Any] {
switch operation {
        case "refreshAdmissionEnd":
            guard !refreshExecutionGate.isActive else { throw ServiceError.busy }
            guard let admissionID = payload["admissionID"] as? String,
                  UUID(uuidString: admissionID)?.uuidString == admissionID else {
                throw ServiceError.invalidRequest
            }
            guard let parsed = UUID(uuidString: target), parsed.uuidString == target else {
                throw ServiceError.invalidRequest
            }
            guard let terminalState = payload["state"] as? String,
                  ["completed", "failed", "notDispatched"].contains(terminalState) else {
                throw ServiceError.invalidRequest
            }
            let recovery: V3OperationRecoveryRecord?
            do { recovery = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            guard let recovery else {
                guard !refreshAdmission.isActive else { throw ServiceError.busy }
                return ["runID": target, "admissionID": admissionID, "released": true, "alreadyReleased": true]
            }
            guard refreshAdmission.requestID == admissionID,
                  recovery.kind == "refreshAll", recovery.sessionID == target,
                  try V3OperationRecoveryJournal.settleRefreshAdmission(runID: target,
                    terminalState: terminalState, terminalConfirmed: true) else {
                throw ServiceError.notFound
            }
            _ = refreshAdmission.release(runID: target)
            return ["runID": target, "admissionID": admissionID, "released": true]
        case "refreshAdmissionReconcile":
            guard !refreshExecutionGate.isActive else { throw ServiceError.busy }
            guard let parsed = UUID(uuidString: target), parsed.uuidString == target,
                  V3WireContract.strictBool(payload["userConfirmed"]) == true,
                  refreshAdmission.ownerLost else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.reconcileRefreshAdmissionAfterDeviceCheck(
                    runID: target, userConfirmed: true) else { throw ServiceError.invalidRequest }
            } catch { throw ServiceError.busy }
            _ = refreshAdmission.release(runID: target)
            return ["runID": target, "released": true, "reconciled": true]
default: throw ServiceError.invalidRequest
}
}
}
@main struct ReleaseCases {
    static func reject(_ expected: ServiceError, _ body: () throws -> [String: Any]) {
        do { _ = try body(); fatalError("expected rejection: \(expected)") }
        catch let error as ServiceError { precondition(error == expected) }
        catch { fatalError("unexpected error \(error)") }
    }
    static func make(_ run: String, _ admission: String) -> ReleaseService {
        V3OperationRecoveryJournal.reset()
        let service = ReleaseService()
        precondition(service.refreshAdmission.acquire(runID: run, requestID: admission,
            authenticationActive: false, anotherMutationActive: false))
        V3OperationRecoveryJournal.record = .init(kind: "refreshAll", sessionID: run)
        return service
    }
    static func main() throws {
        let run = UUID().uuidString, admission = UUID().uuidString, old = UUID().uuidString
        var service = make(run, admission)
        let end: [String: Any] = ["state": "completed", "admissionID": admission]
        let ticket = service.refreshExecutionGate.acquire(hostRunID: run, hostAdmissionID: admission, hostLeaseActive: true)!
        reject(.busy) { try service.dispatch("refreshAdmissionEnd", target: run, payload: end) }
        reject(.busy) { try service.dispatch("refreshAdmissionReconcile", target: run, payload: ["userConfirmed": true]) }
        precondition(V3OperationRecoveryJournal.settleCalls == 0 && V3OperationRecoveryJournal.reconcileCalls == 0)
        precondition(service.refreshAdmission.requestID == admission)
        service.refreshExecutionGate.release(ticket)
        reject(.notFound) { try service.dispatch("refreshAdmissionEnd", target: run, payload: ["state": "completed", "admissionID": old]) }
        precondition(V3OperationRecoveryJournal.settleCalls == 0 && service.refreshAdmission.requestID == admission)
        let result = try service.dispatch("refreshAdmissionEnd", target: run, payload: end)
        precondition(result["admissionID"] as? String == admission && result["released"] as? Bool == true)
        precondition(!service.refreshAdmission.isActive && V3OperationRecoveryJournal.record == nil)
        let duplicate = try service.dispatch("refreshAdmissionEnd", target: run, payload: end)
        precondition(duplicate["alreadyReleased"] as? Bool == true)
        service = make(run, admission)
        V3OperationRecoveryJournal.failRead = true
        reject(.busy) { try service.dispatch("refreshAdmissionEnd", target: run, payload: end) }
        precondition(service.refreshAdmission.isActive)
        V3OperationRecoveryJournal.failRead = false
        V3OperationRecoveryJournal.canSettle = false
        reject(.notFound) { try service.dispatch("refreshAdmissionEnd", target: run, payload: end) }
        precondition(service.refreshAdmission.isActive)
        service = make(run, admission)
        reject(.invalidRequest) { try service.dispatch("refreshAdmissionReconcile", target: run, payload: ["userConfirmed": true]) }
        _ = service.refreshAdmission.expire(now: Date().addingTimeInterval(1000))
        reject(.invalidRequest) { try service.dispatch("refreshAdmissionReconcile", target: run, payload: ["userConfirmed": 1]) }
        let reconciled = try service.dispatch("refreshAdmissionReconcile", target: run, payload: ["userConfirmed": true])
        precondition(reconciled["reconciled"] as? Bool == true && !service.refreshAdmission.isActive)
        service = make(run, admission)
        let external = service.refreshExecutionGate.acquire(hostRunID: nil, hostLeaseActive: false)!
        reject(.busy) { try service.dispatch("refreshAdmissionEnd", target: run, payload: end) }
        service.refreshExecutionGate.release(external)
        for state in ["failed", "notDispatched"] {
            service = make(run, admission)
            let released = try service.dispatch("refreshAdmissionEnd", target: run,
                payload: ["state": state, "admissionID": admission])
            precondition(released["released"] as? Bool == true && !service.refreshAdmission.isActive)
        }
        service = make(run, admission)
        reject(.invalidRequest) { try service.dispatch("refreshAdmissionEnd", target: run,
            payload: ["state": "completed", "admissionID": admission.lowercased()]) }
        reject(.invalidRequest) { try service.dispatch("refreshAdmissionEnd", target: run,
            payload: ["state": "other", "admissionID": admission]) }
        reject(.invalidRequest) { try service.dispatch("refreshAdmissionEnd", target: "bad", payload: end) }
        V3OperationRecoveryJournal.record = .init(kind: "restore", sessionID: run)
        reject(.notFound) { try service.dispatch("refreshAdmissionEnd", target: run, payload: end) }
        precondition(service.refreshAdmission.isActive && V3OperationRecoveryJournal.settleCalls == 0)
        // Actual production wire serializer+decoder rejects malformed release payloads.
        func encoded(_ payload: [String: Any]) -> Data? {
            V3WireContract.encodeRequest(["version": 1, "id": UUID().uuidString,
                "operation": "refreshAdmissionEnd", "target": run,
                "deadline": Date().addingTimeInterval(20), "payload": payload])
        }
        for state in ["completed", "failed", "notDispatched"] {
            let data = encoded(["state": state, "admissionID": admission])!
            precondition(V3WireContract.decodeRequest(data) != nil)
        }
        for bad in [["state": "completed"], ["state": "completed", "admissionID": "invalid"],
                    ["state": "completed", "admissionID": admission.lowercased()],
                    ["state": "other", "admissionID": admission],
                    ["state": "completed", "admissionID": admission, "extra": "x"]] {
            precondition(encoded(bad) == nil)
        }
        print("PASS: exact release/reconcile dispatch, active-host/external gate, stale admission, storage refusal, idle completion, lost-owner reconcile, actual wire validation")
    }
}
