// LC_SERVICE_CONNECTION_V1: platform adapter, with explicit refresh compatibility entry points.
@MainActor
class RefreshHandler: NSObject {
    static let shared = RefreshHandler()
    var progress: Progress?
    var sideStorePid: Int32 = 0
    var client: RefreshClient?
    var v3RefreshToken: UUID?
    var v3RefreshAdmissionRunID: String?
    var v3RefreshDispatchedRunID: String?
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
        NSLog("[V3_SERVICE_START] PROCESS_LAUNCH_BEGIN id=%@", id.uuidString)
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
                    if self.retiringRequestPending == id {
                        ext._kill(9)
                        self.retiringRequestPending = nil
                        if let uuid { self.retiringPID = ext.pid(forRequestIdentifier: uuid) }
                    }
                    return
                }
                guard self.launchRequestPending == id else { return }
                self.launchRequestPending = nil
                guard error == nil, let uuid else { self.failed(id, stage: .extensionLaunch, underlying: error); return }
                let pid = ext.pid(forRequestIdentifier: uuid)
                guard pid > 0 else { self.failed(id, stage: .extensionLaunch); return }
                self.sideStorePid = pid
                NSLog("[V3_SERVICE_START] PROCESS_LAUNCHED id=%@ pid=%d", id.uuidString, pid)
                self.service.signal(.launched, attempt: id)
            }
        }
    }
    fileprivate func accepted(_ incoming: NSXPCConnection, id: UUID) {
        guard launchID == id, connection == nil else { incoming.invalidate(); return }
        NSLog("[V3_SERVICE_START] XPC_CONNECTED id=%@", id.uuidString)
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
        // finishedLaunching may be repeated; one readiness probe owns this launch.
        guard launchID == id, readinessTask == nil else { return }
        NSLog("[V3_SERVICE_START] APPLICATION_READY id=%@", id.uuidString)
        readinessTask = Task { @MainActor in
            do {
                try await awaitServiceReady(id)
                guard launchID == id else { return }
                service.signal(.ready, attempt: id)
            } catch {
                guard !Task.isCancelled, launchID == id else { return }
                failed(id, stage: .serviceReadiness, underlying: error)
            }
        }
    }
    private func awaitServiceReady(_ id: UUID) async throws {
        /*SERVICE_PROBE*/
    }
    fileprivate func failed(_ id: UUID, stage: CombinedFailure.Stage, code: CombinedFailure.Code = .failed, underlying: Error? = nil) {
        guard launchID == id || service.attemptID == id else { return }
        // Never double-wrap: an already structured failure (e.g. the readiness
        // probe's timedOut/invalidResponse) keeps its stage, code, retryable
        // flag and correlation ID instead of degrading to failed/redacted.
        let failure = CombinedFailure.preserving(underlying, operation: refreshContinuation == nil ? "connect" : "refresh",
            stage: stage, code: code, id: refreshRunID ?? id.uuidString,
            retryable: refreshContinuation == nil && code == .interrupted ? true : nil)
        NSLog("[V3_SERVICE_START] START_FAILED id=%@ stage=%@ code=%@", id.uuidString, failure.stage.rawValue, failure.code.rawValue)
        finishRefreshContinuation(.failure(failure))
        service.fail(id, failure)
    }
    private func retire(_ id: UUID) {
        guard launchID == id else { return }
        NSLog("[V3_SERVICE_START] PROCESS_EXITED id=%@", id.uuidString)
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
        try await performRefresh(identifier: identifier, mangledName: mangledName, schedulerRunID: nil)
    }
    func startScheduledRefresh(identifier: String, mangledName: String, runID: String) async throws {
        try await performRefresh(identifier: identifier, mangledName: mangledName, schedulerRunID: runID)
    }
    func performRefresh(identifier: String, mangledName: String) async throws {
        try await performRefresh(identifier: identifier, mangledName: mangledName, schedulerRunID: nil)
    }
    private func performRefresh(identifier: String, mangledName: String,
                                schedulerRunID: String?) async throws {
        guard !identifier.isEmpty, !mangledName.isEmpty else {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .invalidConfiguration, id: UUID().uuidString)
        }
        let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")
        guard let sharedDefaults = defaults else {
            throw CombinedFailure(operation: "refresh", stage: .xpcConnection,
                code: .invalidConfiguration, id: UUID().uuidString)
        }
        // Readiness can await connection startup. Do not reserve the local
        // handler token or publish a direct claim until after this preflight;
        // if a scheduler run starts meanwhile, the direct entry exits without
        // making the scheduler fail its own later handler call.
        try await ensureServiceConnected()
        /*REFRESH_READINESS*/
        try Task.checkCancellation()
        guard v3RefreshToken == nil /*MUTATION_GUARD*/ else {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true, safeCause: .operationInProgress)
        }
        if schedulerRunID == nil,
           sharedDefaults.string(forKey: "liveContainerAutoRefreshActiveRunID") != nil {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true, safeCause: .operationInProgress)
        }
        let token = UUID(); v3RefreshToken = token
        defer { if v3RefreshToken == token { v3RefreshToken = nil } }
        let directClaimID = schedulerRunID == nil ? UUID().uuidString : nil
        if let directClaimID {
            let existingClaim = sharedDefaults.dictionary(forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
            guard !V3DirectRefreshRunClaimPolicy.isActive(
                runID: existingClaim?["run_id"] as? String,
                deadline: existingClaim?["deadline"] as? Date) else {
                throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                    id: directClaimID, retryable: true, safeCause: .operationInProgress)
            }
            sharedDefaults.set(["run_id": directClaimID,
                // Cover the bounded XPC admission handshake; renew immediately
                // once the backend lease is authoritative.
                "deadline": Date().addingTimeInterval(V3RefreshAdmissionLease.lifetime + 60)],
                forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
        }
        defer {
            if let directClaimID,
               sharedDefaults.dictionary(forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)?["run_id"] as? String == directClaimID {
                sharedDefaults.removeObject(forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
            }
        }
        let selectedRun = V3RefreshRunIdentitySelection.select(
            schedulerRunID: schedulerRunID,
            expectedRunID: defaults?.string(forKey: "liveContainerAutoRefreshExpectedRunID"),
            activeRunID: defaults?.string(forKey: "liveContainerAutoRefreshActiveRunID"),
            newRunID: directClaimID ?? UUID().uuidString)
        guard let client else {
            throw CombinedFailure(operation: "refresh", stage: .xpcConnection, code: .invalidConfiguration, id: token.uuidString)
        }
        guard let selectedRun else {
            if let schedulerRunID {
                // Identity validation rejects this before service admission or
                // device dispatch. This is a stale scheduler request, not an
                // uncertain installation result that needs reconciliation.
                throw CombinedFailure(operation: "refresh", stage: .command,
                    code: .staleResult, id: schedulerRunID, retryable: false,
                    safeCause: .staleRefreshAttempt)
            }
            throw CombinedFailure(operation: "refresh", stage: .command,
                code: .busy, id: token.uuidString, retryable: true,
                safeCause: .operationInProgress)
        }
        let run = selectedRun.runID
        guard v3RefreshAdmissionRunID == nil else {
            throw CombinedFailure(operation: "refresh", stage: .serviceReadiness,
                code: .busy, id: run, retryable: true, safeCause: .operationInProgress)
        }
        v3RefreshAdmissionRunID = run
        defer { if v3RefreshAdmissionRunID == run { v3RefreshAdmissionRunID = nil } }
        defer { if v3RefreshDispatchedRunID == run { v3RefreshDispatchedRunID = nil } }
        // The readiness snapshot above is useful to explain current status,
        // but it is not an ownership claim: authentication could begin after
        // that snapshot and before this legacy XPC call. Reserve the mutation
        // through the SideStore command gate before starting the direct refresh
        // path. Authentication and refresh admission are serialized there.
        let admission = try await V3ServiceBridge.shared.request(
            operation: "refreshAdmissionBegin", target: run)
        guard admission["runID"] as? String == run,
              V3ServiceBridge.strictBool(admission["admitted"]) == true else {
            throw CombinedFailure(operation: "refresh", stage: .command,
                code: .busy, id: run, retryable: true, safeCause: .operationInProgress)
        }
        if let directClaimID {
            sharedDefaults.set(["run_id": directClaimID,
                "deadline": Date().addingTimeInterval(V3RefreshAdmissionLease.lifetime)],
                forKey: V3DirectRefreshRunClaimPolicy.defaultsKey)
        }
        defaults?.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")
        defer {
            if !selectedRun.schedulerOwned,
               defaults?.string(forKey: "liveContainerAutoRefreshExpectedRunID") == run {
                defaults?.removeObject(forKey: "liveContainerAutoRefreshExpectedRunID")
            }
        }
        refreshRunID = run
        let timeout = Task { @MainActor in
            do {
                try await Task.sleep(nanoseconds: V3RefreshAdmissionLease.nativeRefreshTimeoutNanoseconds)
            } catch { return }
            guard self.v3RefreshToken == token else { return }
            self.finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .timedOut, id: run)))
            self.service.stop()
        }
        defer { timeout.cancel() }
        do {
            try await withTaskCancellationHandler(operation: {
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                    if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                    refreshContinuation = continuation
                    defaults?.set(run, forKey: "liveContainerAutoRefreshUncertainMutationRunID")
                    v3RefreshDispatchedRunID = run
                    client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName, refreshRunID: run)
                }
            }, onCancel: { Task { @MainActor in
                if self.v3RefreshToken == token && self.v3RefreshDispatchedRunID == run {
                    self.v3_stopService()
                }
            } })
        } catch {
            timeout.cancel()
            // Before native dispatch, cancellation only needs to release the
            // admission lease. After dispatch, timeout/cancellation retires the
            // SideStore process, so avoid reconnecting to release its old state.
            if v3RefreshDispatchedRunID != run ||
               (!Task.isCancelled && !(error is CancellationError) &&
                (error as? CombinedFailure)?.code != .timedOut) {
                await releaseRefreshAdmission(run)
            }
            throw error
        }
        timeout.cancel()
        await releaseRefreshAdmission(run)
    }
    private func releaseRefreshAdmission(_ runID: String) async {
        // Run independently of a caller cancellation so a confirmed terminal
        // callback cannot strand the service's admission state.
        await Task { @MainActor in
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "refreshAdmissionEnd", target: runID)
                guard reply["runID"] as? String == runID,
                      V3ServiceBridge.strictBool(reply["released"]) == true else {
                    NSLog("[V3_REFRESH_ADMISSION] RELEASE_UNCONFIRMED run_id=%@", runID)
                    self.v3_stopService()
                    return
                }
            } catch {
                NSLog("[V3_REFRESH_ADMISSION] RELEASE_UNCONFIRMED run_id=%@", runID)
                self.v3_stopService()
            }
        }.value
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
            if let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") {
                CombinedVerification.clearUncertainty(defaults, runID: runID)
            }
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
        guard CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID) else {
            finishRefreshContinuation(.failure(CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: runID)))
            return
        }
        if manifest["host_handoff"] as? Bool != true && !defaults.bool(forKey: "liveContainerAutoRefreshHostHandoff") {
            CombinedVerification.clearUncertainty(defaults, runID: runID)
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
