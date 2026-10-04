import Foundation

// LC_SERVICE_CONNECTION_V1: executable, injectable startup state machine; no refresh/signing API.
@MainActor
final class CombinedServiceConnection {
    struct Dependencies {
        var resolveHost: () throws -> URL
        var prepareStorage: (URL) throws -> URL
        var createBookmark: (URL) throws -> Data
        var discoverExtension: () throws -> Void
        var launch: (UUID, Data) throws -> Void
        var retire: (UUID) -> Void
    }
    enum Signal { case launched, connected, ready }
    private let dependencies: Dependencies
    private let timeout: TimeInterval
    private var waiters: [UUID: CheckedContinuation<Void, Error>] = [:]
    private var deadline: Task<Void, Never>?
    private(set) var attemptID: UUID?
    private(set) var isReady = false
    private(set) var stage: CombinedFailure.Stage = .hostContainer
    private var launched = false, connected = false, ready = false
    var waitingCount: Int { waiters.count }
    var onFailure: ((CombinedFailure) -> Void)?
    init(dependencies: Dependencies, timeout: TimeInterval = 45) { self.dependencies = dependencies; self.timeout = timeout }
    func ensureConnected() async throws {
        try Task.checkCancellation()
        if isReady { return }
        let waiter = UUID()
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                waiters[waiter] = continuation
                if attemptID == nil { begin() }
            }
        }, onCancel: { Task { @MainActor in self.cancel(waiter) } })
    }
    private func begin() {
        let id = UUID()
        attemptID = id; launched = false; connected = false; ready = false; isReady = false
        do {
            stage = .hostContainer
            let host = try dependencies.resolveHost()
            stage = .storagePreparation
            let storage = try dependencies.prepareStorage(host)
            stage = .bookmarkCreation
            let bookmark = try dependencies.createBookmark(storage)
            stage = .extensionDiscovery
            try dependencies.discoverExtension()
            stage = .extensionLaunch
            deadline = Task { @MainActor in
                do { try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000)) } catch { return }
                self.fail(id, CombinedFailure(operation: "connect", stage: self.stage, code: .timedOut, id: id.uuidString, retryable: true))
            }
            try dependencies.launch(id, bookmark)
        } catch {
            if let known = error as? CombinedFailure {
                fail(id, known)
            } else {
                fail(id, CombinedFailure.capture(error, operation: "connect", stage: stage, id: id.uuidString),
                    launchSourceError: error)
            }
        }
    }
    func signal(_ signal: Signal, attempt id: UUID) {
        guard attemptID == id else { return }
        switch signal {
        case .launched: launched = true
        case .connected: connected = true
        case .ready: ready = true
        }
        stage = !launched ? .extensionLaunch : !connected ? .xpcConnection : .serviceReadiness
        if launched && connected && ready {
            isReady = true; deadline?.cancel(); deadline = nil
            let all = Array(waiters.values); waiters.removeAll()
            all.forEach { $0.resume() }
        }
    }
    func fail(_ id: UUID, _ error: CombinedFailure, launchSourceError: Error? = nil,
              launchSourceStep: CombinedFailure.LaunchContext.Step? = nil) {
        guard attemptID == id else { return }
        let launchStages: Set<CombinedFailure.Stage> = [.extensionDiscovery, .extensionLaunch, .xpcConnection, .serviceReadiness]
        let context: CombinedFailure.LaunchContext?
        if let launchSourceStep {
            context = CombinedFailure.LaunchContext(error: launchSourceError,
                sourceStep: launchSourceStep,
                requestIdentifierObserved: launched ? true : nil,
                pidObserved: launched ? true : nil,
                xpcAccepted: connected ? true : nil,
                applicationReadyObserved: ready ? true : nil)
        } else if error.launchContext == nil, error.operation == "connect", launchStages.contains(error.stage) {
            let source: CombinedFailure.LaunchContext.Step = error.code == .timedOut ? .startupTimeout
                : (error.code == .cancelled || error.code == .interrupted) ? .connectionStopped : .unknown
            context = CombinedFailure.LaunchContext(error: launchSourceError ?? error,
                sourceStep: source,
                requestIdentifierObserved: launched ? true : nil,
                pidObserved: launched ? true : nil,
                xpcAccepted: connected ? true : nil,
                applicationReadyObserved: ready ? true : nil)
        } else {
            context = nil
        }
        let failure = CombinedFailure.preserving(error, operation: error.operation, stage: error.stage,
            code: error.code, id: error.correlationID, retryable: error.retryable,
            launchContext: context)
        attemptID = nil; isReady = false; deadline?.cancel(); deadline = nil
        let all = Array(waiters.values); waiters.removeAll()
        dependencies.retire(id)
        all.forEach { $0.resume(throwing: failure) }
        onFailure?(failure)
    }
    func stop(code: CombinedFailure.Code = .interrupted) {
        guard let id = attemptID else { return }
        fail(id, CombinedFailure(operation: "connect", stage: stage, code: code, id: id.uuidString, retryable: true))
    }
    private func cancel(_ waiter: UUID) {
        waiters.removeValue(forKey: waiter)?.resume(throwing: CancellationError())
        if waiters.isEmpty && !isReady { stop(code: .cancelled) }
    }
    static func resolveHost(_ value: String?, fileManager: FileManager = .default) throws -> URL {
        guard let value, !value.isEmpty, value.hasPrefix("/"), !value.contains("\0"), value != "/",
              !value.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadInvalidFileNameError)
        }
        let home = URL(fileURLWithPath: value, isDirectory: true).standardizedFileURL
        guard home.path != "/" else { throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadInvalidFileNameError) }
        var directory: ObjCBool = false
        guard fileManager.fileExists(atPath: home.path, isDirectory: &directory), directory.boolValue else {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFileNoSuchFileError)
        }
        return home
    }
}
