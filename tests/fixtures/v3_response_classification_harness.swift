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
        // V3_PLIST_LEAF_CONTRACT_V1: the validator must AGREE with
        // Foundation for every leaf the wire can carry. A hardcoded
        // expectation list cannot catch a type list that has drifted, and
        // accepting URL is how a future serialization crash was licensed.
        // ---------------------------------------------------------------
        final class Opaque {}
        let leaves: [(String, Any)] = [
            ("String", "text"),
            ("Bool", true),
            ("Int", 1),
            ("Int8", Int8(1)),
            ("Int64", Int64(1)),
            ("UInt", UInt(1)),
            ("Double", 1.5),
            ("Float", Float(1.5)),
            ("Date", Date()),
            ("Data", Data([0x01])),
            ("Array", [1, 2]),
            ("Dictionary", ["a": "b"]),
            ("NestedArray", [1, ["b": Date()]]),
            ("URL", URL(string: "https://example.invalid")!),
            ("NSNull", NSNull()),
            ("NSError", NSError(domain: "audit", code: 1)),
            ("Set", Set([1])),
            ("Opaque", Opaque()),
            ("BoxedNone", Optional<String>.none as Any),
        ]
        for (name, leaf) in leaves {
            precondition(
                V3WireContract.V3PropertyListValue.isEncodable(leaf) == foundationEncodes(leaf),
                "isEncodable disagrees with PropertyListSerialization for \(name)")
        }
        // A URL must be sent as a string, and a boxed Optional must never encode.
        precondition(!foundationEncodes(URL(string: "https://example.invalid")!),
                     "CoreFoundation now accepts CFURL; revisit the absoluteString contract")
        precondition(V3WireContract.V3PropertyListValue.isEncodable(URL(string: "https://x.invalid")!.absoluteString),
                     "a URL's absoluteString is the intended wire form")
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(Optional<String>.none as Any),
                     "an absent Optional must never be reported as encodable")

        // The plist-safe dictionary helper must omit an absent Optional and keep
        // a present one, and the result must really serialize.
        let omitted = V3WireContract.V3PropertyListValue.dictionary([
            "identifier": "com.example.app", "installedVersion": Optional<String>.none as Any])
        precondition(omitted["identifier"] as? String == "com.example.app")
        precondition(omitted["installedVersion"] == nil,
                     "an absent value must be omitted, not boxed into Any")
        precondition(!foundationEncodes(["installedVersion": Optional<String>.none as Any]),
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
        let encodingReply = V3ResponseEncoder.encode(unencodable, operation: "catalog", limit: V3WireContract.responseLimit)
        precondition(encodingReply.count <= V3WireContract.responseLimit)

        let encodingFailure = hostFailure(encodingReply, operation: "catalog", id: encodingID)
        precondition(encodingFailure.safeCause == .responseEncodingFailed,
                     "an encoding failure must arrive as responseEncodingFailed, got \(String(describing: encodingFailure.safeCause))")
        precondition(encodingFailure.code == .invalidResponse)
        // The fallback reports the wire boundary, which is the truthful stage for
        // a reply that could not be built. The host's own undecodable and
        // oversize boundaries use the request's own stage instead, which is
        // checked by the host-boundary assertions in the Python regression.
        precondition(encodingFailure.stage == .command,
                     "a reply the service could not build failed at the wire boundary")
        precondition(encodingFailure.correlationID == encodingID, "the correlation must survive the fallback")
        precondition(encodingFailure.retryable == false,
                     "a serialization defect is not fixed by repeating the same request")

        // The reply really is the production shape: BOTH keys present.
        let encodingDecoded = try! PropertyListSerialization.propertyList(
            from: encodingReply, format: nil) as! [String: Any]
        precondition(encodingDecoded["error"] as? String == "responseEncodingFailed",
                     "the legacy token is still emitted for an older host")
        precondition(encodingDecoded["failure"] is [String: Any],
                     "the structured envelope is still emitted and is authoritative")

        // An oversized-but-valid reply is a different defect and must not be
        // confused with the encoding failure.
        var oversized: [String: Any] = ["version": 1, "id": encodingID, "ok": true, "result": ["apps": []]]
        oversized["padding"] = String(repeating: "x", count: V3WireContract.responseLimit)
        let oversizeReply = V3ResponseEncoder.encode(oversized, operation: "catalog", limit: V3WireContract.responseLimit)
        precondition(oversizeReply.count > 0)
        let oversizeFailure = hostFailure(oversizeReply, operation: "catalog", id: encodingID)
        precondition(oversizeFailure.safeCause == .responseTooLarge,
                     "an oversized reply must arrive as responseTooLarge, got \(String(describing: oversizeFailure.safeCause))")
        precondition(oversizeFailure.safeCause != encodingFailure.safeCause,
                     "the two encoder failure modes must never be confused")

        // ---------------------------------------------------------------
        // Structured precedence still wins for unrelated typed failures, and
        // the legacy token path still works for a foreign service.
        // ---------------------------------------------------------------
        let unrelatedID = UUID().uuidString
        let busy = V3ResponseEncoder.fallback(id: unrelatedID, operation: "snapshot",
                                               token: "busy", code: .busy)
        precondition(hostFailure(busy, operation: "snapshot", id: unrelatedID).code == .busy,
                     "structured precedence must still deliver a typed unrelated failure")

        let foreignID = UUID().uuidString
        let foreignOnlyToken = try! PropertyListSerialization.data(
            fromPropertyList: ["version": 1, "id": foreignID, "error": "notReady"],
            format: .binary, options: 0)
        let foreign = hostFailure(foreignOnlyToken, operation: "snapshot", id: foreignID)
        precondition(foreign.code == .notReady && foreign.stage == .serviceReadiness,
                     "a legacy-only reply from an older service must still be typed")

        // An unknown token invents nothing.
        let unknownID = UUID().uuidString
        let unknownToken = try! PropertyListSerialization.data(
            fromPropertyList: ["version": 1, "id": unknownID, "error": "somethingNew"],
            format: .binary, options: 0)
        precondition(hostFailure(unknownToken, operation: "snapshot", id: unknownID).safeCause == nil,
                     "an unknown token must not invent a safe cause")

        // A reply for a different request is protocol evidence, never a
        // serialization defect, and never resolved to this caller.
        precondition(hostFailure(encodingReply, operation: "catalog", id: UUID().uuidString).code == .staleResult,
                     "a mismatched correlation must stay staleResult")

        // A successful reply is still accepted. The payload is typed explicitly
        // so a heterogeneous literal cannot be inferred as something the
        // property-list writer will not accept.
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
        // The payload is unwrapped before the cast, so the cast is applied to the
        // value and not to a double optional.
        let okInner = payload["result"] as? [String: Any]
        precondition(okInner != nil, "the result payload must survive the round trip")
        let okRows = okInner?["apps"] as? [Any] ?? []
        precondition(!okRows.isEmpty, "the catalog rows must survive the round trip")

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
        precondition(!secretFailure.safeMessage.contains(secret))
        precondition(!secretFailure.technicalDetails.contains(secret))
        precondition(!secretFailure.recovery.contains(secret))

        // ---------------------------------------------------------------
        // The token-to-cause mapping is total over the two encoder tokens, so a
        // future token cannot silently lose its classification.
        // ---------------------------------------------------------------
        precondition(V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.encodingFailed)
                     == .responseEncodingFailed)
        precondition(V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.tooLarge)
                     == .responseTooLarge)
        precondition(V3ResponseClassifier.safeCause(for: "notReady") == nil)

        // Each of the three reply defects has its own user-facing wording, so a
        // support reader can tell them apart without the diagnostics.
        precondition(encodingFailure.safeMessage != oversizeFailure.safeMessage)
        precondition(encodingFailure.recovery != oversizeFailure.recovery)
        precondition(oversizeFailure.safeMessage != "SideStore could not read this source's saved catalog data.")

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
