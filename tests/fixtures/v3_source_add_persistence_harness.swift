import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

struct SourceAddStoreModel {
    private(set) var diskRows: [String] = []
    private(set) var notifications = 0

    mutating func add(_ identifier: String, fetchSucceeds: Bool = true) throws -> [String: Any] {
        guard fetchSucceeds else { throw NSError(domain: "fixture", code: 1) }
        var privateContextRows: [String] = []
        if !diskRows.contains(identifier) { privateContextRows.append(identifier) }

        // SideStore Source.isAdded() uses a new context and counts disk rows.
        let persistedBeforeFetchSave = diskRows.filter { $0 == identifier }.count == 1
        let sameContextSeesFetchedUnsavedRow = privateContextRows.contains(identifier)
        if !persistedBeforeFetchSave {
            require(sameContextSeesFetchedUnsavedRow,
                    "fixture did not reproduce fetchSource's inserted transient Source")
        }

        switch V3SourceAddPersistencePolicy.decision(sourceIsPersisted: persistedBeforeFetchSave) {
        case .alreadyAdded:
            break
        case .save:
            // Persist exactly the context's fetched insert.
            diskRows.append(contentsOf: privateContextRows)
        }

        // This models a new authoritative context after save.
        let freshContextCount = diskRows.filter { $0 == identifier }.count
        guard let result = V3SourceAddPersistencePolicy.verifiedResult(
            identifier: identifier,
            alreadyAdded: persistedBeforeFetchSave,
            authoritativeCount: freshContextCount) else {
            throw NSError(domain: "fixture", code: 2)
        }
        if !persistedBeforeFetchSave { notifications += 1 }
        return result
    }

    mutating func remove(_ identifier: String) {
        diskRows.removeAll { $0 == identifier }
    }

    func freshSnapshot() -> [String] { diskRows }
}

@main
struct SourceAddPersistenceHarness {
    static func main() throws {
        let sourceID = "com.example.reynard"
        var db = SourceAddStoreModel()

        // Reproduces the exact defect shape: same context sees a new object,
        // while SideStore's fresh-context Source.isAdded() says not persisted.
        let first = try db.add(sourceID)
        require(db.diskRows == [sourceID], "first confirm did not persist exactly one Source")
        require(first["added"] as? Bool == true && first["alreadyAdded"] as? Bool == false,
                "first confirm did not return added=true")
        require(first["persistenceVerified"] as? Bool == true,
                "first confirm omitted authoritative persistence proof")
        require(V3SourceAddPersistencePolicy.confirmationMessage(first) == "Source added.",
                "verified add did not produce the success message")
        require(db.freshSnapshot().contains(sourceID), "fresh snapshot did not contain the Source")
        require(db.notifications == 1, "add notification was not emitted exactly once")

        let duplicate = try db.add(sourceID)
        require(db.diskRows.count == 1, "duplicate add created a second Source row")
        require(duplicate["added"] as? Bool == false && duplicate["alreadyAdded"] as? Bool == true,
                "duplicate add did not return alreadyAdded=true")
        require(V3SourceAddPersistencePolicy.confirmationMessage(duplicate) == "Source already added.",
                "duplicate add did not produce the already-added message")
        require(db.notifications == 1, "duplicate add posted a second added notification")

        db.remove(sourceID)
        let readded = try db.add(sourceID)
        require(readded["added"] as? Bool == true && db.diskRows == [sourceID],
                "remove then add again did not persist a single Source")
        let relaunchedModel = db
        require(relaunchedModel.freshSnapshot() == [sourceID],
                "a new process model could not observe the persisted Source")

        var failedFetch = SourceAddStoreModel()
        do {
            _ = try failedFetch.add(sourceID, fetchSucceeds: false)
            fatalError("fetch/download/JSON failure was treated as success")
        } catch {}
        require(failedFetch.diskRows.isEmpty && failedFetch.notifications == 0,
                "failed source fetch changed persistence or emitted success notification")

        require(V3SourceAddPersistencePolicy.validatedURL(
            "https://github.com/minh-ton/reynard-browser/releases/download/0.0.1-a1/source.json") != nil,
            "valid issue URL was rejected")
        for malformed in ["", "file:///tmp/source.json", "https://user:pass@example.com/source.json",
                          "https://", "javascript:alert(1)"] {
            require(V3SourceAddPersistencePolicy.validatedURL(malformed) == nil,
                    "invalid source URL passed validation")
        }

        let unverified: [String: Any] = ["identifier": sourceID, "added": true,
                                          "alreadyAdded": false, "persistenceVerified": false]
        require(V3SourceAddPersistencePolicy.confirmationMessage(unverified) == nil,
                "host could show Source added without persistence proof")
        let ambiguous: [String: Any] = ["identifier": sourceID, "added": true,
                                         "alreadyAdded": true, "persistenceVerified": true]
        require(V3SourceAddPersistencePolicy.confirmationMessage(ambiguous) == nil,
                "ambiguous add outcome was accepted as success")
        print("V3_SOURCE_ADD_PERSISTENCE_PASS")
    }
}
