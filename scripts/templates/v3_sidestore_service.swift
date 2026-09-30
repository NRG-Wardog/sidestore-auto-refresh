
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
// V3_HEADLESS_SERVICE_V2: headless backend. This file owns the command gate,
// snapshots, and non-interactive reads. All interactive work runs through
// V3HeadlessRuntime sessions; no window, presenter, or visible UI exists here.
// V3_CERTIFICATE_CREATE_ADAPTER_V1: use the upstream portal and persistence
// implementations, but make their non-throwing persistence contract explicit
// at the v3 boundary. Certificate creation must never implicitly activate it.
enum V3CertificateCreateAdapter {
    enum Outcome: String {
        case createdAndStored
        case remoteCreatedLocalStorageUnverified
    }

    static func createAndPersist<Certificate>(
        create: () async throws -> Certificate,
        persist: (Certificate) -> Void,
        verifyStored: (Certificate) -> Bool
    ) async throws -> Outcome {
        let certificate = try await create()
        persist(certificate)
        return verifyStored(certificate) ? .createdAndStored : .remoteCreatedLocalStorageUnverified
    }

    static func matchesStoredCertificate(expectedSerial: String, parsedSerial: String?,
                                         enumeratedSerials: [String]) -> Bool {
        guard !expectedSerial.isEmpty, let parsedSerial, !parsedSerial.isEmpty,
              expectedSerial == parsedSerial else { return false }
        return enumeratedSerials.contains(expectedSerial)
    }
}

// V3_OPERATION_RECOVERY_JOURNAL_V1
// Shared by LiveContainer's App Group and this service process. Serialization
// uses the existing process-shared App Group lock; the record stores only IDs
// and fixed allow-listed markers.
private enum V3DirectMutationRecoveryPhase: String {
    case prepared, dispatched, terminal, unknown
}

private enum V3DirectMutationRecoveryHash {
    static func digest(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

private struct V3DirectMutationRecoveryRecord {
    let requestID: String
    let operation: String
    let phase: V3DirectMutationRecoveryPhase
    let serviceInstanceID: String
    let targetDigest: String?
    let teamDigest: String?
    let identityStampDigest: String?
    let settingsKey: String?
    let settingsType: String?
    let settingsBool: Bool?
    let settingsInt: Int?
    // Accepted for records written by earlier v3.0.3 candidates. New writes
    // omit this field, but retaining it prevents an upgrade from making a
    // valid unresolved request look corrupt.
    let settingsValueDigest: String?
    let terminalOutcome: String?

    // Scope classification: certSetActive/certDelete are local desired-state
    // writes with certList/snapshot readback; signOut has checked keychain and
    // identity-snapshot postconditions; syncAppIDs/refreshSources have callback
    // completion plus developer/source-list readback; clearCache is repeatable;
    // JIT is repeatable enable but still needs device-side verification. SideSign
    // and Anisette replace/reset operations expose getters for readback. These
    // are not covered by this lease and must not be added without their own
    // reconciliation rule. accountImport has an identity-transition wrapper
    // but no idempotency key or durable postcondition, so it is included as a
    // manual-check-only operation. Long auth/op/refresh flows retain their
    // session-owned recovery paths.
    static let allowedOperations: Set<String> = [
        "certCreate", "certRevoke", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "settingsSet", "accountImport"
    ]

    static func isEligible(request: [String: Any]) -> Bool {
        guard let operation = request["operation"] as? String,
              allowedOperations.contains(operation) else { return false }
        guard operation == "settingsSet" else { return true }
        let payload = request["payload"] as? [String: Any] ?? [:]
        guard let key = payload["key"] as? String, let type = payload["type"] as? String else { return false }
        switch type {
        case "bool":
            return (V3BackendCommands.boolSettings.contains(key) || key == "widgetVerboseLogging") &&
                V3WireContract.strictBool(payload["bool"]) != nil
        case "int":
            return V3BackendCommands.intSettings.contains(key) && V3WireContract.strictInt(payload["int"]) != nil
        case "string":
            return V3BackendCommands.stringSettings.contains(key) && payload["string"] is String
        default: return false
        }
    }

    init?(requestID: String, operation: String, phase: V3DirectMutationRecoveryPhase,
          serviceInstanceID: String, targetDigest: String? = nil, teamDigest: String? = nil,
          identityStampDigest: String? = nil,
          settingsKey: String? = nil,
          settingsType: String? = nil, settingsBool: Bool? = nil, settingsInt: Int? = nil,
          settingsValueDigest: String? = nil,
          terminalOutcome: String? = nil) {
        guard UUID(uuidString: requestID)?.uuidString == requestID,
              Self.allowedOperations.contains(operation),
              UUID(uuidString: serviceInstanceID)?.uuidString == serviceInstanceID,
              targetDigest.map({ $0.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil }) ?? true,
              teamDigest.map({ $0.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil }) ?? true,
              identityStampDigest.map({ $0.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil }) ?? true,
              settingsKey.map({ !$0.isEmpty && $0.utf8.count <= 256 }) ?? true,
              settingsType.map({ ["bool", "int", "string"].contains($0) }) ?? true,
              settingsValueDigest.map({ $0.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil }) ?? true,
              terminalOutcome.map({ ["completed", "createdAndStored", "remoteCreatedLocalStorageUnverified"].contains($0) }) ?? true else {
            return nil
        }
        if operation == "settingsSet" {
            guard settingsKey != nil, settingsType != nil else { return nil }
            switch settingsType {
            case "bool": guard settingsBool != nil, settingsInt == nil, settingsValueDigest == nil else { return nil }
            case "int": guard settingsInt != nil, settingsBool == nil, settingsValueDigest == nil else { return nil }
            case "string": guard settingsBool == nil, settingsInt == nil else { return nil }
            default: return nil
            }
        } else if settingsKey != nil || settingsType != nil || settingsBool != nil ||
                    settingsInt != nil || settingsValueDigest != nil {
            return nil
        }
        if operation != "certRevoke", (teamDigest != nil || identityStampDigest != nil) { return nil }
        if phase == .terminal {
            guard terminalOutcome != nil else { return nil }
        } else if terminalOutcome != nil {
            return nil
        }
        self.requestID = requestID
        self.operation = operation
        self.phase = phase
        self.serviceInstanceID = serviceInstanceID
        self.targetDigest = targetDigest
        self.teamDigest = teamDigest
        self.identityStampDigest = identityStampDigest
        self.settingsKey = settingsKey
        self.settingsType = settingsType
        self.settingsBool = settingsBool
        self.settingsInt = settingsInt
        self.settingsValueDigest = settingsValueDigest
        self.terminalOutcome = terminalOutcome
    }

    var propertyListRepresentation: [String: Any] {
        var value: [String: Any] = ["version": 2, "recordType": "directMutation",
            "requestID": requestID, "operation": operation, "phase": phase.rawValue,
            "serviceInstanceID": serviceInstanceID]
        if let targetDigest { value["targetDigest"] = targetDigest }
        if let teamDigest { value["teamDigest"] = teamDigest }
        if let identityStampDigest { value["identityStampDigest"] = identityStampDigest }
        if let settingsKey { value["settingsKey"] = settingsKey }
        if let settingsType { value["settingsType"] = settingsType }
        if let settingsBool { value["settingsBool"] = settingsBool }
        if let settingsInt { value["settingsInt"] = settingsInt }
        if let settingsValueDigest { value["settingsValueDigest"] = settingsValueDigest }
        if let terminalOutcome { value["terminalOutcome"] = terminalOutcome }
        return value
    }

    static func decode(_ value: Any) -> V3DirectMutationRecoveryRecord? {
        guard let plist = value as? [String: Any],
              let version = plist["version"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(), version.intValue == 2,
              plist["recordType"] as? String == "directMutation",
              let requestID = plist["requestID"] as? String,
              let operation = plist["operation"] as? String,
              let phaseRaw = plist["phase"] as? String,
              let phase = V3DirectMutationRecoveryPhase(rawValue: phaseRaw),
              let serviceInstanceID = plist["serviceInstanceID"] as? String else { return nil }
        let allowed: Set<String> = ["version", "recordType", "requestID", "operation", "phase",
            "serviceInstanceID", "targetDigest", "settingsKey", "settingsType", "settingsBool",
            "settingsInt", "settingsValueDigest", "terminalOutcome", "teamDigest", "identityStampDigest"]
        guard Set(plist.keys).isSubset(of: allowed),
              (plist["targetDigest"] == nil || plist["targetDigest"] is String),
              (plist["teamDigest"] == nil || plist["teamDigest"] is String),
              (plist["identityStampDigest"] == nil || plist["identityStampDigest"] is String),
              (plist["settingsKey"] == nil || plist["settingsKey"] is String),
              (plist["settingsType"] == nil || plist["settingsType"] is String),
              (plist["settingsBool"] == nil || V3WireContract.strictBool(plist["settingsBool"]) != nil),
              (plist["settingsInt"] == nil || V3WireContract.strictInt(plist["settingsInt"]) != nil),
              (plist["settingsValueDigest"] == nil || plist["settingsValueDigest"] is String),
              (plist["terminalOutcome"] == nil || plist["terminalOutcome"] is String) else { return nil }
        return V3DirectMutationRecoveryRecord(requestID: requestID, operation: operation,
            phase: phase, serviceInstanceID: serviceInstanceID,
            targetDigest: plist["targetDigest"] as? String,
            teamDigest: plist["teamDigest"] as? String,
            identityStampDigest: plist["identityStampDigest"] as? String,
            settingsKey: plist["settingsKey"] as? String,
            settingsType: plist["settingsType"] as? String,
            settingsBool: V3WireContract.strictBool(plist["settingsBool"]),
            settingsInt: V3WireContract.strictInt(plist["settingsInt"]),
            settingsValueDigest: plist["settingsValueDigest"] as? String,
            terminalOutcome: plist["terminalOutcome"] as? String)
    }

    func replacing(phase: V3DirectMutationRecoveryPhase, serviceInstanceID: String? = nil,
                   terminalOutcome: String? = nil) -> V3DirectMutationRecoveryRecord? {
        V3DirectMutationRecoveryRecord(requestID: requestID, operation: operation, phase: phase,
            serviceInstanceID: serviceInstanceID ?? self.serviceInstanceID,
            targetDigest: targetDigest, teamDigest: teamDigest,
            identityStampDigest: identityStampDigest,
            settingsKey: settingsKey, settingsType: settingsType,
            settingsBool: settingsBool, settingsInt: settingsInt,
            settingsValueDigest: settingsValueDigest, terminalOutcome: terminalOutcome)
    }
}

private enum V3DirectMutationPreDispatchReplyPolicy {
    static func mayClaimInvalidRequestNotDispatched(operation: String, requestID: String?,
                                                     identifierCollision: Bool,
                                                     heldRequestID: String?,
                                                     journalReadable: Bool) -> Bool {
        if V3RequestReplayPolicy.mayClaimNotDispatched(operation: operation,
            identifierCollision: identifierCollision) { return true }
        return !identifierCollision && journalReadable &&
            V3DirectMutationRecoveryRecord.allowedOperations.contains(operation) &&
            requestID != nil && requestID != heldRequestID
    }

    // This helper is only used on receive() exits before beginDirectDispatch.
    // The held request ID guard prevents confusing a replay of the unresolved
    // original with a new request that was rejected by the recovery hold.
    static func annotate(request: [String: Any], heldRequestID: String? = nil,
                         response: inout [String: Any]) -> Bool {
        guard V3DirectMutationRecoveryRecord.isEligible(request: request),
              let requestID = request["id"] as? String,
              response["id"] as? String == requestID,
              requestID != heldRequestID else { return false }
        response["operationNotDispatched"] = true
        return true
    }
}

private enum V3ServiceRecoveryFileRecord {
    case operation(V3OperationRecoveryRecord)
    case directMutation(V3DirectMutationRecoveryRecord)
}

// Only safe OS domain/code pairs leave the service. No path, plist contents,
// account identifier, or arbitrary NSError text is retained in this error.
private struct V3RecoveryStorageFailure: Error {
    enum Kind: String {
        case malformedRecord, incompatibleRecord, storageUnavailable
        case lockUnavailable, readFailure, deleteFailure

        var retryable: Bool {
            switch self {
            case .malformedRecord, .incompatibleRecord: return false
            case .storageUnavailable, .lockUnavailable, .readFailure, .deleteFailure: return true
            }
        }

        var safeCause: CombinedFailure.SafeCause {
            switch self {
            case .malformedRecord: return .recoveryMalformedRecord
            case .incompatibleRecord: return .recoveryIncompatibleRecord
            case .storageUnavailable: return .recoveryStorageUnavailable
            case .lockUnavailable: return .recoveryLockUnavailable
            case .readFailure: return .recoveryReadFailure
            case .deleteFailure: return .recoveryDeleteFailure
            }
        }
    }

    let kind: Kind
    let underlyingDomain: String
    let underlyingCode: Int
    let recordPresent: Bool
    let deletionPossible: Bool
    let sourceStep: String

    init(_ kind: Kind, underlying: Error? = nil, recordPresent: Bool = false,
         deletionPossible: Bool = false, sourceStep: String = "unknown") {
        self.kind = kind
        let native = underlying as NSError?
        if let native, [NSCocoaErrorDomain, NSPOSIXErrorDomain].contains(native.domain) {
            underlyingDomain = native.domain
            underlyingCode = native.code
        } else {
            underlyingDomain = native == nil ? "none" : "redacted"
            underlyingCode = 0
        }
        self.recordPresent = recordPresent
        self.deletionPossible = deletionPossible
        self.sourceStep = sourceStep
    }

    var isMalformedOrIncompatible: Bool {
        kind == .malformedRecord || kind == .incompatibleRecord
    }

    var clearEligible: Bool { isMalformedOrIncompatible && recordPresent && deletionPossible }

    var snapshotValue: [String: Any] {
        ["kind": kind.rawValue, "recordPresent": recordPresent,
         "clearEligible": clearEligible, "underlyingDomain": underlyingDomain,
         "underlyingCode": underlyingCode, "retryable": kind.retryable,
         "sourceStep": sourceStep]
    }

    func combined(operation: String, id: String) -> CombinedFailure {
        let native: Error? = underlyingDomain == "none" || underlyingDomain == "redacted"
            ? nil : NSError(domain: underlyingDomain, code: underlyingCode)
        return CombinedFailure(operation: operation, stage: .persistence,
            code: kind == .storageUnavailable || kind == .lockUnavailable ? .unavailable : .failed,
            id: id, underlying: native, retryable: kind.retryable, safeCause: kind.safeCause)
    }
}

private enum V3OperationRecoveryJournal {
    private static let components = ["Library", "Application Support", "LiveContainer"]
    private static let fileName = "operation-recovery.plist"

    // SideStore's existing Bundle hook resolves to LCSharedUtils.appGroupID.
    // It follows the group validated at LiveProcess launch, including a
    // device-signer suffix or an AltStore group when that is the shared owner.
    private static func runtimeGroup() -> String? {
        #if canImport(Darwin)
        return Bundle.main.altstoreAppGroup
        #else
        return nil
        #endif
    }

    static func appGroupDiagnostic(selectedGroup: String?, inheritedGroup: String?,
                                   signedEntitled: Bool?,
                                   resolveContainer: (String) -> URL?) -> [String: Any] {
        let source: String
        if selectedGroup == nil || selectedGroup?.isEmpty == true { source = "none" }
        else if selectedGroup == inheritedGroup { source = "inherited" }
        else { source = "runtimeSelected" }
        let digest = selectedGroup.map {
            String(SHA256.hash(data: Data($0.utf8)).map { String(format: "%02x", $0) }
                .joined().prefix(12))
        } ?? "none"
        let available = selectedGroup.flatMap(resolveContainer) != nil
        return ["groupHash": digest, "selectionSource": source,
                "signedEntitled": signedEntitled.map { $0 ? "yes" : "no" } ?? "unknown",
                "containerResolves": available]
    }

    static func runtimeAppGroupDiagnostic() -> [String: Any] {
        let selected = runtimeGroup()
        // LiveProcess records these process-local facts before LC swaps its
        // UserDefaults implementation during embedded SideStore bootstrap.
        let inherited = ProcessInfo.processInfo.environment["LC_V3_INHERITED_APP_GROUP"]
        let entitlementValue = ProcessInfo.processInfo.environment["LC_V3_INHERITED_GROUP_ENTITLED"]
        let signed: Bool? = selected != nil && selected == inherited
            ? (entitlementValue == "1" ? true : entitlementValue == "0" ? false : nil) : nil
        return appGroupDiagnostic(selectedGroup: selected, inheritedGroup: inherited,
                                  signedEntitled: signed) {
            FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: $0)
        }
    }

    private static func recordURL(containerRoot container: URL) throws -> URL {
        let directory = components.reduce(container.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw V3RecoveryStorageFailure(.storageUnavailable, sourceStep: "directory")
            }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        } catch let failure as V3RecoveryStorageFailure { throw failure }
          catch { throw V3RecoveryStorageFailure(.storageUnavailable, underlying: error,
                                                sourceStep: "directory") }
        return directory.appendingPathComponent(fileName, isDirectory: false)
    }

    static func resolvedRoot(containerRoot: URL?,
                             selectedGroup: () -> String? = { runtimeGroup() },
                             resolveContainer: (String) -> URL? = {
                                 FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: $0)
                             }) throws -> URL {
        if let containerRoot { return containerRoot }
        // SideStore's identity hook returns LiveContainer's validated, inherited
        // runtime group. A fixed build-time group may not be entitled after the
        // combined app is signed for a device.
        guard let group = selectedGroup(), !group.isEmpty,
              let container = resolveContainer(group) else {
            throw V3RecoveryStorageFailure(.storageUnavailable, sourceStep: "appGroup")
        }
        return container
    }

    private static func withLease<T>(containerRoot: URL?, _ body: (URL) throws -> T) throws -> T {
        let root = try resolvedRoot(containerRoot: containerRoot)
        var acquired = false
        var failedStep = "unknown"
        var failedDomain = "none"
        var failedCode = 0
        do {
            return try V3AppGroupProcessLock.withLock(containerRoot: root,
                onFailure: { step, domain, code in
                    failedStep = step; failedDomain = domain; failedCode = code
                }) {
                acquired = true
                return try body(recordURL(containerRoot: root))
            }
        } catch let failure as V3RecoveryStorageFailure {
            throw failure
        } catch {
            let native: Error? = failedDomain == "none" || failedDomain == "redacted"
                ? nil : NSError(domain: failedDomain, code: failedCode)
            throw V3RecoveryStorageFailure(acquired || failedStep == "directory"
                                           ? .storageUnavailable : .lockUnavailable,
                                           underlying: native ?? error, sourceStep: failedStep)
        }
    }

    private static func isMissingFile(_ error: Error) -> Bool {
        let native = error as NSError
        return (native.domain == NSCocoaErrorDomain && [4, 260].contains(native.code)) ||
            (native.domain == NSPOSIXErrorDomain && native.code == ENOENT)
    }

    private static func canDelete(_ url: URL) -> Bool {
        FileManager.default.isWritableFile(atPath: url.deletingLastPathComponent().path)
    }

    private static func readState(_ url: URL) throws -> V3ServiceRecoveryFileRecord? {
        let values: URLResourceValues
        do {
            values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        } catch {
            if isMissingFile(error) { return nil }
            throw V3RecoveryStorageFailure(.readFailure, underlying: error, sourceStep: "metadata")
        }
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              url.resolvingSymlinksInPath().standardizedFileURL == url.standardizedFileURL else {
            throw V3RecoveryStorageFailure(.readFailure, recordPresent: true, sourceStep: "fileType")
        }
        let data: Data
        do { data = try Data(contentsOf: url) }
        catch {
            if isMissingFile(error) { return nil }
            throw V3RecoveryStorageFailure(.readFailure, underlying: error,
                                           recordPresent: true, sourceStep: "readData")
        }
        let deletionPossible = canDelete(url)
        guard !data.isEmpty, data.count <= 4096,
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil),
              let fields = plist as? [String: Any] else {
            throw V3RecoveryStorageFailure(.malformedRecord, recordPresent: true,
                                           deletionPossible: deletionPossible, sourceStep: "parse")
        }
        guard let version = fields["version"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID() else {
            throw V3RecoveryStorageFailure(.malformedRecord, recordPresent: true,
                                           deletionPossible: deletionPossible, sourceStep: "schema")
        }
        switch version.intValue {
        case 1:
            let allowed: Set<String> = ["version", "session", "kind", "phase", "ipa"]
            guard Set(fields.keys).isSubset(of: allowed) else {
                throw V3RecoveryStorageFailure(.incompatibleRecord, recordPresent: true,
                                               deletionPossible: deletionPossible)
            }
            guard let operation = V3OperationRecoveryRecord.decodePropertyList(fields) else {
                throw V3RecoveryStorageFailure(.malformedRecord, recordPresent: true,
                                               deletionPossible: deletionPossible)
            }
            return .operation(operation)
        case 2:
            let allowed: Set<String> = ["version", "recordType", "requestID", "operation", "phase",
                "serviceInstanceID", "targetDigest", "settingsKey", "settingsType", "settingsBool",
                "settingsInt", "settingsValueDigest", "terminalOutcome", "teamDigest", "identityStampDigest"]
            guard fields["recordType"] as? String == "directMutation",
                  Set(fields.keys).isSubset(of: allowed) else {
                throw V3RecoveryStorageFailure(.incompatibleRecord, recordPresent: true,
                                               deletionPossible: deletionPossible)
            }
            guard let direct = V3DirectMutationRecoveryRecord.decode(fields) else {
                throw V3RecoveryStorageFailure(.malformedRecord, recordPresent: true,
                                               deletionPossible: deletionPossible)
            }
            return .directMutation(direct)
        default:
            throw V3RecoveryStorageFailure(.incompatibleRecord, recordPresent: true,
                                           deletionPossible: deletionPossible)
        }
    }

    private static func read(_ url: URL) throws -> V3OperationRecoveryLease {
        switch try readState(url) {
        case nil: return V3OperationRecoveryLease()
        case .operation(let record): return V3OperationRecoveryLease(record: record)
        case .directMutation: throw V3RecoveryStorageFailure(.incompatibleRecord, recordPresent: true)
        }
    }

    private static func write(_ lease: V3OperationRecoveryLease, to url: URL) throws {
        guard let record = lease.record else {
            if try readState(url) != nil {
                do { try FileManager.default.removeItem(at: url) }
                catch {
                    if !isMissingFile(error) {
                        throw V3RecoveryStorageFailure(.deleteFailure, underlying: error,
                                                       recordPresent: true)
                    }
                }
            }
            return
        }
        do {
            try writePropertyList(record.propertyListRepresentation, to: url)
        } catch { throw V3RecoveryStorageFailure(.storageUnavailable, underlying: error) }
    }

    static func current(containerRoot: URL? = nil) throws -> V3OperationRecoveryRecord? {
        try withLease(containerRoot: containerRoot) { try read($0).record }
    }

    static func currentState(containerRoot: URL? = nil) throws -> V3ServiceRecoveryFileRecord? {
        try withLease(containerRoot: containerRoot) { try readState($0) }
    }

    @discardableResult
    static func discardUnreadableAfterDeviceCheck(userConfirmed: Bool,
                                                   containerRoot: URL? = nil,
                                                   deleteRecord: (URL) throws -> Void = {
                                                       try FileManager.default.removeItem(at: $0)
                                                   }) throws -> Bool {
        guard userConfirmed else { return false }
        return try withLease(containerRoot: containerRoot) { url in
            do {
                guard try readState(url) != nil else { return true }
                return false // A valid operation or direct mutation still owns it.
            } catch let failure as V3RecoveryStorageFailure {
                guard failure.isMalformedOrIncompatible else { throw failure }
                guard failure.clearEligible else {
                    throw V3RecoveryStorageFailure(.deleteFailure, recordPresent: true)
                }
                do { try deleteRecord(url) }
                catch {
                    if !isMissingFile(error) {
                        throw V3RecoveryStorageFailure(.deleteFailure, underlying: error,
                                                       recordPresent: true)
                    }
                }
                // Prove absence under the same process-shared lock before
                // releasing any host or service recovery ownership.
                guard try readState(url) == nil else {
                    throw V3RecoveryStorageFailure(.deleteFailure, recordPresent: true)
                }
                return true
            }
        }
    }

    static func reserve(sessionID: String, kind: String, stagedIPAToken: String? = nil,
                        containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            let result = lease.reserve(sessionID: sessionID, kind: kind, stagedIPAToken: stagedIPAToken)
            if result == .reserved { try write(lease, to: url) }
            return result != .blocked
        }
    }

    static func reserveDirect(request: [String: Any], requestID: String,
                              serviceInstanceID: String, teamIdentifier: String? = nil,
                              identityStamp: String? = nil,
                              containerRoot: URL? = nil) throws -> Bool {
        guard let operation = request["operation"] as? String,
              V3DirectMutationRecoveryRecord.allowedOperations.contains(operation) else { return false }
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        let targetDigest = target.isEmpty || ["pairingImportData", "accountImport"].contains(operation)
            ? nil : V3DirectMutationRecoveryHash.digest(target)
        let teamDigest = operation == "certRevoke"
            ? teamIdentifier.map(V3DirectMutationRecoveryHash.digest) : nil
        let identityStampDigest = operation == "certRevoke"
            ? identityStamp.map(V3DirectMutationRecoveryHash.digest) : nil
        let key = payload["key"] as? String
        let type = payload["type"] as? String
        let record = V3DirectMutationRecoveryRecord(requestID: requestID, operation: operation,
            phase: .prepared, serviceInstanceID: serviceInstanceID,
            targetDigest: targetDigest, teamDigest: teamDigest,
            identityStampDigest: identityStampDigest,
            settingsKey: key, settingsType: type,
            settingsBool: V3WireContract.strictBool(payload["bool"]),
            settingsInt: V3WireContract.strictInt(payload["int"]))
        guard let record else { return false }
        return try withLease(containerRoot: containerRoot) { url in
            guard case nil = try readState(url) else { return false }
            try writeDirect(record, to: url)
            return true
        }
    }

    static func beginDirectDispatch(requestID: String, serviceInstanceID: String,
                                   containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url),
                  current.requestID == requestID, current.phase == .prepared,
                  let dispatched = current.replacing(phase: .dispatched, serviceInstanceID: serviceInstanceID) else { return false }
            try writeDirect(dispatched, to: url)
            return true
        }
    }

    static func clearPreparedDirectAfterNotDispatched(requestID: String,
                                                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url),
                  current.requestID == requestID, current.phase == .prepared else { return false }
            try writeEmpty(to: url)
            return true
        }
    }

    static func settleDirect(requestID: String, terminalOutcome: String,
                             containerRoot: URL? = nil) throws -> Bool {
        guard ["completed", "createdAndStored", "remoteCreatedLocalStorageUnverified"].contains(terminalOutcome) else {
            return false
        }
        return try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url),
                  current.requestID == requestID,
                  (current.phase == .dispatched || current.phase == .unknown),
                  let terminal = current.replacing(phase: .terminal, terminalOutcome: terminalOutcome) else { return false }
            try writeDirect(terminal, to: url)
            return true
        }
    }

    static func direct(containerRoot: URL? = nil) throws -> V3DirectMutationRecoveryRecord? {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let record)? = try readState(url) else { return nil }
            return record
        }
    }

    static func markDirectUnknownIfOwnerLost(requestID: String, currentServiceInstanceID: String,
                                             containerRoot: URL? = nil) throws -> V3DirectMutationRecoveryRecord? {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url), current.requestID == requestID else { return nil }
            guard current.phase == .dispatched, current.serviceInstanceID != currentServiceInstanceID,
                  let unknown = current.replacing(phase: .unknown, serviceInstanceID: currentServiceInstanceID) else {
                return current
            }
            try writeDirect(unknown, to: url)
            return unknown
        }
    }

    static func markDirectUnknownAfterRunFailure(requestID: String, serviceInstanceID: String,
                                                 containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url),
                  current.requestID == requestID, current.phase == .dispatched,
                  current.serviceInstanceID == serviceInstanceID,
                  let unknown = current.replacing(phase: .unknown) else { return false }
            try writeDirect(unknown, to: url)
            return true
        }
    }

    static func reconcileDirect(requestID: String, allowUnknownDeviceCheck: Bool,
                                containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            guard case .directMutation(let current)? = try readState(url), current.requestID == requestID else { return false }
            guard current.phase == .terminal || current.phase == .prepared ||
                  (current.phase == .unknown && allowUnknownDeviceCheck) else { return false }
            try writeEmpty(to: url)
            return true
        }
    }

    private static func writeDirect(_ record: V3DirectMutationRecoveryRecord, to url: URL) throws {
        try writePropertyList(record.propertyListRepresentation, to: url)
    }

    private static func writeEmpty(to url: URL) throws {
        if FileManager.default.fileExists(atPath: url.path) {
            do { try FileManager.default.removeItem(at: url) }
            catch { throw V3SecretHandoffError.unavailable }
        }
    }

    private static func writePropertyList(_ value: [String: Any], to url: URL) throws {
        do {
            let data = try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
            guard data.count <= 4096 else { throw V3SecretHandoffError.unavailable }
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch { throw V3SecretHandoffError.unavailable }
    }

    static func beginDispatch(sessionID: String, kind: String, stagedIPAToken: String? = nil,
                              containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.beginDispatch(sessionID: sessionID, kind: kind, stagedIPAToken: stagedIPAToken) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func settle(sessionID: String, replySessionID: String?, state: String?, backendSettled: Bool,
                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.settle(sessionID: sessionID, replySessionID: replySessionID,
                state: state, backendSettled: backendSettled) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func settleRefreshAdmission(runID: String, terminalState: String?,
                                       terminalConfirmed: Bool,
                                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.settleRefreshAdmission(runID: runID, terminalState: terminalState,
                terminalConfirmed: terminalConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedRefreshAdmissionAfterRequestCancellation(runID: String,
                                                                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedRefreshAdmissionAfterRequestCancellation(runID: runID) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func reconcileRefreshAdmissionAfterDeviceCheck(runID: String, userConfirmed: Bool,
                                                          containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.reconcileRefreshAdmissionAfterDeviceCheck(runID: runID,
                userConfirmed: userConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func reconcileAfterDeviceCheck(sessionID: String, userConfirmed: Bool,
                                          containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.reconcileAfterDeviceCheck(sessionID: sessionID, userConfirmed: userConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedAfterNotDispatched(sessionID: String, expectedRequestID: String,
                                                 replyRequestID: String?, operationNotDispatched: Bool,
                                                 containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedAfterNotDispatched(sessionID: sessionID,
                expectedRequestID: expectedRequestID, replyRequestID: replyRequestID,
                operationNotDispatched: operationNotDispatched) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedAfterConfirmedCancellation(sessionID: String, replySessionID: String?,
        state: String?, backendSettled: Bool, stopConfirmed: Bool, knownStarted: Bool,
        containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedAfterConfirmedCancellation(sessionID: sessionID,
                replySessionID: replySessionID, state: state, backendSettled: backendSettled,
                stopConfirmed: stopConfirmed, knownStarted: knownStarted) else { return false }
            try write(lease, to: url)
            return true
        }
    }
}

/// Keeps the dispatched write, terminal journal, and post-run cancellation
/// check in one ordered path. A thrown operation intentionally leaves the
/// dispatched record for service-restart reconciliation.
private enum V3DirectMutationRecoveryLifecycle {
    static func reserve(request: [String: Any], requestID: String,
                        serviceInstanceID: String, teamIdentifier: String?,
                        identityStamp: String?, containerRoot: URL? = nil) throws -> Bool {
        try V3OperationRecoveryJournal.reserveDirect(request: request, requestID: requestID,
            serviceInstanceID: serviceInstanceID, teamIdentifier: teamIdentifier,
            identityStamp: identityStamp, containerRoot: containerRoot)
    }

    @MainActor
    static func dispatchAndSettle(requestID: String, operation: String,
                                  serviceInstanceID: String,
                                  containerRoot: URL? = nil,
                                  run: @MainActor () async throws -> [String: Any]) async throws -> [String: Any]? {
        guard try V3OperationRecoveryJournal.beginDirectDispatch(requestID: requestID,
            serviceInstanceID: serviceInstanceID, containerRoot: containerRoot) else { return nil }
        do {
            let result = try await run()
            let terminalOutcome = operation == "certCreate"
                ? (result["outcome"] as? String ?? "") : "completed"
            guard try V3OperationRecoveryJournal.settleDirect(requestID: requestID,
                terminalOutcome: terminalOutcome, containerRoot: containerRoot) else { return nil }
            return result
        } catch {
            // The operation task is no longer running. Keep the ambiguity, but
            // make it explicitly reconcilable in this still-live service.
            _ = try? V3OperationRecoveryJournal.markDirectUnknownAfterRunFailure(
                requestID: requestID, serviceInstanceID: serviceInstanceID,
                containerRoot: containerRoot)
            throw error
        }
    }

    static func clearPreparedAfterFailure(requestID: String, containerRoot: URL? = nil) -> Bool {
        (try? V3OperationRecoveryJournal.clearPreparedDirectAfterNotDispatched(
            requestID: requestID, containerRoot: containerRoot)) == true
    }
}

// V3_NATIVE_CALLBACK_GATE_V1: native completions can arrive on arbitrary queues.
// Cancellation does not manufacture a native completion or release the mutation gate.
// The owning service retains it until the real callback returns or the process retires.
final class V3ServiceCallbackGate: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?
    init(_ continuation: CheckedContinuation<Void, Error>) { self.continuation = continuation }
    func settle(_ result: Result<Void, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}
// V3_NATIVE_CALLBACK_GATE_END

private struct V3KnownSourcePolicyFailure: Error {
    enum Kind: Equatable { case network, invalidResponse }
    let kind: Kind
    let underlyingDomain: String
    let underlyingCode: Int

    // Cancellation belongs to the request lifecycle. Let the service-level
    // cancellation path see it instead of translating it into a list parse error.
    static func preservingCancellation(_ error: Error) -> Error {
        if error is CancellationError { return error }
        let cause = error as NSError
        if CombinedFailure.isURLCancellation(domain: cause.domain, code: cause.code) { return error }
        return V3KnownSourcePolicyFailure(error)
    }

    init(_ error: Error) {
        let cause = error as NSError
        // URL-loading errors include local temporary-file I/O. The shared
        // domain-and-code policy separates those from typed transport failures.
        kind = CombinedFailure.knownURLTransportCause(domain: cause.domain, code: cause.code) != nil
            ? .network : .invalidResponse
        underlyingDomain = [NSURLErrorDomain, NSPOSIXErrorDomain,
                            "kCFErrorDomainCFNetwork", "NSCocoaErrorDomain"]
            .contains(cause.domain) ? cause.domain : "redacted"
        underlyingCode = cause.code
    }
}

@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    var tasks: [String: Task<Void, Never>] = [:]
    private var deadlineTasks: [String: Task<Void, Never>] = [:]
    var cancellations: [String: () -> Void] = [:]
    var completed: [String: (data: Data, deadline: Date)] = [:]
    private var completedRequestFingerprints: [String: Data] = [:]
    private var inFlightRequestFingerprints: [String: Data] = [:]
    private var completedCacheBudget = V3MutationReplyCacheBudget()
    private var pendingCancellationReplyReservations: Set<String> = []
    var mutationID: String?
    private var refreshAdmission = V3RefreshAdmissionLease()
    private var pendingRefreshAdmissionRequests: Set<String> = []
    private var pendingAuthStartSessions: [String: String] = [:]
    private var knownSourcesUpdateTask: Task<Void, Error>?
    private let recoveryServiceInstanceID = UUID().uuidString

    @objc(execute:reply:)
    nonisolated static func execute(_ data: Data, reply: @escaping (Data) -> Void) {
        Task { @MainActor in shared.receive(data, reply: reply) }
    }

    private func receive(_ data: Data, reply: @escaping (Data) -> Void) {
        // V3_CORRELATED_INVALID_REQUEST_V1: a request that fails the strict
        // contract is still answered with its own correlation and operation
        // whenever a well-formed envelope can be read, so the host can
        // classify the real reason instead of receiving an idless token it must
        // treat as a stale reply.
        guard let request = V3WireContract.decodeRequest(data) else {
            reply(encode(invalidRequestReply(for: data)))
            return
        }
        guard let id = request["id"] as? String,
              let operation = request["operation"] as? String,
              let deadline = request["deadline"] as? Date,
              deadline > Date(), deadline.timeIntervalSinceNow <= 610 else {
            reply(encode(invalidRequestReply(for: data)))
            return
        }
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        let operationSessionID = V3OperationSessionCorrelationPolicy.requestSessionID(
            operation: operation, target: target, payload: payload)
        let requestFingerprint = V3RequestReplayPolicy.fingerprint(data)
        let expiredReplies = completed.compactMap { key, value in
            value.deadline <= Date() ? (key, value.data.count) : nil
        }
        for (key, byteCount) in expiredReplies {
            completed.removeValue(forKey: key)
            completedRequestFingerprints.removeValue(forKey: key)
            completedCacheBudget.remove(byteCount)
        }
        _ = refreshAdmission.expire()
        if let previous = completed[id] {
            guard V3RequestReplayPolicy.matches(
                cachedFingerprint: completedRequestFingerprints[id], incomingRequestData: data) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
            reply(previous.data)
            return
        }
        // Bind IDs while work is still running as well as after completion.
        // This check must precede the cancellation fast path: a different
        // command reusing an active mutation ID must not cancel unrelated work.
        if tasks[id] != nil {
            guard V3RequestReplayPolicy.matchesInFlight(
                cachedFingerprint: inFlightRequestFingerprints[id], incomingRequestData: data) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
        }
        if operation == "cancel" {
            let target = request["target"] as? String ?? ""
            guard let cancelScope = (request["payload"] as? [String: Any])?["scope"] as? String,
                  V3WireContract.cancellationScopes.contains(cancelScope) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
            guard reserveCancellationReply(operation: operation, id: id) else {
                let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                    id: id, retryable: true, safeCause: .responseCapacityUnavailable)
                reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                    operation: operation))
                return
            }
            let isPendingRefreshAdmission = cancelScope == "request" &&
                (pendingRefreshAdmissionRequests.contains(target) || refreshAdmission.requestID == target)
            if cancelScope == "request", let session = pendingAuthStartSessions[target] {
                _ = V3HeadlessRuntime.shared.auth.cancelBeforeBegin(id: session)
            }
            if !V3HeadlessRuntime.shared.cancelSession(target, scope: cancelScope) {
                tasks[target]?.cancel()
                cancellations[target]?()
            }
            var cancellationReply: [String: Any] = ["id": id, "version": 1, "ok": true]
            if isPendingRefreshAdmission {
                let refreshRunID = refreshAdmission.runID
                if refreshAdmission.release(requestID: target), let refreshRunID,
                   (try? V3OperationRecoveryJournal.clearPreparedRefreshAdmissionAfterRequestCancellation(
                    runID: refreshRunID)) == true {
                    cancellationReply["refreshAdmissionReleased"] = true
                }
            }
            let encoded = encode(cancellationReply, operation: operation)
            _ = finishCancellationReplyReservation(id: id, requestFingerprint: requestFingerprint,
                deadline: deadline, encoded: encoded)
            reply(encoded)
            return
        }
        guard tasks[id] == nil else {
            let failure = operation == "sourceRemoveConfirmed"
                ? CombinedFailure(operation: "source", stage: .source, code: .busy, id: id,
                                  retryable: true, safeCause: .sourceRemoveBusy)
                : operation == "opStart"
                ? CombinedFailure(operation: operation, stage: .command, code: .busy, id: id,
                                  retryable: true, safeCause: .operationInProgress)
                : CombinedFailure(operation: operation, stage: .command, code: .busy, id: id,
                                  retryable: true, safeCause: .operationInProgress)
            var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
            reply(encode(response, operation: operation))
            return
        }
        let mutation = !V3WireContract.readOperations.contains(operation)
        let authenticationActive = V3HeadlessRuntime.shared.auth.hasActiveSession
        let authContinuation = V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            operation,
            ownsActiveSession: V3HeadlessRuntime.shared.auth.ownsActiveSession(target),
            authenticationActive: authenticationActive)
        let recoveryRecord: V3OperationRecoveryRecord?
        var directRecoveryRecord: V3DirectMutationRecoveryRecord?
        let recoveryReadFailed: Bool
        let recoveryStorageFailure: V3RecoveryStorageFailure?
        do {
            switch try V3OperationRecoveryJournal.currentState() {
            case .operation(let value): recoveryRecord = value
            case .directMutation(let value):
                directRecoveryRecord = try V3OperationRecoveryJournal.markDirectUnknownIfOwnerLost(
                    requestID: value.requestID, currentServiceInstanceID: recoveryServiceInstanceID) ?? value
                recoveryRecord = nil
            case nil: recoveryRecord = nil
            }
            recoveryReadFailed = false
            recoveryStorageFailure = nil
        } catch let failure as V3RecoveryStorageFailure {
            recoveryRecord = nil; directRecoveryRecord = nil; recoveryReadFailed = true
            recoveryStorageFailure = failure
        } catch {
            recoveryRecord = nil; directRecoveryRecord = nil; recoveryReadFailed = true
            recoveryStorageFailure = V3RecoveryStorageFailure(.readFailure, underlying: error)
        }
        if mutation, let failure = recoveryStorageFailure, !failure.clearEligible {
            let refusal = operation == "recoveryDiscardUnreadable" && failure.isMalformedOrIncompatible
                ? V3RecoveryStorageFailure(.deleteFailure, recordPresent: true) : failure
            var response: [String: Any] = ["version": 1, "id": id, "error": "failed",
                "failure": refusal.combined(operation: operation, id: id).wire]
            if ["opStart", "authBegin", "authRetryProvisioning"].contains(operation) {
                response["operationNotDispatched"] = true
            }
            _ = V3DirectMutationPreDispatchReplyPolicy.annotate(request: request, response: &response)
            reply(encode(response, operation: operation))
            return
        }
        let directRecoveryControl = directRecoveryRecord?.requestID == target &&
            ["directRecoveryInspect", "directRecoveryReconcile"].contains(operation)
        if ["directRecoveryInspect", "directRecoveryReconcile"].contains(operation),
           !directRecoveryControl {
            reply(encode(["version": 1, "id": id, "error": "invalidRequest",
                "failure": CombinedFailure(operation: operation, stage: .command,
                    code: .invalidConfiguration, id: id).wire], operation: operation))
            return
        }
        if operation == "directRecoveryReconcile", let directRecoveryRecord,
           directRecoveryRecord.phase == .dispatched {
            reply(encode(["version": 1, "id": id, "error": "busy",
                "failure": CombinedFailure(operation: operation, stage: .command,
                    code: .busy, id: id, retryable: false, safeCause: .operationInProgress).wire], operation: operation))
            return
        }
        if let recoveryRecord, recoveryRecord.kind == "refreshAll",
           !refreshAdmission.owns(recoveryRecord.sessionID) {
            _ = refreshAdmission.restoreLost(runID: recoveryRecord.sessionID)
        }
        let recoveryDecision = V3ServiceRecoveryAdmissionPolicy.decide(operation: operation,
            target: target, payload: payload, operationSessionID: operationSessionID,
            recovery: recoveryRecord, recoveryReadFailed: recoveryReadFailed,
            recoveryDiscardable: recoveryStorageFailure?.clearEligible == true,
            refreshOwnerLost: refreshAdmission.ownerLost)
        let policyOperationMutationActive = V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: operation, target: target,
            activeOperationID: V3HeadlessRuntime.shared.operations.activeMutationID) ||
            recoveryDecision.blocksMutation
        let operationMutationActive = (operation == "opRecoveryReconcile" && recoveryDecision.recoveryControl) ||
            directRecoveryControl ? false : policyOperationMutationActive
        if mutation, directRecoveryRecord != nil, !directRecoveryControl {
            let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                id: id, retryable: false, safeCause: .operationInProgress)
            var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
            _ = V3DirectMutationPreDispatchReplyPolicy.annotate(request: request,
                heldRequestID: directRecoveryRecord?.requestID, response: &response)
            reply(encode(response, operation: operation))
            return
        }
        let refreshRelease = recoveryDecision.refreshRelease
        let controlReply = V3MutationReplyCacheBudget.isControlReply(operation: operation)
        let cacheResponse = V3MutationReplyCacheBudget.shouldCacheResponse(operation: operation)
        let cancellationReplay = V3RequestReplayPolicy.requiresCompletedReply(operation: operation)
        let responseCapacityAvailable = !cacheResponse || (!mutation && !cancellationReplay) ||
            (cancellationReplay
                ? canReserveCancellationReply(operation: operation)
                : completed.count + pendingCancellationReplyReservations.count <
                    V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply) &&
             completedCacheBudget.canReserve(
                maximumResponseBytes: V3MutationReplyCacheBudget.minimumReplyBytesToAdmit(operation: operation),
                preservingControlCapacity: !controlReply) &&
             V3MutationReplyCacheBudget.canAdmit(operation: operation,
                completedReplyCount: completed.count + pendingCancellationReplyReservations.count))
        if cancellationReplay && !responseCapacityAvailable {
            let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                id: id, retryable: true, safeCause: .responseCapacityUnavailable)
            reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                operation: operation))
            return
        }
        guard V3ServiceMutationAdmissionPolicy.admits(isMutation: mutation,
            anotherMutationActive: mutationID != nil || operationMutationActive,
            authenticationActive: authenticationActive,
            isAuthContinuation: authContinuation,
            responseCapacityAvailable: responseCapacityAvailable,
            refreshActive: refreshAdmission.isActive,
            isRefreshRelease: refreshRelease) else {
            let safeCause = V3ServiceMutationBusyCausePolicy.safeCause(
                operation: operation,
                anotherMutationActive: mutationID != nil || operationMutationActive,
                responseCapacityAvailable: responseCapacityAvailable,
                refreshActive: refreshAdmission.isActive, refreshRelease: refreshRelease,
                authenticationActive: authenticationActive,
                isAuthContinuation: authContinuation)
            let sourceRemoval = safeCause == .sourceRemoveBusy
            let failure = CombinedFailure(
                operation: sourceRemoval ? "source" : operation.hasPrefix("refreshAdmission") ? "refresh" : operation,
                stage: sourceRemoval ? .source : .command,
                code: .busy, id: id, retryable: true, safeCause: safeCause)
            var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
            if ["opStart", "authBegin", "authRetryProvisioning"].contains(operation) {
                response["operationNotDispatched"] = true
            }
            _ = V3DirectMutationPreDispatchReplyPolicy.annotate(request: request,
                heldRequestID: directRecoveryRecord?.requestID, response: &response)
            clearPreparedOperationRecoveryIfProven(request: request, reply: response)
            reply(encode(response, operation: operation))
            return
        }
        if cancellationReplay, !reserveCancellationReply(operation: operation, id: id) {
            let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                id: id, retryable: true, safeCause: .responseCapacityUnavailable)
            reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                operation: operation))
            return
        }
        if V3DirectMutationRecoveryRecord.isEligible(request: request) {
            do {
                guard try V3DirectMutationRecoveryLifecycle.reserve(request: request, requestID: id,
                    serviceInstanceID: recoveryServiceInstanceID,
                    teamIdentifier: DatabaseManager.shared.activeTeam()?.identifier,
                    identityStamp: AuthManager.shared.v3IdentityIsStable
                        ? AuthManager.shared.v3IdentityStamp : nil) else {
                    throw ServiceError.busy
                }
            } catch {
                let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                    id: id, retryable: false, safeCause: .operationInProgress)
                var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
                let heldRequestID = (try? V3OperationRecoveryJournal.direct())?.requestID
                _ = V3DirectMutationPreDispatchReplyPolicy.annotate(request: request,
                    heldRequestID: heldRequestID, response: &response)
                reply(encode(response, operation: operation))
                return
            }
        }
        if mutation { mutationID = id }
        if operation == "refreshAdmissionBegin" { pendingRefreshAdmissionRequests.insert(id) }
        if ["authBegin", "authRetryProvisioning"].contains(operation),
           let session = (request["payload"] as? [String: Any])?["session"] as? String {
            pendingAuthStartSessions[id] = session
        }
        inFlightRequestFingerprints[id] = requestFingerprint
        tasks[id] = Task { @MainActor in
            defer {
                tasks[id] = nil
                inFlightRequestFingerprints.removeValue(forKey: id)
                deadlineTasks.removeValue(forKey: id)?.cancel()
                cancellations[id] = nil
                pendingRefreshAdmissionRequests.remove(id)
                pendingAuthStartSessions[id] = nil
                if mutationID == id { mutationID = nil }
            }
            var response: [String: Any] = ["version": 1, "id": id]
            do {
                guard DatabaseManager.shared.isStarted else { throw ServiceError.notReady }
                try Task.checkCancellation()
                if V3DirectMutationRecoveryRecord.isEligible(request: request) {
                    guard let result = try await V3DirectMutationRecoveryLifecycle.dispatchAndSettle(
                        requestID: id, operation: operation,
                        serviceInstanceID: recoveryServiceInstanceID, run: {
                            try await run(operation, request: request, id: id)
                        }) else { throw ServiceError.busy }
                    response["result"] = result
                } else {
                    response["result"] = try await run(operation, request: request, id: id)
                }
                try Task.checkCancellation()
                response["ok"] = true
            } catch {
                let directNotDispatched = V3DirectMutationRecoveryRecord.isEligible(request: request) &&
                    V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(requestID: id)
                if operation == "refreshAdmissionBegin", error is CancellationError,
                   let refreshRunID = request["target"] as? String,
                   refreshAdmission.release(runID: refreshRunID) {
                    _ = try? V3OperationRecoveryJournal.clearPreparedRefreshAdmissionAfterRequestCancellation(
                        runID: refreshRunID)
                }
                // Raw framework errors can contain URLs, authentication data or server responses.
                // Detailed errors remain inside the SideStore process.
                if let serviceError = error as? ServiceError { response["error"] = serviceError.rawValue }
                else if let headlessError = error as? V3SideStoreServiceError { response["error"] = headlessError.rawValue }
                else if error is CancellationError { response["error"] = "cancelled" }
                else { response["error"] = "operationFailed" }
                if operation == "opStart",
                   V3OperationStartDispatchPolicy.provesNotDispatched(
                    resultWasReturned: response["result"] != nil) {
                    response["operationNotDispatched"] = true
                } else if ["authBegin", "authRetryProvisioning"].contains(operation),
                          response["result"] == nil,
                          (error is ServiceError || error is V3SideStoreServiceError) {
                    response["operationNotDispatched"] = true
                }
                if directNotDispatched { response["operationNotDispatched"] = true }
                var stage: CombinedFailure.Stage
                switch operation {
                case "snapshot": stage = .serviceReadiness
                case "catalog": stage = .catalog
                case "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning", "accountExport", "accountImport": stage = .authentication
                case "opStart", "opPoll", "opAnswer", "opCancel": stage = .command
                case "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate": stage = .signing
                case "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles", "syncAppIDs": stage = .authentication
                case "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed", "refreshSources": stage = .source
                default: stage = .command
                }
                var readinessNotReady = false
                if let serviceError = error as? ServiceError, case .notReady = serviceError {
                    stage = .serviceReadiness
                    readinessNotReady = true
                }
                if let serviceError = error as? V3SideStoreServiceError, case .notReady = serviceError {
                    stage = .serviceReadiness
                    readinessNotReady = true
                }
                if operation.hasPrefix("refreshAdmission") { stage = .serviceReadiness }
                if operation == "sourceRemoveConfirmed" {
                    if let serviceError = error as? ServiceError, case .notReady = serviceError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .serviceReadiness,
                            code: .notReady, id: id, retryable: true).wire
                    } else if let headlessError = error as? V3SideStoreServiceError,
                              case .notReady = headlessError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .serviceReadiness,
                            code: .notReady, id: id, retryable: true).wire
                    } else if let serviceError = error as? ServiceError, case .busy = serviceError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .busy, id: id, retryable: true, safeCause: .sourceRemoveBusy).wire
                    } else if let headlessError = error as? V3SideStoreServiceError, case .busy = headlessError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .busy, id: id, retryable: true, safeCause: .sourceRemoveBusy).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .failed,
                            id: id, underlying: error, safeCause: .sourceRemoveFailed,
                            sourceStep: .catalogRead).wire
                    }
                } else if let serviceError = error as? ServiceError {
                    let code: CombinedFailure.Code
                    switch serviceError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    }
                    if ["authBegin", "authRetryProvisioning"].contains(operation) && code == .busy {
                        response["failure"] = CombinedFailure(operation: "signIn", stage: .serviceReadiness,
                            code: .busy, id: id, retryable: true, safeCause: .operationInProgress).wire
                    } else if operation.hasPrefix("refreshAdmission") {
                        response["failure"] = CombinedFailure(operation: "refresh",
                            stage: .command, code: code, id: id,
                            retryable: code == .busy,
                            safeCause: code == .busy ? .operationInProgress : nil).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: operation, stage: stage,
                            code: code, id: id,
                            retryable: V3ServiceReadinessRetryPolicy.retryable(
                                operation: operation, stage: stage, code: code,
                                typedNotReady: readinessNotReady)).wire
                    }
                } else if let headlessError = error as? V3SideStoreServiceError {
                    let code: CombinedFailure.Code
                    switch headlessError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    case .authRequired: code = .notReady
                    case .persistenceUnverified: code = .failed
                    // V3_CATALOG_SOURCE_MISSING_V1: a typed, non-manifest cause.
                    case .catalogSourceUnavailable: code = .unavailable
                    }
                    if headlessError == .catalogSourceUnavailable {
                        response["failure"] = CombinedFailure(operation: "catalog", stage: .catalog,
                            code: code, id: id, safeCause: .catalogSourceUnavailable,
                            sourceStep: .catalogRead).wire
                    } else if headlessError == .invalidRequest && ["sourcePreview", "sourceAddConfirmed"].contains(operation) {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .invalidConfiguration, id: id, safeCause: .sourceInvalidURL,
                            sourceStep: .sourceDownload).wire
                    } else if headlessError == .persistenceUnverified && operation == "sourceAddConfirmed" {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: code,
                            id: id, safeCause: .sourcePersistenceUnverified, sourceStep: .catalogRead).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: operation, stage: stage, code: code, id: id,
                            retryable: V3ServiceReadinessRetryPolicy.retryable(
                                operation: operation, stage: stage, code: code,
                                typedNotReady: readinessNotReady)).wire
                    }
                } else if let policyError = error as? V3KnownSourcePolicyFailure {
                    let network = policyError.kind == .network
                    response["failure"] = CombinedFailure(operation: "source", stage: .source,
                        code: network ? .failed : .invalidResponse, id: id,
                        underlying: NSError(domain: policyError.underlyingDomain, code: policyError.underlyingCode),
                        retryable: network ? true : nil,
                        safeCause: network ? .knownSourcePolicyNetworkFailure : .knownSourcePolicyInvalidResponse,
                        sourceStep: network ? .knownSourcePolicyFetch : .knownSourcePolicyParsing).wire
                } else if let sourceError = error as? V3SourceCommandError {
                    switch sourceError.kind {
                    case .network:
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .failed,
                            id: id, underlying: NSError(domain: sourceError.domain, code: sourceError.code),
                            retryable: true, safeCause: sourceError.safeCause,
                            sourceStep: sourceError.sourceStep).wire
                    case .invalidManifest:
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .invalidResponse,
                            id: id, underlying: NSError(domain: sourceError.domain, code: sourceError.code),
                            retryable: false, safeCause: sourceError.safeCause,
                            sourceStep: sourceError.sourceStep).wire
                    case .validation:
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .invalidResponse, id: id,
                            underlying: NSError(domain: sourceError.domain, code: sourceError.code),
                            retryable: false, safeCause: sourceError.safeCause,
                            sourceStep: sourceError.sourceStep).wire
                    }
                } else if operation == "catalog" {
                    response["failure"] = CombinedFailure(operation: "catalog", stage: .catalog, code: .failed,
                        id: id, underlying: error, safeCause: .catalogUnavailable, sourceStep: .catalogRead).wire
                } else if let structuredFailure = error as? CombinedFailure {
                    // V3_WIRE_FAILURE_CORRELATION_V1: preserve the typed cause
                    // but correlate the reply to this XPC request, not to the
                    // auth/operation session that caused the failure.
                    response["failure"] = structuredFailure.correlating(to: id).wire
                } else if operation == "anisetteSync" {
                    // V3_ANISETTE_SYNC_FAILURE_V1: preserve Anisette operation
                    // identity and classify only typed transport/HTTP evidence.
                    response["failure"] = V3AnisetteSyncFailurePolicy.failure(error, id: id).wire
                } else {
                    response["failure"] = CombinedFailure.capture(
                        V3HeadlessPairingFailure.tagIfInvalidPairing(error),
                        operation: operation, stage: stage, id: id).wire
                }
                clearPreparedOperationRecoveryIfProven(request: request, reply: response)
            }
            let encoded = encode(response, operation: operation)
            if mutation && cacheResponse || cancellationReplay && cacheResponse {
                let cached: Bool
                if cancellationReplay {
                    cached = finishCancellationReplyReservation(id: id,
                        requestFingerprint: requestFingerprint, deadline: deadline, encoded: encoded)
                } else if completedCacheBudget.record(encoded.count, controlResponse: controlReply) {
                    completed[id] = (encoded, deadline)
                    completedRequestFingerprints[id] = requestFingerprint
                    cached = true
                } else {
                    cached = false
                }
                if !cached {
                    // Admission reserves one full maximum-size response before
                    // dispatch. Reaching this branch means cache accounting
                    // lost a reservation while the command was running.
                    debugLog("[V3_WIRE] mutation_reply_cache_reservation_failed operation=\(operation)")
                }
            }
            reply(encoded)
        }
        deadlineTasks[id] = Task { @MainActor in
            do {
                try await Task.sleep(nanoseconds: UInt64(max(0, deadline.timeIntervalSinceNow) * 1_000_000_000))
            } catch { return }
            guard self.tasks[id] != nil else {
                self.deadlineTasks[id] = nil
                return
            }
            self.deadlineTasks[id] = nil
            if let session = self.pendingAuthStartSessions[id] {
                _ = V3HeadlessRuntime.shared.auth.cancelBeforeBegin(id: session)
            }
            self.tasks[id]?.cancel()
            self.cancellations[id]?()
        }
    }

    enum ServiceError: String, Error { case notReady, invalidRequest, notFound, unsupported, busy }

    // Reads only the envelope fields the contract already trusts: the request ID
    // must be a valid UUID and the operation must be on the allow list. Nothing
    // from the payload is echoed back.
    private func invalidRequestReply(for data: Data) -> [String: Any] {
        let identity = V3WireContract.invalidRequestIdentity(from: data)
        let id = identity.id ?? UUID().uuidString
        let operation = identity.operation ?? "command"
        let identifierCollision = identity.id.map {
            V3RequestReplayPolicy.isIdentifierCollision(
                cachedFingerprint: inFlightRequestFingerprints[$0] ?? completedRequestFingerprints[$0],
                incomingRequestData: data)
        } ?? false
        var response: [String: Any] = ["version": 1, "id": id, "error": "invalidRequest",
                "failure": CombinedFailure(operation: operation, stage: .command,
                    code: .invalidConfiguration, id: id).wire]
        let directRequest = V3DirectMutationRecoveryRecord.allowedOperations.contains(operation)
        var directJournalReadable = true
        let heldDirectRequestID: String?
        if directRequest {
            do { heldDirectRequestID = try V3OperationRecoveryJournal.direct()?.requestID }
            catch { heldDirectRequestID = nil; directJournalReadable = false }
        } else {
            heldDirectRequestID = nil
        }
        if V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(
            operation: operation, requestID: identity.id,
            identifierCollision: identifierCollision, heldRequestID: heldDirectRequestID,
            journalReadable: directJournalReadable) {
            response["operationNotDispatched"] = true
        }
        return response
    }

    private func canReserveCancellationReply(operation: String) -> Bool {
        guard V3RequestReplayPolicy.requiresCompletedReply(operation: operation) else { return false }
        let controlReply = V3MutationReplyCacheBudget.isControlReply(operation: operation)
        return completed.count + pendingCancellationReplyReservations.count <
                V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply) &&
            completedCacheBudget.canReserve(
                maximumResponseBytes: V3WireContract.responseLimit,
                preservingControlCapacity: !controlReply) &&
            V3MutationReplyCacheBudget.canAdmit(operation: operation, completedReplyCount: completed.count)
    }

    private func reserveCancellationReply(operation: String, id: String) -> Bool {
        guard !pendingCancellationReplyReservations.contains(id),
              canReserveCancellationReply(operation: operation),
              completedCacheBudget.record(V3WireContract.responseLimit, controlResponse: true) else { return false }
        pendingCancellationReplyReservations.insert(id)
        return true
    }

    private func finishCancellationReplyReservation(id: String, requestFingerprint: Data,
                                                     deadline: Date, encoded: Data) -> Bool {
        guard pendingCancellationReplyReservations.remove(id) != nil else { return false }
        completedCacheBudget.remove(V3WireContract.responseLimit)
        guard completedCacheBudget.record(encoded.count, controlResponse: true) else { return false }
        completed[id] = (encoded, deadline)
        completedRequestFingerprints[id] = requestFingerprint
        return true
    }

    // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the encoder and its typed fallback
    // live in the shared wire contract so the host's classifier and the
    // service's encoder can be executed together against real bytes. The
    // classification travels in the structured envelope's safeCause, not only in
    // the legacy "error" token: the host prefers the structured failure and
    // throws it, so a token-only classification was discarded on arrival and
    // every encoding failure reached the user as a generic invalidResponse.
    private func encode(_ value: [String: Any], operation: String = "command") -> Data {
        let correlationID = value["id"] as? String ?? ""
        let encoded = V3ResponseEncoder.encodeDetailed(value, operation: operation,
            limit: V3WireContract.responseLimit)
        // A fallback reply is a defect and it must be visible. A serialization or
        // oversize regression is otherwise indistinguishable in the field from
        // the failure it causes, because the host reports a generic
        // invalidResponse either way. Only the classification and the
        // correlation are recorded; the value that could not be encoded, and
        // the raw error text, never are.
        if let token = encoded.fallbackToken {
            debugLog("[V3_ENCODE] FAIL operation=\(operation) request_id=\(correlationID) classification=\(token) correlated=\(correlationID.isEmpty ? "no" : "yes")")
        }
        return encoded.data
    }

    private func settleOperationRecoveryIfTerminal(_ reply: [String: Any], requestedSessionID: String) {
        let state = reply["state"] as? String
        let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        let backendSettled = !outcomeUnknown && V3WireContract.strictBool(reply["backendSettled"]) == true
        _ = try? V3OperationRecoveryJournal.settle(sessionID: requestedSessionID,
            replySessionID: reply["session"] as? String, state: state, backendSettled: backendSettled)
    }

    private func clearPreparedOperationRecoveryIfProven(request: [String: Any], reply: [String: Any]) {
        guard request["operation"] as? String == "opStart",
              let requestID = request["id"] as? String,
              reply["id"] as? String == requestID,
              V3WireContract.strictBool(reply["operationNotDispatched"]) == true,
              let payload = request["payload"] as? [String: Any],
              let sessionID = payload["session"] as? String else { return }
        _ = try? V3OperationRecoveryJournal.clearPreparedAfterNotDispatched(
            sessionID: sessionID, expectedRequestID: requestID,
            replyRequestID: reply["id"] as? String, operationNotDispatched: true)
    }

    private func run(_ operation: String, request: [String: Any], id: String) async throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        switch operation {
        case "snapshot":
            if V3WireContract.strictBool(payload["readinessOnly"]) == true {
                return ["ready": DatabaseManager.shared.isStarted]
            }
            return try snapshot()
        case "directRecoveryInspect":
            return try await directRecoveryInspection(requestID: target)
        case "directRecoveryReconcile":
            let acknowledgesTerminal = V3WireContract.strictBool(payload["ackTerminal"]) == true
            let userConfirmed = V3WireContract.strictBool(payload["userConfirmed"]) == true
            guard acknowledgesTerminal != userConfirmed,
                  let record = try V3OperationRecoveryJournal.direct(), record.requestID == target else {
                throw ServiceError.invalidRequest
            }
            let current = try V3OperationRecoveryJournal.markDirectUnknownIfOwnerLost(
                requestID: target, currentServiceInstanceID: recoveryServiceInstanceID) ?? record
            guard current.phase != .dispatched else { throw ServiceError.busy }
            guard (current.phase == .terminal && acknowledgesTerminal) ||
                  ((current.phase == .prepared || current.phase == .unknown) && userConfirmed) else {
                throw ServiceError.invalidRequest
            }
            let postcondition = current.phase == .prepared
                ? "notDispatched" : await directRecoveryPostcondition(current)
            guard try V3OperationRecoveryJournal.reconcileDirect(requestID: target,
                allowUnknownDeviceCheck: current.phase == .unknown && userConfirmed) else { throw ServiceError.busy }
            return ["requestID": target, "reconciled": true, "postcondition": postcondition]
        case "refreshAdmissionBegin":
            guard refreshAdmission.acquire(runID: target,
                    requestID: id,
                    authenticationActive: V3HeadlessRuntime.shared.auth.hasActiveSession,
                    anotherMutationActive: (mutationID != nil && mutationID != id) ||
                        V3HeadlessRuntime.shared.operations.activeMutationID != nil
                    // This ownership lifetime follows the native refresh timeout,
                    // not the short XPC begin-request deadline.
                    ) else {
                throw ServiceError.busy
            }
            do {
                guard try V3OperationRecoveryJournal.reserve(sessionID: target, kind: "refreshAll") else {
                    throw ServiceError.busy
                }
            } catch {
                _ = refreshAdmission.release(runID: target)
                throw ServiceError.busy
            }
            return ["runID": target, "admitted": true]
        case "refreshAdmissionEnd":
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
                return ["runID": target, "released": true, "alreadyReleased": true]
            }
            guard recovery.kind == "refreshAll", recovery.sessionID == target,
                  try V3OperationRecoveryJournal.settleRefreshAdmission(runID: target,
                    terminalState: terminalState, terminalConfirmed: true) else {
                throw ServiceError.notFound
            }
            _ = refreshAdmission.release(runID: target)
            return ["runID": target, "released": true]
        case "refreshAdmissionReconcile":
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
        case "appIcon":
            let app: InstalledApp = try object(target)
            guard let image = try await app.loadIcon() else { return [:] }
            try Task.checkCancellation()
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            let thumbnail = UIGraphicsImageRenderer(size: CGSize(width: 192, height: 192), format: format).image { _ in
                image.draw(in: CGRect(x: 0, y: 0, width: 192, height: 192))
            }
            guard let data = thumbnail.pngData(), data.count <= 262_144 else { return [:] }
            return ["icon": data]
        case "backupResult":
            guard mutationID != nil, ["success", "failure"].contains(target) else { throw ServiceError.invalidRequest }
            let result: Result<Void, Error> = target == "success" ? .success(()) : .failure(ServiceError.unsupported)
            NotificationCenter.default.post(name: AppDelegate.appBackupDidFinish, object: nil,
                userInfo: [AppDelegate.appBackupResultKey: result])
            return [:]
        case "catalog":
            // V3_CATALOG_DIAGNOSTICS_V1: the catalog read is measured with
            // privacy-safe facts only: whether the source row exists, whether
            // its identifier matches the request, and how many catalog rows were
            // returned. No source identifier, URL, name, bundle identifier,
            // description, object URI, or filesystem path is ever recorded.
            let sourceQuery = NSFetchRequest<Source>(entityName: "Source")
            sourceQuery.predicate = NSPredicate(format: "identifier == %@", target)
            sourceQuery.fetchLimit = 1
            let storedSource = try context.fetch(sourceQuery).first
            // V3_CATALOG_SOURCE_MISSING_V1: a source that no longer exists must
            // not be reported as a valid source with zero apps, or a stale
            // catalog screen becomes indistinguishable from an empty catalog.
            // This is NOT a manifest problem and is never reported as one.
            guard let storedSource else {
                throw V3SideStoreServiceError.catalogSourceUnavailable
            }
            let sourceMatch = storedSource.identifier == target
            let query = NSFetchRequest<StoreApp>(entityName: "StoreApp")
            query.predicate = NSCompoundPredicate(andPredicateWithSubpredicates: [
                StoreApp.visibleAppsPredicate, NSPredicate(format: "sourceIdentifier == %@", target)])
            let offset = request["cursor"] as? Int ?? 0
            query.sortDescriptors = [NSSortDescriptor(key: "name", ascending: true),
                                     NSSortDescriptor(key: "bundleIdentifier", ascending: true)]
            query.fetchOffset = offset
            query.fetchLimit = 51
            let fetched = try context.fetch(query)
            let apps = Array(fetched.prefix(50))
            debugLog("[V3_CATALOG] RESULT operation=catalog stage=catalogRead request_id=\(id) cursor=\(offset) source_found=yes source_identifier_match=\(sourceMatch ? "yes" : "no") catalog_row_count=\(apps.count) has_more=\(fetched.count > 50 ? "yes" : "no")")
            return ["apps": apps.map { app in
                // V3_CATALOG_ROW_PLIST_SAFE_V1: the row is built explicitly and
                // every value is unwrapped. An app that is not installed has no
                // installedVersion, and an absent key is the correct encoding of
                // an absent value: placing a Swift Optional into this dictionary
                // boxes Optional.none into Any, which PropertyListSerialization
                // cannot encode, so the whole catalog response would fail to
                // serialize even though the Core Data read succeeded.
                V3WireContract.V3PropertyListValue.dictionary([
                    "identifier": app.objectID.uriRepresentation().absoluteString,
                    "bundleID": app.bundleIdentifier,
                    "name": app.name,
                    // Coalesced to a concrete String: the host renders this as a
                    // non-optional version label, so the placeholder is part of
                    // the display contract rather than a leaked Optional.
                    "version": app.latestSupportedVersion?.version ?? "Unavailable",
                    "developer": app.developerName,
                    "description": app.localizedDescription,
                    "iconURL": app.iconURL.absoluteString,
                    "downloadURL": app.latestSupportedVersion?.downloadURL.absoluteString ?? "",
                    "canInstall": app.latestSupportedVersion != nil,
                    "installedID": app.installedApp?.objectID.uriRepresentation().absoluteString ?? "",
                    // The only field that was genuinely optional. It is omitted
                    // entirely when the app is not installed. The host already
                    // models it as an optional, so no placeholder is invented and
                    // no Optional is boxed into the response graph.
                    "installedVersion": app.installedApp?.version
                ])
            }, "nextCursor": fetched.count > 50 ? offset + 50 : -1]
        case "signOut":
            try V3BackendCommands.prepareSignOut()
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
            return try snapshot()
        case "syncAppIDs":
            let credentials = AuthManager.shared.authenticationSnapshot
            if !V3AuthIdentityBindingPolicy.hasTokenBackedRoute(
                credentialRoutePresent: credentials?.isAuthenticated == true,
                dsid: credentials?.appleIDAdsid, xcodeToken: credentials?.appleIDXcodeToken) {
                throw V3SideStoreServiceError.authRequired
            }
            try await callback { done in AppManager.shared.syncAppIDs(completionHandler: done) }
            return try snapshot()
        case "clearCache":
            try await callback { done in AppManager.shared.clearAppCache(completion: done) }
            return try snapshot()
        case "refreshSources":
            try await ensureKnownSourcesUpdated()
            try await callback { done in AppManager.shared.updateAllSources(completion: done) }
            return try snapshot()
        case "jit":
            let app: InstalledApp = try object(target)
            try await callback { done in AppManager.shared.enableJIT(for: app, completionHandler: done) }
            return try snapshot()
        case "authBegin":
            guard let deadline = payload["sessionDeadline"] as? Date,
                  deadline > Date(), deadline.timeIntervalSinceNow <= V3WireContract.authSessionLifetime + 10,
                  let session = payload["session"] as? String, session == target else {
                throw ServiceError.invalidRequest
            }
            guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }
            return await V3HeadlessRuntime.shared.auth.begin(deadline: deadline,
                requestDeadline: request["deadline"] as? Date, sessionID: session)
        case "authRetryProvisioning":
            // V3_PROVISIONING_RESUME_V1: Apple authentication already succeeded.
            // This re-enters provisioning with the saved session so credentials
            // and 2FA are never requested a second time.
            guard let deadline = payload["sessionDeadline"] as? Date,
                  deadline > Date(), deadline.timeIntervalSinceNow <= V3WireContract.authSessionLifetime + 10,
                  let session = payload["session"] as? String, session == target else {
                throw ServiceError.invalidRequest
            }
            guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }
            return await V3HeadlessRuntime.shared.auth.begin(deadline: deadline,
                mode: .resumeProvisioning, requestDeadline: request["deadline"] as? Date,
                sessionID: session)
        case "authPoll":
            guard let reply = V3HeadlessRuntime.shared.auth.poll(id: target) else {
                throw CombinedFailure(operation: "signIn", stage: .authentication,
                    code: .invalidResponse, id: target, retryable: false,
                    safeCause: .authSessionUnavailable)
            }
            return reply
        case "authRespond":
            guard let secretToken = payload["secretToken"] as? String,
                  let promptID = payload["prompt"] as? String else {
                throw ServiceError.invalidRequest
            }
            let answer = try V3SecretHandoff.consumeStringDictionary(secretToken)
            guard let reply = V3HeadlessRuntime.shared.auth.respond(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            return reply
        case "authCancel":
            guard await V3HeadlessRuntime.shared.auth.cancelAndWait(id: target) else { throw ServiceError.invalidRequest }
            guard let reply = V3HeadlessRuntime.shared.auth.poll(id: target) else { throw ServiceError.invalidRequest }
            return reply
        case "opRecoveryPrepare":
            guard let kind = payload["kind"] as? String,
                  let session = payload["session"] as? String,
                  let operationTarget = payload["target"] as? String else {
                throw ServiceError.invalidRequest
            }
            let stagedIPAToken = kind == "installSharedIPA" ? operationTarget : nil
            do {
                guard try V3OperationRecoveryJournal.reserve(sessionID: session, kind: kind,
                    stagedIPAToken: stagedIPAToken) else { throw ServiceError.busy }
            } catch { throw ServiceError.busy }
            return ["session": session, "kind": kind, "phase": "prepared"]
        case "recoveryDiscardUnreadable":
            guard target.isEmpty, V3WireContract.strictBool(payload["userConfirmed"]) == true else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.discardUnreadableAfterDeviceCheck(userConfirmed: true) else {
                    throw CombinedFailure(operation: operation, stage: .persistence,
                        code: .busy, id: id, retryable: false, safeCause: .operationInProgress)
                }
            } catch let failure as V3RecoveryStorageFailure {
                throw failure.combined(operation: operation, id: id)
            }
            if refreshAdmission.ownerLost, let runID = refreshAdmission.runID {
                _ = refreshAdmission.release(runID: runID)
            }
            return ["discardedUnreadable": true]
        case "opRecoveryReconcile":
            guard let parsedID = UUID(uuidString: target), parsedID.uuidString == target,
                  V3WireContract.strictBool(payload["userConfirmed"]) == true else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.reconcileAfterDeviceCheck(
                    sessionID: target, userConfirmed: true) else { throw ServiceError.invalidRequest }
            } catch { throw ServiceError.busy }
            return ["session": target, "reconciled": true]
        case "opStart":
            guard let kind = payload["kind"] as? String,
                  let session = payload["session"] as? String,
                  let deadline = request["deadline"] as? Date else { throw ServiceError.invalidRequest }
            let value: Bool?
            if let rawValue = payload["value"] {
                guard let parsedValue = V3WireContract.strictBool(rawValue) else {
                    throw ServiceError.invalidRequest
                }
                value = parsedValue
            } else {
                value = nil
            }
            let opTarget = payload["target"] as? String ?? target
            let stagedIPAToken = kind == "installSharedIPA" ? opTarget : nil
            do {
                guard try V3OperationRecoveryJournal.beginDispatch(sessionID: session, kind: kind,
                    stagedIPAToken: stagedIPAToken) else { throw ServiceError.busy }
            } catch { throw ServiceError.busy }
            let result = await V3HeadlessRuntime.shared.operations.start(kind: kind, target: opTarget,
                value: value, sessionID: session, deadline: deadline)
            settleOperationRecoveryIfTerminal(result, requestedSessionID: session)
            return result
        case "opPoll":
            guard let reply = V3HeadlessRuntime.shared.operations.poll(id: target) else { throw ServiceError.invalidRequest }
            settleOperationRecoveryIfTerminal(reply, requestedSessionID: target)
            return reply
        case "opAnswer":
            guard let secretToken = payload["secretToken"] as? String,
                  let promptID = payload["prompt"] as? String else {
                throw ServiceError.invalidRequest
            }
            let answer = try V3SecretHandoff.consumeStringDictionary(secretToken)
            guard let reply = V3HeadlessRuntime.shared.operations.answer(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            settleOperationRecoveryIfTerminal(reply, requestedSessionID: target)
            return reply
        case "opCancel":
            let hostReportedKnownStarted = V3WireContract.strictBool(payload["knownStarted"]) ?? true
            let cancellationRecovery: V3OperationRecoveryRecord?
            do { cancellationRecovery = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            let knownStarted = V3OperationCancelKnownStartedPolicy.resolve(sessionID: target,
                hostReportedKnownStarted: hostReportedKnownStarted, recovery: cancellationRecovery)
            guard let result = await V3HeadlessRuntime.shared.operations.cancelAndWait(
                id: target, knownStarted: knownStarted) else {
                throw ServiceError.invalidRequest
            }
            if knownStarted {
                settleOperationRecoveryIfTerminal(result, requestedSessionID: target)
            } else {
                _ = try? V3OperationRecoveryJournal.clearPreparedAfterConfirmedCancellation(
                    sessionID: target, replySessionID: result["session"] as? String,
                    state: result["state"] as? String,
                    backendSettled: V3WireContract.strictBool(result["backendSettled"]) == true,
                    stopConfirmed: V3WireContract.strictBool(result["stopConfirmed"]) == true,
                    knownStarted: false)
            }
            return result
        case "ipaCleanup":
            let recovery: V3OperationRecoveryRecord?
            do { recovery = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            if recovery?.stagedIPAToken == target {
                throw ServiceError.busy
            }
            try V3HeadlessRuntime.shared.operations.cleanupIPA(token: target)
            return [:]
        case "ipaActiveTokens":
            let lease: V3OperationRecoveryRecord?
            do { lease = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            var tokens = V3HeadlessRuntime.shared.operations.activeStagedIPATokens()
            if let token = lease?.stagedIPAToken { tokens.append(token) }
            return ["tokens": Array(Set(tokens)).sorted().prefix(512).map { $0 }]
        case "certList":
            return ["certificates": V3BackendCommands.certificates()]
        case "certSetActive":
            guard let certificate = CertificateManager.shared.getLocalCertificate(serialNumber: target) else {
                throw ServiceError.notFound
            }
            try CertificateManager.shared.setActiveCertificate(certificate)
            return try snapshot()
        case "certDelete":
            CertificateManager.shared.deleteCertificate(serialNumber: target)
            return try snapshot()
        case "certPortalList":
            return try await accountScopedRead("certificates") {
                try await V3BackendCommands.portalCertificates()
            }
        case "certRevoke":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let certificates = try await DeveloperPortalProxy.shared.fetchCertificates(team: team)
            guard let certificate = certificates.first(where: { $0.serialNumber == target }) else {
                throw ServiceError.notFound
            }
            _ = try await DeveloperPortalProxy.shared.revokeCertificate(certificate, team: team)
            return try snapshot()
        case "certCreate":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let name = UIDevice.current.name
            let outcome = try await V3CertificateCreateAdapter.createAndPersist(
                create: {
                    try await DeveloperPortalProxy.shared.createCertificate(
                        machineName: "SideStore - \(team.name)'s \(name)", team: team)
                },
                persist: { certificate in
                    // Reuse SideStore's canonical local certificate storage.
                    // Its save API is non-throwing and can suppress conversion
                    // failures, so success is decided only by the read-back.
                    CertificateManager.shared.saveCertificate(certificate)
                },
                verifyStored: { certificate in
                    let parsedSerial: String? = Keychain.shared[certificateSerial: certificate.serialNumber]
                        .flatMap { p12 in
                            try? CertificateManager.parse(
                                p12, password: CertificateManager.shared.getPassword(for: certificate.serialNumber))
                        }?.serialNumber
                    let enumeratedSerials = CertificateManager.shared.getAllLocalX509Certificates()
                        .map(\.serialNumber)
                    // These are separate persisted facts: validate the actual
                    // per-serial PKCS#12 and SideStore's canonical local index.
                    return V3CertificateCreateAdapter.matchesStoredCertificate(
                        expectedSerial: certificate.serialNumber,
                        parsedSerial: parsedSerial,
                        enumeratedSerials: enumeratedSerials)
                })
            // Do not return a generic failure after Apple has created the
            // certificate: that could encourage a duplicate portal request.
            // The host distinguishes a verified local copy from this partial
            // remote-success outcome and directs the user to inspect/reload.
            return ["outcome": outcome.rawValue]
        case "devTeams":
            return try await accountScopedRead("teams") { try await V3BackendCommands.developerTeams() }
        case "devDevices":
            return try await accountScopedRead("devices") { try await V3BackendCommands.developerDevices() }
        case "devAppIDs":
            return try await accountScopedRead("appIDs") { try await V3BackendCommands.developerAppIDs() }
        case "devGroups":
            return try await accountScopedRead("groups") { try await V3BackendCommands.developerGroups() }
        case "devProfiles":
            return try await accountScopedRead("profiles") { try await V3BackendCommands.developerProfiles() }
        case "sourcePreview":
            guard V3SourceAddPersistencePolicy.validatedURL(target) != nil else {
                throw V3SideStoreServiceError.invalidRequest
            }
            try await ensureKnownSourcesUpdated()
            return try await V3BackendCommands.sourcePreview(urlString: target)
        case "sourceAddConfirmed":
            guard V3SourceAddPersistencePolicy.validatedURL(target) != nil else {
                throw V3SideStoreServiceError.invalidRequest
            }
            try await ensureKnownSourcesUpdated()
            let addResult = try await V3BackendCommands.sourceAddConfirmed(urlString: target)
            var updated = try snapshot()
            let persistedSources = try await V3BackendCommands.authoritativeSourceRows()
            let sourceID = addResult["identifier"] as? String ?? ""
            guard persistedSources.contains(where: { $0["identifier"] as? String == sourceID }) else {
                throw V3SideStoreServiceError.persistenceUnverified
            }
            updated["sources"] = persistedSources
            return updated.merging(addResult) { _, authoritative in authoritative }
        case "sourceRemoveConfirmed":
            try await V3BackendCommands.sourceRemoveConfirmed(identifier: target)
            return try snapshot()
        case "pairingImportData":
            try V3BackendCommands.pairingImportData(token: target)
            return try snapshot()
        case "settingsGet":
            return V3BackendCommands.settingsGet()
        case "settingsSet":
            try V3BackendCommands.settingsSet(payload: payload)
            return try snapshot()
        case "anisetteList":
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteReset":
            _ = try await AnisetteServersManager.shared.resetToOriginalState()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteSync":
            _ = try await AnisetteServersManager.shared.syncWithRemote()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "sidesignGet":
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignSet":
            guard let secretToken = payload["secretToken"] as? String else { throw ServiceError.invalidRequest }
            try await V3BackendCommands.sidesignSet(token: secretToken)
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignReset":
            _ = SideSignConfigManager.shared.resetToDefaults()
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignImport":
            try await V3BackendCommands.sidesignImport(token: target)
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignExport":
            return ["secretToken": try await V3BackendCommands.sidesignExportToken()]
        case "logTail":
            return V3BackendCommands.logTail()
        case "healthSnapshot":
            return await V3BackendCommands.health()
        case "accountExport":
            guard let secretToken = payload["secretToken"] as? String else {
                throw ServiceError.invalidRequest
            }
            let password = try V3SecretHandoff.consumeString(secretToken)
            guard !password.isEmpty else { throw ServiceError.invalidRequest }
            let includeApple: Bool
            if let rawIncludeApple = payload["includeApple"] {
                guard let parsedIncludeApple = V3WireContract.strictBool(rawIncludeApple) else {
                    throw ServiceError.invalidRequest
                }
                includeApple = parsedIncludeApple
            } else {
                includeApple = false
            }
            return ["backup": try V3BackendCommands.accountExport(password: password, includeApplePassword: includeApple)]
        case "accountImport":
            guard let secretToken = payload["secretToken"] as? String else { throw ServiceError.invalidRequest }
            let password = try V3SecretHandoff.consumeString(secretToken)
            AuthManager.shared.v3BeginIdentityTransition()
            defer { AuthManager.shared.v3CompleteIdentityTransition() }
            return try V3BackendCommands.accountImport(token: target, password: password)
        default: throw ServiceError.invalidRequest
        }
    }

    private func object<T: NSManagedObject>(_ identifier: String) throws -> T {
        guard let url = URL(string: identifier),
              let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
              let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else { throw ServiceError.notFound }
        return object
    }

    private func directRecoveryInspection(requestID: String) async throws -> [String: Any] {
        guard let stored = try V3OperationRecoveryJournal.direct(), stored.requestID == requestID else {
            throw ServiceError.notFound
        }
        let record = try V3OperationRecoveryJournal.markDirectUnknownIfOwnerLost(
            requestID: requestID, currentServiceInstanceID: recoveryServiceInstanceID) ?? stored
        var response = safeDirectRecovery(record)
        response["postcondition"] = record.phase == .prepared
            ? "notDispatched" : await directRecoveryPostcondition(record)
        return response
    }

    private func safeDirectRecovery(_ record: V3DirectMutationRecoveryRecord) -> [String: Any] {
        var value: [String: Any] = ["requestID": record.requestID,
            "operation": record.operation, "phase": record.phase.rawValue]
        if let terminalOutcome = record.terminalOutcome { value["resultState"] = terminalOutcome }
        return value
    }

    private func directRecoveryPostcondition(_ record: V3DirectMutationRecoveryRecord) async -> String {
        if record.operation == "certCreate" {
            if record.terminalOutcome == "createdAndStored" { return "achieved" }
            return "manualCheckRequired"
        }
        if ["pairingImportData", "accountImport"].contains(record.operation) { return "manualCheckRequired" }
        if record.operation == "sourceAddConfirmed" || record.operation == "sourceRemoveConfirmed" {
            guard let targetDigest = record.targetDigest,
                  let rows = try? await V3BackendCommands.authoritativeSourceRows() else { return "indeterminate" }
            let matched = rows.contains { row in
                let value = record.operation == "sourceAddConfirmed" ? row["url"] : row["identifier"]
                guard let value = value as? String else { return false }
                return V3DirectMutationRecoveryHash.digest(value) == targetDigest
            }
            if record.operation == "sourceAddConfirmed" { return matched ? "achieved" : "indeterminate" }
            return matched ? "notAchieved" : "achieved"
        }
        if record.operation == "certRevoke" {
            guard let targetDigest = record.targetDigest, let teamDigest = record.teamDigest,
                  let identityStampDigest = record.identityStampDigest,
                  AuthManager.shared.v3IdentityIsStable,
                  V3DirectMutationRecoveryHash.digest(AuthManager.shared.v3IdentityStamp) == identityStampDigest,
                  let activeTeamID = DatabaseManager.shared.activeTeam()?.identifier,
                  V3DirectMutationRecoveryHash.digest(activeTeamID) == teamDigest,
                  let authenticatedTeam = try? await AuthManager.shared.getAuthenticatedTeam(),
                  V3DirectMutationRecoveryHash.digest(authenticatedTeam.identifier) == teamDigest,
                  let rows = try? await V3BackendCommands.portalCertificates() else { return "indeterminate" }
            return rows.contains { row in
                guard let serial = row["serial"] as? String else { return false }
                return V3DirectMutationRecoveryHash.digest(serial) == targetDigest
            } ? "notAchieved" : "achieved"
        }
        if record.operation == "settingsSet" {
            let current = V3BackendCommands.settingsGet()
            switch record.settingsType {
            case "bool":
                guard let key = record.settingsKey,
                      let expected = record.settingsBool,
                      let values = current["bools"] as? [String: Bool],
                      let actual = values[key] else { return "indeterminate" }
                return actual == expected ? "achieved" : "notAchieved"
            case "int":
                guard let key = record.settingsKey,
                      let expected = record.settingsInt,
                      let values = current["ints"] as? [String: Int],
                      let actual = values[key] else { return "indeterminate" }
                return actual == expected ? "achieved" : "notAchieved"
            case "string": return "manualCheckRequired"
            default: return "indeterminate"
            }
        }
        return "manualCheckRequired"
    }

    // Native callbacks may fire more than once or race on arbitrary queues.
    // The first terminal result wins; late callbacks are ignored. Cancellation
    // never releases the continuation early: the task keeps awaiting the
    // native terminal callback so the service mutation gate is not freed early.
    final class V3ServiceCallbackGate {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<Void, Error>?
        private var settled = false
        init(_ continuation: CheckedContinuation<Void, Error>) {
            self.continuation = continuation
        }
        func settle(_ result: Result<Void, Error>) {
            lock.lock()
            let pending = continuation
            let first = !settled
            settled = true
            continuation = nil
            lock.unlock()
            if first, let pending = pending {
                pending.resume(with: result)
            }
        }
    }
    // V3_NATIVE_CALLBACK_GATE_END

    private func callback(_ start: (@escaping (Result<Void, Error>) -> Void) -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let gate = V3ServiceCallbackGate(continuation)
            start { result in gate.settle(result) }
        }
    }

    // Upstream SideStore refreshes its server-owned allow/block source lists at
    // launch. The headless backend has no launch screen, so source preview, add,
    // and source refresh run a bounded cached preflight before AppManager source
    // operations rely on UserDefaults.blockedSources. Concurrent callers share
    // one UpdateKnownSourcesOperation with a 15-second total time bound.
    private func ensureKnownSourcesUpdated() async throws {
        let defaults = UserDefaults.standard
        let hasCachedBlocklist = defaults.blockedSources != nil
        let lastUpdated = defaults.object(forKey: "v3KnownSourcesUpdatedAt") as? Date
        guard V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: hasCachedBlocklist,
            lastSuccessfulUpdate: lastUpdated) else { return }

        if let knownSourcesUpdateTask {
            do { try await knownSourcesUpdateTask.value }
            catch { throw V3KnownSourcePolicyFailure.preservingCancellation(error) }
            guard defaults.blockedSources != nil else { throw ServiceError.notReady }
            return
        }
        let task = Task<Void, Error> { @MainActor in
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    _ = try await UpdateKnownSourcesOperation().execute()
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: 15_000_000_000)
                    throw URLError(.timedOut)
                }
                let completedUpdate = try await group.next()
                guard case .some = completedUpdate else { throw CancellationError() }
                group.cancelAll()
            }
        }
        knownSourcesUpdateTask = task
        defer { knownSourcesUpdateTask = nil }
        do { try await task.value }
        catch { throw V3KnownSourcePolicyFailure.preservingCancellation(error) }
        guard defaults.blockedSources != nil else { throw ServiceError.notReady }
        defaults.set(Date(), forKey: "v3KnownSourcesUpdatedAt")
    }

    private func accountScopedRead(_ key: String,
                                   fetch: () async throws -> Any) async throws -> [String: Any] {
        let auth = AuthManager.shared
        guard auth.v3IdentityIsStable,
              !V3HeadlessRuntime.shared.auth.hasActiveSession else {
            throw V3SideStoreServiceError.authRequired
        }
        let capturedStamp = auth.v3IdentityStamp
        let value = try await fetch()
        guard auth.v3IdentityIsStable, auth.v3IdentityStamp == capturedStamp,
              !V3HeadlessRuntime.shared.auth.hasActiveSession else {
            throw V3SideStoreServiceError.authRequired
        }
        return [key: value, "identityStamp": capturedStamp, "identityStable": true]
    }

    private func snapshot() throws -> [String: Any] {
        let identityGenerationAtStart = AuthManager.shared.v3IdentityGeneration
        let identityStampAtStart = AuthManager.shared.v3IdentityStamp
        let context = DatabaseManager.shared.viewContext
        let apps = InstalledApp.all(in: context)
        let sources = try context.fetch(NSFetchRequest<Source>(entityName: "Source"))
        let storedTeam = DatabaseManager.shared.activeTeam()
        let activeCertificate = CertificateManager.shared.activeCertificate
        let certificate = activeCertificate?.certificate.x509
        // V3_AUTH_SESSION_SNAPSHOT_V1: Apple authentication can succeed before
        // the account row is activated, because activation happens at the end
        // of SignInOperation.finalizeAuthentication. Reporting "Not signed in"
        // in that window made a successful sign-in look like a failed one and
        // hid the authenticated session from Retry Provisioning. The session
        // itself is authoritative; the active row is reported separately as
        // provisioningIncomplete so no active team is ever implied.
        let storedAccount = DatabaseManager.shared.activeAccount()
        let authCredentials = AuthManager.shared.authenticationSnapshot
        let identityReadStable = AuthManager.shared.v3IdentityIsStable &&
            V3AuthIdentityBindingPolicy.mayProjectIdentity(
            generationBefore: identityGenerationAtStart,
            generationAfter: AuthManager.shared.v3IdentityGeneration) &&
            identityStampAtStart == AuthManager.shared.v3IdentityStamp
        let credentialRoutePresent = identityReadStable && authCredentials?.isAuthenticated == true
        let credentialAppleID = V3AuthIdentityBindingPolicy.normalizedOwner(authCredentials?.appleIDEmailAddress)
        let authenticated = credentialRoutePresent &&
            authCredentials?.appleIDAdsid?.isEmpty == false && authCredentials?.appleIDXcodeToken?.isEmpty == false
        let activeAccount = identityReadStable ? storedAccount.flatMap { candidate in
            V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: credentialAppleID,
                teamOwner: candidate.appleID) ? candidate : nil
        } : nil
        let team = identityReadStable ? storedTeam.flatMap { candidate in
            // A directly attached account is authoritative. Only an ownerless
            // active team may inherit the current active account's identity.
            let owner = V3AuthIdentityBindingPolicy.normalizedOwner(candidate.account?.appleID) ??
                V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
                    storedTeamOwners: [], activeTeamIdentifier: storedTeam?.identifier,
                    requestedTeamIdentifier: candidate.identifier,
                    activeAccountOwner: activeAccount?.appleID, sessionOwner: credentialAppleID)
            return V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: credentialAppleID,
                teamOwner: owner) ? candidate : nil
        } : nil
        let account = activeAccount?.appleID ?? (identityReadStable && credentialRoutePresent
            ? authCredentials?.appleIDEmailAddress : nil) ?? "Not signed in"
        let activeAuthenticationSessionID = V3HeadlessRuntime.shared.auth.activeSessionIDForSnapshot
        _ = refreshAdmission.expire()
        let operationRecovery: V3OperationRecoveryRecord?
        var directRecoveryRecord: V3DirectMutationRecoveryRecord?
        let recoveryJournalUnreadable: Bool
        let recoveryStorageFailure: V3RecoveryStorageFailure?
        do {
            switch try V3OperationRecoveryJournal.currentState() {
            case .operation(let value): operationRecovery = value
            case .directMutation(let value):
                directRecoveryRecord = try V3OperationRecoveryJournal.markDirectUnknownIfOwnerLost(
                    requestID: value.requestID, currentServiceInstanceID: recoveryServiceInstanceID) ?? value
                operationRecovery = nil
            case nil: operationRecovery = nil
            }
            recoveryJournalUnreadable = false
            recoveryStorageFailure = nil
        } catch let failure as V3RecoveryStorageFailure {
            operationRecovery = nil; recoveryJournalUnreadable = true
            recoveryStorageFailure = failure
        } catch {
            operationRecovery = nil; recoveryJournalUnreadable = true
            recoveryStorageFailure = V3RecoveryStorageFailure(.readFailure, underlying: error)
        }
        let activeMutation = mutationID != nil || activeAuthenticationSessionID != nil ||
            V3HeadlessRuntime.shared.operations.activeMutationID != nil || refreshAdmission.isActive
        var response: [String: Any] = ["updatedAt": Date(), "busy": mutationID != nil ||
                    activeAuthenticationSessionID != nil ||
                    V3HeadlessRuntime.shared.operations.activeMutationID != nil || refreshAdmission.isActive ||
                    operationRecovery != nil || directRecoveryRecord != nil || recoveryJournalUnreadable,
                 "activeMutation": activeMutation,
                 "recoveryHold": operationRecovery != nil || directRecoveryRecord != nil || recoveryJournalUnreadable,
                 "recoveryJournalUnreadable": recoveryJournalUnreadable,
                 "account": account,
                 "authenticated": authenticated,
                 "credentialRoutePresent": credentialRoutePresent,
                 "identityStamp": identityStampAtStart,
                 "identityStable": identityReadStable,
                 "activeAccountPresent": activeAccount != nil,
                 "activeTeamPresent": team != nil,
                 "activeCertificatePresent": activeCertificate != nil,
                 "authenticationActive": activeAuthenticationSessionID != nil,
                 "provisioningIncomplete": authenticated && activeAccount == nil,
                "provisioningRetryAvailable": V3HeadlessRuntime.shared.auth.canResumeProvisioning(),
                "team": team?.name ?? "No active team", "teamID": team?.identifier ?? "",
                "signing": team == nil ? "Sign in required" : "Team selected",
                "certificate": activeCertificate == nil ? "No active certificate" : "Active certificate available",
                "certificateExpiration": certificate?.expiryDate ?? Date.distantPast,
                "pairing": V3BackendCommands.pairingFileStatus(),
                "installedApps": apps.map { app in
                    ["identifier": app.objectID.uriRepresentation().absoluteString,
                     "bundleID": app.bundleIdentifier, "name": app.name, "version": app.version,
                     "isActive": app.isActive, "expirationDate": app.expirationDate,
                     "refreshedDate": app.refreshedDate, "hasUpdate": app.hasUpdate,
                     "certificateStatus": app.certificateStatusRaw ?? "unknown",
                     "openURL": app.openAppURL.absoluteString,
                     "isHost": app.bundleIdentifier == StoreApp.altstoreAppID] as [String: Any]
                },
                "sources": sources.map { source in
                    ["identifier": source.identifier, "name": source.name, "subtitle": source.subtitle ?? "",
                     "url": source.sourceURL.absoluteString, "appCount": source.apps.count,
                     "canRemove": source.identifier != Source.altStoreIdentifier] as [String: Any]
                },
                "settings": ["betaUpdates": UserDefaults.standard.isBetaUpdatesEnabled,
                             "idleTimeoutDisabled": UserDefaults.standard.isIdleTimeoutDisableEnabled,
                             "responseCachingDisabled": UserDefaults.standard.responseCachingDisabled,
                             "verboseOperations": UserDefaults.standard.isVerboseOperationsLoggingEnabled]]
        if let recoveryStorageFailure {
            response["recoveryStorageFailure"] = recoveryStorageFailure.snapshotValue
        }
        response["recoveryAppGroup"] = V3OperationRecoveryJournal.runtimeAppGroupDiagnostic()
        if let activeSessionID = activeAuthenticationSessionID {
            response["authenticationSessionID"] = activeSessionID
        }
        if let operationRecovery, operationRecovery.kind != "refreshAll" {
            var safeRecovery: [String: Any] = ["session": operationRecovery.sessionID,
                "kind": operationRecovery.kind, "phase": operationRecovery.phase.rawValue]
            if let token = operationRecovery.stagedIPAToken { safeRecovery["stagedIPAToken"] = token }
            response["operationRecovery"] = safeRecovery
        }
        if let directRecoveryRecord {
            response["directRecovery"] = safeDirectRecovery(directRecoveryRecord)
        }
        if let operationRecovery, operationRecovery.kind == "refreshAll",
           !refreshAdmission.owns(operationRecovery.sessionID) {
            _ = refreshAdmission.restoreLost(runID: operationRecovery.sessionID)
        }
        if let operationRecovery, operationRecovery.kind == "refreshAll",
           refreshAdmission.ownerLost {
            response["refreshRecovery"] = ["runID": operationRecovery.sessionID, "ownerLost": true]
        } else if refreshAdmission.ownerLost, let runID = refreshAdmission.runID {
            response["refreshRecovery"] = ["runID": runID, "ownerLost": true]
        }
        return response
    }
}
