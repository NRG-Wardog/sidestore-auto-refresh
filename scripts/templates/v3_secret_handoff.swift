import Foundation
import Security

enum V3SecretHandoffError: Error, LocalizedError {
    case unavailable
    case invalidToken
    case expired
    case malformed
    case capacity

    var errorDescription: String? {
        switch self {
        case .unavailable: return "Secure response storage is unavailable."
        case .invalidToken: return "The secure response reference is invalid."
        case .expired: return "The secure response expired before SideStore received it."
        case .malformed: return "The secure response could not be read."
        case .capacity: return "Secure response storage is busy."
        }
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

    static func storeString(_ value: String) throws -> String {
        guard value.utf8.count <= 8192 else { throw V3SecretHandoffError.malformed }
        return try store(Data(value.utf8), kind: "string")
    }

    static func consumeString(_ token: String) throws -> String {
        let data = try consume(token, kind: "string")
        guard let value = String(data: data, encoding: .utf8), value.utf8.count <= 8192 else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func storeStringDictionary(_ value: [String: String]) throws -> String {
        guard value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }),
              let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0),
              data.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        return try store(data, kind: "stringDictionary")
    }

    static func consumeStringDictionary(_ token: String) throws -> [String: String] {
        let data = try consume(token, kind: "stringDictionary")
        guard let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String],
              value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }) else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func discard(_ token: String) {
        guard isValidToken(token), let group = try? sharedKeychainAccessGroup() else { return }
        _ = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
    }

    static func cleanupExpiredItems() {
        guard let group = try? sharedKeychainAccessGroup(),
              let rows = try? listedItems(group: group) else { return }
        _ = try? removeExpiredItems(group: group, rows: rows, now: Date())
    }

    private static func store(_ payload: Data, kind: String) throws -> String {
        guard payload.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        let group = try sharedKeychainAccessGroup()
        let now = Date()
        let rows = try listedItems(group: group)
        guard try removeExpiredItems(group: group, rows: rows, now: now) < maximumOutstandingItems else {
            throw V3SecretHandoffError.capacity
        }
        let token = UUID().uuidString
        guard let record = V3SecretHandoffRecord.encode(kind: kind, payload: payload, createdAt: now) else {
            throw V3SecretHandoffError.malformed
        }
        var query = itemQuery(token, group: group)
        query[kSecValueData as String] = record
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw V3SecretHandoffError.unavailable }
        return token
    }

    private static func consume(_ token: String, kind: String) throws -> Data {
        guard isValidToken(token) else { throw V3SecretHandoffError.invalidToken }
        let group = try sharedKeychainAccessGroup()
        var query = itemQuery(token, group: group)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let record = result as? Data else {
            throw status == errSecItemNotFound ? V3SecretHandoffError.expired : V3SecretHandoffError.unavailable
        }
        let deleteStatus = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
        guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
            throw V3SecretHandoffError.unavailable
        }
        guard let payload = V3SecretHandoffRecord.decode(record, expectedKind: kind, now: Date()) else {
            throw V3SecretHandoffError.expired
        }
        return payload
    }

    static func sharedKeychainAccessGroup() throws -> String {
        guard let task = SecTaskCreateFromSelf(nil),
              let raw = SecTaskCopyValueForEntitlement(task, "keychain-access-groups" as CFString, nil),
              let groups = raw.takeRetainedValue() as? [String],
              let group = groups.first(where: { $0.hasSuffix(".com.kdt.livecontainer.shared") }) else {
            throw V3SecretHandoffError.unavailable
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
        guard status == errSecSuccess else { throw V3SecretHandoffError.unavailable }
        if let rows = result as? [[String: Any]] { return rows }
        if let row = result as? [String: Any] { return [row] }
        throw V3SecretHandoffError.unavailable
    }

    private static func removeExpiredItems(group: String, rows: [[String: Any]], now: Date) throws -> Int {
        var retained = 0
        for row in rows {
            guard let token = row[kSecAttrAccount as String] as? String, isValidToken(token) else { continue }
            guard let createdAt = row[kSecAttrCreationDate as String] as? Date,
                  createdAt <= now, now.timeIntervalSince(createdAt) <= V3SecretHandoffRecord.lifetime else {
                let status = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
                guard status == errSecSuccess || status == errSecItemNotFound else {
                    throw V3SecretHandoffError.unavailable
                }
                continue
            }
            retained += 1
        }
        return retained
    }
}
