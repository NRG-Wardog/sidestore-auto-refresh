import Foundation

// V3_CATALOG_RESPONSE_ENCODING_HARNESS_V1
// Executes the REAL wire contract and the REAL plist-safe dictionary builder
// against Foundation's actual PropertyListSerialization.
//
// This exists because the physical failure was not a classification problem: a
// catalog row placed `app.installedApp?.version` (a `String?`) straight into a
// `[String: Any]`. That boxes `Optional<String>.none` into `Any`, which
// PropertyListSerialization cannot encode, so the whole catalog response failed
// to serialize even though the Core Data read had succeeded. Asserting that the
// key "exists in the source text" would not have caught it, so the encode and
// decode are actually performed here.

@main
struct CatalogResponseEncodingHarness {
    static func main() {
        // A minimal stand-in for a decoded StoreApp, mirroring only the fields
        // the catalog row reads and their real optionality.
        struct Row {
            let identifier: String
            let name: String
            let version: String
            let developer: String
            let description: String
            let iconURL: String
            let downloadURL: String
            let canInstall: Bool
            let installedID: String
            let installedVersion: String?
        }

        func makeRow(identifier: String, installedVersion: String?) -> [String: Any] {
            let row = Row(identifier: identifier, name: "App \(identifier)",
                           version: "1.0", developer: "Dev", description: "Desc",
                           iconURL: "https://example.invalid/icon.png",
                           downloadURL: "https://example.invalid/app.ipa",
                           canInstall: true, installedID: "", installedVersion: installedVersion)
            // The exact construction the service uses.
            return V3WireContract.V3PropertyListValue.dictionary([
                "identifier": row.identifier,
                "bundleID": "com.example.\(row.identifier)",
                "name": row.name,
                "version": row.version,
                "developer": row.developer,
                "description": row.description,
                "iconURL": row.iconURL,
                "downloadURL": row.downloadURL,
                "canInstall": row.canInstall,
                "installedID": row.installedID,
                "installedVersion": row.installedVersion
            ])
        }

        // 1. An app that is NOT installed: installedVersion must be absent, and
        //    the response must still serialize.
        let notInstalled = makeRow(identifier: "notinstalled", installedVersion: nil)
        precondition(notInstalled["installedVersion"] == nil,
                     "an absent optional must be omitted, not represented as a placeholder")

        // 2. An app that IS installed: installedVersion is present.
        let installed = makeRow(identifier: "installed", installedVersion: "2.1")
        precondition(installed["installedVersion"] as? String == "2.1")

        // 3. A mixed catalog, exactly the shape that broke on device.
        let catalog: [String: Any] = ["apps": [notInstalled, installed], "nextCursor": -1]

        // The regression itself: the OLD construction, with the Optional boxed
        // into Any, must fail to serialize. If this ever starts succeeding, the
        // premise of the fix has changed and must be re-examined.
        func serializes(_ value: [String: Any]) -> Bool {
            (try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)) != nil
        }
        let boxed: [String: Any] = ["identifier": "x", "installedVersion": Optional<String>.none as Any]
        precondition(serializes(boxed) == false,
                     "a boxed Optional.none unexpectedly serialized; the P0 premise changed")

        // The fix: the real catalog response encodes and round-trips.
        guard let encoded = try? PropertyListSerialization.data(fromPropertyList: catalog,
                                                                 format: .binary, options: 0) else {
            preconditionFailure("the catalog response must serialize")
        }
        guard let decoded = try? PropertyListSerialization.propertyList(from: encoded, format: nil)
                as? [String: Any],
              let apps = decoded["apps"] as? [[String: Any]],
              apps.count == 2 else {
            preconditionFailure("the catalog response must round-trip with both rows")
        }
        precondition(apps[0]["installedVersion"] == nil,
                     "the not-installed row must stay absent after a round-trip")
        precondition(apps[1]["installedVersion"] as? String == "2.1",
                     "the installed row must keep its version after a round-trip")
        precondition(decoded["nextCursor"] as? Int == -1)

        // An empty catalog must also be valid: a source with zero apps is a
        // success, not a failure.
        let empty: [String: Any] = ["apps": [[String: Any]](), "nextCursor": -1]
        precondition((try? PropertyListSerialization.data(fromPropertyList: empty,
                                                          format: .binary, options: 0)) != nil,
                     "an empty catalog must serialize")

        // V3_PROPERTY_LIST_VALUE_V1: unwrapping removes the Optional box rather
        // than stringifying it.
        let wrapped: String? = "value"
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(wrapped) as? String == "value")
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(Optional<String>.none) == nil)
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(nil) == nil)
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(42) as? Int == 42)

        // A present but unrepresentable value is preserved, so serialization
        // fails loudly instead of silently dropping data.
        final class Opaque {}
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(Opaque()))
        precondition(V3WireContract.V3PropertyListValue.isEncodable("text"))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(1))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(true))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(Date()))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(URL(string: "https://example.invalid")!))
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(Optional<String>.none as Any))

        // The encoder must distinguish the two failure modes. This mirrors the
        // service's encode path with the same limit and the same tokens.
        let limit = 4_194_304
        func classify(_ value: [String: Any]) -> String {
            do {
                let data = try PropertyListSerialization.data(fromPropertyList: value,
                                                               format: .binary, options: 0)
                return data.count <= limit ? "ok" : "responseTooLarge"
            } catch {
                return "responseEncodingFailed"
            }
        }
        precondition(classify(["version": 1, "id": "u", "ok": true]) == "ok")
        precondition(classify(["version": 1, "id": "u", "bad": Opaque()]) == "responseEncodingFailed")
        // A genuinely oversized but valid payload is a different defect.
        let oversized = "x".padding(toLength: limit + 16, withPad: "x", startingAt: 0)
        precondition(classify(["version": 1, "id": "u", "blob": oversized]) == "responseTooLarge")

        // The fallback reply must itself always serialize, and must carry the
        // correlation and operation.
        let fallback: [String: Any] = ["version": 1, "id": "u", "error": "responseEncodingFailed"]
        precondition((try? PropertyListSerialization.data(fromPropertyList: fallback,
                                                         format: .binary, options: 0)) != nil,
                     "the correlated fallback must always serialize")

        // V3_CATALOG_ROW_POLICY_V1: duplicates are removed within a page and
        // across pages, first-seen order preserved.
        func row(_ id: String) -> [String: Any] { ["identifier": id, "name": "n\(id)"] }
        let samePage = [[String: Any]]([row("a"), row("b"), row("a"), row("c"), row("b")])
        let dedupedSamePage = V3CatalogRowPolicy.dedupe(samePage)
        precondition(dedupedSamePage.count == 3, "a same-page duplicate survived")
        precondition(dedupedSamePage.compactMap { $0["identifier"] as? String } == ["a", "b", "c"],
                     "first-seen ordering was not preserved")
        let acrossPages = V3CatalogRowPolicy.appending([row("b"), row("d")], to: dedupedSamePage)
        precondition(acrossPages.compactMap { $0["identifier"] as? String } == ["a", "b", "c", "d"])
        // A row with no usable identifier cannot be deduplicated, so it is
        // rejected rather than silently displayed.
        precondition(V3CatalogRowPolicy.dedupe([["name": "no id"]]).isEmpty)

        print("V3_CATALOG_RESPONSE_ENCODING_PASS")
    }
}
