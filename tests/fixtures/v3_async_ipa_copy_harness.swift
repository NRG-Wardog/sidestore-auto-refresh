import Foundation

struct CombinedIPAFileError: Error {
    enum Problem { case fileAccess, stagingFailed, invalidToken, missingFile, emptyFile, invalidPackage }
    let problem: Problem
    init(_ problem: Problem) { self.problem = problem }
}
enum V3SharedAppGroup {
    static let packagedGroup = "group.test"
    struct Identity { let containerRoot: URL }
    static func identity(selectedGroup: String?, inheritedGroup: String?, usesEnvironment: Bool,
                         bundleInfo: [String: Any], resolveContainer: (String) -> URL?) -> Identity? { nil }
    static func runtimeIdentity(selectedGroup: String?, bundle: Bundle, fileManager: FileManager) -> Identity? { nil }
}

$STAGING$

private final class CopyProbe: @unchecked Sendable {
    static let shared = CopyProbe()
    enum Mode { case normal, paused, diskFull, sourceRevoked, coordinatorFailure }
    private let condition = NSCondition()
    private var mode: Mode = .normal
    private var released = false
    private var startedDestination: URL?
    func configure(_ mode: Mode) {
        condition.lock(); defer { condition.unlock() }
        self.mode = mode; released = false; startedDestination = nil
    }
    var failsCoordination: Bool {
        condition.lock(); defer { condition.unlock() }; return mode == .coordinatorFailure
    }
    var destination: URL? {
        condition.lock(); defer { condition.unlock() }; return startedDestination
    }
    func release() {
        condition.lock(); released = true; condition.broadcast(); condition.unlock()
    }
    func begin(_ destination: URL) throws -> Mode {
        precondition(!Thread.isMainThread, "synchronous copy entered the UI thread")
        condition.lock(); defer { condition.unlock() }
        startedDestination = destination
        // Make the incomplete copy resemble a provider that preserves an old
        // source timestamp early, before its last bytes have arrived.
        if mode == .paused || mode == .diskFull {
            try Data([1]).write(to: destination)
            try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 0)],
                                                  ofItemAtPath: destination.path)
        }
        while mode == .paused && !released { condition.wait() }
        return mode
    }
}
// Forward to the real native coordinator except for an injected provider
// coordination failure, where Foundation does not invoke the accessor at all.
private final class CoordinatedFileAccess {
    init(filePresenter: Any?) {}
    func coordinate(readingItemAt source: URL, options: NSFileCoordinator.ReadingOptions,
                    error: inout NSError?, byAccessor accessor: @Sendable (URL) -> Void) {
        if CopyProbe.shared.failsCoordination {
            error = NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError)
            return
        }
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: source,
            options: options, error: &error, byAccessor: accessor)
    }
}
private final class ControlledFileManager: FileManager, @unchecked Sendable {
    override func copyItem(at source: URL, to destination: URL) throws {
        let mode = try CopyProbe.shared.begin(destination)
        if mode == .diskFull {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteOutOfSpaceError)
        }
        if mode == .sourceRevoked { try super.removeItem(at: source) }
        if mode == .paused { try super.removeItem(at: destination) }
        try super.copyItem(at: source, to: destination)
    }
}
extension V3IPAStaging {
    static func stage(sourceURL: URL, bookmark: Data?, containerRoot: URL) throws -> String {
        try stageNative(sourceURL: sourceURL, bookmark: bookmark, containerRoot: containerRoot,
                        fileManager: ControlledFileManager())
    }
}

@main struct AsyncIPACopyHarness {
    @MainActor static func main() async throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let inputRoot = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        try fm.createDirectory(at: inputRoot, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root); try? fm.removeItem(at: inputRoot) }
        let source = inputRoot.appendingPathComponent("large.ipa")
        // Bytes are intentionally not parsed as an archive at this boundary.
        let bytes = Data(repeating: 0x42, count: 16 * 1024 * 1024)
        try bytes.write(to: source)
        let probe = CopyProbe.shared
        probe.configure(.paused)
        let copy = Task { try await V3IPAStaging.stageOffMainActor(sourceURL: source, containerRoot: root) }
        for _ in 0..<500 where probe.destination == nil { try await Task.sleep(nanoseconds: 2_000_000) }
        guard let partial = probe.destination else { preconditionFailure("worker never entered copy") }
        precondition(partial.pathExtension == "partial")
        // This MainActor heartbeat and cancellation run while FileManager is
        // blocked. A MainActor-inherited synchronous copy deadlocks this test.
        var heartbeats = 0
        for _ in 0..<5 { heartbeats += 1; await Task.yield() }
        precondition(heartbeats == 5)
        let lease = partial.deletingPathExtension().appendingPathExtension("lease")
        try fm.setAttributes([.modificationDate: Date(timeIntervalSince1970: 0)], ofItemAtPath: lease.path)
        let pruned = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [])
        precondition(pruned == 0 && fm.fileExists(atPath: partial.path),
                     "orphan pruning touched the still-running copy")
        copy.cancel()
        precondition(fm.fileExists(atPath: partial.path), "UI cancellation deleted an in-use partial file")
        probe.release()
        do { _ = try await copy.value; preconditionFailure("cancelled worker published a token") }
        catch is CancellationError {}
        let directory = V3IPAStaging.stagingDirectory(containerRoot: root)
        let afterCancel = try fm.contentsOfDirectory(atPath: directory.path)
        precondition(afterCancel.isEmpty, "cancelled worker left partial or completed IPA bytes")

        // The same selected IPA can be staged again without stale ownership.
        probe.configure(.normal)
        let token = try await V3IPAStaging.stageOffMainActor(sourceURL: source, containerRoot: root)
        let staged = try V3IPAStaging.resolve(token: token, containerRoot: root)
        let stagedBytes = try Data(contentsOf: staged)
        precondition(stagedBytes == bytes)
        let permissions = try fm.attributesOfItem(atPath: staged.path)[.posixPermissions] as! NSNumber
        precondition(permissions.intValue == 0o600)
        await V3IPAStaging.cleanupUnclaimedOffMainActor(token: token, containerRoot: root)
        precondition(!fm.fileExists(atPath: staged.path))

        // Real stage() cleanup runs after a partial-copy disk-full error and
        // after the provider revokes/removes its source during coordination.
        for mode in [CopyProbe.Mode.diskFull, .coordinatorFailure, .sourceRevoked] {
            probe.configure(mode)
            do {
                _ = try await V3IPAStaging.stageOffMainActor(sourceURL: source, containerRoot: root)
                preconditionFailure("failed I/O returned a token")
            } catch let error as CombinedIPAFileError {
                precondition(error.problem == .stagingFailed)
            }
            let afterFailure = try fm.contentsOfDirectory(atPath: directory.path)
            precondition(afterFailure.isEmpty)
        }
        print("V3_ASYNC_IPA_COPY_PASS")
    }
}
