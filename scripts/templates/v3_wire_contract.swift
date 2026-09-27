import Foundation
import CoreFoundation

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

    static func authSnapshot(_ reply: [String: Any]) -> [String: Bool]? {
        guard let authenticated = strictBool(reply["authenticated"]),
              let provisioningIncomplete = strictBool(reply["provisioningIncomplete"]),
              let provisioningRetryAvailable = strictBool(reply["provisioningRetryAvailable"]) else {
            return nil
        }
        return ["authenticated": authenticated,
                "provisioningIncomplete": provisioningIncomplete,
                "provisioningRetryAvailable": provisioningRetryAvailable]
    }

    static func invalidRequestIdentity(from data: Data) -> (id: String?, operation: String?) {
        guard data.count <= requestLimit,
              let envelope = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
            return (nil, nil)
        }
        let rawID = envelope["id"] as? String
        let id = rawID.flatMap { UUID(uuidString: $0)?.uuidString == $0 ? $0 : nil }
        let rawOperation = envelope["operation"] as? String
        let operation = rawOperation.flatMap { operations.contains($0) ? $0 : nil } ?? "command"
        return (id, operation)
    }

    static let operations: Set<String> = ["snapshot", "catalog", "appIcon", "cancel", "refreshSources",
        "refreshAdmissionBegin", "refreshAdmissionEnd",
        "signOut", "syncAppIDs", "clearCache", "jit", "backupResult",
        "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning",
        "opStart", "opPoll", "opAnswer", "opCancel", "ipaCleanup", "ipaActiveTokens",
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
        if let cursor = request["cursor"] {
            guard operation == "catalog", let value = strictInt(cursor),
                  value >= 0, value <= 1_000_000 else { return nil }
        }
        if let payload = request["payload"] {
            guard payload as? [String: Any] != nil else { return nil }
        }
        if ["authBegin", "authRetryProvisioning"].contains(operation) {
            guard let payload = request["payload"] as? [String: Any],
                  let session = payload["session"] as? String,
                  let parsedSession = UUID(uuidString: session), parsedSession.uuidString == session,
                  session == target,
                  let sessionDeadline = payload["sessionDeadline"] as? Date,
                  sessionDeadline > now,
                  sessionDeadline.timeIntervalSince(now) <= authSessionLifetime + 10 else { return nil }
        }
        if operation == "cancel" {
            guard let payload = request["payload"] as? [String: Any],
                  let scope = payload["scope"] as? String,
                  cancellationScopes.contains(scope) else { return nil }
        }
        return request
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
    static let reservedControlBytes = V3WireContract.responseLimit
    // A single authentication/session may require multiple user responses.
    // Keep enough reply identities reserved so ordinary mutations cannot make
    // a later 2FA/prompt answer impossible to acknowledge.
    static let reservedControlReplies = 32
    private(set) var storedBytes = 0

    static func isControlReply(operation: String) -> Bool {
        ["refreshAdmissionEnd", "authRespond", "opAnswer"].contains(operation)
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
    case failed(String)
    case ready

    static func decode(_ data: Data, requestID: String) -> V3ServiceReadinessReply {
        guard !data.isEmpty, data.count <= V3WireContract.responseLimit,
              let reply = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              V3WireContract.strictInt(reply["version"]) == 1,
              reply["id"] as? String == requestID else { return .invalid }
        if let error = reply["error"] as? String { return .failed(error) }
        guard V3WireContract.strictBool(reply["ok"]) == true,
              reply["result"] as? [String: Any] != nil else { return .invalid }
        return .ready
    }
}
