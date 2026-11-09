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
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) {
            operation.progress.completedUnitCount = 1
            completionHandler(result)
        }
        return operation
    }
}
enum V3HeadlessPairingFailure {
    static func tagIfInvalidPairing(_ error: Error) -> Error { error }
}
@MainActor final class V3SideStoreService {
    static let shared = V3SideStoreService()
    var expected: V3RefreshExecutionTicket?
    func validateRefreshExecution(_ ticket: V3RefreshExecutionTicket) throws {
        guard expected == ticket else { throw ProbeError.stale }
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
    private var consumedHostRunID: String?
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
        if let hostAdmissionID {
            consumedHostAdmissionID = hostAdmissionID
            consumedHostRunID = hostRunID
        }
        return ticket
    }

    func hasDispatched(hostRunID: String, hostAdmissionID: String) -> Bool {
        consumedHostAdmissionID == hostAdmissionID && consumedHostRunID == hostRunID
    }

    func owns(_ ticket: V3RefreshExecutionTicket) -> Bool { current == ticket }

    mutating func release(_ ticket: V3RefreshExecutionTicket) {
        guard current == ticket else { return }
        current = nil
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
@main struct BackendCases {
    @MainActor static func main() async throws {
        let ticket = V3RefreshExecutionTicket(id: UUID(), hostRunID: UUID().uuidString,
            hostAdmissionID: UUID().uuidString)
        let service = V3SideStoreService.shared
        let manager = AppManager.shared
        let db = DatabaseManager.shared
        service.expected = ticket
        manager.delay = CommandLine.arguments.contains("--long") ? 28.1 : 0.05
        let before = Date()
        try await V3RefreshIntentBackend.run(presentsNotifications: false,
            progress: Progress(totalUnitCount: 1), ticket: ticket)
        precondition(manager.calls == 1)
        if CommandLine.arguments.contains("--long") { precondition(Date().timeIntervalSince(before) >= 27) }
        manager.delay = 0
        manager.plan = .perAppFailure
        do {
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: Progress(totalUnitCount: 1), ticket: ticket)
            fatalError("per-app failure must propagate")
        } catch ProbeError.pipeline { }
        manager.plan = .factoryThrow
        do {
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: Progress(totalUnitCount: 1), ticket: ticket)
            fatalError("factory error must resume continuation")
        } catch ProbeError.factory { }
        manager.plan = .success
        db.fail = true
        let callsBeforeDB = manager.calls
        do {
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: Progress(totalUnitCount: 1), ticket: ticket)
            fatalError("database error must propagate")
        } catch ProbeError.database { }
        precondition(manager.calls == callsBeforeDB)
        db.fail = false
        db.afterStart = { await MainActor.run { service.expected = nil } }
        do {
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: Progress(totalUnitCount: 1), ticket: ticket)
            fatalError("owner loss during await must stop before device operation")
        } catch ProbeError.stale { }
        precondition(manager.calls == callsBeforeDB)
        db.afterStart = nil
        service.expected = ticket
        manager.plan = .noApps
        try await V3RefreshIntentBackend.run(presentsNotifications: false,
            progress: Progress(totalUnitCount: 1), ticket: ticket)
        let callsBeforeCancel = manager.calls
        let cancelled = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            try await V3RefreshIntentBackend.run(presentsNotifications: false,
                progress: Progress(totalUnitCount: 1), ticket: ticket)
        }
        do { try await cancelled.value; fatalError("cancellation must propagate") }
        catch is CancellationError { }
        precondition(manager.calls == callsBeforeCancel)
        print("PASS: extracted backend delayed success, per-app/factory/DB failure, owner loss, no-app compatibility and cancellation")
    }
}
