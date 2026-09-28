import Foundation

// The harness compiles the production V3OperationRecoveryJournal and
// V3AppGroupProcessLock declarations into this file. The only environment
// substitution is passing this temporary root through their existing
// `containerRoot` parameter.
extension Bundle {
    var altstoreAppGroup: String? { nil }
}

@main
struct OperationRecoveryJournalHarness {
    static func main() throws {
        if CommandLine.arguments.count == 5, CommandLine.arguments[1] == "--verify" {
            try verifyFreshProcess(root: URL(fileURLWithPath: CommandLine.arguments[2]),
                sessionID: CommandLine.arguments[3], kind: CommandLine.arguments[4])
            return
        }
        if CommandLine.arguments.count == 4, CommandLine.arguments[1] == "--verify-refresh" {
            try verifyFreshRefreshProcess(root: URL(fileURLWithPath: CommandLine.arguments[2]),
                runID: CommandLine.arguments[3])
            return
        }
        if CommandLine.arguments.count == 5, CommandLine.arguments[1] == "--lock-probe" {
            let root = URL(fileURLWithPath: CommandLine.arguments[2])
            let started = URL(fileURLWithPath: CommandLine.arguments[3])
            let marker = URL(fileURLWithPath: CommandLine.arguments[4])
            try Data("started".utf8).write(to: started, options: .atomic)
            try V3AppGroupProcessLock.withLock(containerRoot: root) {
                try Data("locked".utf8).write(to: marker, options: .atomic)
            }
            return
        }

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("v3-operation-journal-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        try testNonInstallSchemaAndTerminalRemoval(root: root)
        try testRefreshAdmissionSurvivesServiceRecreation(root: root)
        try testPreparedCancellationAndDispatchedProtection(root: root)
        try testStagedIPATokenPersistsUntilSettledTerminal(root: root)
        try testProcessSharedLockWithTemporaryRoot(root: root)
        print("V3_OPERATION_RECOVERY_JOURNAL_PASS")
    }

    private static func recordURL(root: URL) -> URL {
        ["Library", "Application Support", "LiveContainer", "operation-recovery.plist"]
            .reduce(root.standardizedFileURL) { $0.appendingPathComponent($1) }
    }

    private static func testNonInstallSchemaAndTerminalRemoval(root: URL) throws {
        let sessionID = UUID().uuidString
        let reserved = try V3OperationRecoveryJournal.reserve(sessionID: sessionID, kind: "delete",
            containerRoot: root)
        precondition(reserved)
        let url = recordURL(root: root)
        let data = try Data(contentsOf: url)
        let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as! [String: Any]
        precondition(Set(plist.keys) == Set(["version", "session", "kind", "phase"]))
        precondition(V3OperationRecoveryRecord.decodePropertyList(plist)?.stagedIPAToken == nil)
        let dispatched = try V3OperationRecoveryJournal.beginDispatch(sessionID: sessionID, kind: "delete",
            containerRoot: root)
        precondition(dispatched)
        try verifyInFreshProcess(root: root, sessionID: sessionID, kind: "delete")
        let wrongTerminalSettled = try V3OperationRecoveryJournal.settle(sessionID: sessionID,
            replySessionID: UUID().uuidString, state: "completed", backendSettled: true,
            containerRoot: root)
        precondition(!wrongTerminalSettled, "a non-correlated reply must leave the durable record intact")
        let terminalSettled = try V3OperationRecoveryJournal.settle(sessionID: sessionID,
            replySessionID: sessionID, state: "completed", backendSettled: true,
            containerRoot: root)
        precondition(terminalSettled)
        let remainingRecord = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(remainingRecord == nil)
        precondition(!FileManager.default.fileExists(atPath: url.path),
            "a correlated terminal removes the durable plist")
    }

    private static func testStagedIPATokenPersistsUntilSettledTerminal(root: URL) throws {
        let sessionID = UUID().uuidString
        let token = UUID().uuidString.lowercased()
        let staging = ["Library", "Application Support", "LiveContainer", "V3IPAStaging"]
            .reduce(root.standardizedFileURL) { $0.appendingPathComponent($1, isDirectory: true) }
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        let stagedFile = staging.appendingPathComponent(token + ".ipa")
        try Data("temporary harness bytes".utf8).write(to: stagedFile, options: .atomic)

        let reserved = try V3OperationRecoveryJournal.reserve(sessionID: sessionID,
            kind: "installSharedIPA", stagedIPAToken: token, containerRoot: root)
        precondition(reserved)
        let dispatched = try V3OperationRecoveryJournal.beginDispatch(sessionID: sessionID,
            kind: "installSharedIPA", stagedIPAToken: token, containerRoot: root)
        precondition(dispatched)
        try verifyInFreshProcess(root: root, sessionID: sessionID, kind: "installSharedIPA")
        let recoveredRecord = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(recoveredRecord?.stagedIPAToken == token)
        precondition(FileManager.default.fileExists(atPath: stagedFile.path),
            "the staged token's local file remains present while the operation is unresolved")
        let unsettledTerminalAccepted = try V3OperationRecoveryJournal.settle(sessionID: sessionID,
            replySessionID: sessionID, state: "completed", backendSettled: false,
            containerRoot: root)
        precondition(!unsettledTerminalAccepted, "an unsettled response cannot release the IPA token")
        precondition(FileManager.default.fileExists(atPath: stagedFile.path))
        let terminalSettled = try V3OperationRecoveryJournal.settle(sessionID: sessionID,
            replySessionID: sessionID, state: "completed", backendSettled: true,
            containerRoot: root)
        precondition(terminalSettled)
        try FileManager.default.removeItem(at: stagedFile)
        precondition(!FileManager.default.fileExists(atPath: stagedFile.path),
            "the local IPA can be removed after the correlated settled terminal")
    }

    private static func testPreparedCancellationAndDispatchedProtection(root: URL) throws {
        let preparedID = UUID().uuidString
        let reserved = try V3OperationRecoveryJournal.reserve(sessionID: preparedID, kind: "delete",
            containerRoot: root)
        precondition(reserved)
        let cancelled = try V3OperationRecoveryJournal.clearPreparedAfterConfirmedCancellation(
            sessionID: preparedID, replySessionID: preparedID, state: "cancelled",
            backendSettled: true, stopConfirmed: true, knownStarted: false, containerRoot: root)
        precondition(cancelled)
        let afterCancel = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(afterCancel == nil)

        let dispatchedID = UUID().uuidString
        let dispatchedReservation = try V3OperationRecoveryJournal.reserve(sessionID: dispatchedID,
            kind: "delete", containerRoot: root)
        precondition(dispatchedReservation)
        let didDispatch = try V3OperationRecoveryJournal.beginDispatch(sessionID: dispatchedID,
            kind: "delete", containerRoot: root)
        precondition(didDispatch)
        let incorrectlyCleared = try V3OperationRecoveryJournal.clearPreparedAfterConfirmedCancellation(
            sessionID: dispatchedID, replySessionID: dispatchedID, state: "cancelled",
            backendSettled: true, stopConfirmed: true, knownStarted: false, containerRoot: root)
        precondition(!incorrectlyCleared)
        let retained = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(retained?.sessionID == dispatchedID && retained?.phase == .dispatched)
        _ = try V3OperationRecoveryJournal.reconcileAfterDeviceCheck(sessionID: dispatchedID,
            userConfirmed: true, containerRoot: root)
    }

    private static func testRefreshAdmissionSurvivesServiceRecreation(root: URL) throws {
        let runID = UUID().uuidString
        let reserved = try V3OperationRecoveryJournal.reserve(sessionID: runID, kind: "refreshAll",
            containerRoot: root)
        precondition(reserved)
        let persisted = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(persisted?.sessionID == runID && persisted?.kind == "refreshAll" &&
            persisted?.phase == .prepared)
        try verifyRefreshInFreshProcess(root: root, runID: runID)

        // A new service process has no in-memory owner. Reconstructing from the
        // durable record marks the lease uncertain and keeps mutation admission
        // closed until an exact terminal or explicit device check arrives.
        var restartedLease = V3RefreshAdmissionLease()
        precondition(restartedLease.restoreLost(runID: persisted!.sessionID))
        precondition(restartedLease.ownerLost && restartedLease.isActive)
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: true, refreshActive: restartedLease.isActive))
        let rejectedReconcile = try V3OperationRecoveryJournal.reconcileRefreshAdmissionAfterDeviceCheck(
            runID: runID, userConfirmed: false, containerRoot: root)
        precondition(!rejectedReconcile)
        let stillOwned = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(stillOwned?.sessionID == runID)
        let settled = try V3OperationRecoveryJournal.settleRefreshAdmission(runID: runID,
            terminalState: "failed", terminalConfirmed: true, containerRoot: root)
        precondition(settled)
        let released = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(released == nil)

        let checkedRunID = UUID().uuidString
        let checkedReservation = try V3OperationRecoveryJournal.reserve(sessionID: checkedRunID,
            kind: "refreshAll", containerRoot: root)
        precondition(checkedReservation)
        let reconciled = try V3OperationRecoveryJournal.reconcileRefreshAdmissionAfterDeviceCheck(
            runID: checkedRunID, userConfirmed: true, containerRoot: root)
        precondition(reconciled)
        let checkedReleased = try V3OperationRecoveryJournal.current(containerRoot: root)
        precondition(checkedReleased == nil)
    }

    private static func verifyRefreshInFreshProcess(root: URL, runID: String) throws {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        child.arguments = ["--verify-refresh", root.path, runID]
        try child.run()
        child.waitUntilExit()
        precondition(child.terminationStatus == 0,
            "a fresh service process must restore the active refresh reservation")
    }

    private static func verifyFreshRefreshProcess(root: URL, runID: String) throws {
        guard let record = try V3OperationRecoveryJournal.current(containerRoot: root),
              record.sessionID == runID, record.kind == "refreshAll", record.phase == .prepared else {
            throw NSError(domain: "V3OperationRecoveryHarness", code: 5)
        }
        var lease = V3RefreshAdmissionLease()
        guard lease.restoreLost(runID: record.sessionID), lease.ownerLost,
              lease.isActive,
              !V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
                anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
                responseCapacityAvailable: true, refreshActive: lease.isActive) else {
            throw NSError(domain: "V3OperationRecoveryHarness", code: 6)
        }
    }

    private static func testProcessSharedLockWithTemporaryRoot(root: URL) throws {
        let started = root.appendingPathComponent("lock-child-started")
        let marker = root.appendingPathComponent("lock-child-acquired")
        let child = Process()
        child.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        child.arguments = ["--lock-probe", root.path, started.path, marker.path]

        try V3AppGroupProcessLock.withLock(containerRoot: root) {
            try child.run()
            let startDeadline = Date().addingTimeInterval(5)
            while !FileManager.default.fileExists(atPath: started.path) && Date() < startDeadline {
                Thread.sleep(forTimeInterval: 0.01)
            }
            precondition(FileManager.default.fileExists(atPath: started.path),
                "the second process must start while the first owns flock")
            Thread.sleep(forTimeInterval: 0.15)
            precondition(!FileManager.default.fileExists(atPath: marker.path),
                "the second process must wait for the process-shared lock")
        }
        child.waitUntilExit()
        precondition(child.terminationStatus == 0)
        precondition(FileManager.default.fileExists(atPath: marker.path),
            "the second process acquires the lock after the first releases it")
    }

    private static func verifyInFreshProcess(root: URL, sessionID: String, kind: String) throws {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        child.arguments = ["--verify", root.path, sessionID, kind]
        try child.run()
        child.waitUntilExit()
        precondition(child.terminationStatus == 0,
            "a fresh process must read the production journal from disk")
    }

    private static func verifyFreshProcess(root: URL, sessionID: String, kind: String) throws {
        guard let record = try V3OperationRecoveryJournal.current(containerRoot: root),
              record.sessionID == sessionID, record.kind == kind, record.phase == .dispatched else {
            throw NSError(domain: "V3OperationRecoveryHarness", code: 1)
        }
        if kind == "installSharedIPA" {
            guard record.stagedIPAToken != nil else {
                throw NSError(domain: "V3OperationRecoveryHarness", code: 2)
            }
        } else {
            guard record.stagedIPAToken == nil else {
                throw NSError(domain: "V3OperationRecoveryHarness", code: 3)
            }
        }
    }
}
