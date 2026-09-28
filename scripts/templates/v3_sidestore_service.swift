
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
// V3_HEADLESS_SERVICE_V2: headless backend. This file owns the command gate,
// snapshots, and non-interactive reads. All interactive work runs through
// V3HeadlessRuntime sessions; no window, presenter, or visible UI exists here.
// V3_OPERATION_RECOVERY_JOURNAL_V1
// Shared by LiveContainer's App Group and this service process. Serialization
// uses the existing process-shared App Group lock; the record stores only IDs
// and fixed allow-listed markers.
private enum V3OperationRecoveryJournal {
    private static let components = ["Library", "Application Support", "LiveContainer"]
    private static let fileName = "operation-recovery.plist"

    private static func recordURL(containerRoot: URL?) throws -> URL {
        let container: URL
        if let containerRoot { container = containerRoot }
        else if let sharedContainer = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: V3IPAStaging.sideStoreAppGroupIdentifier) {
            container = sharedContainer
        } else {
            throw V3SecretHandoffError.unavailable
        }
        let directory = components.reduce(container.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw V3SecretHandoffError.unavailable
            }
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        } catch { throw V3SecretHandoffError.unavailable }
        return directory.appendingPathComponent(fileName, isDirectory: false)
    }

    private static func withLease<T>(containerRoot: URL?, _ body: (URL) throws -> T) throws -> T {
        try V3AppGroupProcessLock.withLock(containerRoot: containerRoot) {
            try body(recordURL(containerRoot: containerRoot))
        }
    }

    private static func read(_ url: URL) throws -> V3OperationRecoveryLease {
        guard FileManager.default.fileExists(atPath: url.path) else { return V3OperationRecoveryLease() }
        do {
            let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard values.isRegularFile == true, values.isSymbolicLink != true,
                  url.resolvingSymlinksInPath().standardizedFileURL == url.standardizedFileURL else {
                throw V3SecretHandoffError.malformed
            }
            let data = try Data(contentsOf: url)
            guard data.count <= 4096,
                  let plist = try PropertyListSerialization.propertyList(from: data, format: nil),
                  let record = V3OperationRecoveryRecord.decodePropertyList(plist) else {
                throw V3SecretHandoffError.malformed
            }
            return V3OperationRecoveryLease(record: record)
        } catch { throw V3SecretHandoffError.malformed }
    }

    private static func write(_ lease: V3OperationRecoveryLease, to url: URL) throws {
        guard let record = lease.record else {
            if FileManager.default.fileExists(atPath: url.path) {
                do { try FileManager.default.removeItem(at: url) }
                catch { throw V3SecretHandoffError.unavailable }
            }
            return
        }
        do {
            let data = try PropertyListSerialization.data(fromPropertyList: record.propertyListRepresentation,
                format: .binary, options: 0)
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch { throw V3SecretHandoffError.unavailable }
    }

    static func current(containerRoot: URL? = nil) throws -> V3OperationRecoveryRecord? {
        try withLease(containerRoot: containerRoot) { try read($0).record }
    }

    @discardableResult
    static func discardUnreadableAfterDeviceCheck(userConfirmed: Bool,
                                                   containerRoot: URL? = nil) throws -> Bool {
        guard userConfirmed else { return false }
        return try withLease(containerRoot: containerRoot) { url in
            do {
                _ = try read(url)
                return false
            } catch {
                try FileManager.default.removeItem(at: url)
                return true
            }
        }
    }

    static func reserve(sessionID: String, kind: String, stagedIPAToken: String? = nil,
                        containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            let result = lease.reserve(sessionID: sessionID, kind: kind, stagedIPAToken: stagedIPAToken)
            if result == .reserved { try write(lease, to: url) }
            return result != .blocked
        }
    }

    static func beginDispatch(sessionID: String, kind: String, stagedIPAToken: String? = nil,
                              containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.beginDispatch(sessionID: sessionID, kind: kind, stagedIPAToken: stagedIPAToken) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func settle(sessionID: String, replySessionID: String?, state: String?, backendSettled: Bool,
                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.settle(sessionID: sessionID, replySessionID: replySessionID,
                state: state, backendSettled: backendSettled) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func settleRefreshAdmission(runID: String, terminalState: String?,
                                       terminalConfirmed: Bool,
                                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.settleRefreshAdmission(runID: runID, terminalState: terminalState,
                terminalConfirmed: terminalConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedRefreshAdmissionAfterRequestCancellation(runID: String,
                                                                       containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedRefreshAdmissionAfterRequestCancellation(runID: runID) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func reconcileRefreshAdmissionAfterDeviceCheck(runID: String, userConfirmed: Bool,
                                                          containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.reconcileRefreshAdmissionAfterDeviceCheck(runID: runID,
                userConfirmed: userConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func reconcileAfterDeviceCheck(sessionID: String, userConfirmed: Bool,
                                          containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.reconcileAfterDeviceCheck(sessionID: sessionID, userConfirmed: userConfirmed) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedAfterNotDispatched(sessionID: String, expectedRequestID: String,
                                                 replyRequestID: String?, operationNotDispatched: Bool,
                                                 containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedAfterNotDispatched(sessionID: sessionID,
                expectedRequestID: expectedRequestID, replyRequestID: replyRequestID,
                operationNotDispatched: operationNotDispatched) else { return false }
            try write(lease, to: url)
            return true
        }
    }

    @discardableResult
    static func clearPreparedAfterConfirmedCancellation(sessionID: String, replySessionID: String?,
        state: String?, backendSettled: Bool, stopConfirmed: Bool, knownStarted: Bool,
        containerRoot: URL? = nil) throws -> Bool {
        try withLease(containerRoot: containerRoot) { url in
            var lease = try read(url)
            guard lease.clearPreparedAfterConfirmedCancellation(sessionID: sessionID,
                replySessionID: replySessionID, state: state, backendSettled: backendSettled,
                stopConfirmed: stopConfirmed, knownStarted: knownStarted) else { return false }
            try write(lease, to: url)
            return true
        }
    }
}

// V3_NATIVE_CALLBACK_GATE_V1: native completions can arrive on arbitrary queues.
// Cancellation does not manufacture a native completion or release the mutation gate.
// The owning service retains it until the real callback returns or the process retires.
final class V3ServiceCallbackGate: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?
    init(_ continuation: CheckedContinuation<Void, Error>) { self.continuation = continuation }
    func settle(_ result: Result<Void, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}
// V3_NATIVE_CALLBACK_GATE_END

private struct V3KnownSourcePolicyFailure: Error {
    enum Kind: Equatable { case network, invalidResponse }
    let kind: Kind
    let underlyingDomain: String
    let underlyingCode: Int

    init(_ error: Error) {
        let cause = error as NSError
        kind = [NSURLErrorDomain, NSPOSIXErrorDomain, "CFNetwork"].contains(cause.domain)
            ? .network : .invalidResponse
        underlyingDomain = [NSURLErrorDomain, NSPOSIXErrorDomain, "CFNetwork", "NSCocoaErrorDomain"]
            .contains(cause.domain) ? cause.domain : "redacted"
        underlyingCode = cause.code
    }
}

@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    var tasks: [String: Task<Void, Never>] = [:]
    private var deadlineTasks: [String: Task<Void, Never>] = [:]
    var cancellations: [String: () -> Void] = [:]
    var completed: [String: (data: Data, deadline: Date)] = [:]
    private var completedRequestFingerprints: [String: Data] = [:]
    private var inFlightRequestFingerprints: [String: Data] = [:]
    private var completedCacheBudget = V3MutationReplyCacheBudget()
    private var pendingCancellationReplyReservations: Set<String> = []
    var mutationID: String?
    private var refreshAdmission = V3RefreshAdmissionLease()
    private var pendingRefreshAdmissionRequests: Set<String> = []
    private var pendingAuthStartSessions: [String: String] = [:]
    private var knownSourcesUpdateTask: Task<Void, Error>?

    @objc(execute:reply:)
    nonisolated static func execute(_ data: Data, reply: @escaping (Data) -> Void) {
        Task { @MainActor in shared.receive(data, reply: reply) }
    }

    private func receive(_ data: Data, reply: @escaping (Data) -> Void) {
        // V3_CORRELATED_INVALID_REQUEST_V1: a request that fails the strict
        // contract is still answered with its own correlation and operation
        // whenever a well-formed envelope can be read, so the host can
        // classify the real reason instead of receiving an idless token it must
        // treat as a stale reply.
        guard let request = V3WireContract.decodeRequest(data) else {
            reply(encode(invalidRequestReply(for: data)))
            return
        }
        guard let id = request["id"] as? String,
              let operation = request["operation"] as? String,
              let deadline = request["deadline"] as? Date,
              deadline > Date(), deadline.timeIntervalSinceNow <= 610 else {
            reply(encode(invalidRequestReply(for: data)))
            return
        }
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        let operationSessionID = V3OperationSessionCorrelationPolicy.requestSessionID(
            operation: operation, target: target, payload: payload)
        let requestFingerprint = V3RequestReplayPolicy.fingerprint(data)
        let expiredReplies = completed.compactMap { key, value in
            value.deadline <= Date() ? (key, value.data.count) : nil
        }
        for (key, byteCount) in expiredReplies {
            completed.removeValue(forKey: key)
            completedRequestFingerprints.removeValue(forKey: key)
            completedCacheBudget.remove(byteCount)
        }
        _ = refreshAdmission.expire()
        if let previous = completed[id] {
            guard V3RequestReplayPolicy.matches(
                cachedFingerprint: completedRequestFingerprints[id], incomingRequestData: data) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
            reply(previous.data)
            return
        }
        // Bind IDs while work is still running as well as after completion.
        // This check must precede the cancellation fast path: a different
        // command reusing an active mutation ID must not cancel unrelated work.
        if tasks[id] != nil {
            guard V3RequestReplayPolicy.matchesInFlight(
                cachedFingerprint: inFlightRequestFingerprints[id], incomingRequestData: data) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
        }
        if operation == "cancel" {
            let target = request["target"] as? String ?? ""
            guard let cancelScope = (request["payload"] as? [String: Any])?["scope"] as? String,
                  V3WireContract.cancellationScopes.contains(cancelScope) else {
                reply(encode(invalidRequestReply(for: data), operation: operation))
                return
            }
            guard reserveCancellationReply(operation: operation, id: id) else {
                let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                    id: id, retryable: true, safeCause: .responseCapacityUnavailable)
                reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                    operation: operation))
                return
            }
            let isPendingRefreshAdmission = cancelScope == "request" &&
                (pendingRefreshAdmissionRequests.contains(target) || refreshAdmission.requestID == target)
            if cancelScope == "request", let session = pendingAuthStartSessions[target] {
                _ = V3HeadlessRuntime.shared.auth.cancelBeforeBegin(id: session)
            }
            if !V3HeadlessRuntime.shared.cancelSession(target, scope: cancelScope) {
                tasks[target]?.cancel()
                cancellations[target]?()
            }
            var cancellationReply: [String: Any] = ["id": id, "version": 1, "ok": true]
            if isPendingRefreshAdmission {
                let refreshRunID = refreshAdmission.runID
                if refreshAdmission.release(requestID: target), let refreshRunID,
                   (try? V3OperationRecoveryJournal.clearPreparedRefreshAdmissionAfterRequestCancellation(
                    runID: refreshRunID)) == true {
                    cancellationReply["refreshAdmissionReleased"] = true
                }
            }
            let encoded = encode(cancellationReply, operation: operation)
            _ = finishCancellationReplyReservation(id: id, requestFingerprint: requestFingerprint,
                deadline: deadline, encoded: encoded)
            reply(encoded)
            return
        }
        guard tasks[id] == nil else {
            let failure = operation == "sourceRemoveConfirmed"
                ? CombinedFailure(operation: "source", stage: .source, code: .busy, id: id,
                                  retryable: true, safeCause: .sourceRemoveBusy)
                : operation == "opStart"
                ? CombinedFailure(operation: operation, stage: .command, code: .busy, id: id,
                                  retryable: true, safeCause: .operationInProgress)
                : CombinedFailure(operation: operation, stage: .command, code: .busy, id: id,
                                  retryable: true, safeCause: .operationInProgress)
            var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
            reply(encode(response, operation: operation))
            return
        }
        let mutation = !V3WireContract.readOperations.contains(operation)
        let authenticationActive = V3HeadlessRuntime.shared.auth.hasActiveSession
        let authContinuation = V3ServiceMutationAdmissionPolicy.permitsAuthenticationControl(
            operation,
            ownsActiveSession: V3HeadlessRuntime.shared.auth.ownsActiveSession(target),
            authenticationActive: authenticationActive)
        let recoveryRecord: V3OperationRecoveryRecord?
        let recoveryReadFailed: Bool
        do { recoveryRecord = try V3OperationRecoveryJournal.current(); recoveryReadFailed = false }
        catch { recoveryRecord = nil; recoveryReadFailed = true }
        if let recoveryRecord, recoveryRecord.kind == "refreshAll",
           !refreshAdmission.owns(recoveryRecord.sessionID) {
            _ = refreshAdmission.restoreLost(runID: recoveryRecord.sessionID)
        }
        let recoveryDecision = V3ServiceRecoveryAdmissionPolicy.decide(operation: operation,
            target: target, payload: payload, operationSessionID: operationSessionID,
            recovery: recoveryRecord, recoveryReadFailed: recoveryReadFailed,
            refreshOwnerLost: refreshAdmission.ownerLost)
        let policyOperationMutationActive = V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: operation, target: target,
            activeOperationID: V3HeadlessRuntime.shared.operations.activeMutationID) ||
            recoveryDecision.blocksMutation
        let operationMutationActive = operation == "opRecoveryReconcile" && recoveryDecision.recoveryControl
            ? false : policyOperationMutationActive
        let refreshRelease = recoveryDecision.refreshRelease
        let controlReply = V3MutationReplyCacheBudget.isControlReply(operation: operation)
        let cacheResponse = V3MutationReplyCacheBudget.shouldCacheResponse(operation: operation)
        let cancellationReplay = V3RequestReplayPolicy.requiresCompletedReply(operation: operation)
        let responseCapacityAvailable = !cacheResponse || (!mutation && !cancellationReplay) ||
            (cancellationReplay
                ? canReserveCancellationReply(operation: operation)
                : completed.count + pendingCancellationReplyReservations.count <
                    V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply) &&
             completedCacheBudget.canReserve(
                maximumResponseBytes: V3MutationReplyCacheBudget.minimumReplyBytesToAdmit(operation: operation),
                preservingControlCapacity: !controlReply) &&
             V3MutationReplyCacheBudget.canAdmit(operation: operation,
                completedReplyCount: completed.count + pendingCancellationReplyReservations.count))
        if cancellationReplay && !responseCapacityAvailable {
            let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                id: id, retryable: true, safeCause: .responseCapacityUnavailable)
            reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                operation: operation))
            return
        }
        guard V3ServiceMutationAdmissionPolicy.admits(isMutation: mutation,
            anotherMutationActive: mutationID != nil || operationMutationActive,
            authenticationActive: authenticationActive,
            isAuthContinuation: authContinuation,
            responseCapacityAvailable: responseCapacityAvailable,
            refreshActive: refreshAdmission.isActive,
            isRefreshRelease: refreshRelease) else {
            let safeCause = V3ServiceMutationBusyCausePolicy.safeCause(
                operation: operation,
                anotherMutationActive: mutationID != nil || operationMutationActive,
                responseCapacityAvailable: responseCapacityAvailable,
                refreshActive: refreshAdmission.isActive, refreshRelease: refreshRelease,
                authenticationActive: authenticationActive,
                isAuthContinuation: authContinuation)
            let sourceRemoval = safeCause == .sourceRemoveBusy
            let failure = CombinedFailure(
                operation: sourceRemoval ? "source" : operation.hasPrefix("refreshAdmission") ? "refresh" : operation,
                stage: sourceRemoval ? .source : .command,
                code: .busy, id: id, retryable: true, safeCause: safeCause)
            var response: [String: Any] = ["version": 1, "id": id, "error": "busy", "failure": failure.wire]
            if ["opStart", "authBegin", "authRetryProvisioning"].contains(operation) {
                response["operationNotDispatched"] = true
            }
            clearPreparedOperationRecoveryIfProven(request: request, reply: response)
            reply(encode(response, operation: operation))
            return
        }
        if cancellationReplay, !reserveCancellationReply(operation: operation, id: id) {
            let failure = CombinedFailure(operation: operation, stage: .command, code: .busy,
                id: id, retryable: true, safeCause: .responseCapacityUnavailable)
            reply(encode(["version": 1, "id": id, "error": "busy", "failure": failure.wire],
                operation: operation))
            return
        }
        if mutation { mutationID = id }
        if operation == "refreshAdmissionBegin" { pendingRefreshAdmissionRequests.insert(id) }
        if ["authBegin", "authRetryProvisioning"].contains(operation),
           let session = (request["payload"] as? [String: Any])?["session"] as? String {
            pendingAuthStartSessions[id] = session
        }
        inFlightRequestFingerprints[id] = requestFingerprint
        tasks[id] = Task { @MainActor in
            defer {
                tasks[id] = nil
                inFlightRequestFingerprints.removeValue(forKey: id)
                deadlineTasks.removeValue(forKey: id)?.cancel()
                cancellations[id] = nil
                pendingRefreshAdmissionRequests.remove(id)
                pendingAuthStartSessions[id] = nil
                if mutationID == id { mutationID = nil }
            }
            var response: [String: Any] = ["version": 1, "id": id]
            do {
                guard DatabaseManager.shared.isStarted else { throw ServiceError.notReady }
                try Task.checkCancellation()
                response["result"] = try await run(operation, request: request, id: id)
                try Task.checkCancellation()
                response["ok"] = true
            } catch {
                if operation == "refreshAdmissionBegin", error is CancellationError,
                   let refreshRunID = request["target"] as? String,
                   refreshAdmission.release(runID: refreshRunID) {
                    _ = try? V3OperationRecoveryJournal.clearPreparedRefreshAdmissionAfterRequestCancellation(
                        runID: refreshRunID)
                }
                // Raw framework errors can contain URLs, authentication data or server responses.
                // Detailed errors remain inside the SideStore process.
                if let serviceError = error as? ServiceError { response["error"] = serviceError.rawValue }
                else if let headlessError = error as? V3SideStoreServiceError { response["error"] = headlessError.rawValue }
                else if error is CancellationError { response["error"] = "cancelled" }
                else { response["error"] = "operationFailed" }
                if operation == "opStart",
                   V3OperationStartDispatchPolicy.provesNotDispatched(
                    resultWasReturned: response["result"] != nil) {
                    response["operationNotDispatched"] = true
                } else if ["authBegin", "authRetryProvisioning"].contains(operation),
                          response["result"] == nil,
                          (error is ServiceError || error is V3SideStoreServiceError) {
                    response["operationNotDispatched"] = true
                }
                var stage: CombinedFailure.Stage
                switch operation {
                case "snapshot": stage = .serviceReadiness
                case "catalog": stage = .catalog
                case "authBegin", "authPoll", "authRespond", "authCancel", "authRetryProvisioning", "accountExport", "accountImport": stage = .authentication
                case "opStart", "opPoll", "opAnswer", "opCancel": stage = .command
                case "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate": stage = .signing
                case "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles", "syncAppIDs": stage = .authentication
                case "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed", "refreshSources": stage = .source
                default: stage = .command
                }
                var readinessNotReady = false
                if let serviceError = error as? ServiceError, case .notReady = serviceError {
                    stage = .serviceReadiness
                    readinessNotReady = true
                }
                if let serviceError = error as? V3SideStoreServiceError, case .notReady = serviceError {
                    stage = .serviceReadiness
                    readinessNotReady = true
                }
                if operation.hasPrefix("refreshAdmission") { stage = .serviceReadiness }
                if operation == "sourceRemoveConfirmed" {
                    if let serviceError = error as? ServiceError, case .notReady = serviceError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .serviceReadiness,
                            code: .notReady, id: id, retryable: true).wire
                    } else if let headlessError = error as? V3SideStoreServiceError,
                              case .notReady = headlessError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .serviceReadiness,
                            code: .notReady, id: id, retryable: true).wire
                    } else if let serviceError = error as? ServiceError, case .busy = serviceError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .busy, id: id, retryable: true, safeCause: .sourceRemoveBusy).wire
                    } else if let headlessError = error as? V3SideStoreServiceError, case .busy = headlessError {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .busy, id: id, retryable: true, safeCause: .sourceRemoveBusy).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .failed,
                            id: id, underlying: error, safeCause: .sourceRemoveFailed,
                            sourceStep: .catalogRead).wire
                    }
                } else if let serviceError = error as? ServiceError {
                    let code: CombinedFailure.Code
                    switch serviceError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    }
                    if ["authBegin", "authRetryProvisioning"].contains(operation) && code == .busy {
                        response["failure"] = CombinedFailure(operation: "signIn", stage: .serviceReadiness,
                            code: .busy, id: id, retryable: true, safeCause: .operationInProgress).wire
                    } else if operation.hasPrefix("refreshAdmission") {
                        response["failure"] = CombinedFailure(operation: "refresh",
                            stage: .command, code: code, id: id,
                            retryable: code == .busy,
                            safeCause: code == .busy ? .operationInProgress : nil).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: operation, stage: stage,
                            code: code, id: id,
                            retryable: V3ServiceReadinessRetryPolicy.retryable(
                                operation: operation, stage: stage, code: code,
                                typedNotReady: readinessNotReady)).wire
                    }
                } else if let headlessError = error as? V3SideStoreServiceError {
                    let code: CombinedFailure.Code
                    switch headlessError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    case .authRequired: code = .notReady
                    case .persistenceUnverified: code = .failed
                    // V3_CATALOG_SOURCE_MISSING_V1: a typed, non-manifest cause.
                    case .catalogSourceUnavailable: code = .unavailable
                    }
                    if headlessError == .catalogSourceUnavailable {
                        response["failure"] = CombinedFailure(operation: "catalog", stage: .catalog,
                            code: code, id: id, safeCause: .catalogSourceUnavailable,
                            sourceStep: .catalogRead).wire
                    } else if headlessError == .invalidRequest && ["sourcePreview", "sourceAddConfirmed"].contains(operation) {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source,
                            code: .invalidConfiguration, id: id, safeCause: .sourceInvalidURL,
                            sourceStep: .sourceDownload).wire
                    } else if headlessError == .persistenceUnverified && operation == "sourceAddConfirmed" {
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: code,
                            id: id, safeCause: .sourcePersistenceUnverified, sourceStep: .catalogRead).wire
                    } else {
                        response["failure"] = CombinedFailure(operation: operation, stage: stage, code: code, id: id,
                            retryable: V3ServiceReadinessRetryPolicy.retryable(
                                operation: operation, stage: stage, code: code,
                                typedNotReady: readinessNotReady)).wire
                    }
                } else if let policyError = error as? V3KnownSourcePolicyFailure {
                    let network = policyError.kind == .network
                    response["failure"] = CombinedFailure(operation: "source", stage: .source,
                        code: network ? .failed : .invalidResponse, id: id,
                        underlying: NSError(domain: policyError.underlyingDomain, code: policyError.underlyingCode),
                        retryable: network ? true : nil,
                        safeCause: network ? .knownSourcePolicyNetworkFailure : .knownSourcePolicyInvalidResponse,
                        sourceStep: network ? .knownSourcePolicyFetch : .knownSourcePolicyParsing).wire
                } else if let sourceError = error as? V3SourceCommandError {
                    switch sourceError.kind {
                    case .network:
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .failed,
                            id: id, underlying: NSError(domain: sourceError.domain, code: sourceError.code),
                            safeCause: .sourceNetworkFailure, sourceStep: .sourceDownload).wire
                    case .invalidManifest:
                        response["failure"] = CombinedFailure(operation: "source", stage: .source, code: .invalidResponse,
                            id: id, safeCause: .sourceInvalidManifest, sourceStep: .manifestParsing).wire
                    }
                } else if operation == "catalog" {
                    response["failure"] = CombinedFailure(operation: "catalog", stage: .catalog, code: .failed,
                        id: id, underlying: error, safeCause: .catalogUnavailable, sourceStep: .catalogRead).wire
                } else if let structuredFailure = error as? CombinedFailure {
                    // V3_WIRE_FAILURE_CORRELATION_V1: preserve the typed cause
                    // but correlate the reply to this XPC request, not to the
                    // auth/operation session that caused the failure.
                    response["failure"] = structuredFailure.correlating(to: id).wire
                } else {
                    response["failure"] = CombinedFailure.capture(
                        V3HeadlessPairingFailure.tagIfInvalidPairing(error),
                        operation: operation, stage: stage, id: id).wire
                }
                clearPreparedOperationRecoveryIfProven(request: request, reply: response)
            }
            let encoded = encode(response, operation: operation)
            if mutation && cacheResponse || cancellationReplay && cacheResponse {
                let cached: Bool
                if cancellationReplay {
                    cached = finishCancellationReplyReservation(id: id,
                        requestFingerprint: requestFingerprint, deadline: deadline, encoded: encoded)
                } else if completedCacheBudget.record(encoded.count, controlResponse: controlReply) {
                    completed[id] = (encoded, deadline)
                    completedRequestFingerprints[id] = requestFingerprint
                    cached = true
                } else {
                    cached = false
                }
                if !cached {
                    // Admission reserves one full maximum-size response before
                    // dispatch. Reaching this branch means cache accounting
                    // lost a reservation while the command was running.
                    debugLog("[V3_WIRE] mutation_reply_cache_reservation_failed operation=\(operation)")
                }
            }
            reply(encoded)
        }
        deadlineTasks[id] = Task { @MainActor in
            do {
                try await Task.sleep(nanoseconds: UInt64(max(0, deadline.timeIntervalSinceNow) * 1_000_000_000))
            } catch { return }
            guard self.tasks[id] != nil else {
                self.deadlineTasks[id] = nil
                return
            }
            self.deadlineTasks[id] = nil
            if let session = self.pendingAuthStartSessions[id] {
                _ = V3HeadlessRuntime.shared.auth.cancelBeforeBegin(id: session)
            }
            self.tasks[id]?.cancel()
            self.cancellations[id]?()
        }
    }

    enum ServiceError: String, Error { case notReady, invalidRequest, notFound, unsupported, busy }

    // Reads only the envelope fields the contract already trusts: the request ID
    // must be a valid UUID and the operation must be on the allow list. Nothing
    // from the payload is echoed back.
    private func invalidRequestReply(for data: Data) -> [String: Any] {
        let identity = V3WireContract.invalidRequestIdentity(from: data)
        let id = identity.id ?? UUID().uuidString
        let operation = identity.operation ?? "command"
        let identifierCollision = identity.id.map {
            V3RequestReplayPolicy.isIdentifierCollision(
                cachedFingerprint: inFlightRequestFingerprints[$0] ?? completedRequestFingerprints[$0],
                incomingRequestData: data)
        } ?? false
        var response: [String: Any] = ["version": 1, "id": id, "error": "invalidRequest",
                "failure": CombinedFailure(operation: operation, stage: .command,
                    code: .invalidConfiguration, id: id).wire]
        if V3RequestReplayPolicy.mayClaimNotDispatched(
            operation: operation, identifierCollision: identifierCollision) {
            response["operationNotDispatched"] = true
        }
        return response
    }

    private func canReserveCancellationReply(operation: String) -> Bool {
        guard V3RequestReplayPolicy.requiresCompletedReply(operation: operation) else { return false }
        let controlReply = V3MutationReplyCacheBudget.isControlReply(operation: operation)
        return completed.count + pendingCancellationReplyReservations.count <
                V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply) &&
            completedCacheBudget.canReserve(
                maximumResponseBytes: V3WireContract.responseLimit,
                preservingControlCapacity: !controlReply) &&
            V3MutationReplyCacheBudget.canAdmit(operation: operation, completedReplyCount: completed.count)
    }

    private func reserveCancellationReply(operation: String, id: String) -> Bool {
        guard !pendingCancellationReplyReservations.contains(id),
              canReserveCancellationReply(operation: operation),
              completedCacheBudget.record(V3WireContract.responseLimit, controlResponse: true) else { return false }
        pendingCancellationReplyReservations.insert(id)
        return true
    }

    private func finishCancellationReplyReservation(id: String, requestFingerprint: Data,
                                                     deadline: Date, encoded: Data) -> Bool {
        guard pendingCancellationReplyReservations.remove(id) != nil else { return false }
        completedCacheBudget.remove(V3WireContract.responseLimit)
        guard completedCacheBudget.record(encoded.count, controlResponse: true) else { return false }
        completed[id] = (encoded, deadline)
        completedRequestFingerprints[id] = requestFingerprint
        return true
    }

    // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the encoder and its typed fallback
    // live in the shared wire contract so the host's classifier and the
    // service's encoder can be executed together against real bytes. The
    // classification travels in the structured envelope's safeCause, not only in
    // the legacy "error" token: the host prefers the structured failure and
    // throws it, so a token-only classification was discarded on arrival and
    // every encoding failure reached the user as a generic invalidResponse.
    private func encode(_ value: [String: Any], operation: String = "command") -> Data {
        let correlationID = value["id"] as? String ?? ""
        let encoded = V3ResponseEncoder.encodeDetailed(value, operation: operation,
            limit: V3WireContract.responseLimit)
        // A fallback reply is a defect and it must be visible. A serialization or
        // oversize regression is otherwise indistinguishable in the field from
        // the failure it causes, because the host reports a generic
        // invalidResponse either way. Only the classification and the
        // correlation are recorded; the value that could not be encoded, and
        // the raw error text, never are.
        if let token = encoded.fallbackToken {
            debugLog("[V3_ENCODE] FAIL operation=\(operation) request_id=\(correlationID) classification=\(token) correlated=\(correlationID.isEmpty ? "no" : "yes")")
        }
        return encoded.data
    }

    private func settleOperationRecoveryIfTerminal(_ reply: [String: Any], requestedSessionID: String) {
        let state = reply["state"] as? String
        let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        let backendSettled = !outcomeUnknown && V3WireContract.strictBool(reply["backendSettled"]) == true
        _ = try? V3OperationRecoveryJournal.settle(sessionID: requestedSessionID,
            replySessionID: reply["session"] as? String, state: state, backendSettled: backendSettled)
    }

    private func clearPreparedOperationRecoveryIfProven(request: [String: Any], reply: [String: Any]) {
        guard request["operation"] as? String == "opStart",
              let requestID = request["id"] as? String,
              reply["id"] as? String == requestID,
              V3WireContract.strictBool(reply["operationNotDispatched"]) == true,
              let payload = request["payload"] as? [String: Any],
              let sessionID = payload["session"] as? String else { return }
        _ = try? V3OperationRecoveryJournal.clearPreparedAfterNotDispatched(
            sessionID: sessionID, expectedRequestID: requestID,
            replyRequestID: reply["id"] as? String, operationNotDispatched: true)
    }

    private func run(_ operation: String, request: [String: Any], id: String) async throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        switch operation {
        case "snapshot":
            if V3WireContract.strictBool(payload["readinessOnly"]) == true {
                return ["ready": DatabaseManager.shared.isStarted]
            }
            return try snapshot()
        case "refreshAdmissionBegin":
            guard refreshAdmission.acquire(runID: target,
                    requestID: id,
                    authenticationActive: V3HeadlessRuntime.shared.auth.hasActiveSession,
                    anotherMutationActive: (mutationID != nil && mutationID != id) ||
                        V3HeadlessRuntime.shared.operations.activeMutationID != nil
                    // This ownership lifetime follows the native refresh timeout,
                    // not the short XPC begin-request deadline.
                    ) else {
                throw ServiceError.busy
            }
            do {
                guard try V3OperationRecoveryJournal.reserve(sessionID: target, kind: "refreshAll") else {
                    throw ServiceError.busy
                }
            } catch {
                _ = refreshAdmission.release(runID: target)
                throw ServiceError.busy
            }
            return ["runID": target, "admitted": true]
        case "refreshAdmissionEnd":
            guard let parsed = UUID(uuidString: target), parsed.uuidString == target else {
                throw ServiceError.invalidRequest
            }
            guard let terminalState = payload["state"] as? String,
                  ["completed", "failed", "notDispatched"].contains(terminalState) else {
                throw ServiceError.invalidRequest
            }
            let recovery: V3OperationRecoveryRecord?
            do { recovery = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            guard let recovery else {
                guard !refreshAdmission.isActive else { throw ServiceError.busy }
                return ["runID": target, "released": true, "alreadyReleased": true]
            }
            guard recovery.kind == "refreshAll", recovery.sessionID == target,
                  try V3OperationRecoveryJournal.settleRefreshAdmission(runID: target,
                    terminalState: terminalState, terminalConfirmed: true) else {
                throw ServiceError.notFound
            }
            _ = refreshAdmission.release(runID: target)
            return ["runID": target, "released": true]
        case "refreshAdmissionReconcile":
            guard let parsed = UUID(uuidString: target), parsed.uuidString == target,
                  V3WireContract.strictBool(payload["userConfirmed"]) == true,
                  refreshAdmission.ownerLost else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.reconcileRefreshAdmissionAfterDeviceCheck(
                    runID: target, userConfirmed: true) else { throw ServiceError.invalidRequest }
            } catch { throw ServiceError.busy }
            _ = refreshAdmission.release(runID: target)
            return ["runID": target, "released": true, "reconciled": true]
        case "appIcon":
            let app: InstalledApp = try object(target)
            guard let image = try await app.loadIcon() else { return [:] }
            try Task.checkCancellation()
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            let thumbnail = UIGraphicsImageRenderer(size: CGSize(width: 192, height: 192), format: format).image { _ in
                image.draw(in: CGRect(x: 0, y: 0, width: 192, height: 192))
            }
            guard let data = thumbnail.pngData(), data.count <= 262_144 else { return [:] }
            return ["icon": data]
        case "backupResult":
            guard mutationID != nil, ["success", "failure"].contains(target) else { throw ServiceError.invalidRequest }
            let result: Result<Void, Error> = target == "success" ? .success(()) : .failure(ServiceError.unsupported)
            NotificationCenter.default.post(name: AppDelegate.appBackupDidFinish, object: nil,
                userInfo: [AppDelegate.appBackupResultKey: result])
            return [:]
        case "catalog":
            // V3_CATALOG_DIAGNOSTICS_V1: the catalog read is measured with
            // privacy-safe facts only: whether the source row exists, whether
            // its identifier matches the request, and how many catalog rows were
            // returned. No source identifier, URL, name, bundle identifier,
            // description, object URI, or filesystem path is ever recorded.
            let sourceQuery = NSFetchRequest<Source>(entityName: "Source")
            sourceQuery.predicate = NSPredicate(format: "identifier == %@", target)
            sourceQuery.fetchLimit = 1
            let storedSource = try context.fetch(sourceQuery).first
            // V3_CATALOG_SOURCE_MISSING_V1: a source that no longer exists must
            // not be reported as a valid source with zero apps, or a stale
            // catalog screen becomes indistinguishable from an empty catalog.
            // This is NOT a manifest problem and is never reported as one.
            guard let storedSource else {
                throw V3SideStoreServiceError.catalogSourceUnavailable
            }
            let sourceMatch = storedSource.identifier == target
            let query = NSFetchRequest<StoreApp>(entityName: "StoreApp")
            query.predicate = NSCompoundPredicate(andPredicateWithSubpredicates: [
                StoreApp.visibleAppsPredicate, NSPredicate(format: "sourceIdentifier == %@", target)])
            let offset = request["cursor"] as? Int ?? 0
            query.sortDescriptors = [NSSortDescriptor(key: "name", ascending: true),
                                     NSSortDescriptor(key: "bundleIdentifier", ascending: true)]
            query.fetchOffset = offset
            query.fetchLimit = 51
            let fetched = try context.fetch(query)
            let apps = Array(fetched.prefix(50))
            debugLog("[V3_CATALOG] RESULT operation=catalog stage=catalogRead request_id=\(id) cursor=\(offset) source_found=yes source_identifier_match=\(sourceMatch ? "yes" : "no") catalog_row_count=\(apps.count) has_more=\(fetched.count > 50 ? "yes" : "no")")
            return ["apps": apps.map { app in
                // V3_CATALOG_ROW_PLIST_SAFE_V1: the row is built explicitly and
                // every value is unwrapped. An app that is not installed has no
                // installedVersion, and an absent key is the correct encoding of
                // an absent value: placing a Swift Optional into this dictionary
                // boxes Optional.none into Any, which PropertyListSerialization
                // cannot encode, so the whole catalog response would fail to
                // serialize even though the Core Data read succeeded.
                V3WireContract.V3PropertyListValue.dictionary([
                    "identifier": app.objectID.uriRepresentation().absoluteString,
                    "bundleID": app.bundleIdentifier,
                    "name": app.name,
                    // Coalesced to a concrete String: the host renders this as a
                    // non-optional version label, so the placeholder is part of
                    // the display contract rather than a leaked Optional.
                    "version": app.latestSupportedVersion?.version ?? "Unavailable",
                    "developer": app.developerName,
                    "description": app.localizedDescription,
                    "iconURL": app.iconURL.absoluteString,
                    "downloadURL": app.latestSupportedVersion?.downloadURL.absoluteString ?? "",
                    "canInstall": app.latestSupportedVersion != nil,
                    "installedID": app.installedApp?.objectID.uriRepresentation().absoluteString ?? "",
                    // The only field that was genuinely optional. It is omitted
                    // entirely when the app is not installed. The host already
                    // models it as an optional, so no placeholder is invented and
                    // no Optional is boxed into the response graph.
                    "installedVersion": app.installedApp?.version
                ])
            }, "nextCursor": fetched.count > 50 ? offset + 50 : -1]
        case "signOut":
            try V3BackendCommands.prepareSignOut()
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
            return try snapshot()
        case "syncAppIDs":
            if !AuthManager.shared.isAuthenticated {
                throw V3SideStoreServiceError.authRequired
            }
            try await callback { done in AppManager.shared.syncAppIDs(completionHandler: done) }
            return try snapshot()
        case "clearCache":
            try await callback { done in AppManager.shared.clearAppCache(completion: done) }
            return try snapshot()
        case "refreshSources":
            try await ensureKnownSourcesUpdated()
            try await callback { done in AppManager.shared.updateAllSources(completion: done) }
            return try snapshot()
        case "jit":
            let app: InstalledApp = try object(target)
            try await callback { done in AppManager.shared.enableJIT(for: app, completionHandler: done) }
            return try snapshot()
        case "authBegin":
            guard let deadline = payload["sessionDeadline"] as? Date,
                  deadline > Date(), deadline.timeIntervalSinceNow <= V3WireContract.authSessionLifetime + 10,
                  let session = payload["session"] as? String, session == target else {
                throw ServiceError.invalidRequest
            }
            guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }
            return await V3HeadlessRuntime.shared.auth.begin(deadline: deadline,
                requestDeadline: request["deadline"] as? Date, sessionID: session)
        case "authRetryProvisioning":
            // V3_PROVISIONING_RESUME_V1: Apple authentication already succeeded.
            // This re-enters provisioning with the saved session so credentials
            // and 2FA are never requested a second time.
            guard let deadline = payload["sessionDeadline"] as? Date,
                  deadline > Date(), deadline.timeIntervalSinceNow <= V3WireContract.authSessionLifetime + 10,
                  let session = payload["session"] as? String, session == target else {
                throw ServiceError.invalidRequest
            }
            guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }
            return await V3HeadlessRuntime.shared.auth.begin(deadline: deadline,
                mode: .resumeProvisioning, requestDeadline: request["deadline"] as? Date,
                sessionID: session)
        case "authPoll":
            guard let reply = V3HeadlessRuntime.shared.auth.poll(id: target) else {
                throw CombinedFailure(operation: "signIn", stage: .authentication,
                    code: .invalidResponse, id: target, retryable: false,
                    safeCause: .authSessionUnavailable)
            }
            return reply
        case "authRespond":
            guard let secretToken = payload["secretToken"] as? String,
                  let promptID = payload["prompt"] as? String else {
                throw ServiceError.invalidRequest
            }
            let answer = try V3SecretHandoff.consumeStringDictionary(secretToken)
            guard let reply = V3HeadlessRuntime.shared.auth.respond(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            return reply
        case "authCancel":
            guard await V3HeadlessRuntime.shared.auth.cancelAndWait(id: target) else { throw ServiceError.invalidRequest }
            guard let reply = V3HeadlessRuntime.shared.auth.poll(id: target) else { throw ServiceError.invalidRequest }
            return reply
        case "opRecoveryPrepare":
            guard let kind = payload["kind"] as? String,
                  let session = payload["session"] as? String,
                  let operationTarget = payload["target"] as? String else {
                throw ServiceError.invalidRequest
            }
            let stagedIPAToken = kind == "installSharedIPA" ? operationTarget : nil
            do {
                guard try V3OperationRecoveryJournal.reserve(sessionID: session, kind: kind,
                    stagedIPAToken: stagedIPAToken) else { throw ServiceError.busy }
            } catch { throw ServiceError.busy }
            return ["session": session, "kind": kind, "phase": "prepared"]
        case "recoveryDiscardUnreadable":
            guard target.isEmpty, V3WireContract.strictBool(payload["userConfirmed"]) == true else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.discardUnreadableAfterDeviceCheck(userConfirmed: true) else {
                    throw ServiceError.invalidRequest
                }
            } catch { throw ServiceError.busy }
            if refreshAdmission.ownerLost, let runID = refreshAdmission.runID {
                _ = refreshAdmission.release(runID: runID)
            }
            return ["discardedUnreadable": true]
        case "opRecoveryReconcile":
            guard let parsedID = UUID(uuidString: target), parsedID.uuidString == target,
                  V3WireContract.strictBool(payload["userConfirmed"]) == true else {
                throw ServiceError.invalidRequest
            }
            do {
                guard try V3OperationRecoveryJournal.reconcileAfterDeviceCheck(
                    sessionID: target, userConfirmed: true) else { throw ServiceError.invalidRequest }
            } catch { throw ServiceError.busy }
            return ["session": target, "reconciled": true]
        case "opStart":
            guard let kind = payload["kind"] as? String,
                  let session = payload["session"] as? String,
                  let deadline = request["deadline"] as? Date else { throw ServiceError.invalidRequest }
            let value: Bool?
            if let rawValue = payload["value"] {
                guard let parsedValue = V3WireContract.strictBool(rawValue) else {
                    throw ServiceError.invalidRequest
                }
                value = parsedValue
            } else {
                value = nil
            }
            let opTarget = payload["target"] as? String ?? target
            let stagedIPAToken = kind == "installSharedIPA" ? opTarget : nil
            do {
                guard try V3OperationRecoveryJournal.beginDispatch(sessionID: session, kind: kind,
                    stagedIPAToken: stagedIPAToken) else { throw ServiceError.busy }
            } catch { throw ServiceError.busy }
            let result = await V3HeadlessRuntime.shared.operations.start(kind: kind, target: opTarget,
                value: value, sessionID: session, deadline: deadline)
            settleOperationRecoveryIfTerminal(result, requestedSessionID: session)
            return result
        case "opPoll":
            guard let reply = V3HeadlessRuntime.shared.operations.poll(id: target) else { throw ServiceError.invalidRequest }
            settleOperationRecoveryIfTerminal(reply, requestedSessionID: target)
            return reply
        case "opAnswer":
            guard let secretToken = payload["secretToken"] as? String,
                  let promptID = payload["prompt"] as? String else {
                throw ServiceError.invalidRequest
            }
            let answer = try V3SecretHandoff.consumeStringDictionary(secretToken)
            guard let reply = V3HeadlessRuntime.shared.operations.answer(id: target, promptID: promptID, answer: answer) else {
                throw ServiceError.invalidRequest
            }
            settleOperationRecoveryIfTerminal(reply, requestedSessionID: target)
            return reply
        case "opCancel":
            let knownStarted = V3WireContract.strictBool(payload["knownStarted"]) ?? true
            guard let result = await V3HeadlessRuntime.shared.operations.cancelAndWait(
                id: target, knownStarted: knownStarted) else {
                throw ServiceError.invalidRequest
            }
            if knownStarted {
                settleOperationRecoveryIfTerminal(result, requestedSessionID: target)
            } else {
                _ = try? V3OperationRecoveryJournal.clearPreparedAfterConfirmedCancellation(
                    sessionID: target, replySessionID: result["session"] as? String,
                    state: result["state"] as? String,
                    backendSettled: V3WireContract.strictBool(result["backendSettled"]) == true,
                    stopConfirmed: V3WireContract.strictBool(result["stopConfirmed"]) == true,
                    knownStarted: false)
            }
            return result
        case "ipaCleanup":
            let recovery: V3OperationRecoveryRecord?
            do { recovery = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            if recovery?.stagedIPAToken == target {
                throw ServiceError.busy
            }
            try V3HeadlessRuntime.shared.operations.cleanupIPA(token: target)
            return [:]
        case "ipaActiveTokens":
            let lease: V3OperationRecoveryRecord?
            do { lease = try V3OperationRecoveryJournal.current() }
            catch { throw ServiceError.busy }
            var tokens = V3HeadlessRuntime.shared.operations.activeStagedIPATokens()
            if let token = lease?.stagedIPAToken { tokens.append(token) }
            return ["tokens": Array(Set(tokens)).sorted().prefix(512).map { $0 }]
        case "certList":
            return ["certificates": V3BackendCommands.certificates()]
        case "certSetActive":
            guard let certificate = CertificateManager.shared.getLocalCertificate(serialNumber: target) else {
                throw ServiceError.notFound
            }
            try CertificateManager.shared.setActiveCertificate(certificate)
            return try snapshot()
        case "certDelete":
            CertificateManager.shared.deleteCertificate(serialNumber: target)
            return try snapshot()
        case "certPortalList":
            return ["certificates": try await V3BackendCommands.portalCertificates()]
        case "certRevoke":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let certificates = try await DeveloperPortalProxy.shared.fetchCertificates(team: team)
            guard let certificate = certificates.first(where: { $0.serialNumber == target }) else {
                throw ServiceError.notFound
            }
            _ = try await DeveloperPortalProxy.shared.revokeCertificate(certificate, team: team)
            return try snapshot()
        case "certCreate":
            _ = try await AuthManager.shared.getAuthenticatedSession()
            let team = try await AuthManager.shared.getAuthenticatedTeam()
            let name = UIDevice.current.name
            let created = try await DeveloperPortalProxy.shared.createCertificate(
                machineName: "SideStore - \(team.name)'s \(name)", team: team)
            CertificateManager.shared.saveCertificate(created)
            if let local = CertificateManager.shared.getLocalCertificate(serialNumber: created.serialNumber) {
                try? CertificateManager.shared.setActiveCertificate(local)
            }
            return try snapshot()
        case "devTeams":
            return ["teams": try await V3BackendCommands.developerTeams()]
        case "devDevices":
            return ["devices": try await V3BackendCommands.developerDevices()]
        case "devAppIDs":
            return ["appIDs": try await V3BackendCommands.developerAppIDs()]
        case "devGroups":
            return ["groups": try await V3BackendCommands.developerGroups()]
        case "devProfiles":
            return ["profiles": try await V3BackendCommands.developerProfiles()]
        case "sourcePreview":
            guard V3SourceAddPersistencePolicy.validatedURL(target) != nil else {
                throw V3SideStoreServiceError.invalidRequest
            }
            try await ensureKnownSourcesUpdated()
            return try await V3BackendCommands.sourcePreview(urlString: target)
        case "sourceAddConfirmed":
            guard V3SourceAddPersistencePolicy.validatedURL(target) != nil else {
                throw V3SideStoreServiceError.invalidRequest
            }
            try await ensureKnownSourcesUpdated()
            let addResult = try await V3BackendCommands.sourceAddConfirmed(urlString: target)
            var updated = try snapshot()
            let persistedSources = try await V3BackendCommands.authoritativeSourceRows()
            let sourceID = addResult["identifier"] as? String ?? ""
            guard persistedSources.contains(where: { $0["identifier"] as? String == sourceID }) else {
                throw V3SideStoreServiceError.persistenceUnverified
            }
            updated["sources"] = persistedSources
            return updated.merging(addResult) { _, authoritative in authoritative }
        case "sourceRemoveConfirmed":
            try await V3BackendCommands.sourceRemoveConfirmed(identifier: target)
            return try snapshot()
        case "pairingImportData":
            try V3BackendCommands.pairingImportData(token: target)
            return try snapshot()
        case "settingsGet":
            return V3BackendCommands.settingsGet()
        case "settingsSet":
            try V3BackendCommands.settingsSet(payload: payload)
            return try snapshot()
        case "anisetteList":
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteReset":
            _ = try await AnisetteServersManager.shared.resetToOriginalState()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "anisetteSync":
            _ = try await AnisetteServersManager.shared.syncWithRemote()
            return ["servers": await V3BackendCommands.anisetteList()]
        case "sidesignGet":
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignSet":
            guard let secretToken = payload["secretToken"] as? String else { throw ServiceError.invalidRequest }
            try await V3BackendCommands.sidesignSet(token: secretToken)
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignReset":
            _ = SideSignConfigManager.shared.resetToDefaults()
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignImport":
            try await V3BackendCommands.sidesignImport(token: target)
            return ["secretToken": try await V3BackendCommands.sidesignConfigToken()]
        case "sidesignExport":
            return ["secretToken": try await V3BackendCommands.sidesignExportToken()]
        case "logTail":
            return V3BackendCommands.logTail()
        case "healthSnapshot":
            return await V3BackendCommands.health()
        case "accountExport":
            guard let secretToken = payload["secretToken"] as? String else {
                throw ServiceError.invalidRequest
            }
            let password = try V3SecretHandoff.consumeString(secretToken)
            guard !password.isEmpty else { throw ServiceError.invalidRequest }
            let includeApple: Bool
            if let rawIncludeApple = payload["includeApple"] {
                guard let parsedIncludeApple = V3WireContract.strictBool(rawIncludeApple) else {
                    throw ServiceError.invalidRequest
                }
                includeApple = parsedIncludeApple
            } else {
                includeApple = false
            }
            return ["backup": try V3BackendCommands.accountExport(password: password, includeApplePassword: includeApple)]
        case "accountImport":
            guard let secretToken = payload["secretToken"] as? String else { throw ServiceError.invalidRequest }
            let password = try V3SecretHandoff.consumeString(secretToken)
            return try V3BackendCommands.accountImport(token: target, password: password)
        default: throw ServiceError.invalidRequest
        }
    }

    private func object<T: NSManagedObject>(_ identifier: String) throws -> T {
        guard let url = URL(string: identifier),
              let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
              let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else { throw ServiceError.notFound }
        return object
    }

    // Native callbacks may fire more than once or race on arbitrary queues.
    // The first terminal result wins; late callbacks are ignored. Cancellation
    // never releases the continuation early: the task keeps awaiting the
    // native terminal callback so the service mutation gate is not freed early.
    final class V3ServiceCallbackGate {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<Void, Error>?
        private var settled = false
        init(_ continuation: CheckedContinuation<Void, Error>) {
            self.continuation = continuation
        }
        func settle(_ result: Result<Void, Error>) {
            lock.lock()
            let pending = continuation
            let first = !settled
            settled = true
            continuation = nil
            lock.unlock()
            if first, let pending = pending {
                pending.resume(with: result)
            }
        }
    }
    // V3_NATIVE_CALLBACK_GATE_END

    private func callback(_ start: (@escaping (Result<Void, Error>) -> Void) -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let gate = V3ServiceCallbackGate(continuation)
            start { result in gate.settle(result) }
        }
    }

    // Upstream SideStore refreshes its server-owned allow/block source lists at
    // launch. The headless backend has no launch screen, so source preview, add,
    // and source refresh run a bounded cached preflight before AppManager source
    // operations rely on UserDefaults.blockedSources. Concurrent callers share
    // one UpdateKnownSourcesOperation with a 15-second total time bound.
    private func ensureKnownSourcesUpdated() async throws {
        let defaults = UserDefaults.standard
        let hasCachedBlocklist = defaults.blockedSources != nil
        let lastUpdated = defaults.object(forKey: "v3KnownSourcesUpdatedAt") as? Date
        guard V3KnownSourcePreflightPolicy.shouldRefresh(
            hasCachedBlocklist: hasCachedBlocklist,
            lastSuccessfulUpdate: lastUpdated) else { return }

        if let knownSourcesUpdateTask {
            do { try await knownSourcesUpdateTask.value }
            catch { throw V3KnownSourcePolicyFailure(error) }
            guard defaults.blockedSources != nil else { throw ServiceError.notReady }
            return
        }
        let task = Task<Void, Error> { @MainActor in
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    _ = try await UpdateKnownSourcesOperation().execute()
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: 15_000_000_000)
                    throw URLError(.timedOut)
                }
                let completedUpdate = try await group.next()
                guard case .some = completedUpdate else { throw CancellationError() }
                group.cancelAll()
            }
        }
        knownSourcesUpdateTask = task
        defer { knownSourcesUpdateTask = nil }
        do { try await task.value }
        catch { throw V3KnownSourcePolicyFailure(error) }
        guard defaults.blockedSources != nil else { throw ServiceError.notReady }
        defaults.set(Date(), forKey: "v3KnownSourcesUpdatedAt")
    }

    private func snapshot() throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let apps = InstalledApp.all(in: context)
        let sources = try context.fetch(NSFetchRequest<Source>(entityName: "Source"))
        let team = DatabaseManager.shared.activeTeam()
        let certificate = CertificateManager.shared.activeCertificate?.certificate.x509
        // V3_AUTH_SESSION_SNAPSHOT_V1: Apple authentication can succeed before
        // the account row is activated, because activation happens at the end
        // of SignInOperation.finalizeAuthentication. Reporting "Not signed in"
        // in that window made a successful sign-in look like a failed one and
        // hid the authenticated session from Retry Provisioning. The session
        // itself is authoritative; the active row is reported separately as
        // provisioningIncomplete so no active team is ever implied.
        let activeAccount = DatabaseManager.shared.activeAccount()
        let authenticated = AuthManager.shared.isAuthenticated
        let account = activeAccount?.appleID
            ?? (authenticated ? AuthManager.shared.currentAppleID : nil)
            ?? "Not signed in"
        let activeAuthenticationSessionID = V3HeadlessRuntime.shared.auth.activeSessionIDForSnapshot
        _ = refreshAdmission.expire()
        let operationRecovery: V3OperationRecoveryRecord?
        let recoveryJournalUnreadable: Bool
        do {
            operationRecovery = try V3OperationRecoveryJournal.current()
            recoveryJournalUnreadable = false
        } catch {
            operationRecovery = nil
            recoveryJournalUnreadable = true
        }
        var response: [String: Any] = ["updatedAt": Date(), "busy": mutationID != nil ||
                    activeAuthenticationSessionID != nil ||
                    V3HeadlessRuntime.shared.operations.activeMutationID != nil || refreshAdmission.isActive ||
                    operationRecovery != nil || recoveryJournalUnreadable,
                 "recoveryJournalUnreadable": recoveryJournalUnreadable,
                 "account": account,
                 "authenticated": authenticated,
                 "authenticationActive": activeAuthenticationSessionID != nil,
                 "provisioningIncomplete": authenticated && activeAccount == nil,
                "provisioningRetryAvailable": V3HeadlessRuntime.shared.auth.canResumeProvisioning(),
                "team": team?.name ?? "No active team", "teamID": team?.identifier ?? "",
                "signing": team == nil ? "Sign in required" : "Team selected",
                "certificate": CertificateManager.shared.activeCertificate == nil ? "No active certificate" : "Active certificate available",
                "certificateExpiration": certificate?.expiryDate ?? Date.distantPast,
                "pairing": V3BackendCommands.pairingFileStatus(),
                "installedApps": apps.map { app in
                    ["identifier": app.objectID.uriRepresentation().absoluteString,
                     "bundleID": app.bundleIdentifier, "name": app.name, "version": app.version,
                     "isActive": app.isActive, "expirationDate": app.expirationDate,
                     "refreshedDate": app.refreshedDate, "hasUpdate": app.hasUpdate,
                     "certificateStatus": app.certificateStatusRaw ?? "unknown",
                     "openURL": app.openAppURL.absoluteString,
                     "isHost": app.bundleIdentifier == StoreApp.altstoreAppID] as [String: Any]
                },
                "sources": sources.map { source in
                    ["identifier": source.identifier, "name": source.name, "subtitle": source.subtitle ?? "",
                     "url": source.sourceURL.absoluteString, "appCount": source.apps.count,
                     "canRemove": source.identifier != Source.altStoreIdentifier] as [String: Any]
                },
                "settings": ["betaUpdates": UserDefaults.standard.isBetaUpdatesEnabled,
                             "idleTimeoutDisabled": UserDefaults.standard.isIdleTimeoutDisableEnabled,
                             "responseCachingDisabled": UserDefaults.standard.responseCachingDisabled,
                             "verboseOperations": UserDefaults.standard.isVerboseOperationsLoggingEnabled]]
        if let activeSessionID = activeAuthenticationSessionID {
            response["authenticationSessionID"] = activeSessionID
        }
        if let operationRecovery, operationRecovery.kind != "refreshAll" {
            var safeRecovery: [String: Any] = ["session": operationRecovery.sessionID,
                "kind": operationRecovery.kind, "phase": operationRecovery.phase.rawValue]
            if let token = operationRecovery.stagedIPAToken { safeRecovery["stagedIPAToken"] = token }
            response["operationRecovery"] = safeRecovery
        }
        if let operationRecovery, operationRecovery.kind == "refreshAll",
           !refreshAdmission.owns(operationRecovery.sessionID) {
            _ = refreshAdmission.restoreLost(runID: operationRecovery.sessionID)
        }
        if let operationRecovery, operationRecovery.kind == "refreshAll",
           refreshAdmission.ownerLost {
            response["refreshRecovery"] = ["runID": operationRecovery.sessionID, "ownerLost": true]
        } else if refreshAdmission.ownerLost, let runID = refreshAdmission.runID {
            response["refreshRecovery"] = ["runID": runID, "ownerLost": true]
        }
        return response
    }
}
