@main struct ProcessLockHarness {
    static func append(_ text: String, to url: URL) throws {
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: Data())
        }
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data((text + "\n").utf8))
    }

    static func withProductionLock(_ lockName: String, root: URL, body: () throws -> Void) throws {
        switch lockName {
        case "embedded":
            try LCSharedKeychainFileLock.withLock(appGroup: nil, containerRoot: root, body)
        case "handoff":
            try V3AppGroupProcessLock.withLock(containerRoot: root, body)
        default:
            throw NSError(domain: "KeychainProcessLockHarness", code: 1)
        }
    }

    static func main() throws {
        let arguments = CommandLine.arguments
        if arguments.count > 1 && arguments[1] == "worker" {
            let lockName = arguments[2]
            let label = arguments[3]
            let root = URL(fileURLWithPath: arguments[4], isDirectory: true)
            let events = root.appendingPathComponent("events.txt")
            try withProductionLock(lockName, root: root) {
                try append("start-" + label, to: events)
                usleep(250_000)
                try append("end-" + label, to: events)
            }
            return
        }

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("keychain-process-lock-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let executable = URL(fileURLWithPath: arguments[0])
        let workers = [("embedded", "A"), ("handoff", "B")].map { pair in
            let (lockName, label) = pair
            let process = Process()
            process.executableURL = executable
            process.arguments = ["worker", lockName, label, root.path]
            return process
        }
        for worker in workers { try worker.run() }
        for worker in workers {
            worker.waitUntilExit()
            precondition(worker.terminationStatus == 0, "lock worker failed")
        }
        let events = try String(contentsOf: root.appendingPathComponent("events.txt"), encoding: .utf8)
            .split(whereSeparator: \.isNewline).map(String.init)
        precondition(events.count == 4)
        precondition(events[0].hasPrefix("start-") && events[1] == "end-" + String(events[0].dropFirst(6)),
            "a competing process entered the shared transaction before its predecessor exited")
        precondition(events[2].hasPrefix("start-") && events[3] == "end-" + String(events[2].dropFirst(6)))
        print("KEYCHAIN_PROCESS_LOCK_PASS")
    }
}
