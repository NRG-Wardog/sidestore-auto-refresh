@main
struct SecretHandoffWireHarness {
    static func main() throws {
        let now = Date(timeIntervalSince1970: 1000)
        let secretAnswer = ["appleID": "person@example.com", "password": "private-password",
                            "verificationCode": "123456"]
        let secretBytes = try PropertyListSerialization.data(fromPropertyList: secretAnswer,
            format: .binary, options: 0)
        let record = V3SecretHandoffRecord.encode(kind: "stringDictionary", payload: secretBytes,
            createdAt: now)!
        precondition(V3SecretHandoffRecord.isStrictVersionOne(NSNumber(value: 1)))
        precondition(!V3SecretHandoffRecord.isStrictVersionOne(NSNumber(value: 1.0)),
            "a plist real cannot impersonate the handoff schema version")
        precondition(!V3SecretHandoffRecord.isStrictVersionOne(NSNumber(value: true)),
            "a Boolean cannot impersonate the handoff schema version")
        precondition(V3SecretHandoffRecord.decode(record, expectedKind: "stringDictionary", now: now) == secretBytes)
        var fractionalVersion = try PropertyListSerialization.propertyList(from: record, format: nil) as! [String: Any]
        fractionalVersion["version"] = 1.5
        let fractionalVersionRecord = try PropertyListSerialization.data(fromPropertyList: fractionalVersion,
            format: .binary, options: 0)
        precondition(V3SecretHandoffRecord.decode(fractionalVersionRecord,
            expectedKind: "stringDictionary", now: now) == nil,
            "fractional versions are rejected before interpreting the payload")
        precondition(V3SecretHandoffRecord.decode(record, expectedKind: "string", now: now) == nil)
        precondition(V3SecretHandoffRecord.decode(record, expectedKind: "stringDictionary",
            now: now.addingTimeInterval(V3SecretHandoffRecord.lifetime + 1)) == nil)
        precondition(V3SecretHandoffRecord.decode(Data([1, 2, 3]), expectedKind: "stringDictionary", now: now) == nil)

        let containerRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("V3SharedFileRecordTests." + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: containerRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: containerRoot) }
        let legacySuiteName = "V3LegacySharedFileTests." + UUID().uuidString
        let legacyDefaults = UserDefaults(suiteName: legacySuiteName)!
        defer { legacyDefaults.removePersistentDomain(forName: legacySuiteName) }
        legacyDefaults.set(Data("old raw payload".utf8), forKey: "V3SharedFile.old-token")
        legacyDefaults.set("unrelated", forKey: "V3SharedFileOther")
        V3SharedFileRecord.removeLegacyDefaultsRecords(legacyDefaults)
        precondition(legacyDefaults.object(forKey: "V3SharedFile.old-token") == nil &&
                     legacyDefaults.string(forKey: "V3SharedFileOther") == "unrelated",
            "upgrade cleanup removes only abandoned legacy staged payload keys")
        let stagingNow = Date()
        let pairingPayload = Data("pairing-record".utf8)
        let pairingToken = V3SharedFileRecord.stage(pairingPayload, purpose: "pairing",
            containerRoot: containerRoot, now: stagingNow)!
        precondition(UUID(uuidString: pairingToken)?.uuidString == pairingToken)
        let stagedState = V3SharedFileRecord.sweep(containerRoot: containerRoot, now: stagingNow)
        precondition(stagedState.count == 1 && stagedState.storedBytes > pairingPayload.count &&
                     stagedState.storedBytes <= pairingPayload.count + 4096,
            "a live staged file stays available while the host waits for the service")
        let selectedFile = containerRoot.appendingPathComponent("selected.json")
        try Data("bounded-input".utf8).write(to: selectedFile)
        let boundedInput = try V3SharedFileInput.readBounded(selectedFile)
        precondition(boundedInput == Data("bounded-input".utf8),
            "selected provider files use the bounded reader")
        let tooLargeFile = containerRoot.appendingPathComponent("too-large.json")
        try Data(repeating: 7, count: V3SharedFileInput.maximumBytes + 1).write(to: tooLargeFile)
        do {
            _ = try V3SharedFileInput.readBounded(tooLargeFile)
            preconditionFailure("oversized files must be rejected before payload staging")
        } catch V3SharedFileInputError.tooLarge { }
        do {
            _ = try V3SharedFileInput.readBounded(containerRoot)
            preconditionFailure("directories are not import payloads")
        } catch V3SharedFileInputError.unavailable { }
        precondition(V3SharedFileRecord.consume(pairingToken, purpose: "accountImport",
            containerRoot: containerRoot, now: stagingNow) == nil,
            "a staged file token cannot be consumed for another operation purpose")
        let stagedFile = V3SharedFileRecord.stagingDirectory(containerRoot: containerRoot)
            .appendingPathComponent(pairingToken + ".bin")
        precondition(FileManager.default.fileExists(atPath: stagedFile.path),
            "a wrong-purpose request cannot destroy the correct pending import")
        let stagedAttributes = try FileManager.default.attributesOfItem(atPath: stagedFile.path)
        precondition((stagedAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o600,
            "staged shared files are private to the entitled processes")
        precondition(V3SharedFileRecord.consume("../" + pairingToken, purpose: "pairing",
            containerRoot: containerRoot, now: now) == nil,
            "the file token cannot escape the canonical staging directory")
        precondition(V3SharedFileRecord.consume(pairingToken, purpose: "pairing",
            containerRoot: containerRoot, now: stagingNow) == pairingPayload,
            "the intended operation receives the staged bytes")
        precondition(V3SharedFileRecord.consume(pairingToken, purpose: "pairing",
            containerRoot: containerRoot, now: stagingNow) == nil, "staged bytes are consumed only once")

        let old = stagingNow.addingTimeInterval(-V3SharedFileRecord.lifetime - 1)
        let expiredToken = V3SharedFileRecord.stage(pairingPayload, purpose: "sidesign",
            containerRoot: containerRoot, now: old)!
        let afterExpiry = V3SharedFileRecord.sweep(containerRoot: containerRoot, now: stagingNow)
        precondition(afterExpiry.count == 0 && afterExpiry.storedBytes == 0,
            "crash-left and failed-before-dispatch staging is removed after its TTL")
        precondition(!FileManager.default.fileExists(atPath: V3SharedFileRecord.stagingDirectory(containerRoot: containerRoot)
            .appendingPathComponent(expiredToken + ".bin").path))

        for _ in 0..<V3SharedFileRecord.maximumPendingFiles {
            precondition(V3SharedFileRecord.stage(Data([7]), purpose: "accountImport",
                containerRoot: containerRoot, now: stagingNow) != nil)
        }
        precondition(V3SharedFileRecord.stage(Data([8]), purpose: "accountImport",
            containerRoot: containerRoot, now: stagingNow) == nil,
            "pending shared-file tokens have a hard count bound")
        _ = V3SharedFileRecord.sweep(containerRoot: containerRoot,
            now: stagingNow.addingTimeInterval(V3SharedFileRecord.lifetime + 1))
        precondition(V3SharedFileRecord.sweep(containerRoot: containerRoot,
            now: stagingNow.addingTimeInterval(V3SharedFileRecord.lifetime + 1)).count == 0)

        let concurrentRoot = containerRoot.appendingPathComponent("concurrent", isDirectory: true)
        try FileManager.default.createDirectory(at: concurrentRoot, withIntermediateDirectories: true)
        let resultLock = NSLock()
        var concurrentTokens: [String] = []
        DispatchQueue.concurrentPerform(iterations: V3SharedFileRecord.maximumPendingFiles * 2) { _ in
            if let next = V3SharedFileRecord.stage(Data(repeating: 1, count: 128),
                    purpose: "pairing", containerRoot: concurrentRoot, now: stagingNow) {
                resultLock.lock()
                concurrentTokens.append(next)
                resultLock.unlock()
            }
        }
        precondition(concurrentTokens.count == V3SharedFileRecord.maximumPendingFiles &&
                     V3SharedFileRecord.sweep(containerRoot: concurrentRoot, now: stagingNow).count ==
                        V3SharedFileRecord.maximumPendingFiles,
            "parallel imports cannot exceed the shared-file count budget")

        let token = UUID().uuidString
        let requestID = UUID().uuidString
        let sessionID = UUID().uuidString
        // The answer travels in the request. A re-signer never grants the shared
        // Keychain group to the service extension, so a token reference could
        // never be read back: this contract must carry the answer itself.
        let request: [String: Any] = ["version": 1, "id": requestID, "operation": "authRespond",
            "target": sessionID, "deadline": now.addingTimeInterval(30),
            "payload": ["prompt": "credentials-prompt",
                        "answer": ["appleID": "user@example.com", "password": "private-password"]]]
        let requestData = try PropertyListSerialization.data(fromPropertyList: request, format: .binary, options: 0)
        precondition(requestData.range(of: Data("private-password".utf8)) != nil,
            "the credential is carried by the request the two signed peers exchange")
        let decoded = V3WireContract.decodeRequest(requestData, now: now)
        precondition(decoded != nil, "the service accepts the answer for a sensitive prompt")
        precondition(V3WireContract.encodeRequest(request, now: now) != nil,
            "the host and service share the same validated request contract")
        let replayFingerprint = V3RequestReplayPolicy.fingerprint(requestData)
        precondition(V3RequestReplayPolicy.matches(cachedFingerprint: replayFingerprint,
            incomingRequestData: requestData))
        precondition(V3RequestReplayPolicy.matchesInFlight(cachedFingerprint: replayFingerprint,
            incomingRequestData: requestData),
            "an identical in-flight request may be recognized as a duplicate")
        var reusedIDForDifferentAction = request
        reusedIDForDifferentAction["payload"] = ["prompt": "different-prompt",
                                                 "answer": ["password": "private-password"]]
        let differentActionData = try PropertyListSerialization.data(
            fromPropertyList: reusedIDForDifferentAction, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(differentActionData, now: now) != nil &&
                     !V3RequestReplayPolicy.matches(cachedFingerprint: replayFingerprint,
                        incomingRequestData: differentActionData),
            "the same request ID cannot replay a cached result for a different valid prompt")
        precondition(!V3RequestReplayPolicy.matchesInFlight(cachedFingerprint: replayFingerprint,
            incomingRequestData: differentActionData),
            "a different in-flight action reusing an ID must be rejected before cancel dispatch")
        // Only the declared `answer` key is exempt from the sweep, and only for
        // the operations that declare it.
        var outboundRawSecret = request
        outboundRawSecret["payload"] = ["prompt": "credentials-prompt", "password": "private-password"]
        precondition(V3WireContract.encodeRequest(outboundRawSecret, now: now) == nil,
            "a password outside the answer carrier is rejected before the request crosses")
        var nestedUnderAnswer = request
        nestedUnderAnswer["payload"] = ["prompt": "credentials-prompt",
                                        "answer": ["nested": ["password": "private-password"]]]
        precondition(V3WireContract.encodeRequest(nestedUnderAnswer, now: now) == nil,
            "the answer carrier stays flat, so the sweep's exemption is not a hole")
        var oversizedAnswer = request
        oversizedAnswer["payload"] = ["prompt": "credentials-prompt",
                                      "answer": ["password": String(repeating: "x", count: 4097)]]
        precondition(V3WireContract.encodeRequest(oversizedAnswer, now: now) == nil,
            "an oversized answer is rejected")
        var answerOnUnrelatedOperation = request
        answerOnUnrelatedOperation["operation"] = "authBegin"
        answerOnUnrelatedOperation["target"] = sessionID
        answerOnUnrelatedOperation["payload"] = ["session": sessionID,
            "sessionDeadline": now.addingTimeInterval(30),
            "answer": ["password": "private-password"]]
        precondition(V3WireContract.encodeRequest(answerOnUnrelatedOperation, now: now) == nil,
            "the exemption does not extend to operations that do not declare it")

        var unrelatedOperationWithNestedPassword = request
        unrelatedOperationWithNestedPassword["operation"] = "snapshot"
        unrelatedOperationWithNestedPassword["target"] = ""
        unrelatedOperationWithNestedPassword["payload"] = ["metadata": ["Password": "private-password"]]
        let nestedPasswordBytes = try PropertyListSerialization.data(
            fromPropertyList: unrelatedOperationWithNestedPassword, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(nestedPasswordBytes, now: now) == nil,
            "generic payloads must not smuggle a secret field through XPC")

        unrelatedOperationWithNestedPassword["payload"] = ["form": ["verification_code": "123456"]]
        let nestedCodeBytes = try PropertyListSerialization.data(
            fromPropertyList: unrelatedOperationWithNestedPassword, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(nestedCodeBytes, now: now) == nil,
            "generic payloads must reject nested verification-code fields")

        unrelatedOperationWithNestedPassword["payload"] = ["readinessOnly": true]
        let validReadinessBytes = try PropertyListSerialization.data(
            fromPropertyList: unrelatedOperationWithNestedPassword, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validReadinessBytes, now: now) != nil,
            "the explicit lightweight readiness snapshot shape remains supported")
        unrelatedOperationWithNestedPassword["payload"] = ["readinessOnly": true, "extra": "ignored"]
        let extraReadinessBytes = try PropertyListSerialization.data(
            fromPropertyList: unrelatedOperationWithNestedPassword, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(extraReadinessBytes, now: now) == nil,
            "snapshot readiness payloads cannot carry extra fields")

        var rawSessionTarget = request
        rawSessionTarget["operation"] = "authPoll"
        rawSessionTarget["payload"] = nil
        rawSessionTarget["target"] = "private-password"
        let rawSessionTargetBytes = try PropertyListSerialization.data(
            fromPropertyList: rawSessionTarget, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(rawSessionTargetBytes, now: now) == nil,
            "session-scoped requests must not carry arbitrary values in the target field")

        var ipaCleanup = request
        ipaCleanup["operation"] = "ipaCleanup"
        ipaCleanup["target"] = UUID().uuidString.lowercased()
        ipaCleanup["payload"] = nil
        let validCleanupBytes = try PropertyListSerialization.data(
            fromPropertyList: ipaCleanup, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validCleanupBytes, now: now) != nil,
            "canonical IPA tokens remain valid XPC targets")
        ipaCleanup["target"] = "../../private/path.ipa"
        let pathTraversalBytes = try PropertyListSerialization.data(
            fromPropertyList: ipaCleanup, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(pathTraversalBytes, now: now) == nil,
            "the service protocol rejects paths in IPA cleanup targets")

        var operationStart = request
        operationStart["operation"] = "opStart"
        operationStart["target"] = ""
        operationStart["payload"] = ["kind": "installURL", "target": "https://example.invalid/app.ipa",
            "session": sessionID]
        let validURLInstallBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validURLInstallBytes, now: now) != nil,
            "an HTTPS URL install target is valid despite containing slashes")
        operationStart["payload"] = ["kind": "installURL", "target": "file:///private/app.ipa",
            "session": sessionID]
        let invalidURLInstallBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(invalidURLInstallBytes, now: now) == nil,
            "file URLs are never accepted as install URL targets")
        operationStart["payload"] = ["kind": "update",
            "target": "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/InstalledApp/p42",
            "session": sessionID]
        let validInstalledAppBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validInstalledAppBytes, now: now) != nil,
            "a managed installed-app URI is valid for an update target")
        operationStart["payload"] = ["kind": "refreshApp",
            "target": "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/InstalledApp/p42",
            "session": sessionID]
        let validInstalledAppEntityBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validInstalledAppEntityBytes, now: now) != nil,
            "the actual InstalledApp entity URI is valid for refresh operations")
        operationStart["payload"] = ["kind": "install",
            "target": "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/StoreApp/p7",
            "session": sessionID]
        let validCatalogAppBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validCatalogAppBytes, now: now) != nil,
            "a managed catalog-app URI is valid for install")
        operationStart["payload"] = ["kind": "update",
            "target": "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/App/p42",
            "session": sessionID]
        let invalidEntityBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(invalidEntityBytes, now: now) == nil,
            "unknown Core Data entities are not accepted as operation targets")
        operationStart["payload"] = ["kind": "delete", "target": "../../App/p42", "session": sessionID]
        let traversalOperationBytes = try PropertyListSerialization.data(
            fromPropertyList: operationStart, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(traversalOperationBytes, now: now) == nil,
            "managed-operation targets reject filesystem traversal strings")

        var operationCancel = request
        operationCancel["operation"] = "opCancel"
        operationCancel["target"] = sessionID
        operationCancel["payload"] = ["knownStarted": true]
        let validOperationCancelBytes = try PropertyListSerialization.data(
            fromPropertyList: operationCancel, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(validOperationCancelBytes, now: now) != nil,
            "session-scoped cancellation accepts its explicit boolean schema")
        operationCancel["payload"] = ["knownStarted": "true"]
        let malformedOperationCancelBytes = try PropertyListSerialization.data(
            fromPropertyList: operationCancel, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(malformedOperationCancelBytes, now: now) == nil,
            "session-scoped cancellation rejects non-boolean values")
        var sideSignConfig = request
        sideSignConfig["operation"] = "sidesignSet"
        sideSignConfig["target"] = ""
        sideSignConfig["payload"] = ["secretToken": token]
        precondition(V3WireContract.encodeRequest(sideSignConfig, now: now) != nil,
            "SideSign configuration crosses XPC only as a one-time Keychain reference")
        sideSignConfig["payload"] = ["config": "{\"Authorization\":\"Bearer PRIVATE_TOKEN\"}"]
        precondition(V3WireContract.encodeRequest(sideSignConfig, now: now) == nil,
            "header JSON cannot bypass the raw-secret outbound wire guard")

        var legacyAnswer = request
        legacyAnswer["payload"] = ["prompt": "credentials-prompt", "answer": secretAnswer]
        let legacyBytes = try PropertyListSerialization.data(fromPropertyList: legacyAnswer, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(legacyBytes, now: now) == nil,
            "raw credentials and one-time codes must be rejected by the XPC plist contract")

        var backupExport = request
        backupExport["operation"] = "accountExport"
        backupExport["target"] = ""
        backupExport["payload"] = ["secretToken": token, "includeApple": false]
        let exportBytes = try PropertyListSerialization.data(fromPropertyList: backupExport, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(exportBytes, now: now) != nil)
        backupExport["payload"] = ["password": "private-backup-passphrase", "includeApple": false]
        let rawBackupBytes = try PropertyListSerialization.data(fromPropertyList: backupExport, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(rawBackupBytes, now: now) == nil,
            "backup passphrases must be rejected by the XPC plist contract")
        print("V3_SECRET_HANDOFF_WIRE_PASS")
    }
}
