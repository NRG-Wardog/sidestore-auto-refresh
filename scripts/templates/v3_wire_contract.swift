import Foundation
import CoreFoundation

// V3_WIRE_CONTRACT_V1: shared source, compiled independently in each process.
// V3_HEADLESS_CONTRACT_V2: SideStore is a headless backend. All presentation
// decisions cross as data (prompts/confirmations); no remote UI is addressed.
enum V3WireContract {
    static let requestLimit = 16_384
    static let responseLimit = 4_194_304
    static let operations: Set<String> = ["snapshot", "catalog", "appIcon", "cancel", "refreshSources",
        "signOut", "syncAppIDs", "clearCache", "jit", "backupResult",
        "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning",
        "opStart", "opPoll", "opAnswer", "opCancel", "ipaCleanup",
        "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "settingsGet", "settingsSet",
        "anisetteList", "anisetteReset", "anisetteSync",
        "sidesignGet", "sidesignSet", "sidesignReset", "sidesignImport", "sidesignExport",
        "logTail", "healthSnapshot", "accountExport", "accountImport"]
    static let readOperations: Set<String> = ["snapshot", "catalog", "appIcon",
        "authPoll", "opPoll", "opCancel", "ipaCleanup", "authCancel", "certList", "certPortalList",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "settingsGet",
        "anisetteList", "sidesignGet", "sidesignExport", "logTail", "healthSnapshot"]

    static func decodeRequest(_ data: Data, now: Date = Date()) -> [String: Any]? {
        guard data.count <= requestLimit,
              let request = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(request.keys).isSubset(of: ["version", "id", "operation", "target", "deadline", "cursor", "payload"]),
              let version = request["version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
              request["version"] as? Int == 1,
              let id = request["id"] as? String, UUID(uuidString: id) != nil,
              let operation = request["operation"] as? String, operations.contains(operation),
              let target = request["target"] as? String, target.utf8.count <= 4096,
              let deadline = request["deadline"] as? Date,
              deadline > now, deadline.timeIntervalSince(now) <= 610 else { return nil }
        if request["value"] != nil { return nil }
        if let cursor = request["cursor"] {
            guard operation == "catalog", let number = cursor as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(),
                  let value = cursor as? Int, value >= 0, value <= 1_000_000 else { return nil }
        }
        if let payload = request["payload"] {
            guard payload as? [String: Any] != nil else { return nil }
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

// V3_RESPONSE_CLASSIFICATION_CARRIER_V1
// The service's reply encoder and the host's reply classifier are separated by
// a property-list boundary, and the classification of a reply the service could
// not deliver has to survive that boundary. It previously did not: the service
// wrote the specific token under a legacy "error" key and a cause-less
// structured "failure", and the host prefers the structured envelope, so every
// encoding failure arrived as a generic invalidResponse.
//
// Both halves live here, as pure functions, so the pair can be executed together
// against real property-list bytes rather than asserted about in source text.
// The host still prefers the structured envelope; the classification simply
// travels inside it now, and the legacy token remains for an older host.
enum V3ResponseClassifier {
    /// The legacy string tokens a service may put in the "error" key.
    enum Token {
        static let encodingFailed = "responseEncodingFailed"
        static let tooLarge = "responseTooLarge"
    }

    /// The safe cause that carries a token's classification across the wire.
    static func safeCause(for token: String) -> CombinedFailure.SafeCause? {
        switch token {
        case Token.encodingFailed: return .responseEncodingFailed
        case Token.tooLarge: return .responseTooLarge
        default: return nil
        }
    }
}

// V3_RESPONSE_ENCODER_V1
// The service side of the classification pair. It is a separate enum rather than
// a private method so the harness can execute the real encoder, and it reads the
// shared responseLimit instead of repeating the literal.
enum V3ResponseEncoder {
    /// Encodes a reply, or returns a correlated, typed fallback that says which
    /// of the two failure modes occurred.
    static func encode(_ value: [String: Any], operation: String = "command") -> Data {
        let correlationID = value["id"] as? String ?? ""
        do {
            let data = try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
            guard data.count <= V3WireContract.responseLimit else {
                return fallback(id: correlationID, operation: operation,
                                token: V3ResponseClassifier.Token.tooLarge,
                                code: .invalidResponse,
                                safeCause: V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.tooLarge))
            }
            return data
        } catch {
            return fallback(id: correlationID, operation: operation,
                            token: V3ResponseClassifier.Token.encodingFailed,
                            code: .invalidResponse,
                            safeCause: V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.encodingFailed))
        }
    }

    /// Builds a small, correlated, typed fallback reply. Always serializable
    /// because every value is a concrete String, Bool or Int.
    ///
    /// The reply deliberately carries BOTH the legacy "error" token and the
    /// structured "failure" envelope, because that is the shape production
    /// emits. The structured envelope is authoritative on the host, so the
    /// classification that survives is the safeCause set here.
    static func fallback(id: String, operation: String, token: String,
                         code: CombinedFailure.Code,
                         safeCause: CombinedFailure.SafeCause? = nil) -> Data {
        let value: [String: Any] = [
            "version": 1,
            "id": id,
            "error": token,
            "failure": CombinedFailure(operation: operation, stage: .command, code: code,
                                       id: id, safeCause: safeCause).wire
        ]
        return (try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)) ?? Data()
    }
}
