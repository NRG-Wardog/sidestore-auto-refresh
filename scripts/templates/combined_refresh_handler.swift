// LC_SERVICE_CONNECTION_V1: platform adapter, with explicit refresh compatibility entry points.
enum V3ServiceReadinessProbeState: Equatable {
    case pending
    case ready
    case invalid
    case failed
    case timedOut

    static func resolve(ready: Bool, invalid: Bool, hasTerminalFailure: Bool,
                        expired: Bool) -> V3ServiceReadinessProbeState {
        if ready { return .ready }
        if invalid { return .invalid }
        if hasTerminalFailure { return .failed }
        return expired ? .timedOut : .pending
    }

    @MainActor
    static func recheckAfterYield(ready: @MainActor () -> Bool,
                                  invalid: @MainActor () -> Bool,
                                  hasTerminalFailure: @MainActor () -> Bool) async -> V3ServiceReadinessProbeState {
        await Task.yield()
        return resolve(ready: ready(), invalid: invalid(),
            hasTerminalFailure: hasTerminalFailure(), expired: true)
    }
}

extension V3ServiceReadinessFailure {
    func combinedFailure(id: String, operation overrideOperation: String? = nil) -> CombinedFailure {
        guard let resolvedStage = CombinedFailure.Stage(rawValue: stage),
              let resolvedCode = CombinedFailure.Code(rawValue: code),
              let validatedContext = CombinedFailure.validatedSigningContext(signingContext) else {
            return CombinedFailure(operation: "connect", stage: .serviceReadiness,
                code: .invalidResponse, id: id)
        }
        let resolvedCause = safeCause.flatMap(CombinedFailure.SafeCause.init(rawValue:))
        let resolvedStep = sourceStep.flatMap(CombinedFailure.SourceStep.init(rawValue:))
        return CombinedFailure(operation: overrideOperation ?? operation, stage: resolvedStage, code: resolvedCode, id: id,
            underlying: NSError(domain: underlyingDomain, code: underlyingCode), retryable: retryable,
            safeCause: resolvedCause, sourceStep: resolvedStep, signingContext: validatedContext)
    }
}

struct V3ServiceReadinessBackoff {
    private(set) var delay: TimeInterval = 0.2
    static let maximumDelay: TimeInterval = 1.0

    mutating func nextDelay(remaining: TimeInterval) -> TimeInterval? {
        guard remaining.isFinite, remaining > 0 else { return nil }
        let result = min(delay, remaining)
        delay = min(delay * 2, Self.maximumDelay)
        return result
    }
}

enum V3RefreshAdmissionFailureResolution: Equatable {
    case releaseNotDispatched
    case releaseTerminalFailure
    case retainUnknownOutcome

    static func resolve(runID: String, dispatchedRunID: String?, terminalCallbackRunID: String?) -> Self {
        guard dispatchedRunID == runID else { return .releaseNotDispatched }
        return terminalCallbackRunID == runID ? .releaseTerminalFailure : .retainUnknownOutcome
    }
}

@MainActor
class RefreshHandler: NSObject {
    static let shared = RefreshHandler()
    var progress: Progress?
    var sideStorePid: Int32 = 0
    var client: RefreshClient?
    var v3RefreshToken: UUID?
    var v3RefreshAdmissionRunID: String?
    var v3RefreshDispatchedRunID: String?
    private var v3RefreshTerminalCallbackRunID: String?
    private var extensionProcess: NSExtension?
    private var listener: NSXPCListener?
    private var connection: NSXPCConnection?
    private var pendingPeerConnections: [NSXPCConnection] = []
    private var launchID: UUID?
    private var refreshRunID: String?
    private var refreshContinuation: CheckedContinuation<Void, Error>?
    private var readinessTask: Task<Void, Never>?
    private var retiringProcess: NSExtension?
    private var retiringPID: Int32 = 0
    private var launchRequestPending: UUID?
    private var retiringRequestPending: UUID?
    private var launchRequestIdentifierObserved = false
    private var launchApplicationReadyObserved = false
    private var launchPeerPIDRejected = false
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
        let id = service.attemptID?.uuidString ?? UUID().uuidString
        guard let bundle = UserDefaults.lcMainBundle() else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .hostBundleUnavailable, retryable: false)
        }
        guard let pluginsURL = bundle.builtInPlugInsURL else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .missingPluginDirectory, retryable: false)
        }
        let url = pluginsURL.appendingPathComponent("LiveProcess.appex", isDirectory: true)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .liveProcessBundleMissing, retryable: false)
        }
        guard let liveProcess = Bundle(url: url) else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .liveProcessBundleUnreadable, retryable: false)
        }
        guard let identifier = liveProcess.bundleIdentifier else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .bundleIdentifierMissing, retryable: false)
        }
        guard let executable = liveProcess.executableURL else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .executableMetadataMissing, retryable: false)
        }
        guard FileManager.default.fileExists(atPath: executable.path) else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unavailable,
                id: id, sourceStep: .executableFileMissing, retryable: false)
        }
        do {
            extensionProcess = try NSExtension(identifier: identifier)
        } catch {
            throw CombinedFailure.LaunchContext.launchFailure(error, stage: .extensionDiscovery,
                id: id, sourceStep: .extensionFactory, retryable: false)
        }
        guard extensionProcess != nil else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .extensionDiscovery, code: .unsupported,
                id: id, sourceStep: .extensionFactoryNil, retryable: false)
        }
    }
    private func launchEmbeddedSideStore(id: UUID, bookmark: Data) throws {
        guard let ext = extensionProcess else { throw NSError(domain: NSCocoaErrorDomain, code: NSFeatureUnsupportedError) }
        launchID = id
        NSLog("[V3_SERVICE_START] PROCESS_LAUNCH_BEGIN id=%@", id.uuidString)
        let callbacks = CombinedServiceCallbacks(owner: self, identity: id)
        guard let listener = startAnonymousListener(callbacks) else {
            throw CombinedFailure.LaunchContext.launchFailure(stage: .xpcConnection, code: .unavailable,
                id: id.uuidString, sourceStep: .listenerCreation, requestIdentifierObserved: false,
                pidObserved: false, xpcAccepted: false, retryable: true)
        }
        self.listener = listener
        let item = NSExtensionItem()
        item.userInfo = V3EmbeddedSideStoreLaunchPayload.userInfo(
            bookmark: bookmark, endpoint: listener.endpoint,
            identity: V3SharedAppGroup.runtimeIdentity())
        ext.setRequestCancellationBlock { [weak self] _, error in
            Task { @MainActor in self?.failed(id, stage: .extensionLaunch, underlying: error,
                launchSourceStep: .requestCancellation) }
        }
        ext.setRequestInterruptionBlock { [weak self] _ in
            Task { @MainActor in self?.failed(id, stage: .extensionLaunch, code: .interrupted,
                launchSourceStep: .requestInterruption) }
        }
        launchRequestIdentifierObserved = false
        launchApplicationReadyObserved = false
        launchPeerPIDRejected = false
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
                self.launchRequestIdentifierObserved = uuid != nil
                guard error == nil, let uuid else {
                    let step: CombinedFailure.LaunchContext.Step = uuid == nil ? .requestCallbackNoIdentifier : .requestCallbackError
                    self.failed(id, stage: .extensionLaunch, underlying: error,
                        launchSourceStep: step, requestIdentifierObserved: uuid != nil)
                    return
                }
                let pid = ext.pid(forRequestIdentifier: uuid)
                guard pid > 0 else {
                    self.failed(id, stage: .extensionLaunch, launchSourceStep: .processIdentifierUnavailable,
                        requestIdentifierObserved: true, pidObserved: false)
                    return
                }
                self.sideStorePid = pid
                NSLog("[V3_SERVICE_START] PROCESS_LAUNCHED id=%@ pid=%d", id.uuidString, pid)
                self.service.signal(.launched, attempt: id)
                self.confirmLaunchedPeer(id)
            }
        }
    }
    fileprivate func accepted(_ incoming: NSXPCConnection, id: UUID) {
        guard launchID == id, connection == nil else { incoming.invalidate(); return }
        // The endpoint is a private launch capability, but possession alone
        // does not prove that its holder is the extension we launched. Keep
        // incoming connections suspended until NSExtension supplies that PID.
        guard sideStorePid > 0 else {
            guard pendingPeerConnections.count < 8 else { incoming.invalidate(); return }
            pendingPeerConnections.append(incoming)
            return
        }
        guard incoming.processIdentifier == sideStorePid else {
            launchPeerPIDRejected = true
            incoming.invalidate()
            return
        }
        NSLog("[V3_SERVICE_START] XPC_CONNECTED id=%@", id.uuidString)
        connection = incoming
        incoming.remoteObjectInterface = NSXPCInterface(with: RefreshClient.self)
        client = incoming.remoteObjectProxyWithErrorHandler { [weak self] error in
            Task { @MainActor in self?.failed(id, stage: .xpcConnection, underlying: error,
                launchSourceStep: .xpcRemoteObjectError) }
        } as? RefreshClient
        incoming.invalidationHandler = { [weak self] in Task { @MainActor in self?.failed(id, stage: .xpcConnection,
            code: .interrupted, launchSourceStep: .xpcInvalidation) } }
        incoming.interruptionHandler = incoming.invalidationHandler
        guard client != nil else { failed(id, stage: .xpcConnection); return }
        incoming.resume()
        service.signal(.connected, attempt: id)
    }
    private func confirmLaunchedPeer(_ id: UUID) {
        guard launchID == id, sideStorePid > 0 else { return }
        let candidates = pendingPeerConnections
        pendingPeerConnections.removeAll()
        for candidate in candidates { accepted(candidate, id: id) }
    }
    var v3ServiceIdentity: UUID? {
        service.isReady && connection != nil && sideStorePid > 0 ? launchID : nil
    }
    fileprivate func applicationReady(_ id: UUID) {
        // finishedLaunching may be repeated; one readiness probe owns this launch.
        guard launchID == id, readinessTask == nil else { return }
        launchApplicationReadyObserved = true
        NSLog("[V3_SERVICE_START] APPLICATION_READY id=%@", id.uuidString)
        readinessTask = Task { @MainActor in
            do {
                try await awaitServiceReady(id)
                guard launchID == id else { return }
                service.signal(.ready, attempt: id)
            } catch {
                guard !Task.isCancelled, launchID == id else { return }
                failed(id, stage: .serviceReadiness, underlying: error,
                    launchSourceStep: .readinessProbe)
            }
        }
    }
    private func awaitServiceReady(_ id: UUID) async throws {
        /*SERVICE_PROBE*/
    }
    fileprivate func failed(_ id: UUID, stage: CombinedFailure.Stage, code: CombinedFailure.Code = .failed,
                            underlying: Error? = nil, launchSourceStep: CombinedFailure.LaunchContext.Step? = nil,
                            requestIdentifierObserved: Bool? = nil, pidObserved: Bool? = nil) {
        guard launchID == id || service.attemptID == id else { return }
        // Never double-wrap: an already structured failure (e.g. the readiness
        // probe's timedOut/invalidResponse) keeps its stage, code, retryable
        // flag and correlation ID instead of degrading to failed/redacted.
        let context: CombinedFailure.LaunchContext? = (refreshContinuation == nil &&
            [.extensionDiscovery, .extensionLaunch, .xpcConnection, .serviceReadiness].contains(stage))
            ? CombinedFailure.LaunchContext(error: underlying, sourceStep: launchSourceStep ?? .unknown,
                stage: stage, requestIdentifierObserved: requestIdentifierObserved ?? launchRequestIdentifierObserved,
                pidObserved: pidObserved ?? (sideStorePid > 0), xpcAccepted: connection != nil,
                applicationReadyObserved: launchApplicationReadyObserved,
                peerPIDRejected: launchPeerPIDRejected) : nil
        let failure = CombinedFailure.preserving(underlying, operation: refreshContinuation == nil ? "connect" : "refresh",
            stage: stage, code: code, id: refreshRunID ?? id.uuidString,
            retryable: refreshContinuation == nil && code == .interrupted ? true : nil,
            launchContext: context)
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
        for candidate in pendingPeerConnections { candidate.invalidate() }
        pendingPeerConnections.removeAll()
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
        let previousRefreshRunID = refreshRunID
        refreshRunID = schedulerRunID
        defer {
            if refreshContinuation == nil, refreshRunID == schedulerRunID {
                refreshRunID = previousRefreshRunID
            }
        }
        // V3_RUNTIME_SHARED_REFRESH_STORE_V1: these keys are the host/service
        // refresh contract. They live in the one runtime App Group, never in a
        // fixed suite name and never in a per-process fallback: a store that
        // cannot be opened is a typed recoverable failure, because a private
        // store would strand the run identity the service is about to write.
        // The structured envelope carries the retryable cause, so the refusal
        // reaches the user as a correlated failure rather than an untyped throw.
        let sharedDefaults: UserDefaults
        do {
            sharedDefaults = try V3SharedAppGroup.requireSharedUserDefaults()
        } catch {
            throw CombinedFailure(operation: "refresh", stage: .persistence,
                code: .unavailable, id: schedulerRunID ?? UUID().uuidString,
                retryable: true, safeCause: .sharedStoreUnavailable)
        }
        if schedulerRunID == nil && V3DirectRefreshPreflightPolicy.isBlocked(
            activeRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshActiveRunID"),
            hostHandoffPending: sharedDefaults.bool(forKey: "liveContainerAutoRefreshHostHandoff"),
            uncertainMutationRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshUncertainMutationRunID")) {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true, safeCause: .operationInProgress)
        }
        // Connect and verify service readiness before claiming local mutation
        // state. The authoritative refreshAdmissionBegin request serializes
        // against active service mutations below, so a separate full snapshot
        // here would only duplicate the readiness probe.
        try await ensureServiceConnected()
        /*REFRESH_READINESS*/
        try Task.checkCancellation()
        guard v3RefreshToken == nil /*MUTATION_GUARD*/ else {
            throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                id: UUID().uuidString, retryable: true, safeCause: .operationInProgress)
        }
        // The connection startup above suspends. Recheck shared scheduler
        // ownership after resuming so a handoff or uncertain mutation created
        // during that await cannot be overwritten by this direct run.
        if schedulerRunID == nil && V3DirectRefreshPreflightPolicy.isBlocked(
            activeRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshActiveRunID"),
            hostHandoffPending: sharedDefaults.bool(forKey: "liveContainerAutoRefreshHostHandoff"),
            uncertainMutationRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshUncertainMutationRunID")) {
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
            expectedRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshExpectedRunID"),
            activeRunID: sharedDefaults.string(forKey: "liveContainerAutoRefreshActiveRunID"),
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
        defer { if v3RefreshTerminalCallbackRunID == run { v3RefreshTerminalCallbackRunID = nil } }
        // Reserve mutation ownership through the SideStore command gate before
        // starting the legacy XPC refresh path. Authentication and refresh
        // admission are serialized there.
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
        sharedDefaults.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")
        defer {
            if !selectedRun.schedulerOwned,
               sharedDefaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") == run {
                sharedDefaults.removeObject(forKey: "liveContainerAutoRefreshExpectedRunID")
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
                    sharedDefaults.set(run, forKey: "liveContainerAutoRefreshUncertainMutationRunID")
                    v3RefreshTerminalCallbackRunID = nil
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
            let resolution = V3RefreshAdmissionFailureResolution.resolve(
                runID: run, dispatchedRunID: v3RefreshDispatchedRunID,
                terminalCallbackRunID: v3RefreshTerminalCallbackRunID)
            switch resolution {
            case .releaseNotDispatched:
                await releaseRefreshAdmission(run,
                    terminalState: "notDispatched")
            case .releaseTerminalFailure:
                await releaseRefreshAdmission(run, terminalState: "failed")
            case .retainUnknownOutcome:
                // XPC loss, timeout, or cancellation after dispatch is not
                // proof that the device-side pipeline settled. Keep the
                // durable service lease until a matching callback or an
                // explicit device-check reconciliation.
                NSLog("[V3_REFRESH_ADMISSION] OUTCOME_UNKNOWN run_id=%@", run)
            }
            throw error
        }
        timeout.cancel()
        await releaseRefreshAdmission(run, terminalState: "completed")
    }
    private func releaseRefreshAdmission(_ runID: String, terminalState: String) async {
        // Run independently of a caller cancellation so a confirmed terminal
        // callback cannot strand the service's admission state.
        await Task { @MainActor in
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "refreshAdmissionEnd", target: runID,
                    payload: ["state": terminalState])
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
        v3RefreshTerminalCallbackRunID = runID
        if let error {
            if let defaults = V3SharedAppGroup.sharedUserDefaults() {
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
              let defaults = V3SharedAppGroup.sharedUserDefaults() else {
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
        guard launchID == id, refreshContinuation != nil, let run = refreshRunID else { return }
        // The legacy callback carries no run ID or verification manifest, so it
        // cannot prove which native run settled. Fail the caller conservatively;
        // admission remains held for explicit reconciliation.
        finishRefreshContinuation(.failure(CombinedFailure.fromEncodedString(error ?? "", expectedID: run) ??
            CombinedFailure(operation: "refresh", stage: .refreshVerification, code: .missingResult, id: run)))
    }
}

/// Builds the extension payload for the dedicated embedded-SideStore launch.
///
/// The normal guest launch is not the only way LiveProcess starts: this launch
/// hosts the embedded SideStore service itself, and that service runs every
/// cross-process store from inside LiveProcess. Without the selected group in
/// this payload LiveProcess publishes nothing, and its own Info.plist fallback
/// is what a re-sign leaves behind: iLoader rewrites ALTAppGroups on the main
/// bundle and signs extensions from regenerated provisioning profiles, so after
/// a re-sign LiveProcess is entitled to `group.com.SideStore.SideStore.<TEAM>`
/// while its Info.plist still lists the pre-resign `group.com.SideStore.SideStore`,
/// which it can no longer open.
///
/// The group therefore comes from the one resolver, already validated for this
/// process, and is the same value the guest launch forwards. There is no second
/// selection policy here: an unopenable identity yields no key at all, and
/// LiveProcess then resolves nothing rather than a different store.
enum V3EmbeddedSideStoreLaunchPayload {
    static let appGroupKey = "lcAppGroupID"

    static func userInfo(bookmark: Data, endpoint: Any,
                         identity: V3SharedAppGroup.Identity?) -> [String: Any] {
        var userInfo: [String: Any] = [
            "selected": "builtinSideStore", "bookmarks": [bookmark], "endpoint": endpoint]
        if let identity { userInfo[appGroupKey] = identity.identifier }
        return userInfo
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
