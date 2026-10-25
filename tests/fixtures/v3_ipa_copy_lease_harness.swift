import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

struct CombinedIPAFileError: Error {
    enum Problem { case invalidToken, fileAccess, stagingFailed }
    let problem: Problem
    init(_ problem: Problem) { self.problem = problem }
}

// Inject the exact open/flock scheduling edge: the descriptor remains open,
// but a cleaner unlinks the name before the writer acquires its lock.
enum LeaseFault { static var unlinkAfterOpen = false }
func open(_ path: String, _ flags: Int32, _ mode: mode_t) -> Int32 {
    #if canImport(Darwin)
    let fd = Darwin.open(path, flags, mode)
    #else
    let fd = Glibc.open(path, flags, mode)
    #endif
    if LeaseFault.unlinkAfterOpen && fd >= 0 {
        LeaseFault.unlinkAfterOpen = false
        _ = unlink(path)
    }
    return fd
}

enum V3IPAStaging {
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3IPAStaging"]
    static let orphanRetention: TimeInterval = 24 * 60 * 60
    $LEASE_METHODS$
}
extension V3IPAStaging {
    static func leaseForTest(token: String, directory: URL, create: Bool) throws -> Int32? {
        try acquireCopyLease(token: token, directory: directory, create: create)
    }
}

@main struct IPACopyLeaseHarness {
    static func main() throws {
        if CommandLine.arguments.count == 4 && CommandLine.arguments[1] == "hold" {
            let directory = URL(fileURLWithPath: CommandLine.arguments[2])
            let token = CommandLine.arguments[3]
            guard let fd = try V3IPAStaging.leaseForTest(token: token, directory: directory, create: false) else {
                preconditionFailure("child failed to acquire copy lease")
            }
            _ = fd // Deliberately do not close/unlock: test OS crash-style release.
            FileHandle.standardOutput.write(Data("LOCKED\n".utf8))
            _ = FileHandle.standardInput.readData(ofLength: 1)
            _exit(0)
        }
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let directory = V3IPAStaging.stagingDirectory(containerRoot: root)
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }
        let old = Date().addingTimeInterval(-V3IPAStaging.orphanRetention - 60)
        func record(oldLease: Bool = true, partial: Bool = true) throws -> (String, URL, URL) {
            let token = UUID().uuidString.lowercased()
            let lease = directory.appendingPathComponent(token + ".lease")
            let payload = directory.appendingPathComponent(token + ".partial")
            try Data().write(to: lease)
            if partial { try Data([1, 2, 3]).write(to: payload) }
            if oldLease { try fm.setAttributes([.modificationDate: old], ofItemAtPath: lease.path) }
            return (token, lease, payload)
        }
        let active = try record()
        let child = Process(); let input = Pipe(); let output = Pipe()
        child.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        child.arguments = ["hold", directory.path, active.0]
        child.standardInput = input; child.standardOutput = output
        try child.run()
        let handshake = output.fileHandleForReading.readData(ofLength: 7)
        precondition(handshake == Data("LOCKED\n".utf8))
        let whileAlive = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [])
        precondition(whileAlive == 0 && fm.fileExists(atPath: active.2.path),
                     "orphan cleanup deleted another process's active copy")
        input.fileHandleForWriting.write(Data([1])); child.waitUntilExit()
        precondition(child.terminationStatus == 0)
        let afterExit = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [])
        precondition(afterExit == 1 && !fm.fileExists(atPath: active.1.path) && !fm.fileExists(atPath: active.2.path),
                     "abandoned partial and lease were not reclaimed after process exit")

        let lockOnly = try record(partial: false)
        let lockRemoved = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [])
        precondition(lockRemoved == 1 && !fm.fileExists(atPath: lockOnly.1.path))
        let recent = try record(oldLease: false)
        let preserved = try record()
        let keep = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [preserved.0])
        precondition(keep == 0 && fm.fileExists(atPath: recent.2.path) && fm.fileExists(atPath: preserved.2.path))
        try fm.removeItem(at: recent.1); try fm.removeItem(at: recent.2)
        try fm.removeItem(at: preserved.1); try fm.removeItem(at: preserved.2)

        let sentinel = root.appendingPathComponent("keep.txt")
        try Data("KEEP".utf8).write(to: sentinel)
        let symlinkLease = directory.appendingPathComponent(UUID().uuidString.lowercased() + ".lease")
        try fm.createSymbolicLink(at: symlinkLease, withDestinationURL: sentinel)
        let unsafePartial = try record(partial: false)
        try fm.createSymbolicLink(at: unsafePartial.2, withDestinationURL: sentinel)
        let unsafeRemoved = try V3IPAStaging.cleanupOrphans(containerRoot: root, preservingTokens: [])
        let sentinelBytes = try Data(contentsOf: sentinel)
        precondition(unsafeRemoved == 0 && sentinelBytes == Data("KEEP".utf8))

        let pausedToken = UUID().uuidString.lowercased()
        LeaseFault.unlinkAfterOpen = true
        do {
            _ = try V3IPAStaging.leaseForTest(token: pausedToken, directory: directory, create: true)
            preconditionFailure("writer accepted an unlinked lease descriptor")
        } catch let failure as CombinedIPAFileError {
            precondition(failure.problem == .stagingFailed)
        }
        precondition(!fm.fileExists(atPath: directory.appendingPathComponent(pausedToken + ".lease").path))
        print("V3_IPA_COPY_LEASE_PASS")
    }
}
