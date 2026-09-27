import Foundation

// V3_RESPONSE_CLASSIFICATION_CARRIER_V1
//
// This harness runs the REAL service encoder and the REAL host reply classifier
// against each other over real property-list bytes. It deliberately does not
// re-implement either side, and it does not inspect source text.
//
// The defect it exists to catch: the service emitted BOTH a legacy "error" token
// and a structured "failure" envelope, the host preferred the structured
// envelope, and the classification lived only in the token. Every encoding
// failure therefore reached the user as a generic invalidResponse.

@main
struct ResponseClassificationHarness {
    static func main() {
        // Foundation's own answer for a leaf, so the wire contract cannot drift
        // from CoreFoundation in either direction.
        func foundationEncodes(_ value: Any) -> Bool {
            (try? PropertyListSerialization.data(fromPropertyList: ["leaf": value],
                                                  format: .binary, options: 0)) != nil
        }

        // ---------------------------------------------------------------
        // V3_PLIST_LEAF_CONTRACT_V1: the validator must AGREE with Foundation
        // for every leaf the wire can carry. A hardcoded expectation list
        // cannot catch a type list that has drifted, and accepting URL is how a
        // future serialization crash was licensed.
        // ---------------------------------------------------------------
        final class Opaque {}
        let leaves: [(String, Any)] = [
            ("a string", "text"),
            ("a bool", true),
            ("an int", 1),
            ("an Int8", Int8(1)),
            ("an Int64", Int64(1)),
            ("a UInt", UInt(1)),
            ("a double", 1.5),
            ("a float", Float(1.5)),
            ("a date", Date()),
            ("data", Data([0x01])),
            ("an array", [1, 2] as [Any]),
            ("a dictionary", ["a": "b"] as [String: Any]),
            ("a nested container", [1, ["b": Date() as Any]] as [Any]),
            ("a URL", URL(string: "https://example.invalid")!),
            ("NSNull", NSNull()),
            ("an NSError", NSError(domain: "audit", code: 1)),
            ("a Set", Set([1])),
            ("an unknown class", Opaque()),
            ("a boxed absent Optional", Optional<String>.none as Any),
        ]
        for (name, leaf) in leaves {
            precondition(
                V3WireContract.V3PropertyListValue.isEncodable(leaf) == foundationEncodes(leaf),
                "isEncodable disagrees with PropertyListSerialization for \(name)")
        }
        // A URL must be sent as a string, and a boxed absent Optional must never
        // encode. Both are the properties the wire contract relies on.
        precondition(!foundationEncodes(URL(string: "https://example.invalid")!),
                     "CoreFoundation accepted a URL; revisit the absoluteString contract")
        precondition(V3WireContract.V3PropertyListValue.isEncodable(
                        URL(string: "https://x.invalid")!.absoluteString),
                     "a URL's absoluteString must be the accepted wire form")
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(
                        Optional<String>.none as Any),
                     "an absent Optional must never be reported as encodable")

        // The plist-safe dictionary helper omits an absent Optional, and the
        // original defect class is still real in Foundation.
        let omitted = V3WireContract.V3PropertyListValue.dictionary([
            "identifier": "com.example.app", "installedVersion": Optional<String>.none as Any])
        precondition(omitted["identifier"] as? String == "com.example.app",
                     "a present value must survive the plist-safe helper")
        precondition(omitted["installedVersion"] == nil,
                     "an absent value must be omitted, not boxed into Any")
        let boxed: [String: Any] = ["installedVersion": Optional<String>.none as Any]
        precondition(!foundationEncodes(boxed),
                     "the boxed Optional premise changed; the original defect class is gone")

        // ---------------------------------------------------------------
        // The two encoder failure modes, end to end.
        // ---------------------------------------------------------------
        let encodingID = UUID().uuidString
        // A boxed Optional is the exact value that broke the real catalog reply.
        let unencodable: [String: Any] = [
            "version": 1, "id": encodingID, "ok": true,
            "result": ["apps": [["identifier": "com.example.app",
                                 "installedVersion": Optional<String>.none as Any]]]]
        let encodingReply = V3ResponseEncoder.encode(unencodable, operation: "catalog",
                                                     limit: V3WireContract.responseLimit)
        precondition(encodingReply.count <= V3WireContract.responseLimit,
                     "the typed fallback must be small enough to transport")

        let encodingFailure = hostFailure(encodingReply, operation: "catalog", id: encodingID)
        precondition(encodingFailure.safeCause == .responseEncodingFailed,
                     "an encoding failure must arrive as responseEncodingFailed, not a generic "
                     + "invalidResponse; got \(String(describing: encodingFailure.safeCause))")
        precondition(encodingFailure.code == .invalidResponse,
                     "the code stays invalidResponse; the cause carries the distinction")
        // Both reply-level defects use one canonical stage. Malformed or stale
        // replies continue to use the request/command boundary.
        precondition(encodingFailure.stage == .replyEncoding,
                     "a reply that could not be encoded uses the reply boundary stage")
        precondition(encodingFailure.correlationID == encodingID,
                     "the correlation must survive the fallback")
        precondition(encodingFailure.retryable == false,
                     "a serialization defect is not fixed by repeating the same request")

        // The reply really is the production shape: BOTH keys present.
        let encodingDecoded = try! PropertyListSerialization.propertyList(
            from: encodingReply, format: nil) as! [String: Any]
        precondition(encodingDecoded["error"] as? String == "responseEncodingFailed",
                     "the legacy token is still emitted for an older host")
        precondition((encodingDecoded["failure"] as? [String: Any]) != nil,
                     "the structured envelope is still emitted and is authoritative")

        // An oversized but valid reply is a different defect and must not be
        // confused with the encoding failure.
        var oversized: [String: Any] = ["version": 1, "id": encodingID, "ok": true,
                                        "result": ["apps": []]]
        oversized["padding"] = String(repeating: "x", count: V3WireContract.responseLimit)
        let oversizeReply = V3ResponseEncoder.encode(oversized, operation: "catalog",
                                                     limit: V3WireContract.responseLimit)
        precondition(oversizeReply.count > 0, "the oversized reply must be a typed fallback")
        let oversizeFailure = hostFailure(oversizeReply, operation: "catalog", id: encodingID)
        precondition(oversizeFailure.safeCause == .responseTooLarge,
                     "an oversized reply must arrive as responseTooLarge; got "
                     + String(describing: oversizeFailure.safeCause))
        precondition(oversizeFailure.safeCause != encodingFailure.safeCause,
                     "the two encoder failure modes must never be confused")
        precondition(oversizeFailure.stage == .replyEncoding,
                     "a reply over the byte limit uses the same reply boundary stage")

        // ---------------------------------------------------------------
        // Structured precedence still wins for unrelated typed failures, and the
        // legacy token path still works for a foreign service.
        // ---------------------------------------------------------------
        let busyID = UUID().uuidString
        let busy = V3ResponseEncoder.fallback(id: busyID, operation: "snapshot",
                                               token: "busy", code: .busy)
        precondition(hostFailure(busy, operation: "snapshot", id: busyID).code == .busy,
                     "structured precedence must still deliver a typed unrelated failure")

        let foreignID = UUID().uuidString
        let foreignOnlyToken = try! PropertyListSerialization.data(
            fromPropertyList: ["version": 1, "id": foreignID, "error": "notReady"],
            format: .binary, options: 0)
        let foreign = hostFailure(foreignOnlyToken, operation: "snapshot", id: foreignID)
        precondition(foreign.code == .notReady && foreign.stage == .serviceReadiness,
                     "a legacy-only reply from an older service must still be typed")

        let unknownID = UUID().uuidString
        let unknownToken = try! PropertyListSerialization.data(
            fromPropertyList: ["version": 1, "id": unknownID, "error": "somethingNew"],
            format: .binary, options: 0)
        precondition(hostFailure(unknownToken, operation: "snapshot", id: unknownID).safeCause == nil,
                     "an unknown token must not invent a safe cause")

        // A reply for a different request is protocol evidence, never a
        // serialization defect, and never resolved to this caller.
        precondition(hostFailure(encodingReply, operation: "catalog",
                                 id: UUID().uuidString).code == .staleResult,
                     "a mismatched correlation must stay staleResult")

        // A successful reply is still returned rather than thrown. The payload is
        // typed explicitly so a heterogeneous literal is not inferred as
        // something the property-list writer will refuse.
        let okID = UUID().uuidString
        let okPayload: [String: Any] = [
            "version": 1, "id": okID, "ok": true,
            "result": ["apps": [["identifier": "com.example.app"]]]]
        let okReply = try! PropertyListSerialization.data(fromPropertyList: okPayload,
                                                         format: .binary, options: 0)
        var returned: [String: Any]? = nil
        do {
            returned = try V3CatalogRequestContext.classifyReply(okReply, operation: "catalog", id: okID)
        } catch {
            preconditionFailure("a well-formed success reply must be returned, not thrown: \(error)")
        }
        guard let payload = returned else {
            preconditionFailure("a well-formed success reply must be returned, not nil")
        }
        // classifyReply returns the inner result payload, not the whole envelope,
        // so the catalog rows sit directly under the returned dictionary.
        let okRows = payload["apps"] as? [Any]
        precondition((okRows?.isEmpty == false), "the catalog rows must survive the round trip")
        let okFirst = okRows?.first as? [String: Any]
        precondition(okFirst?["identifier"] as? String == "com.example.app",
                     "the row identifier must survive the round trip")

        // ---------------------------------------------------------------
        // No sensitive value crosses the boundary in a fallback. The offending
        // value's own text must not appear anywhere in the reply.
        // ---------------------------------------------------------------
        let secret = "SUPER-SECRET-PAIRING-BLOB"
        let secretReply = V3ResponseEncoder.encode(
            ["version": 1, "id": encodingID, "ok": true,
             "result": ["pairing": secret, "installedVersion": Optional<String>.none as Any]],
            operation: "snapshot", limit: V3WireContract.responseLimit)
        precondition(!String(decoding: secretReply, as: UTF8.self).contains(secret),
                     "a fallback must never carry the value that could not be encoded")
        let secretFailure = hostFailure(secretReply, operation: "snapshot", id: encodingID)
        precondition(!secretFailure.safeMessage.contains(secret),
                     "the safe message must not carry the offending value")
        precondition(!secretFailure.technicalDetails.contains(secret),
                     "the diagnostics must not carry the offending value")
        precondition(!secretFailure.recovery.contains(secret),
                     "the recovery must not carry the offending value")

        // ---------------------------------------------------------------
        // The token-to-cause mapping is total over the two encoder tokens, so a
        // future token cannot silently lose its classification.
        // ---------------------------------------------------------------
        precondition(V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.encodingFailed)
                     == .responseEncodingFailed,
                     "the encoding token must map to the encoding cause")
        precondition(V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.tooLarge)
                     == .responseTooLarge,
                     "the oversize token must map to the oversize cause")
        precondition(V3ResponseClassifier.safeCause(for: "notReady") == nil,
                     "an unrelated token must map to no cause at all")

        // Each of the three reply defects has its own user-facing wording, so a
        // support reader can tell them apart without the diagnostics.
        precondition(encodingFailure.safeMessage != oversizeFailure.safeMessage,
                     "an encoding failure and an oversize reply must read differently")
        precondition(encodingFailure.recovery != oversizeFailure.recovery,
                     "their recovery guidance must differ too")
        precondition(oversizeFailure.safeMessage
                     != "SideStore could not read this source's saved catalog data.",
                     "an oversize reply must not be described as a catalog read failure")

        print("V3_RESPONSE_CLASSIFICATION_PASS")
    }

    /// Runs the real host classifier and returns the typed failure it produced.
    private static func hostFailure(_ reply: Data, operation: String, id: String) -> CombinedFailure {
        var thrown: Error?
        do {
            _ = try V3CatalogRequestContext.classifyReply(reply, operation: operation, id: id)
        } catch {
            thrown = error
        }
        guard let failure = thrown as? CombinedFailure else {
            preconditionFailure("expected a CombinedFailure for operation \(operation), got "
                                + String(describing: thrown))
        }
        return failure
    }
}
