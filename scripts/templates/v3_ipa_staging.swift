import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

// IPA bytes live only in a private directory inside the shared App Group.
// XPC carries a canonical UUID token; the service derives every path itself.
enum V3IPAStaging {
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3IPAStaging"]
    /// The packaged group name, retained for diagnostics and for the packaged
    /// fallback ranking. It is never used as a fixed runtime group: a re-signed
    /// build may only be entitled to a team-suffixed variant, or to the group
    /// LiveContainer itself selected.
    static let sideStoreAppGroupIdentifier = V3SharedAppGroup.packagedGroup
    static let orphanRetention: TimeInterval = 24 * 60 * 60

    /// Staging, the secret handoff lock, the recovery journal, the service
    /// Keychain lock and the cross-process refresh store all resolve the group
    /// through V3SharedAppGroup, so they cannot end up in different containers.
    /// This entry point takes every input explicitly and never reads process
    /// state, so the same call with the same facts always yields the same
    /// container in both processes.
    static func sharedIdentity(selectedGroup: String? = nil,
                               inheritedGroup: String? = nil,
                               bundleInfo: [String: Any],
                               resolveContainer: (String) -> URL?) -> V3SharedAppGroup.Identity? {
        V3SharedAppGroup.identity(selectedGroup: selectedGroup, inheritedGroup: inheritedGroup,
                                  usesEnvironment: false, bundleInfo: bundleInfo,
                                  resolveContainer: resolveContainer)
    }

    static func sideStoreContainerRoot(bundleInfo: [String: Any],
                                       selectedGroup: String? = nil,
                                       resolveContainer: (String) -> URL?) -> URL? {
        sharedIdentity(selectedGroup: selectedGroup, bundleInfo: bundleInfo,
                       resolveContainer: resolveContainer)?.containerRoot
    }

    static func sideStoreContainerRoot(bundle: Bundle = .main,
                                       fileManager: FileManager = .default,
                                       selectedGroup: String? = nil) -> URL? {
        V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup, bundle: bundle,
                                         fileManager: fileManager)?.containerRoot
    }

    private final class CopyStatus: @unchecked Sendable {
        private let lock = NSLock()
        private var failed = false
        func markFailed() { lock.withLock { failed = true } }
        var didFail: Bool { lock.withLock { failed } }
    }

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

    private static func removePartial(_ file: URL?, directory: URL?, fileManager: FileManager) {
        guard let file, let directory,
              file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isSymbolicLinkKey]),
              values.isSymbolicLink != true,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return }
        try? fileManager.removeItem(at: file)
    }

    private static func copyLeaseURL(token: String, directory: URL) -> URL {
        directory.appendingPathComponent(token + ".lease", isDirectory: false)
    }

    /// A per-token flock survives actor/process scheduling and is released by
    /// the OS after a crash. Never wait for an active copy during orphan cleanup.
    private static func acquireCopyLease(token: String, directory: URL, create: Bool) throws -> Int32? {
        _ = try canonicalToken(token)
        let lease = copyLeaseURL(token: token, directory: directory)
        let flags = O_RDWR | O_NOFOLLOW | O_CLOEXEC | (create ? O_CREAT | O_EXCL : 0)
        let descriptor = open(lease.path, flags, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else {
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            _ = close(descriptor)
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        var opened = stat()
        var named = stat()
        // A writer may have paused between open and flock while a cleaner
        // acquired/unlinked the lease. It must not copy through that stale FD.
        guard fstat(descriptor, &opened) == 0, lstat(lease.path, &named) == 0,
              opened.st_dev == named.st_dev, opened.st_ino == named.st_ino,
              opened.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              !create || fchmod(descriptor, S_IRUSR | S_IWUSR) == 0 else {
            _ = flock(descriptor, LOCK_UN)
            _ = close(descriptor)
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        return descriptor
    }

    private static func releaseCopyLease(_ descriptor: Int32) {
        _ = flock(descriptor, LOCK_UN)
        _ = close(descriptor)
    }

    /// Inputs are value snapshots: no store, picker, or mutable UI ownership
    /// crosses into this detached worker. Security scope and coordination stay
    /// inside stage() until the synchronous copy has fully returned.
    static func stageOffMainActor(sourceURL: URL, bookmark: Data? = nil, containerRoot: URL) async throws -> String {
        let worker = Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let token = try stage(sourceURL: sourceURL, bookmark: bookmark, containerRoot: containerRoot)
            guard !Task.isCancelled else {
                // Cancellation cannot interrupt FileManager.copyItem safely.
                // This token has no host/service owner until we return it.
                try? cleanup(token: token, containerRoot: containerRoot)
                throw CancellationError()
            }
            return token
        }
        return try await withTaskCancellationHandler(operation: {
            try await worker.value
        }, onCancel: {
            worker.cancel()
        })
    }

    /// Only for a completed token which was never handed to an install attempt.
    /// Active or terminal backend tokens still use the service lease checks.
    static func cleanupUnclaimedOffMainActor(token: String, containerRoot: URL) async {
        await Task.detached(priority: .utility) {
            try? cleanup(token: token, containerRoot: containerRoot)
        }.value
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
        var partialDirectory: URL?
        var partialDestination: URL?
        do {
            let sourceValues = try source.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard sourceValues.isSymbolicLink != true, sourceValues.isRegularFile == true else { throw CombinedIPAFileError(.fileAccess) }
            guard (sourceValues.fileSize ?? 0) > 0 else { throw CombinedIPAFileError(.emptyFile) }
            let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
            partialDirectory = directory
            let token = UUID().uuidString.lowercased()
            let destination = try url(token: token, directory: directory)
            guard !fileManager.fileExists(atPath: destination.path) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            // An in-flight copy is not a published IPA token. In particular,
            // an old source mtime must not let orphan pruning delete a file
            // while the provider/FileManager is still writing it.
            let partial = directory.appendingPathComponent(token + ".partial", isDirectory: false)
            guard !fileManager.fileExists(atPath: partial.path) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            guard let lease = try acquireCopyLease(token: token, directory: directory, create: true) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            defer {
                // Keep the lease if cleanup still owes a partial file. A later
                // orphan pass can reclaim both after proving no process owns it.
                if !fileManager.fileExists(atPath: partial.path) {
                    try? fileManager.removeItem(at: copyLeaseURL(token: token, directory: directory))
                }
                releaseCopyLease(lease)
            }
            partialDestination = partial
            defer { removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager) }
            let coordinator = NSFileCoordinator(filePresenter: nil)
            var coordinationError: NSError?
            let copyStatus = CopyStatus()
            coordinator.coordinate(readingItemAt: source, options: [], error: &coordinationError) { readableURL in
                do { try fileManager.copyItem(at: readableURL, to: partial) }
                catch { copyStatus.markFailed() }
            }
            guard coordinationError == nil, !copyStatus.didFail else { throw CombinedIPAFileError(.stagingFailed) }
            try fileManager.setAttributes([.posixPermissions: 0o600, .modificationDate: Date()],
                                          ofItemAtPath: partial.path)
            try requireRegularNonEmptyFile(partial, fileManager: fileManager)
            try fileManager.moveItem(at: partial, to: destination)
            partialDestination = nil
            return token
        } catch let error as CombinedIPAFileError {
            removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager)
            throw error
        } catch {
            removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager)
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

    /// Recover canonical IPA files absent from the ownership snapshot, plus
    /// abandoned partial-copy records whose per-token lease can be acquired.
    /// Age alone never establishes that a copy or backend token is unowned.
    @discardableResult
    static func cleanupOrphans(containerRoot: URL, preservingTokens: Set<String>, now: Date = Date(),
                               fileManager: FileManager = .default) throws -> Int {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let files: [URL]
        do {
            files = try fileManager.contentsOfDirectory(at: directory,
                includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey],
                options: [.skipsHiddenFiles])
        } catch {
            throw CombinedIPAFileError(.stagingFailed)
        }
        var removed = 0
        for file in files {
            guard file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL else { continue }
            if file.pathExtension == "lease" {
                let token = file.deletingPathExtension().lastPathComponent
                guard (try? canonicalToken(token)) == token, !preservingTokens.contains(token),
                      let lease = try acquireCopyLease(token: token, directory: directory, create: false) else { continue }
                defer { releaseCopyLease(lease) }
                // Re-read age after taking the lock, and only touch a sibling
                // regular partial belonging to this exact canonical token.
                guard let values = try? file.resourceValues(forKeys: [.contentModificationDateKey]),
                      let modified = values.contentModificationDate,
                      now.timeIntervalSince(modified) >= orphanRetention else { continue }
                let partial = directory.appendingPathComponent(token + ".partial", isDirectory: false)
                do {
                    if fileManager.fileExists(atPath: partial.path) {
                        let partialValues = try partial.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
                        guard partialValues.isRegularFile == true, partialValues.isSymbolicLink != true,
                              partial.resolvingSymlinksInPath().standardizedFileURL == partial else { continue }
                        try fileManager.removeItem(at: partial)
                    }
                    try fileManager.removeItem(at: file)
                    removed += 1
                } catch { continue }
                continue
            }
            guard file.pathExtension == "ipa" else { continue }
            let token = file.deletingPathExtension().lastPathComponent
            guard (try? canonicalToken(token)) == token,
                  !preservingTokens.contains(token),
                  !fileManager.fileExists(atPath: copyLeaseURL(token: token, directory: directory).path),
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true,
                  let modified = values.contentModificationDate,
                  now.timeIntervalSince(modified) >= orphanRetention,
                  file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { continue }
            do {
                try fileManager.removeItem(at: file)
                removed += 1
            } catch {
                // One undeletable orphan must not block staging or cleanup for
                // the remaining canonical files.
                continue
            }
        }
        return removed
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
