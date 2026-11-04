import Foundation

enum ProbeError: Error { case database, pipeline, factory, stale }
enum OperationError: Error { case noInstalledApps }
struct InstalledApp: Sendable {
    static func fetchAppsForRefreshingAll(in context: DBContext) -> [InstalledApp] { [InstalledApp()] }
}
final class DBContext {
    func perform<T>(_ body: () -> T) async -> T { body() }
}
final class PersistentContainer {
    func newBackgroundContext() -> DBContext { DBContext() }
}
final class DatabaseManager {
    static let shared = DatabaseManager()
    let persistentContainer = PersistentContainer()
    var fail = false
    var afterStart: (() async -> Void)?
    func start() async throws {
        if fail { throw ProbeError.database }
        if let afterStart { await afterStart() }
    }
}
final class BackgroundRefreshAppsOperation: @unchecked Sendable {
    var ignoresServerNotFoundError = true
    let progress = Progress(totalUnitCount: 1)
}
final class AppManager {
    static let shared = AppManager()
    enum Plan { case success, factoryThrow, perAppFailure, noApps }
    var plan = Plan.success
    var delay: TimeInterval = 0
    var calls = 0
    var afterDispatch: (() async -> Void)?
    func backgroundRefresh(_ apps: [InstalledApp], presentsNotifications: Bool,
        completionHandler: @escaping (Result<[String: Result<InstalledApp, Error>], Error>) -> Void) throws -> BackgroundRefreshAppsOperation {
        calls += 1
        if plan == .factoryThrow { throw ProbeError.factory }
        let operation = BackgroundRefreshAppsOperation()
        let result: Result<[String: Result<InstalledApp, Error>], Error>
        switch plan {
        case .success: result = .success(["fixture": .success(InstalledApp())])
        case .perAppFailure: result = .success(["fixture": .failure(ProbeError.pipeline)])
        case .noApps: result = .failure(OperationError.noInstalledApps)
        case .factoryThrow: fatalError("unreachable")
        }
        let hook = afterDispatch
        let delay = self.delay
        let capturedRun = V3SharedAppGroup.defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID")
        Task {
            if let hook { await hook() }
            DispatchQueue.global().asyncAfter(deadline: .now() + delay) {
                if let capturedRun {
                    V3SharedAppGroup.defaults.set(["run_id": capturedRun, "fixture": true],
                        forKey: "liveContainerAutoRefreshVerification")
                }
                operation.progress.completedUnitCount = 1
                completionHandler(result)
            }
        }
        return operation
    }
}
enum V3HeadlessPairingFailure {
    static func tagIfInvalidPairing(_ error: Error) -> Error { error }
}

// These are deliberately inert boundaries. The service methods and backend
// under test are copied verbatim from the proposal; no real user store exists.
struct CombinedFailure: Error {
    enum Stage: String { case command, serviceReadiness }
    enum Code: String { case staleResult, busy, unavailable, failed }
    enum SafeCause { case staleRefreshAttempt, operationInProgress }
    let code: Code
    let id: String
    init(operation: String, stage: Stage, code: Code, id: String,
         retryable: Bool, safeCause: SafeCause? = nil) { self.code = code; self.id = id }
    var encodedString: String { code.rawValue + ":" + id }
    static func capture(_ error: Error, operation: String, stage: Stage, id: String) -> CombinedFailure {
        if let known = error as? CombinedFailure { return known }
        return CombinedFailure(operation: operation, stage: stage, code: .failed, id: id, retryable: false)
    }
}
enum V3SharedAppGroup {
    static let suite = "synthetic-refresh-probe-" + UUID().uuidString
    static let defaults = UserDefaults(suiteName: suite)!
    static func requireSharedUserDefaults() throws -> UserDefaults { defaults }
    static func sharedUserDefaults() -> UserDefaults? { defaults }
}
struct JournalRecord { let kind: String; let sessionID: String }
enum V3OperationRecoveryJournal {
    static var record: JournalRecord?
    static func current() throws -> JournalRecord? { record }
    static func currentState() throws -> JournalRecord? { record }
}
enum V3DirectRefreshRunClaimPolicy {
    static let defaultsKey = "fixtureDirectClaim"
    static func isActive(runID: String?, deadline: Date?) -> Bool {
        guard let runID, UUID(uuidString: runID) != nil, let deadline else { return false }
        return deadline > Date()
    }
}
final class MockAuth { var hasActiveSession = false }
final class MockOperations { var activeMutationID: String? }
@MainActor final class V3HeadlessRuntime {
    static let shared = V3HeadlessRuntime()
    let auth = MockAuth()
    let operations = MockOperations()
}
enum CombinedVerification {
    static func sanitized(_ raw: [String: Any], runID: String) -> [String: Any] {
        guard let manifest = raw["liveContainerAutoRefreshVerification"] as? [String: Any],
              manifest["run_id"] as? String == runID else { return [:] }
        return ["liveContainerAutoRefreshVerification": manifest]
    }
}

// V3_REFRESH_EXECUTION_GATE_V1: one in-process execution owner across the
// external AppIntent and the admitted host XPC path. MainActor owns this value.
struct V3RefreshExecutionTicket: Equatable, Sendable {
    let id: UUID
    let hostRunID: String?
    let hostAdmissionID: String?
}

struct V3RefreshExecutionGate {
    private(set) var current: V3RefreshExecutionTicket?
    private var consumedHostAdmissionID: String?
    var isActive: Bool { current != nil }
    var isExternalActive: Bool { current != nil && current?.hostRunID == nil }

    mutating func acquire(hostRunID: String?, hostAdmissionID: String? = nil, hostLeaseActive: Bool) -> V3RefreshExecutionTicket? {
        guard current == nil else { return nil }
        if let hostRunID {
            guard hostLeaseActive, UUID(uuidString: hostRunID)?.uuidString == hostRunID,
                  let hostAdmissionID, UUID(uuidString: hostAdmissionID)?.uuidString == hostAdmissionID,
                  consumedHostAdmissionID != hostAdmissionID else { return nil }
        } else {
            guard !hostLeaseActive, hostAdmissionID == nil else { return nil }
        }
        let ticket = V3RefreshExecutionTicket(id: UUID(), hostRunID: hostRunID, hostAdmissionID: hostAdmissionID)
        current = ticket
        if let hostAdmissionID { consumedHostAdmissionID = hostAdmissionID }
        return ticket
    }

    func owns(_ ticket: V3RefreshExecutionTicket) -> Bool { current == ticket }

    mutating func release(_ ticket: V3RefreshExecutionTicket) {
        guard current == ticket else { return }
        current = nil
    }
}

struct V3RefreshAdmissionLease {
    static let nativeRefreshTimeout: TimeInterval = 600
    static let retirementGrace: TimeInterval = 60
    static let lifetime: TimeInterval = nativeRefreshTimeout + retirementGrace
    static let nativeRefreshTimeoutNanoseconds: UInt64 = 600_000_000_000

    private(set) var runID: String?
    private(set) var requestID: String?
    private(set) var expiresAt: Date?
    private(set) var ownerLost = false

    var isActive: Bool { runID != nil }

    mutating func expire(now: Date = Date()) -> Bool {
        guard let expiresAt, expiresAt <= now, !ownerLost else { return false }
        ownerLost = true
        self.expiresAt = nil
        return true
    }

    mutating func acquire(runID: String, requestID: String,
                          authenticationActive: Bool,
                          anotherMutationActive: Bool,
                          now: Date = Date()) -> Bool {
        _ = expire(now: now)
        guard let parsed = UUID(uuidString: runID), parsed.uuidString == runID,
              let parsedRequest = UUID(uuidString: requestID), parsedRequest.uuidString == requestID,
              self.runID == nil, !authenticationActive, !anotherMutationActive,
              Self.lifetime > 0 else { return false }
        self.runID = runID
        self.requestID = requestID
        expiresAt = now.addingTimeInterval(Self.lifetime)
        ownerLost = false
        return true
    }

    func owns(_ candidate: String) -> Bool { runID == candidate }

    mutating func restoreLost(runID: String) -> Bool {
        guard self.runID == nil,
              let parsed = UUID(uuidString: runID), parsed.uuidString == runID else { return false }
        self.runID = runID
        requestID = nil
        expiresAt = nil
        ownerLost = true
        return true
    }

    @discardableResult
    mutating func release(runID: String) -> Bool {
        guard self.runID == runID else { return false }
        self.runID = nil
        requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func release(requestID: String) -> Bool {
        guard self.requestID == requestID else { return false }
        runID = nil
        self.requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func reconcileAfterDeviceCheck(runID: String, userConfirmed: Bool) -> Bool {
        guard ownerLost, userConfirmed, owns(runID) else { return false }
        return release(runID: runID)
    }
}

enum V3RefreshIntentStartPolicy {
    static func create<T>(_ factory: () throws -> T,
                          continuation: CheckedContinuation<Void, Error>,
                          classify: (Error) -> Error = { $0 }) -> T? {
        do {
            return try factory()
        } catch {
            continuation.resume(throwing: classify(error))
            return nil
        }
    }
}

enum V3RefreshIntentBackend {
    static func run(presentsNotifications: Bool, progress: Progress,
                    ticket: V3RefreshExecutionTicket,
                    onStarted: @escaping @Sendable (BackgroundRefreshAppsOperation) async -> Void = { _ in }) async throws {
        try await V3SideStoreService.shared.validateRefreshExecution(ticket)

        try await DatabaseManager.shared.start()
        
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let installedApps = await context.perform { InstalledApp.fetchAppsForRefreshingAll(in: context) }
        
        try await V3SideStoreService.shared.validateRefreshExecution(ticket)
        try Task.checkCancellation()
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let operation = V3RefreshIntentStartPolicy.create({
                try AppManager.shared.backgroundRefresh(installedApps, presentsNotifications: presentsNotifications) { (result) in
                do
                {
                    let results = try result.get()
                    
                    for (_, result) in results
                    {
                        guard case let .failure(error) = result else { continue }
                        throw V3HeadlessPairingFailure.tagIfInvalidPairing(error)
                    }
                    
                    continuation.resume()
                }
                catch OperationError.noInstalledApps
                {
                    continuation.resume()
                }
                catch
                {
                    continuation.resume(throwing: error)
                }
            }
            }, continuation: continuation,
                classify: V3HeadlessPairingFailure.tagIfInvalidPairing)
            guard let operation else { return }
            
            operation.ignoresServerNotFoundError = false
            
            progress.addChild(operation.progress, withPendingUnitCount: 1)
            
            Task {
                await onStarted(operation)
            }
        }
    }
}
@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    private var refreshAdmission = V3RefreshAdmissionLease()
    var mutationID: String?
    private var refreshExecutionGate = V3RefreshExecutionGate()

    private func hostRefreshDefaults(for runID: String) throws -> UserDefaults {
        _ = refreshAdmission.expire()
        let defaults = try V3SharedAppGroup.requireSharedUserDefaults()
        let journal = try V3OperationRecoveryJournal.current()
        let activeRunID = defaults.string(forKey: "liveContainerAutoRefreshActiveRunID")
        let direct = defaults.dictionary(forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
        let directOwned = direct?["run_id"] as? String == runID &&
            V3DirectRefreshRunClaimPolicy.isActive(runID: runID, deadline: direct?["deadline"] as? Date)
        guard UUID(uuidString: runID)?.uuidString == runID,
              (refreshAdmission.isActive && !refreshAdmission.ownerLost), refreshAdmission.owns(runID),
              defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") == runID,
              activeRunID == runID || (activeRunID == nil && directOwned),
              journal?.kind == "refreshAll", journal?.sessionID == runID else {
            throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                code: .staleResult, id: runID, retryable: false, safeCause: .staleRefreshAttempt)
        }
        return defaults
    }

    func beginExternalRefreshExecution() throws -> V3RefreshExecutionTicket {
        // External refresh is a mutation, not a recovery-control operation.
        // Any unresolved journal record (including directMutation) blocks it.
        let journal = try V3OperationRecoveryJournal.currentState()
        guard journal == nil,
              mutationID == nil,
              !V3HeadlessRuntime.shared.auth.hasActiveSession,
              V3HeadlessRuntime.shared.operations.activeMutationID == nil,
              let ticket = refreshExecutionGate.acquire(hostRunID: nil,
                  hostLeaseActive: refreshAdmission.isActive) else {
            throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                code: .busy, id: UUID().uuidString, retryable: false, safeCause: .operationInProgress)
        }
        return ticket
    }

    func validateRefreshExecution(_ ticket: V3RefreshExecutionTicket) throws {
        guard refreshExecutionGate.owns(ticket) else {
            throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                code: .staleResult, id: ticket.hostRunID ?? ticket.id.uuidString,
                retryable: false, safeCause: .staleRefreshAttempt)
        }
        if let runID = ticket.hostRunID {
            guard refreshAdmission.requestID == ticket.hostAdmissionID else {
                throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                    code: .staleResult, id: runID, retryable: false, safeCause: .staleRefreshAttempt)
            }
            _ = try hostRefreshDefaults(for: runID)
        } else {
            guard !refreshAdmission.isActive else {
                throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                    code: .busy, id: ticket.id.uuidString, retryable: false,
                    safeCause: .operationInProgress)
            }
        }
    }

    func endRefreshExecution(_ ticket: V3RefreshExecutionTicket) {
        refreshExecutionGate.release(ticket)
    }

    // Invoked only through the existing host-to-service XPC refresh method.
    // The explicit run must already own the admission lease and journal.
    // This does not use LNAction, AppIntent execution or foreground continuation.
    @objc(refreshForHost:progress:completion:)
    nonisolated static func refreshForHost(_ runID: String,
        progress: @escaping (Double) -> Void,
        completion: @escaping (String?, Data?) -> Void) {
        Task { @MainActor in
            await shared.performHostRefresh(runID: runID, progress: progress, completion: completion)
        }
    }

    private func refreshVerificationPayload(runID: String) -> Data? {
        guard let defaults = V3SharedAppGroup.sharedUserDefaults(),
              defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") == runID else { return nil }
        var raw: [String: Any] = [:]
        for key in ["liveContainerAutoRefreshVerification", "liveContainerAutoRefreshHostHandoff",
                    "liveContainerAutoRefreshHostHandoffRunID", "liveContainerAutoRefreshHostHandoffStartedAt",
                    "liveContainerAutoRefreshHostPreviousExpiration"] {
            if let value = defaults.object(forKey: key) { raw[key] = value }
        }
        let payload = CombinedVerification.sanitized(raw, runID: runID)
        guard let data = try? PropertyListSerialization.data(fromPropertyList: payload,
                    format: .binary, options: 0), data.count <= 262144 else { return nil }
        return data
    }

    private func performHostRefresh(runID: String, progress: @escaping (Double) -> Void,
                                    completion: @escaping (String?, Data?) -> Void) async {
        var acquired: V3RefreshExecutionTicket?
        var observation: NSKeyValueObservation?
        var failure: CombinedFailure?
        do {
            guard #available(iOS 17.0, tvOS 17.0, *) else {
                throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                    code: .unavailable, id: runID, retryable: false)
            }
            let defaults = try hostRefreshDefaults(for: runID)
            guard let ticket = refreshExecutionGate.acquire(hostRunID: runID,
                    hostAdmissionID: refreshAdmission.requestID, hostLeaseActive: (refreshAdmission.isActive && !refreshAdmission.ownerLost)) else {
                throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                    code: .busy, id: runID, retryable: false, safeCause: .operationInProgress)
            }
            acquired = ticket
            // Clear only after this explicit invocation owns the run. Never
            // stamp a delayed request over a newer host's expected run.
            defaults.removeObject(forKey: "liveContainerAutoRefreshVerification")
            defaults.removeObject(forKey: "liveContainerAutoRefreshHostHandoff")
            defaults.removeObject(forKey: "liveContainerAutoRefreshHostHandoffRunID")
            let parentProgress = Progress(totalUnitCount: 1)
            observation = parentProgress.observe(\.fractionCompleted, options: [.new]) { value, _ in
                let fraction = value.fractionCompleted
                if fraction.isFinite { progress(max(0, min(1, fraction))) }
            }
            print("[V3_REFRESH_EXECUTION] route=direct_host phase=started")
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: parentProgress, ticket: ticket)
            try validateRefreshExecution(ticket)
            print("[V3_REFRESH_EXECUTION] route=direct_host phase=pipelineCompleted")
        } catch {
            failure = CombinedFailure.capture(V3HeadlessPairingFailure.tagIfInvalidPairing(error),
                operation: "refresh", stage: .command, id: runID)
            print("[V3_REFRESH_EXECUTION] route=direct_host phase=pipelineThrew")
        }
        observation?.invalidate()
        // Capture exact-run immutable bytes before releasing the execution slot.
        // Admission failures have no producer result and must not borrow one.
        let payload = acquired == nil ? nil : refreshVerificationPayload(runID: runID)
        if let acquired { endRefreshExecution(acquired) }
        completion(failure?.encodedString, payload)
    }

}

extension V3SideStoreService {
    func fixtureReset() {
        refreshAdmission = V3RefreshAdmissionLease()
        refreshExecutionGate = V3RefreshExecutionGate()
        V3OperationRecoveryJournal.record = nil
        V3SharedAppGroup.defaults.removePersistentDomain(forName: V3SharedAppGroup.suite)
        AppManager.shared.calls = 0
        AppManager.shared.plan = .success
        AppManager.shared.delay = 0
        AppManager.shared.afterDispatch = nil
        DatabaseManager.shared.afterStart = nil
    }
    func fixtureAdmit(_ runID: String, direct: Bool = false) {
        precondition(refreshAdmission.acquire(runID: runID, requestID: UUID().uuidString,
            authenticationActive: false, anotherMutationActive: refreshExecutionGate.isActive))
        V3OperationRecoveryJournal.record = JournalRecord(kind: "refreshAll", sessionID: runID)
        let defaults = V3SharedAppGroup.defaults
        defaults.set(runID, forKey: "liveContainerAutoRefreshExpectedRunID")
        if direct {
            defaults.set(["run_id": runID, "deadline": Date().addingTimeInterval(600)],
                forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
        } else { defaults.set(runID, forKey: "liveContainerAutoRefreshActiveRunID") }
    }
    func fixtureLoseOwner() { _ = refreshAdmission.expire(now: Date().addingTimeInterval(1_000)) }
    var fixtureOwnerLost: Bool { refreshAdmission.ownerLost }
    var fixtureExecutionActive: Bool { refreshExecutionGate.isActive }
}

@main struct ServiceCases {
    @MainActor static func execute(_ run: String) async -> (String?, Data?) {
        await withCheckedContinuation { c in
            V3SideStoreService.refreshForHost(run, progress: { value in
                precondition(value.isFinite && value >= 0 && value <= 1)
            }, completion: { error, payload in c.resume(returning: (error, payload)) })
        }
    }
    @MainActor static func main() async throws {
        let service = V3SideStoreService.shared
        precondition(V3SideStoreService.responds(to: NSSelectorFromString("refreshForHost:progress:completion:")))
        defer { V3SharedAppGroup.defaults.removePersistentDomain(forName: V3SharedAppGroup.suite) }
        service.fixtureReset()
        let invalid = await execute(UUID().uuidString)
        precondition(invalid.0 != nil && invalid.1 == nil && AppManager.shared.calls == 0)
        let run = UUID().uuidString
        service.fixtureAdmit(run)
        do { _ = try service.beginExternalRefreshExecution(); fatalError("external overlap admitted") }
        catch { }
        AppManager.shared.delay = CommandLine.arguments.contains("--long") ? 28.1 : 0.05
        let complete = await execute(run)
        precondition(complete.0 == nil && complete.1 != nil && AppManager.shared.calls == 1)
        precondition(!service.fixtureExecutionActive)
        // A completed admission cannot be consumed a second time.
        let replay = await execute(run)
        precondition(replay.0 != nil && replay.1 == nil && AppManager.shared.calls == 1)
        service.fixtureReset()
        let directRun = UUID().uuidString
        service.fixtureAdmit(directRun, direct: true)
        let directComplete = await execute(directRun)
        precondition(directComplete.0 == nil && AppManager.shared.calls == 1)
        service.fixtureReset()
        let cancelledRun = UUID().uuidString
        service.fixtureAdmit(cancelledRun)
        AppManager.shared.delay = 0.05
        AppManager.shared.afterDispatch = { await MainActor.run { service.fixtureLoseOwner() } }
        let cancelled = await execute(cancelledRun)
        precondition(cancelled.0?.hasPrefix("staleResult:") == true)
        precondition(AppManager.shared.calls == 1 && service.fixtureOwnerLost)
        precondition(V3OperationRecoveryJournal.record?.sessionID == cancelledRun)
        precondition(!service.fixtureExecutionActive)
        // Owner lost before queueing: never touch a device operation.
        service.fixtureReset()
        let earlyRun = UUID().uuidString
        service.fixtureAdmit(earlyRun)
        DatabaseManager.shared.afterStart = { await MainActor.run { service.fixtureLoseOwner() } }
        let early = await execute(earlyRun)
        precondition(early.0 != nil && AppManager.shared.calls == 0)
        service.fixtureReset()
        V3OperationRecoveryJournal.record = JournalRecord(kind: "install", sessionID: UUID().uuidString)
        do { _ = try service.beginExternalRefreshExecution(); fatalError("unresolved recovery admitted") }
        catch { }
        service.fixtureReset()
        let external = try service.beginExternalRefreshExecution()
        precondition(service.fixtureExecutionActive)
        service.endRefreshExecution(external)
        precondition(!service.fixtureExecutionActive)
        print("PASS: actual service methods selector, callback, admission, replay, direct run, owner loss and recovery controls")
    }
}
