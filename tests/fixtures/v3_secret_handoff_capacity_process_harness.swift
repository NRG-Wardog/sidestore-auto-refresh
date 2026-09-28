@main
struct SecretHandoffCapacityProcessHarness {
    static let maximumItems = 32

    static func main() throws {
        let arguments = CommandLine.arguments
        if arguments.count == 4, arguments[1] == "worker" {
            try runWorker(root: URL(fileURLWithPath: arguments[2], isDirectory: true),
                          label: arguments[3])
            return
        }
        if arguments.count == 3, arguments[1] == "consumer" {
            try runConsumer(root: URL(fileURLWithPath: arguments[2], isDirectory: true))
            return
        }

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("v3-handoff-capacity-" + UUID().uuidString, isDirectory: true)
        let records = itemDirectory(root)
        try FileManager.default.createDirectory(at: records, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let createdAt = Date()
        precondition(V3SecretHandoffRecord.encode(kind: "string",
            payload: Data(repeating: 1, count: V3SecretHandoffRecord.maximumPayloadBytes + 1),
            createdAt: createdAt) == nil,
            "the per-record payload byte limit remains enforced")
        for index in 0..<(maximumItems - 1) {
            let file = records.appendingPathComponent("seed-\(index).record")
            try recordData("seed", createdAt: createdAt).write(to: file, options: .atomic)
        }
        let expiredFile = records.appendingPathComponent("expired.record")
        try recordData("expired", createdAt: createdAt.addingTimeInterval(
            -V3SecretHandoffRecord.lifetime - 1)).write(to: expiredFile, options: .atomic)

        let executable = URL(fileURLWithPath: arguments[0])
        let first = Process()
        first.executableURL = executable
        first.arguments = ["worker", root.path, "A"]
        try first.run()
        precondition(waitFor(root.appendingPathComponent("started-A"), timeout: 5),
                     "first process did not reach the shared-store transaction")
        precondition(waitFor(root.appendingPathComponent("checked-A"), timeout: 5),
                     "first process did not hold the admission lock after counting the 31 seeded items")

        let second = Process()
        second.executableURL = executable
        second.arguments = ["worker", root.path, "B"]
        try second.run()
        precondition(waitFor(root.appendingPathComponent("started-B"), timeout: 5),
                     "second process did not attempt shared-store admission")

        // A remains paused inside the real production admission helper after its
        // count. With a process-shared lock, B cannot perform its own count/add
        // until A commits. Without that lock, B sees the same count (31) and
        // finishes successfully before A is released.
        let secondFinishedBeforeFirstCommit = waitFor(
            root.appendingPathComponent("result-B"), timeout: 1)
        try mark(root, "release-A")
        precondition(waitFor(root.appendingPathComponent("result-A"), timeout: 5))
        precondition(waitFor(root.appendingPathComponent("result-B"), timeout: 5))
        first.waitUntilExit()
        second.waitUntilExit()
        precondition(first.terminationStatus == 0 && second.terminationStatus == 0,
                     "a concurrent admission worker failed")

        let resultA = try String(contentsOf: root.appendingPathComponent("result-A"), encoding: .utf8)
        let resultB = try String(contentsOf: root.appendingPathComponent("result-B"), encoding: .utf8)
        precondition(!secondFinishedBeforeFirstCommit,
                     "the second process completed before the first process committed its capacity slot")
        precondition(resultA == "success" && resultB == "capacity",
                     "at 31 live entries, exactly one of two concurrent processes may add the 32nd record")

        let live = try FileManager.default.contentsOfDirectory(at: records,
            includingPropertiesForKeys: nil).filter { $0.pathExtension == "record" }
        var payloadBytes = 0
        for file in live {
            guard let data = try? Data(contentsOf: file),
                  let payload = V3SecretHandoffRecord.decode(data, expectedKind: "string",
                      now: Date()) else {
                preconditionFailure("all admitted keychain records must remain decodable")
            }
            precondition(payload.count <= V3SecretHandoffRecord.maximumPayloadBytes,
                         "a stored secret payload cannot exceed its per-record byte bound")
            payloadBytes += payload.count
        }
        let aggregatePayloadBound = maximumItems * V3SecretHandoffRecord.maximumPayloadBytes
        precondition(live.count == maximumItems && payloadBytes <= aggregatePayloadBound,
                     "the record count and derived aggregate payload bounds must hold after concurrent admission")
        precondition(!FileManager.default.fileExists(atPath: expiredFile.path),
                     "expired entries are purged as part of the locked admission count")

        // The store admission and production consume/discard path use the same
        // flock. Hold the lock in a consumer-like process, start another store,
        // then release the consumer. Both must finish without nesting/deadlock;
        // the store must count after the consumed item has been removed.
        let consumer = Process()
        consumer.executableURL = executable
        consumer.arguments = ["consumer", root.path]
        try consumer.run()
        precondition(waitFor(root.appendingPathComponent("consume-entered"), timeout: 5),
                     "consumer process did not enter the shared lock")
        let third = Process()
        third.executableURL = executable
        third.arguments = ["worker", root.path, "C"]
        try third.run()
        precondition(waitFor(root.appendingPathComponent("started-C"), timeout: 5))
        let storeFinishedBeforeConsume = waitFor(root.appendingPathComponent("result-C"), timeout: 1)
        try mark(root, "release-consume")
        precondition(waitFor(root.appendingPathComponent("result-consume"), timeout: 5))
        precondition(waitFor(root.appendingPathComponent("result-C"), timeout: 5))
        consumer.waitUntilExit()
        third.waitUntilExit()
        precondition(consumer.terminationStatus == 0 && third.terminationStatus == 0,
                     "consume/discard coordination deadlocked or failed")
        let resultC = try String(contentsOf: root.appendingPathComponent("result-C"), encoding: .utf8)
        precondition(!storeFinishedBeforeConsume && resultC == "success",
                     "the waiting store observes the consumer's released capacity slot")
        let finalRecords = try FileManager.default.contentsOfDirectory(at: records,
            includingPropertiesForKeys: nil).filter { $0.pathExtension == "record" }
        var finalPayloadBytes = 0
        for file in finalRecords {
            guard let data = try? Data(contentsOf: file),
                  let payload = V3SecretHandoffRecord.decode(data, expectedKind: "string",
                      now: Date()) else {
                preconditionFailure("consumer/store coordination must leave only valid outstanding records")
            }
            finalPayloadBytes += payload.count
        }
        precondition(finalRecords.count == maximumItems,
                     "consume and a waiting add remain within the shared outstanding-item cap")
        precondition(finalPayloadBytes <= aggregatePayloadBound,
                     "consume/store interleaving also stays within the derived payload-byte bound")
        print("V3_SECRET_HANDOFF_CROSS_PROCESS_CAPACITY_PASS")
    }

    private static func runWorker(root: URL, label: String) throws {
        try mark(root, "started-\(label)")
        do {
            let token = try V3SecretHandoffStoreAdmission.add(
                containerRoot: root, maximumOutstandingItems: maximumItems,
                liveItemCount: {
                    let count = try liveItemCount(root: root)
                    try mark(root, "checked-\(label)")
                    if label == "A" {
                        guard waitFor(root.appendingPathComponent("release-A"), timeout: 8) else {
                            throw V3SecretHandoffError.unavailable
                        }
                    }
                    return count
                },
                insert: {
                    let token = UUID().uuidString
                    let file = itemDirectory(root).appendingPathComponent(token + ".record")
                    try recordData(label, createdAt: Date()).write(to: file, options: .atomic)
                    return token
                })
            _ = token
            try mark(root, "result-\(label)", contents: "success")
        } catch V3SecretHandoffError.capacity {
            try mark(root, "result-\(label)", contents: "capacity")
        }
    }

    private static func runConsumer(root: URL) throws {
        try V3AppGroupProcessLock.withLock(containerRoot: root) {
            try mark(root, "consume-entered")
            guard waitFor(root.appendingPathComponent("release-consume"), timeout: 8) else {
                throw V3SecretHandoffError.unavailable
            }
            let records = try FileManager.default.contentsOfDirectory(at: itemDirectory(root),
                includingPropertiesForKeys: nil).filter { $0.pathExtension == "record" }
            guard let selected = records.first else { throw V3SecretHandoffError.expired }
            try FileManager.default.removeItem(at: selected)
        }
        try mark(root, "result-consume")
    }

    private static func liveItemCount(root: URL) throws -> Int {
        let records = try FileManager.default.contentsOfDirectory(at: itemDirectory(root),
            includingPropertiesForKeys: nil).filter { $0.pathExtension == "record" }
        let now = Date()
        var count = 0
        for file in records {
            guard let data = try? Data(contentsOf: file),
                  V3SecretHandoffRecord.decode(data, expectedKind: "string", now: now) != nil else {
                try FileManager.default.removeItem(at: file)
                continue
            }
            count += 1
        }
        return count
    }

    private static func itemDirectory(_ root: URL) -> URL {
        root.appendingPathComponent("keychain-items", isDirectory: true)
    }

    private static func recordData(_ label: String, createdAt: Date) -> Data {
        let byte = label.utf8.first ?? 0
        return V3SecretHandoffRecord.encode(kind: "string",
            payload: Data(repeating: byte, count: V3SecretHandoffRecord.maximumPayloadBytes),
            createdAt: createdAt)!
    }

    private static func mark(_ root: URL, _ name: String, contents: String = "ready") throws {
        try Data(contents.utf8).write(to: root.appendingPathComponent(name), options: .atomic)
    }

    private static func waitFor(_ file: URL, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if FileManager.default.fileExists(atPath: file.path) { return true }
            usleep(1_000)
        }
        return FileManager.default.fileExists(atPath: file.path)
    }
}
