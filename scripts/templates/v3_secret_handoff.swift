import Foundation
import Security

#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Advisory process-shared lock for operations that must coordinate between
/// the LiveContainer app and its embedded service process. NSLock is process local.
enum V3AppGroupProcessLock {
    static func withLock<T>(containerRoot: URL? = nil,
                            selectedGroup: String? = nil,
                            onFailure: ((String, String, Int) -> Void)? = nil,
                            diagnostics: V3SecretHandoffDiagnostics? = nil,
                            _ operation: () throws -> T) throws -> T {
        #if canImport(Darwin)
        let container: URL
        if let containerRoot { container = containerRoot }
        else {
            // This helper is compiled into the host, the SideStoreSupport
            // framework and the embedded service. Only Foundation is visible in
            // all three, so the group is injected: the host passes
            // LiveContainer's own selection, and the service resolves the group
            // LiveProcess validated and published. Both land on the same
            // V3SharedAppGroup identity IPA staging and the recovery journal
            // use, so the two processes take the same lock file.
            guard let shared = V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup) else {
                onFailure?("appGroup", "none", 0)
                throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
            }
            container = shared.containerRoot
        }

        #elseif canImport(Glibc)
        guard let containerRoot else {
            onFailure?("appGroup", "none", 0)
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
        }
        let container = containerRoot
        #else
        throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
        #endif
        let directory = ["Library", "Application Support", "LiveContainer"].reduce(
            container.standardizedFileURL) { $0.appendingPathComponent($1, isDirectory: true) }.standardizedFileURL
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
            }
        } catch {
            let native = error as NSError
            let safe = [NSCocoaErrorDomain, NSPOSIXErrorDomain].contains(native.domain)
            onFailure?("directory", safe ? native.domain : "redacted", safe ? native.code : 0)
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: safe ? Int32(native.code) : 0)
        }
        let path = directory.appendingPathComponent("keychain-transaction.lock").path
        let descriptor = open(path, O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else {
            onFailure?("open", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        defer { _ = close(descriptor) }
        guard fchmod(descriptor, S_IRUSR | S_IWUSR) == 0 else {
            onFailure?("permissions", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        guard flock(descriptor, LOCK_EX) == 0 else {
            onFailure?("flock", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        defer { _ = flock(descriptor, LOCK_UN) }
        return try operation()
    }
}

/// Why a secure handoff did not complete, at the granularity that tells an
/// engineer where to look without revealing anything sensitive.
///
/// Every case carries only an OSStatus integer, booleans, and a role. No case
/// carries an Apple ID, a password, a token, Keychain item data, or an access
/// group string, because this value reaches a device log.
public enum V3SecretHandoffFailure: String, Sendable {
    /// The process-shared App Group lock could not be taken, so the two
    /// processes could not serialize this transaction.
    case appGroupLockUnavailable
    /// This process could not read back which Keychain access group it owns, so
    /// the shared group could not be derived from its own entitlement.
    case keychainGroupDiscoveryFailed
    /// The derived shared group was refused by the Keychain: this process is not
    /// entitled to it. This is the shape a re-sign produces when the group is
    /// granted to the main app but not to its extensions.
    case keychainExplicitGroupUnauthorized
    /// The item was absent when it should have been present.
    case keychainItemNotFound
    /// Reading the item failed for a reason other than absence.
    case keychainReadFailed
    /// The one-time take could not delete the item after reading it.
    case keychainDeleteFailed
    /// The record existed but its lifetime had elapsed.
    case tokenExpired
    /// The record could not be decoded, or the token was not canonical.
    case tokenMalformed
    /// The outstanding-item budget was exhausted.
    case capacity
    /// The transaction lock is held but the shared group could not be resolved.
    case sharedGroupUnavailable
}

/// Privacy-safe evidence for one handoff step. Safe to log.
public struct V3SecretHandoffDiagnostics: Sendable, Equatable {
    /// "host" or "service".
    public var role: String
    public var operation: String
    public var failure: V3SecretHandoffFailure?
    /// Raw OSStatus, or 0 when the failure was not an OS call. Integer only.
    public var osStatus: Int32
    /// Whether this process could discover its own default access group.
    public var groupDiscovered: Bool
    /// Whether the token was a canonical UUID. Never the token itself.
    public var tokenWellFormed: Bool

    public init(role: String, operation: String, failure: V3SecretHandoffFailure? = nil,
                osStatus: Int32 = 0, groupDiscovered: Bool = false,
                tokenWellFormed: Bool = false) {
        self.role = role
        self.operation = operation
        self.failure = failure
        self.osStatus = osStatus
        self.groupDiscovered = groupDiscovered
        self.tokenWellFormed = tokenWellFormed
    }

    /// A single line with no secret material. Group names are deliberately
    /// absent: they embed the team identifier and were previously reported
    /// only as a boolean elsewhere.
    public var safeLine: String {
        var parts = ["handoff=1", "role=\(role)", "op=\(operation)"]
        parts.append("group_discovered=\(groupDiscovered)")
        parts.append("token_well_formed=\(tokenWellFormed)")
        if let failure { parts.append("cause=\(failure.rawValue)") }
        else { parts.append("cause=none") }
        parts.append("osstatus=\(osStatus)")
        return parts.joined(separator: " ")
    }
}

/// Emits one privacy-safe line per handoff step. Replaces the previous
/// `onFailure` callback, which carried an untyped domain string.
public enum V3SecretHandoffTrace {
    /// Set to false only by tests that assert on the absence of output.
    public static var isEnabled = true

    public static func emit(_ diagnostics: V3SecretHandoffDiagnostics) {
        guard isEnabled else { return }
        NSLog("[V3_SECRET_HANDOFF] %@", diagnostics.safeLine)
    }
}

/// Decides whether a thrown handoff error is reported as a secure-transport
/// failure rather than as whatever the surrounding operation was doing.
///
/// An authRespond that fails here never reached Apple. Reporting it as
/// signIn/authentication/failed tells the user their password was rejected,
/// which is false and sends them to change a password that never failed.
public enum V3SecretHandoffFailurePolicy {
    /// Operations whose payload crosses the secure channel first.
    public static let handoffCarryingOperations: Set<String> = [
        "authRespond", "opAnswer", "accountExport", "accountImport",
        "certCreate", "devPortalLogin"]

    public static func applies(to operation: String) -> Bool {
        handoffCarryingOperations.contains(operation)
    }

    /// The stage a handoff failure belongs to. Persistence, not authentication:
    /// the response is intact and the channel is what is broken.
    public static func stage(for operation: String) -> CombinedFailure.Stage { .persistence }

    /// Distinguishes a transient channel problem from one that needs a re-sign.
    public static func code(for failure: V3SecretHandoffFailure) -> CombinedFailure.Code {
        switch failure {
        case .appGroupLockUnavailable, .keychainReadFailed, .keychainDeleteFailed, .capacity:
            return .busy
        case .keychainGroupDiscoveryFailed, .keychainExplicitGroupUnauthorized,
             .keychainItemNotFound, .tokenExpired, .tokenMalformed, .sharedGroupUnavailable:
            return .unavailable
        }
    }

    /// Only a transient cause may be retried. Retrying cannot grant an access
    /// group or recreate an item the service is entitled to read.
    public static func isRetryable(_ failure: V3SecretHandoffFailure) -> Bool {
        switch failure {
        case .appGroupLockUnavailable, .keychainReadFailed, .keychainDeleteFailed, .capacity:
            return true
        case .keychainGroupDiscoveryFailed, .keychainExplicitGroupUnauthorized,
             .keychainItemNotFound, .tokenExpired, .tokenMalformed, .sharedGroupUnavailable:
            return false
        }
    }

    public static func failure(_ error: V3SecretHandoffError, operation: String,
                               id: String) -> CombinedFailure {
        let reason = error.failure
        // The OSStatus is evidence and is safe: an integer from the Keychain.
        // The group name is never included, because it embeds the team id.
        let underlying = NSError(domain: "V3SecretHandoff", code: Int(error.osStatusValue))
        return CombinedFailure(operation: operation, stage: stage(for: operation),
            code: code(for: reason), id: id, underlying: underlying,
            retryable: isRetryable(reason), safeCause: .secretHandoffUnavailable)
    }
}

/// Which side of the handoff is running. Injected so the same binary reports
/// honestly in the host, the SideStoreSupport framework and the service.
public enum V3SecretHandoffRole {
    public static let host = "host"
    public static let service = "service"
    /// The role of the running process, detected rather than declared. The host
    /// bundle identifier is the only one that is not the embedded service, and
    /// it is read from the process's own identity rather than passed in, so a
    /// call site cannot mislabel it.
    public static var current: String = resolve()

    static func resolve(bundle: Bundle = .main) -> String {
        bundle.bundleIdentifier?.hasSuffix(".LiveProcess") == true ? service : host
    }
}

public enum V3SecretHandoffError: Error, LocalizedError {
    case unavailable(V3SecretHandoffFailure, osStatus: Int32 = 0, groupDiscovered: Bool = false,
                     tokenWellFormed: Bool = false)
    case invalidToken
    case expired
    case malformed
    case capacity

    /// The OSStatus behind this error, or 0 when there was no OS call.
    public var osStatusValue: Int32 {
        if case .unavailable(_, let osStatus, _, _) = self { return osStatus }
        return 0
    }

    public var failure: V3SecretHandoffFailure {
        switch self {
        case .unavailable(let reason, _, _, _): return reason
        case .invalidToken: return .tokenMalformed
        case .expired: return .tokenExpired
        case .malformed: return .tokenMalformed
        case .capacity: return .capacity
        }
    }

    /// The safe line for this error, so every call site reports identically.
    public var diagnostics: V3SecretHandoffDiagnostics {
        switch self {
        case .unavailable(let reason, let osStatus, let groupDiscovered, let tokenWellFormed):
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: reason, osStatus: osStatus,
                groupDiscovered: groupDiscovered, tokenWellFormed: tokenWellFormed)
        case .invalidToken:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenMalformed, tokenWellFormed: false)
        case .expired:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenExpired, tokenWellFormed: true)
        case .malformed:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenMalformed, tokenWellFormed: true)
        case .capacity:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "store", failure: .capacity)
        }
    }

    public var errorDescription: String? {
        switch self {
        case .unavailable(let reason, _, _, _): return reason.userFacingMessage
        case .invalidToken: return "The secure response reference is invalid."
        case .expired: return "The secure response expired before SideStore received it."
        case .malformed: return "The secure response could not be read."
        case .capacity: return "Secure response storage is busy."
        }
    }
}

extension V3SecretHandoffFailure {
    /// What the user is told. This never implies Apple rejected anything: the
    /// response never reached Apple when the handoff failed.
    var userFacingMessage: String {
        switch self {
        case .appGroupLockUnavailable:
            return "The secure channel to the embedded service could not be locked. Try again."
        case .keychainGroupDiscoveryFailed:
            return "This build's secure storage group could not be identified. Reinstall or re-sign the app."
        case .keychainExplicitGroupUnauthorized:
            return "This build's secure storage group is not available to every part of the app, so the response could not be delivered. Re-sign the app so its extensions share the secure group."
        case .keychainItemNotFound:
            return "The secure response was already used or is no longer present. Enter it again."
        case .keychainReadFailed:
            return "The secure response could not be read from secure storage. Try again."
        case .keychainDeleteFailed:
            return "The secure response could not be cleared from secure storage. Try again."
        case .tokenExpired:
            return "The secure response expired before SideStore received it. Enter it again."
        case .tokenMalformed:
            return "The secure response could not be decoded. Enter it again."
        case .capacity:
            return "Secure response storage is busy. Try again."
        case .sharedGroupUnavailable:
            return "The shared App Group is unavailable, so the secure channel is unavailable."
        }
    }
}

/// Serializes the full shared-Keychain admission transaction across the host
/// and embedded service processes. The count/purge callback and SecItemAdd
/// callback must remain within this one lock scope.
enum V3SecretHandoffStoreAdmission {
    static func add<T>(containerRoot: URL? = nil, selectedGroup: String? = nil, maximumOutstandingItems: Int,
                       liveItemCount: () throws -> Int,
                       insert: () throws -> T) throws -> T {
        try V3AppGroupProcessLock.withLock(containerRoot: containerRoot, selectedGroup: selectedGroup) {
            guard try liveItemCount() < maximumOutstandingItems else {
                throw V3SecretHandoffError.capacity
            }
            return try insert()
        }
    }
}

extension V3SecretHandoffError {
    /// Builds a typed handoff failure and reports it once, so no call site has
    /// to remember to emit.
    static func fail(_ reason: V3SecretHandoffFailure,
                     as base: V3SecretHandoffDiagnostics?,
                     operation: String,
                     osStatus: Int32 = 0, groupDiscovered: Bool = false,
                     tokenWellFormed: Bool = false) -> V3SecretHandoffError {
        var resolved = base ?? V3SecretHandoffDiagnostics(
            role: V3SecretHandoffRole.current, operation: operation)
        resolved.operation = operation
        resolved.failure = reason
        resolved.osStatus = osStatus
        if groupDiscovered { resolved.groupDiscovered = true }
        if tokenWellFormed { resolved.tokenWellFormed = true }
        V3SecretHandoffTrace.emit(resolved)
        return .unavailable(reason, osStatus: osStatus,
                            groupDiscovered: resolved.groupDiscovered,
                            tokenWellFormed: resolved.tokenWellFormed)
    }
}

enum V3SecretHandoffRecord {
    static let lifetime: TimeInterval = 120
    static let maximumPayloadBytes = 64 * 1024
    private static let allowedKinds: Set<String> = ["string", "stringDictionary"]

    static func isStrictVersionOne(_ value: Any?) -> Bool {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
        let type = String(cString: number.objCType)
        return ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) &&
            number.intValue == 1
    }

    static func encode(kind: String, payload: Data, createdAt: Date) -> Data? {
        guard allowedKinds.contains(kind), !payload.isEmpty, payload.count <= maximumPayloadBytes else { return nil }
        let expiresAt = createdAt.addingTimeInterval(lifetime)
        let value: [String: Any] = ["version": 1, "kind": kind, "createdAt": createdAt,
            "expiresAt": expiresAt, "payload": payload]
        return try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
    }

    static func decode(_ data: Data, expectedKind: String, now: Date) -> Data? {
        guard !data.isEmpty, data.count <= maximumPayloadBytes + 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(value.keys) == Set(["version", "kind", "createdAt", "expiresAt", "payload"]),
              isStrictVersionOne(value["version"]),
              value["kind"] as? String == expectedKind, allowedKinds.contains(expectedKind),
              let createdAt = value["createdAt"] as? Date,
              let expiresAt = value["expiresAt"] as? Date,
              let payload = value["payload"] as? Data, !payload.isEmpty,
              payload.count <= maximumPayloadBytes,
              createdAt <= now, expiresAt > now,
              expiresAt.timeIntervalSince(createdAt) <= lifetime else { return nil }
        return payload
    }
}

enum V3SharedFileRecord {
    static let lifetime: TimeInterval = 60 * 60
    static let maximumPayloadBytes = 4_194_304
    static let maximumPendingFiles = 16
    static let maximumPendingStoredBytes = 16_777_216
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3SharedFileStaging"]
    private static let allowedPurposes: Set<String> = ["pairing", "sidesign", "accountImport"]
    private static let transactionLock = NSLock()

    static func stagingDirectory(containerRoot: URL) -> URL {
        directoryComponents.reduce(containerRoot.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
    }

    static func stage(_ payload: Data, purpose: String, containerRoot: URL,
                      now: Date = Date(), fileManager: FileManager = .default) -> String? {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard allowedPurposes.contains(purpose), !payload.isEmpty,
              payload.count <= maximumPayloadBytes else { return nil }
        guard let directory = ensureDirectory(containerRoot: containerRoot, fileManager: fileManager) else { return nil }
        guard let record = encode(payload, purpose: purpose, createdAt: now) else { return nil }
        let current = sweep(directory: directory, now: now, fileManager: fileManager)
        guard current.count < maximumPendingFiles,
              current.storedBytes <= maximumPendingStoredBytes - record.count else { return nil }
        let token = UUID().uuidString
        guard let file = fileURL(token: token, directory: directory),
              !fileManager.fileExists(atPath: file.path) else { return nil }
        do {
            try record.write(to: file, options: .atomic)
            try fileManager.setAttributes([.posixPermissions: 0o600, .modificationDate: now],
                                          ofItemAtPath: file.path)
            guard readRecordFile(file, directory: directory) != nil else {
                removeFile(file, directory: directory, fileManager: fileManager)
                return nil
            }
        } catch {
            removeFile(file, directory: directory, fileManager: fileManager)
            return nil
        }
        return token
    }

    static func consume(_ token: String, purpose: String, containerRoot: URL,
                        now: Date = Date(), fileManager: FileManager = .default) -> Data? {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard isCanonicalToken(token), allowedPurposes.contains(purpose),
              let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager),
              let file = fileURL(token: token, directory: directory),
              let data = readRecordFile(file, directory: directory) else { return nil }
        guard let record = decodeRecord(data, now: now) else {
            removeFile(file, directory: directory, fileManager: fileManager)
            return nil
        }
        guard record.purpose == purpose else { return nil }
        removeFile(file, directory: directory, fileManager: fileManager)
        return record.payload
    }

    static func discard(_ token: String, containerRoot: URL,
                        fileManager: FileManager = .default) {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard isCanonicalToken(token),
              let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager),
              let file = fileURL(token: token, directory: directory) else { return }
        removeFile(file, directory: directory, fileManager: fileManager)
    }

    static func removeLegacyDefaultsRecords(_ defaults: UserDefaults) {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("V3SharedFile.") {
            defaults.removeObject(forKey: key)
        }
    }

    @discardableResult
    static func sweep(containerRoot: URL, now: Date = Date(),
                      fileManager: FileManager = .default) -> (count: Int, storedBytes: Int) {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager) else {
            return (0, 0)
        }
        return sweep(directory: directory, now: now, fileManager: fileManager)
    }

    private static func sweep(directory: URL, now: Date,
                              fileManager: FileManager) -> (count: Int, storedBytes: Int) {
        var count = 0
        var storedBytes = 0
        guard let files = try? fileManager.contentsOfDirectory(at: directory,
                includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey,
                    .fileSizeKey, .contentModificationDateKey],
                options: [.skipsHiddenFiles]) else { return (0, 0) }
        for file in files {
            guard file.pathExtension == "bin",
                  isCanonicalToken(file.deletingPathExtension().lastPathComponent),
                  file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey,
                    .fileSizeKey, .contentModificationDateKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true,
                  file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { continue }
            let size = values.fileSize ?? 0
            let modified = values.contentModificationDate ?? .distantPast
            guard size > 0, size <= maximumPayloadBytes + 4096,
                  modified <= now, now.timeIntervalSince(modified) <= lifetime else {
                removeFile(file, directory: directory, fileManager: fileManager)
                continue
            }
            count += 1
            storedBytes += size
        }
        return (count, storedBytes)
    }

    private static func ensureDirectory(containerRoot: URL, fileManager: FileManager) -> URL? {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        do {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else { return nil }
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            return directory
        } catch { return nil }
    }

    private static func existingDirectory(containerRoot: URL, fileManager: FileManager) -> URL? {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        guard let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
              values.isDirectory == true, values.isSymbolicLink != true,
              directory.resolvingSymlinksInPath().standardizedFileURL == directory else { return nil }
        return directory
    }

    private static func fileURL(token: String, directory: URL) -> URL? {
        guard isCanonicalToken(token) else { return nil }
        let file = directory.appendingPathComponent(token + ".bin", isDirectory: false).standardizedFileURL
        guard file.deletingLastPathComponent() == directory.standardizedFileURL,
              file.lastPathComponent == token + ".bin" else { return nil }
        return file
    }

    private static func readRecordFile(_ file: URL, directory: URL) -> Data? {
        guard file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              values.isRegularFile == true, values.isSymbolicLink != true,
              (values.fileSize ?? 0) > 0, (values.fileSize ?? 0) <= maximumPayloadBytes + 4096,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return nil }
        guard let data = try? Data(contentsOf: file),
              data.count <= maximumPayloadBytes + 4096,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return nil }
        return data
    }

    private static func removeFile(_ file: URL?, directory: URL, fileManager: FileManager) {
        guard let file, file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isSymbolicLinkKey]),
              values.isSymbolicLink != true,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return }
        try? fileManager.removeItem(at: file)
    }

    private static func encode(_ payload: Data, purpose: String, createdAt: Date) -> Data? {
        guard allowedPurposes.contains(purpose), !payload.isEmpty,
              payload.count <= maximumPayloadBytes else { return nil }
        let value: [String: Any] = ["version": 1, "purpose": purpose, "createdAt": createdAt,
            "expiresAt": createdAt.addingTimeInterval(lifetime), "payload": payload]
        return try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
    }

    private static func decodeRecord(_ data: Data, now: Date) -> (purpose: String, payload: Data)? {
        guard !data.isEmpty, data.count <= maximumPayloadBytes + 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(value.keys) == Set(["version", "purpose", "createdAt", "expiresAt", "payload"]),
              V3SecretHandoffRecord.isStrictVersionOne(value["version"]),
              let purpose = value["purpose"] as? String, allowedPurposes.contains(purpose),
              let createdAt = value["createdAt"] as? Date,
              let expiresAt = value["expiresAt"] as? Date,
              let payload = value["payload"] as? Data, !payload.isEmpty,
              payload.count <= maximumPayloadBytes,
              createdAt <= now, expiresAt > now,
              expiresAt.timeIntervalSince(createdAt) <= lifetime else { return nil }
        return (purpose, payload)
    }

    private static func isCanonicalToken(_ token: String) -> Bool {
        guard let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString == token
    }
}

enum V3SharedFileInputError: Error, LocalizedError {
    case unavailable
    case empty
    case tooLarge

    var errorDescription: String? {
        switch self {
        case .unavailable: return "The selected file is unavailable. Choose an accessible file and try again."
        case .empty: return "The selected file is empty. Choose a valid export file and try again."
        case .tooLarge: return "The selected import file is larger than 4 MiB."
        }
    }
}

enum V3SharedFileInput {
    static let maximumBytes = V3SharedFileRecord.maximumPayloadBytes
    static let chunkBytes = 64 * 1024

    // Inspect the provider URL before reading, then enforce the same bound while
    // streaming so a replaced/growing file cannot allocate an unbounded Data.
    // Callers hold security-scoped access for the duration of this method.
    static func readBounded(_ url: URL) throws -> Data {
        guard url.isFileURL,
              let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              values.isRegularFile == true, values.isSymbolicLink != true,
              let fileSize = values.fileSize, fileSize > 0 else { throw V3SharedFileInputError.unavailable }
        guard fileSize <= maximumBytes else { throw V3SharedFileInputError.tooLarge }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var data = Data()
        data.reserveCapacity(fileSize)
        while data.count <= maximumBytes {
            let remaining = maximumBytes + 1 - data.count
            guard let chunk = try handle.read(upToCount: min(chunkBytes, remaining)), !chunk.isEmpty else { break }
            data.append(chunk)
            if data.count > maximumBytes { throw V3SharedFileInputError.tooLarge }
        }
        guard !data.isEmpty else { throw V3SharedFileInputError.empty }
        return data
    }

    static func readBoundedAsync(_ url: URL) async throws -> Data {
        try await Task.detached(priority: .utility) {
            try V3SharedFileInput.readBounded(url)
        }.value
    }
}

enum V3SecretHandoff {
    private static let service = "com.kdt.livecontainer.v3-secret-handoff"
    private static let maximumOutstandingItems = 32

    static func isValidToken(_ token: String?) -> Bool {
        guard let token, let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString == token
    }

    static func storeString(_ value: String, selectedGroup: String? = nil) throws -> String {
        guard value.utf8.count <= 8192 else { throw V3SecretHandoffError.malformed }
        return try store(Data(value.utf8), kind: "string", selectedGroup: selectedGroup)
    }

    static func consumeString(_ token: String, selectedGroup: String? = nil) throws -> String {
        let data = try consume(token, kind: "string", selectedGroup: selectedGroup)
        guard let value = String(data: data, encoding: .utf8), value.utf8.count <= 8192 else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func storeStringDictionary(_ value: [String: String], selectedGroup: String? = nil) throws -> String {
        guard value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }),
              let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0),
              data.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        return try store(data, kind: "stringDictionary", selectedGroup: selectedGroup)
    }

    static func consumeStringDictionary(_ token: String, selectedGroup: String? = nil) throws -> [String: String] {
        let data = try consume(token, kind: "stringDictionary", selectedGroup: selectedGroup)
        guard let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String],
              value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }) else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func discard(_ token: String, selectedGroup: String? = nil) {
        guard isValidToken(token), let group = try? sharedKeychainAccessGroup() else { return }
        _ = try? V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            _ = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
        }
    }

    static func cleanupExpiredItems(selectedGroup: String? = nil) {
        guard let group = try? sharedKeychainAccessGroup() else { return }
        _ = try? V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            let rows = try listedItems(group: group)
            _ = try removeExpiredItems(group: group, rows: rows, now: Date())
        }
    }

    private static func store(_ payload: Data, kind: String, selectedGroup: String? = nil) throws -> String {
        guard payload.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        let group = try sharedKeychainAccessGroup()
        return try V3SecretHandoffStoreAdmission.add(selectedGroup: selectedGroup,
            maximumOutstandingItems: maximumOutstandingItems,
            liveItemCount: {
                let rows = try listedItems(group: group)
                return try removeExpiredItems(group: group, rows: rows, now: Date())
            },
            insert: {
                // Start the lifetime only after this request owns the shared
                // transaction lock and has passed the capacity check.
                let now = Date()
                let token = UUID().uuidString
                guard let record = V3SecretHandoffRecord.encode(kind: kind, payload: payload, createdAt: now) else {
                    throw V3SecretHandoffError.malformed
                }
                var query = itemQuery(token, group: group)
                query[kSecValueData as String] = record
                query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
                let status = SecItemAdd(query as CFDictionary, nil)
                guard status == errSecSuccess else {
                    let reason: V3SecretHandoffFailure = status == errSecMissingEntitlement
                        ? .keychainExplicitGroupUnauthorized : .keychainReadFailed
                    throw V3SecretHandoffError.fail(reason,
                        as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                            operation: "secretStore", groupDiscovered: true, tokenWellFormed: true),
                        operation: "secretStore", osStatus: status,
                        groupDiscovered: true, tokenWellFormed: true)
                }
                V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretStore", failure: nil, groupDiscovered: true, tokenWellFormed: true))
                return token
            })
    }

    private static func consume(_ token: String, kind: String, selectedGroup: String? = nil) throws -> Data {
        let wellFormed = isValidToken(token)
        guard wellFormed else {
            let error = V3SecretHandoffError.invalidToken
            V3SecretHandoffTrace.emit(error.diagnostics)
            throw error
        }
        // The process-shared advisory lock surrounds both copy and delete.
        // This makes competing patched processes serialize the one-time take;
        // NSLock alone cannot coordinate separate app/service processes.
        return try V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            try consumeLocked(token, kind: kind)
        }
    }

    private static func consumeLocked(_ token: String, kind: String) throws -> Data {
        // Any failure below reports itself, so a caller that only logs the
        // returned error still gets the step that failed.
        let group = try sharedKeychainAccessGroup()
        var query = itemQuery(token, group: group)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let record = result as? Data else {
            let reason: V3SecretHandoffFailure = status == errSecItemNotFound
                ? .keychainItemNotFound : (status == errSecMissingEntitlement
                  ? .keychainExplicitGroupUnauthorized : .keychainReadFailed)
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: status, groupDiscovered: true, tokenWellFormed: true)
        }
        let deleteStatus = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
        guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
            throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: Int32(deleteStatus),
                groupDiscovered: true, tokenWellFormed: true)
        }
        guard let payload = V3SecretHandoffRecord.decode(record, expectedKind: kind, now: Date()) else {
            let error = V3SecretHandoffError.expired
            V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "secretDecode", failure: .tokenExpired,
                groupDiscovered: true, tokenWellFormed: true))
            throw error
        }
        V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
            operation: "secretDecode", failure: nil, groupDiscovered: true, tokenWellFormed: true))
        return payload
    }

    static func sharedKeychainAccessGroup() throws -> String {
        // SecTask entitlement APIs are not exposed by the iOS SDK. Ask the
        // public Keychain API which default access group this signed process
        // owns, then verify the derived shared group with an explicit add.
        //
        // The two steps fail for different reasons and must stay distinguishable.
        // Discovery uses this process's own default group and therefore always
        // succeeds for a signed process. The explicit probe is the one that a
        // re-sign breaks when the shared group is granted to the main app but
        // not to its extensions, which is the shape of the reported failure.
        let defaultGroup: String
        do {
            defaultGroup = try probeAccessGroup()
        } catch let error as V3SecretHandoffError {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed, as: error.diagnostics,
                                            operation: "groupDiscovery", osStatus: error.osStatusValue)
        }
        guard let group = V3SharedKeychainAccessGroupPolicy.sharedGroup(fromDefaultGroup: defaultGroup) else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupDiscovery", groupDiscovered: true),
                operation: "groupDiscovery")
        }
        do {
            let verified = try probeAccessGroup(explicitGroup: group)
            guard verified == group else {
                throw V3SecretHandoffError.fail(.keychainExplicitGroupUnauthorized,
                    as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                        operation: "groupAuthorize", groupDiscovered: true),
                    operation: "groupAuthorize", osStatus: Int32(errSecParam))
            }
        } catch let error as V3SecretHandoffError {
            // errSecMissingEntitlement is the signature of a group this process
            // was never granted. Report it as such rather than as a generic
            // read failure, because the two need different fixes.
            let reason: V3SecretHandoffFailure =
                error.osStatusValue == errSecMissingEntitlement || error.osStatusValue == errSecNoAccessForItem
                ? .keychainExplicitGroupUnauthorized : .keychainGroupDiscoveryFailed
            throw V3SecretHandoffError.fail(reason, as: error.diagnostics,
                operation: "groupAuthorize", osStatus: error.osStatusValue, groupDiscovered: true)
        }
        return group
    }

    private static func probeAccessGroup(explicitGroup: String? = nil) throws -> String {
        let service = "com.kdt.livecontainer.v3-access-group-probe"
        let account = UUID().uuidString
        var item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse,
            // Group discovery also runs during background refresh after the
            // first unlock, matching the credential client's accessibility.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data([0]),
            kSecReturnAttributes as String: true]
        var deletion: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse]
        if let explicitGroup {
            item[kSecAttrAccessGroup as String] = explicitGroup
            deletion[kSecAttrAccessGroup as String] = explicitGroup
        }
        var result: CFTypeRef?
        let status = SecItemAdd(item as CFDictionary, &result)
        guard status == errSecSuccess else {
            let reason: V3SecretHandoffFailure = explicitGroup != nil && status == errSecMissingEntitlement
                ? .keychainExplicitGroupUnauthorized
                : (explicitGroup == nil ? .keychainGroupDiscoveryFailed : .keychainReadFailed)
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupProbe", groupDiscovered: explicitGroup == nil),
                operation: "groupProbe", osStatus: status, groupDiscovered: explicitGroup == nil)
        }
        let group = (result as? [String: Any])?[kSecAttrAccessGroup as String] as? String
        let deleteStatus = SecItemDelete(deletion as CFDictionary)
        guard deleteStatus == errSecSuccess, let group else {
            throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupProbe", groupDiscovered: true),
                operation: "groupProbe", osStatus: Int32(deleteStatus), groupDiscovered: true)
        }
        return group
    }

    private static func itemQuery(_ token: String, group: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: token,
         kSecAttrAccessGroup as String: group,
         kSecAttrSynchronizable as String: kCFBooleanFalse]
    }

    private static func listedItems(group: String) throws -> [[String: Any]] {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccessGroup as String: group,
            kSecAttrSynchronizable as String: kCFBooleanFalse,
            kSecReturnAttributes as String: true, kSecMatchLimit as String: kSecMatchLimitAll]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess else {
            let reason: V3SecretHandoffFailure = status == errSecMissingEntitlement
                ? .keychainExplicitGroupUnauthorized : .keychainReadFailed
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretList", groupDiscovered: true),
                operation: "secretList", osStatus: status, groupDiscovered: true)
        }
        if let rows = result as? [[String: Any]] { return rows }
        if let row = result as? [String: Any] { return [row] }
        throw V3SecretHandoffError.fail(.keychainReadFailed,
            as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "secretList", groupDiscovered: true),
            operation: "secretList", groupDiscovered: true)
    }

    private static func removeExpiredItems(group: String, rows: [[String: Any]], now: Date) throws -> Int {
        var retained = 0
        for row in rows {
            guard let token = row[kSecAttrAccount as String] as? String, isValidToken(token) else { continue }
            guard let createdAt = row[kSecAttrCreationDate as String] as? Date,
                  createdAt <= now, now.timeIntervalSince(createdAt) <= V3SecretHandoffRecord.lifetime else {
                let status = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
                guard status == errSecSuccess || status == errSecItemNotFound else {
                    throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                        as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                            operation: "secretSweep", groupDiscovered: true, tokenWellFormed: true),
                        operation: "secretSweep", osStatus: Int32(status),
                        groupDiscovered: true, tokenWellFormed: true)
                }
                continue
            }
            retained += 1
        }
        return retained
    }
}
