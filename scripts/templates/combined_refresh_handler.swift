// LC_SERVICE_CONNECTION_V1: platform adapter, with explicit refresh compatibility entry points.
@MainActor
class RefreshHandler: NSObject {
    static let shared = RefreshHandler()
    var progress: Progress?
    var sideStorePid: Int32 = 0
    var client: RefreshClient?
    var v3RefreshToken: UUID?
    private var extensionProcess: NSExtension?
    private var listener: NSXPCListener?
    private var connection: NSXPCConnection?
    private var launchID: UUID?
    private var refreshRunID: String?
    private var refreshContinuation: CheckedContinuation<Void, Error>?
    private var readinessTask: Task<Void, Never>?
    private var retiringProcess: NSExtension?
    private var retiringPID: Int32 = 0
    private var launchRequestPending: UUID?
    private var retiringRequestPending: UUID?
    private lazy var service: CombinedServiceConnection = CombinedServiceConnection(dependencies: .init(
        resolveHost: {
            guard !UserDefaults.isSideStore(), !UserDefaults.isLiveProcess() else {
                throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError)
            }
            let value = getenv("LC_HOME_PATH").flatMap { String(validatingUTF8: $0) }
            return try CombinedServiceConnection.resolveHost(value)
        },
        prepareStorage: { host in
            let storage = host.appendingPathComponent("Documents/SideStore", isDirectory: true)
            var error: NSError?
            guard LCPrepareServiceStorage(storage, &error) else {
                throw error ?? NSError(domain: NSCocoaErrorDomain, code: NSFileWriteUnknownError)
            }
            return storage
        },
        createBookmark: { storage in
            var error: NSError?
            guard let data = LCCreateServiceBookmark(storage, &error) else {
                throw error ?? NSError(domain: NSCocoaErrorDomain, code: NSFileWriteUnknownError)
            }
            return data
        },
        discoverExtension: { [unowned self] in try self.discoverExtension() },
        launch: { [unowned self] id, bookmark in try self.launchEmbeddedSideStore(id: id, bookmark: bookmark) },
        retire: { [unowned self] id in self.retire(id) }))

    func ensureServiceConnected() async throws {
        // A cancelled begin-request may still call back with a newly launched process.
        // Do not open a second database owner while that launch remains unresolved.
        if retiringRequestPending != nil {
            let until = Date().addingTimeInterval(3)
            while retiringRequestPending != nil && Date() < until {
                try Task.checkCancellation()
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            guard retiringRequestPending == nil else {
                throw CombinedFailure(operation: "connect", stage: .extensionLaunch, code: .busy, id: UUID().uuidString)
            }
        }
        if retiringPID > 0 {
            let until = Date().addingTimeInterval(3)
            while getpgid(retiringPID) > 0 && Date() < until {
                try Task.checkCancellation()
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            if getpgid(retiringPID) > 0 {
                retiringProcess?._kill(9)
                throw CombinedFailure(operation: "connect", stage: .extensionLaunch, code: .busy, id: UUID().uuidString, retryable: true)
            }
            retiringPID = 0; retiringProcess = nil
        }
        if service.isReady && (sideStorePid <= 0 || getpgid(sideStorePid) <= 0) { service.stop() }
        try await service.ensureConnected()
    }
    private func discoverExtension() throws {
        guard let bundle = UserDefaults.lcMainBundle(),
              let url = bundle.builtInPlugInsURL?.appendingPathComponent("LiveProcess.appex"),
              let liveProcess = Bundle(url: url), let identifier = liveProcess.bundleIdentifier,
              let executable = liveProcess.executableURL,
              FileManager.default.fileExists(atPath: executable.path) else {
            throw CombinedFailure(operation: "connect", stage: .extensionDiscovery, code: .unavailable, id: service.attemptID?.uuidString ?? UUID().uuidString, retryable: false)
        }
        extensionProcess = try NSExtension(identifier: identifier)
        guard extensionProcess != nil else {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError)
        }
    }
    private func launchEmbeddedSideStore(id: UUID, bookmark: Data) throws {
        guard let ext = extensionProcess else { throw NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError) }
        launchID = id
        let callbacks = CombinedServiceCallbacks(owner: self, identity: id)
        guard let listener = startAnonymousListener(callbacks) else {
            throw CombinedFailure(operation: "connect", stage: .xpcConnection, code: .unavailable, id: id.uuidString, retryable: true)
        }
        self.listener = listener
        let item = NSExtensionItem()
        item.userInfo = ["selected": "builtinSideStore", "bookmarks": [bookmark], "endpoint": listener.endpoint]
        ext.setRequestCancellationBlock { [weak self] _, error in
            Task { @MainActor in self?.failed(id, stage: .extensionLaunch, underlying: error) }
        }
        ext.setRequestInterruptionBlock { [weak self] _ in
            Task { @MainActor in self?.failed(id, stage: .extensionLaunch, code: .interrupted) }
        }
        launchRequestPending = id
        LCLaunchServiceExtension(ext, item) { [weak self] uuid, error in
            Task { @MainActor in
                guard let self else { ext._kill(9); return }
                guard self.launchID == id else {
                    ext._kill(9)
                    if self.retiringRequestPending == id {
                        self.retiringRequestPending = nil
                        if let uuid { self.retiringPID = ext.pid(forRequestIdentifier: uuid) }
                    }
                    return
                }
                self.launchRequestPending = nil
                guard error == nil, let uuid else { self.failed(id, stage: .extensionLaunch, underlying: error); return }
                let pid = ext.pid(forRequestIdentifier: uuid)
                guard pid > 0 else { self.failed(id, stage: .extensionLaunch); return }
                self.sideStorePid = pid
                self.service.signal(.launched, attempt: id)
            }
        }
    }
    fileprivate func accepted(_ incoming: NSXPCConnection, id: UUID) {
        guard launchID == id, connection == nil else { incoming.invalidate(); return }
        connection = incoming
        incoming.remoteObjectInterface = NSXPCInterface(with: RefreshClient.self)
        client = incoming.remoteObjectProxyWithErrorHandler { [weak self] error in
            Task { @MainActor in self?.failed(id, stage: .xpcConnection, underlying: error) }
        } as? RefreshClient
        incoming.invalidationHandler = { [weak self] in Task { @MainActor in self?.failed(id, stage: .xpcConnection, code: .interrupted) } }
        incoming.interruptionHandler = incoming.invalidationHandler
        guard client != nil else { failed(id, stage: .xpcConnection); return }
        service.signal(.connected, attempt: id)
    }
    fileprivate func applicationReady(_ id: UUID) {
        guard launchID == id else { return }
        readinessTask?.cancel()
        readinessTask = Task { @MainActor in
            do {
                try await awaitServiceReady(id)
                guard launchID == id else { return }
                service.signal(.ready, attempt: id)
            } catch { failed(id, stage: .serviceReadiness, underlying: error) }
        }
    }
    private func awaitServiceReady(_ id: UUID) async throws {
        /*SERVICE_PROBE*/
    }
    fileprivate func failed(_ id: UUID, stage: CombinedFailure.Stage, code: CombinedFailure.Code = .failed, underlying: Error? = nil) {
        guard launchID == id || service.attemptID == id else { return }
        let failure = CombinedFailure(operation: refreshContinuation == nil ? "connect" : "refresh",
            stage: stage, code: code, id: refreshRunID ?? id.uuidString, underlying: underlying,
            retryable: refreshContinuation == nil && code == .interrupted ? true : nil)
        finishRefreshContinuation(.failure(failure))
        service.fail(id, failure)
    }
    private func retire(_ id: UUID) {
        guard launchID == id else { return }
        launchID = nil
        if launchRequestPending == id { retiringRequestPending = id; launchRequestPending = nil }
        readinessTask?.cancel(); readinessTask = nil
        listener?.invalidate(); listener = nil
        connection?.invalidate(); connection = nil; client = nil
        retiringProcess = extensionProcess; retiringPID = sideStorePid
        extensionProcess?._kill(15)
        extensionProcess = nil; sideStorePid = 0
        /*DISCONNECTED*/
    }
    func v3_stopService() {
        let id = refreshRunID ?? UUID().uuidString
        finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .command, code: .cancelled, id: id)))
        service.stop(code: .cancelled)
    }

    // Compatibility adapter for existing AppIntents and scheduler ABI. This always means refresh.
    func startRefresh(identifier: String, mangledName: String) async throws {
        try await performRefresh(identifier: identifier, mangledName: mangledName)
    }
    func performRefresh(identifier: String, mangledName: String) async throws {
        guard !identifier.isEmpty, !mangledName.isEmpty else {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .invalidConfiguration, id: UUID().uuidString)
        }
        guard v3RefreshToken == nil /*MUTATION_GUARD*/ else {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .busy, id: UUID().uuidString, retryable: true)
        }
        let token = UUID(); v3RefreshToken = token
        defer { if v3RefreshToken == token { v3RefreshToken = nil } }
        try await ensureServiceConnected()
        /*REFRESH_READINESS*/
        try Task.checkCancellation()
        let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")
        let run = defaults?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? UUID().uuidString
        guard UUID(uuidString: run) != nil, let client else {
            throw CombinedFailure(operation: "refresh", stage: .xpcConnection, code: .invalidConfiguration, id: token.uuidString)
        }
        defaults?.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")
        refreshRunID = run
        let timeout = Task { @MainActor in
            do { try await Task.sleep(nanoseconds: 600_000_000_000) } catch { return }
            guard self.v3RefreshToken == token else { return }
            self.finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .timedOut, id: run)))
            self.service.stop()
        }
        defer { timeout.cancel() }
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                refreshContinuation = continuation
                client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName, refreshRunID: run)
            }
        }, onCancel: { Task { @MainActor in if self.v3RefreshToken == token { self.v3_stopService() } } })
    }
    private func finishRefreshContinuation(_ result: Result<Void, Error>) {
        let pending = refreshContinuation; refreshContinuation = nil; refreshRunID = nil
        pending?.resume(with: result)
    }
    fileprivate func updateProgress(_ value: Double, id: UUID) {
        guard launchID == id, refreshContinuation != nil, value.isFinite else { return }
        progress?.completedUnitCount = Int64(max(0, min(1, value)) * 100)
    }
    fileprivate func completedRefresh(_ error: String?, runID: String, verification: Data?, id: UUID) {
        guard launchID == id, refreshContinuation != nil, refreshRunID == runID else { return }
        if let error {
            finishRefreshContinuation(.failure(CombinedFailure.fromEncodedString(error, expectedID: runID) ??
                CombinedFailure(operation: "refresh", stage: .command, id: runID)))
            return
        }
        guard let verification, verification.count <= 262144,
              let payload = try? PropertyListSerialization.propertyList(from: verification, format: nil) as? [String: Any],
              let manifest = payload["liveContainerAutoRefreshVerification"] as? [String: Any],
              manifest["run_id"] as? String == runID,
              let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") else {
            finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID)))
            return
        }
        defaults.set(manifest, forKey: "liveContainerAutoRefreshVerification")
        if payload["liveContainerAutoRefreshHostHandoffRunID"] as? String == runID {
            for key in ["liveContainerAutoRefreshHostHandoff", "liveContainerAutoRefreshHostHandoffRunID", "liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
                if let value = payload[key] { defaults.set(value, forKey: key) }
            }
        }
        NSLog("[LIVE_CONTAINER_REFRESH] RESULT_RECEIVED run_id=%@", runID)
        // The existing scheduler evaluates the imported installation evidence; this is command completion only.
        finishRefreshContinuation(.success(()))
    }
    fileprivate func legacyCompletion(_ error: String?, id: UUID) {
        guard launchID == id, let run = refreshRunID else { return }
        finishRefreshContinuation(.failure(CombinedFailure.fromEncodedString(error ?? "", expectedID: run) ??
            CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: run)))
    }
}

private final class CombinedServiceCallbacks: NSObject, RefreshServer {
    weak var owner: RefreshHandler?
    let identity: UUID
    init(owner: RefreshHandler, identity: UUID) { self.owner = owner; self.identity = identity }
    func onConnection(_ connection: NSXPCConnection!) {
        guard let connection else { return }
        Task { @MainActor in self.owner?.accepted(connection, id: self.identity) }
    }
    func finishedLaunching() { Task { @MainActor in self.owner?.applicationReady(self.identity) } }
    func updateProgress(_ value: Double) { Task { @MainActor in self.owner?.updateProgress(value, id: self.identity) } }
    func finish(_ error: String?) { Task { @MainActor in self.owner?.legacyCompletion(error, id: self.identity) } }
    func finishRefresh(_ error: String?, runID: String, verification: Data?) {
        Task { @MainActor in self.owner?.completedRefresh(error, runID: runID, verification: verification, id: self.identity) }
    }
    func add(_ request: UNNotificationRequest) {
        Task { @MainActor in
            guard self.owner?.launchIDForCallbacks == self.identity else { return }
            UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
        }
    }
    func removePendingNotificationRequests(withIdentifiers identifiers: [String]) {
        Task { @MainActor in
            guard self.owner?.launchIDForCallbacks == self.identity else { return }
            UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: identifiers)
        }
    }
}
extension RefreshHandler { fileprivate var launchIDForCallbacks: UUID? { launchID } }
