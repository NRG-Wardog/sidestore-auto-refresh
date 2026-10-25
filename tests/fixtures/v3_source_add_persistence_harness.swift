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
        let sourceFailure = V3SourceAddPersistencePolicy.unverifiedPersistenceFailure(correlationID: UUID().uuidString)
        let unverifiedFailure = V3OperationFailureDetails(sourceFailure)
        require(unverifiedFailure.whatHappened == "SideStore could not confirm that the source was saved.\n" + sourceFailure.diagnosticLabel,
                "an unverified source add must be reported as a source persistence failure")
        require(!unverifiedFailure.whatHappened.contains("sourceAddConfirmed") &&
                unverifiedFailure.whatToDo.contains("reload the list") &&
                unverifiedFailure.retryDisposition == .blocked,
                "unverified persistence guidance must avoid an internal operation token and blind retry")
        let internalFallback = V3SourceAddFailurePolicy.normalized(CombinedFailure(
            operation: "sourceAddConfirmed", stage: .command, code: .invalidResponse,
            id: UUID().uuidString, retryable: false))
        require(internalFallback.operation == "source" && internalFallback.stage == .source &&
                !internalFallback.safeMessage.contains("sourceAddConfirmed") &&
                internalFallback.recovery.contains("Return to Sources"),
                "generic add-result failures must not expose internal command names or connection guidance")
        let busyFallback = V3SourceAddFailurePolicy.normalized(CombinedFailure(
            operation: "sourceAddConfirmed", stage: .command, code: .busy,
            id: UUID().uuidString, retryable: true))
        require(busyFallback.safeCause == .sourceAddBusy &&
                busyFallback.recovery.contains("preview and confirm the add again"),
                "a busy source add gets source-specific recovery guidance")
        // The pinned Source identifier strips scheme and query and lowercases
        // path. It is a persistence key, never a fetch target (issues #38/#37).
        let originalURL = "https://Example.com/CaseSensitive/Source.json?channel=Beta&v=2"
        let normalizedID = "example.com/casesensitive/source.json"
        let terminal = sourceRecoveryTerminal(V3RequiresSourceError(sourceID: normalizedID,
            sourceName: "Example", sourceURL: originalURL))
        // Execute the same start-failure envelope storage/readback as the
        // operation center; terminalFailure alone hid a failedToStart routing bug.
        let host = SourceRecoveryStartHost()
        let hostGeneration = host.attempt.begin()
        let sourceSession = hostGeneration.uuidString
        var startFailure = terminal
        startFailure["failedToStart"] = true
        let terminalStore = V3OperationTerminalResponse()
        require(terminalStore.finishOrResolve(startFailure, backendSettled: true), "terminal rejected")
        let startReply = terminalStore.reply(sessionID: sourceSession, backendSettled: true)!
        require(V3SourceRecoveryPolicy.isSettledStartReply(startReply, sessionID: sourceSession),
                "real failedToStart requiresSource envelope must reach source recovery")
        host.receive(startReply, generation: hostGeneration)
        require(!host.genericFailure && host.applied?["sourceURL"] as? String == originalURL,
                "production start handler swallowed missing-source recovery")
        require(host.attempt.isTerminal, "missing-source start reply did not settle host attempt")
        require(!V3SourceRecoveryPolicy.isSettledStartReply(startReply, sessionID: UUID().uuidString),
                "foreign session must not show a source mutation offer")
        for value in [false, 1, "true"] as [Any] {
            var bad = startReply; bad["backendSettled"] = value
            require(!V3SourceRecoveryPolicy.isSettledStartReply(bad, sessionID: sourceSession),
                    "unsettled/malformed terminal must not show source recovery")
        }
        for value in [true, 0, "false"] as [Any] {
            var bad = startReply; bad["outcomeUnknown"] = value
            require(!V3SourceRecoveryPolicy.isSettledStartReply(bad, sessionID: sourceSession),
                    "unknown/malformed outcome must not show source recovery")
        }
        var oldReply = startReply; oldReply.removeValue(forKey: "sourceURL")
        require(V3SourceRecoveryPolicy.isSettledStartReply(oldReply, sessionID: sourceSession),
                "old backend must reach manual source recovery")
        let legacyHost = SourceRecoveryStartHost()
        let legacyGeneration = legacyHost.attempt.begin()
        oldReply["session"] = legacyGeneration.uuidString
        legacyHost.receive(oldReply, generation: legacyGeneration)
        require(!legacyHost.genericFailure && legacyHost.applied?["state"] as? String == "requiresSource",
                "production start handler must preserve manual fallback for old backend")
        var generic = startReply; generic["state"] = "failed"
        require(!V3SourceRecoveryPolicy.isSettledStartReply(generic, sessionID: sourceSession),
                "generic start failure must preserve existing failure handling")
        let serialized = try JSONSerialization.data(withJSONObject: startReply)
        let decoded = try JSONSerialization.jsonObject(with: serialized) as! [String: Any]
        let recoveredURL = V3SourceRecoveryPolicy.target(sourceID: decoded["sourceID"] as! String,
                                                         sourceURL: decoded["sourceURL"] as? String)
        require(recoveredURL == originalURL, "wire recovery lost original URL spelling/query")
        let privateTerminal = sourceRecoveryTerminal(V3RequiresSourceError(sourceID: normalizedID,
            sourceName: "Example", sourceURL: "https://user:secret@example.com/a"))
        require(privateTerminal["sourceURL"] == nil, "producer must not expose URL credentials")
        var attempt = V3OperationAttemptState()
        let generation = attempt.begin()
        let session = attempt.sessionID!
        require(attempt.accept(state: "requiresSource", generation: generation, sessionID: session),
                "missing-source terminal must be admitted")
        require(attempt.owns(generation: generation, sessionID: session),
                "terminal source recovery still owns its generation")
        _ = attempt.supersede()
        require(!attempt.owns(generation: generation, sessionID: session),
                "late preview must not mutate after a newer attempt")
        require(V3SourceAddPersistencePolicy.validatedURL(normalizedID) == nil,
                "baseline identifier unexpectedly became a fetchable URL")
        require(V3SourceRecoveryPolicy.target(sourceID: normalizedID, sourceURL: nil) == nil,
                "old backend must request manual recovery, not guess HTTPS")
        require(V3SourceRecoveryPolicy.target(sourceID: "", sourceURL: originalURL) == nil,
                "missing identity must fail closed")
        for invalid in [normalizedID, "file:///tmp/source.json", "https://user:secret@example.com/a",
                        "ftp://example.com/a", "https://user@example.com/a"] {
            require(V3SourceRecoveryPolicy.target(sourceID: normalizedID, sourceURL: invalid) == nil,
                    "invalid/private URL was admitted")
        }
        require(V3SourceRecoveryPolicy.matchesPreview(["identifier": normalizedID], sourceID: normalizedID),
                "matching preview was rejected")
        require(!V3SourceRecoveryPolicy.matchesPreview(["identifier": "other"], sourceID: normalizedID),
                "redirected/mismatched source may not be added")
        require(!V3SourceRecoveryPolicy.matchesPreview([:], sourceID: normalizedID),
                "missing preview identity may not be added")
        var verified: [String: Any] = ["identifier": normalizedID, "added": true,
            "alreadyAdded": false, "persistenceVerified": true,
            "sources": [["identifier": normalizedID]]]
        require(V3SourceRecoveryPolicy.verifiedAddition(verified, sourceID: normalizedID),
                "persisted matching add should permit install retry")
        verified["added"] = false
        verified["alreadyAdded"] = true
        require(V3SourceRecoveryPolicy.verifiedAddition(verified, sourceID: normalizedID),
                "persisted duplicate should permit install retry")
        verified["sources"] = [["identifier": "other"]]
        require(!V3SourceRecoveryPolicy.verifiedAddition(verified, sourceID: normalizedID),
                "missing durable source must not retry install")
        verified["sources"] = [["identifier": normalizedID]]
        verified["persistenceVerified"] = false
        require(!V3SourceRecoveryPolicy.verifiedAddition(verified, sourceID: normalizedID),
                "unverified add must not retry install")

        print("V3_SOURCE_ADD_PERSISTENCE_PASS")
    }
}
