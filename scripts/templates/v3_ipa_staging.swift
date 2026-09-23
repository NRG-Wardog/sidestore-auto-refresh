import Foundation

// IPA bytes live only in a private directory inside the shared App Group.
// XPC carries a canonical UUID token; the service derives every path itself.
enum V3IPAStaging {
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3IPAStaging"]

    static func canonicalToken(_ token: String) throws -> String {
        guard token.utf8.count == 36,
              let value = UUID(uuidString: token),
              value.uuidString.lowercased() == token else {
            throw CombinedIPAFileError(.invalidToken)
        }
        return token
    }

    static func stagingDirectory(containerRoot: URL) -> URL {
        directoryComponents.reduce(containerRoot.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
    }

    private static func ensureDirectory(containerRoot: URL, fileManager: FileManager) throws -> URL {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        do {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw CombinedIPAFileError(.fileAccess)
            }
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            return directory
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.stagingFailed)
        }
    }

    private static func url(token: String, directory: URL) throws -> URL {
        let canonical = try canonicalToken(token)
        let candidate = directory.appendingPathComponent(canonical + ".ipa", isDirectory: false).standardizedFileURL
        guard candidate.deletingLastPathComponent() == directory.standardizedFileURL,
              candidate.lastPathComponent == canonical + ".ipa" else {
            throw CombinedIPAFileError(.invalidToken)
        }
        return candidate
    }

    private static func requireRegularNonEmptyFile(_ file: URL, fileManager: FileManager) throws {
        do {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values.isSymbolicLink != true, values.isRegularFile == true else {
                throw CombinedIPAFileError(.missingFile)
            }
            guard (values.fileSize ?? 0) > 0 else { throw CombinedIPAFileError(.emptyFile) }
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.missingFile)
        }
    }

    static func stage(sourceURL: URL, bookmark: Data? = nil, containerRoot: URL,
                      fileManager: FileManager = .default) throws -> String {
        var source = sourceURL
        if let bookmark {
            var stale = false
            do {
                source = try URL(resolvingBookmarkData: bookmark, options: .withoutUI,
                                 relativeTo: nil, bookmarkDataIsStale: &stale)
            } catch {
                throw CombinedIPAFileError(.fileAccess)
            }
            _ = stale // A stale bookmark is usable only for this immediate copy.
        }
        guard source.isFileURL, source.pathExtension.lowercased() == "ipa" else {
            throw CombinedIPAFileError(.invalidPackage)
        }

        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        do {
            let sourceValues = try source.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            guard sourceValues.isRegularFile == true else { throw CombinedIPAFileError(.fileAccess) }
            guard (sourceValues.fileSize ?? 0) > 0 else { throw CombinedIPAFileError(.emptyFile) }
            let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
            let token = UUID().uuidString.lowercased()
            let destination = try url(token: token, directory: directory)
            let coordinator = NSFileCoordinator(filePresenter: nil)
            var coordinationError: NSError?
            var copyFailed = false
            coordinator.coordinate(readingItemAt: source, options: [], error: &coordinationError) { readableURL in
                do { try fileManager.copyItem(at: readableURL, to: destination) }
                catch { copyFailed = true }
            }
            guard coordinationError == nil, !copyFailed else { throw CombinedIPAFileError(.stagingFailed) }
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
            try requireRegularNonEmptyFile(destination, fileManager: fileManager)
            return token
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.stagingFailed)
        }
    }

    static func resolve(token: String, containerRoot: URL,
                        fileManager: FileManager = .default) throws -> URL {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let file = try url(token: token, directory: directory)
        try requireRegularNonEmptyFile(file, fileManager: fileManager)
        guard file.resolvingSymlinksInPath().standardizedFileURL == file else {
            throw CombinedIPAFileError(.missingFile)
        }
        return file
    }

    static func cleanup(token: String, containerRoot: URL,
                        fileManager: FileManager = .default) throws {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let file = try url(token: token, directory: directory)
        guard fileManager.fileExists(atPath: file.path) else { return }
        do {
            let values = try file.resourceValues(forKeys: [.isSymbolicLinkKey])
            guard values.isSymbolicLink != true,
                  file.resolvingSymlinksInPath().standardizedFileURL == file else {
                throw CombinedIPAFileError(.fileAccess)
            }
            try fileManager.removeItem(at: file)
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.fileAccess)
        }
    }

    static func inspect<T>(token: String, containerRoot: URL,
                           fileManager: FileManager = .default,
                           readMetadata: (URL) throws -> T) throws -> T {
        let file = try resolve(token: token, containerRoot: containerRoot, fileManager: fileManager)
        do { return try readMetadata(file) }
        catch let error as CombinedIPAFileError { throw error }
        catch { throw CombinedIPAFileError(.invalidPackage) }
    }
}
