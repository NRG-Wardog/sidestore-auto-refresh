import Foundation

// A picker selection survives the sheet transition and any in-flight snapshot
// reload. The view may hand off only after UIKit reports dismissal and the
// host is free to present the operation cover.
struct V3InstallPresentationRequest: Equatable {
    let token: String
    let title: String
}

// Local IPA, URL, and catalog installs all converge on the same AppOperation
// builder after resolution has produced an AppProtocol value.
enum V3InstallInputRoute: String, Equatable { case localIPA, remoteURL, catalog }

enum V3InstallPipelineParity {
    static func makeOperation<ResolvedApp, Operation>(
        route: V3InstallInputRoute,
        _ resolvedApp: ResolvedApp,
        build: (ResolvedApp) -> Operation
    ) -> (route: V3InstallInputRoute, operation: Operation) {
        (route, build(resolvedApp))
    }
}

struct V3InstallPresentationHandoff {
    private(set) var pending: V3InstallPresentationRequest?
    private var pickerDismissed = true

    var hasPendingRequest: Bool { pending != nil }

    @discardableResult
    mutating func stage(token: String, title: String, waitsForPickerDismissal: Bool) -> Bool {
        guard pending == nil, UUID(uuidString: token) != nil,
              !title.isEmpty, title.utf8.count <= 160 else { return false }
        pending = V3InstallPresentationRequest(token: token, title: title)
        pickerDismissed = !waitsForPickerDismissal
        return true
    }

    mutating func pickerDidDismiss() {
        guard pending != nil else { return }
        pickerDismissed = true
    }

    mutating func takeIfReady(isLoading: Bool, hasActivePresentation: Bool) -> V3InstallPresentationRequest? {
        guard pickerDismissed, !isLoading, !hasActivePresentation else { return nil }
        let request = pending
        pending = nil
        pickerDismissed = true
        return request
    }
}

// Deletion completion is based on SideStore's pipeline/native uninstall result
// plus its persisted app-library state. Progress and a host-side list update do
// not establish success on their own.
struct V3DeleteCompletionContract {
    enum BackendResult: Equatable { case pending, succeeded, failed }
    enum Terminal: Equatable { case completed, failed }

    private(set) var terminal: Terminal?

    mutating func resolve(backend: BackendResult, nativeUninstallSucceeded: Bool,
                          appStillInAuthoritativeLibrary: Bool, deadlineExpired: Bool,
                          progress: Double) -> Terminal? {
        _ = progress // Progress is deliberately never a success signal.
        guard terminal == nil else { return terminal }
        if backend == .failed {
            terminal = .failed
        } else if !appStillInAuthoritativeLibrary &&
                    (backend == .succeeded || (backend == .pending && nativeUninstallSucceeded && deadlineExpired)) {
            terminal = .completed
        } else if deadlineExpired {
            terminal = .failed
        }
        return terminal
    }
}

final class V3DeleteNativeSuccessRegistry: @unchecked Sendable {
    static let shared = V3DeleteNativeSuccessRegistry()
    private let lock = NSLock()
    private var sessions: Set<String> = []

    func record(sessionID: String) {
        guard UUID(uuidString: sessionID) != nil else { return }
        lock.lock()
        sessions.insert(sessionID)
        lock.unlock()
    }

    func contains(sessionID: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return sessions.contains(sessionID)
    }

    func remove(sessionID: String) {
        lock.lock()
        sessions.remove(sessionID)
        lock.unlock()
    }
}

// Shared state primitives used by the UI/backend and executable regression
// harnesses. These types deliberately carry no paths, credentials, or logs.
struct V3OperationAttemptState {
    private(set) var generation = UUID()
    private(set) var sessionID: String?
    private(set) var isTerminal = false
    private(set) var transitionInFlight = false

    mutating func begin() -> UUID {
        generation = UUID()
        sessionID = generation.uuidString
        isTerminal = false
        return generation
    }

    mutating func bind(sessionID: String, generation: UUID) -> Bool {
        guard self.generation == generation, !isTerminal,
              self.sessionID == sessionID else { return false }
        return true
    }

    @discardableResult
    mutating func acceptStartFailure(generation: UUID) -> Bool {
        guard self.generation == generation, !isTerminal else { return false }
        isTerminal = true
        return true
    }

    func matches(generation: UUID, sessionID: String) -> Bool {
        self.generation == generation && self.sessionID == sessionID && !isTerminal
    }

    @discardableResult
    mutating func accept(state: String, generation: UUID, sessionID: String) -> Bool {
        guard matches(generation: generation, sessionID: sessionID) else { return false }
        if !["working", "awaitingPrompt"].contains(state) { isTerminal = true }
        return true
    }

    mutating func supersede() -> String? {
        let previousSession = sessionID
        generation = UUID()
        sessionID = nil
        isTerminal = true
        return previousSession
    }

    mutating func beginTransition() -> Bool {
        guard !transitionInFlight else { return false }
        transitionInFlight = true
        return true
    }

    mutating func endTransition() {
        transitionInFlight = false
    }
}

struct V3OperationMutationRegistry {
    enum StartResult: Equatable { case started, cancelledBeforeStart, busy }
    enum CancelResult: Equatable { case active, recordedBeforeStart }

    private(set) var activeID: String?
    private var cancelledBeforeStart: [String: Date] = [:]

    mutating func begin(_ id: String, now: Date = Date()) -> StartResult {
        prune(now: now)
        if cancelledBeforeStart.removeValue(forKey: id) != nil { return .cancelledBeforeStart }
        guard activeID == nil else { return .busy }
        activeID = id
        return .started
    }

    mutating func cancel(_ id: String, now: Date = Date()) -> CancelResult {
        if activeID == id { return .active }
        cancelledBeforeStart[id] = now.addingTimeInterval(600)
        prune(now: now)
        return .recordedBeforeStart
    }

    @discardableResult
    mutating func finish(_ id: String) -> Bool {
        guard activeID == id else { return false }
        activeID = nil
        return true
    }

    private mutating func prune(now: Date) {
        cancelledBeforeStart = cancelledBeforeStart.filter { $0.value > now }
        guard cancelledBeforeStart.count > 256 else { return }
        let oldest = cancelledBeforeStart.sorted { $0.value < $1.value }
        for (id, _) in oldest.prefix(cancelledBeforeStart.count - 256) {
            cancelledBeforeStart.removeValue(forKey: id)
        }
    }
}

// Terminal responses are write-once. Callback and cancellation paths may race,
// so the first terminal result is authoritative and later results are ignored.
final class V3TerminalResponse: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: Any]?

    @discardableResult
    func setIfEmpty(_ response: [String: Any]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard storage == nil else { return false }
        storage = response
        return true
    }

    var value: [String: Any]? {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    var isEmpty: Bool { if case nil = value { return true }; return false }
}

struct V3SettingsWriteGeneration {
    private var values: [String: UInt64] = [:]

    mutating func begin(_ key: String) -> UInt64 {
        let next = (values[key] ?? 0) &+ 1
        values[key] = next
        return next
    }

    func isCurrent(_ generation: UInt64, for key: String) -> Bool {
        values[key] == generation
    }

    func current(for key: String) -> UInt64 {
        values[key] ?? 0
    }
}

enum V3RefreshResultVerifier {
    static func verified<Value>(expectedBundleID: String,
                                results: [String: Result<Value, Error>],
                                bundleIdentifier: (Value) -> String) throws -> Value {
        guard let result = results[expectedBundleID] else { throw CombinedRefreshVerificationError.missingResult }
        switch result {
        case .failure(let error): throw error
        case .success(let value):
            guard bundleIdentifier(value) == expectedBundleID else { throw CombinedRefreshVerificationError.staleResult }
            return value
        }
    }
}

// The install pipeline may call the handler even when there is nothing to
// remove. Keep this branch executable so a zero-item prompt cannot regress.
enum V3ExtensionRemovalPromptPolicy {
    static func decide<Element: Hashable, Decision>(
        excessExtensions: Set<Element>,
        whenEmpty: Decision,
        prompt: () async throws -> Decision
    ) async rethrows -> Decision {
        guard !excessExtensions.isEmpty else { return whenEmpty }
        return try await prompt()
    }
}

enum V3RefreshAllPhase: String {
    case idle, starting, refreshing, verifying, completed, failed
}

// Request identity, rather than process-local notifications or global health,
// owns the Home refresh UI. A terminal record is absorbing for this attempt.
struct V3RefreshAllAttemptState {
    private(set) var requestID = ""
    private(set) var runID = ""
    private(set) var phase: V3RefreshAllPhase = .idle
    private(set) var terminalMessage = ""

    var isTerminal: Bool { phase == .completed || phase == .failed }

    mutating func begin(requestID: String) {
        self.requestID = requestID
        runID = ""
        phase = .starting
        terminalMessage = ""
    }

    @discardableResult
    mutating func observe(_ record: [String: Any], schedulerHealth: String? = nil,
                          activeRunID: String? = nil) -> Bool {
        guard !isTerminal,
              record["request_id"] as? String == requestID,
              let observedRunID = record["run_id"] as? String,
              UUID(uuidString: observedRunID) != nil else { return false }
        _ = schedulerHealth
        _ = activeRunID
        if runID.isEmpty { runID = observedRunID }
        guard runID == observedRunID else { return false }

        switch record["state"] as? String {
        case "running":
            if phase == .starting { phase = .refreshing }
        case "verifying":
            phase = .verifying
        case "completed":
            // Health and activeRun defaults may be observed out of order. The
            // correlated terminal record is authoritative, including when a
            // stale activeRun value is still visible to this view.
            guard Self.manifestIsVerified(record["manifest"] as? [String: Any], runID: runID) else {
                phase = .failed
                terminalMessage = "Refresh reported completion without a matching verified manifest."
                return true
            }
            phase = .completed
            let skippedCount = ((record["manifest"] as? [String: Any])?["skipped_ids"] as? [String])?.count ?? 0
            terminalMessage = skippedCount == 0
                ? "Refresh completed. All requested app results were verified."
                : "Refresh completed. Results for this run were verified; \(skippedCount) running app(s) were skipped."
        case "failed":
            phase = .failed
            guard let failure = record["failure"] as? [String: Any],
                  failure["operation"] as? String == "refresh",
                  failure["correlationID"] as? String == runID else {
                terminalMessage = "Refresh failed during refreshVerification, but no safe underlying cause was available."
                return true
            }
            terminalMessage = (record["message"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                ?? "Refresh failed during refreshVerification, but no safe underlying cause was available."
        default:
            return false
        }
        return true
    }

    mutating func markDidNotStart() {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = "Refresh did not start."
    }

    mutating func markTimedOut() {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = "Refresh did not reach a verified terminal result."
    }

    mutating func acknowledge() {
        requestID = ""
        runID = ""
        phase = .idle
        terminalMessage = ""
    }

    static func record(in ledger: [String: Any], requestID: String,
                       runID: String? = nil) -> [String: Any]? {
        let records = ledger.values.compactMap { $0 as? [String: Any] }
        return records.first { record in
            guard record["request_id"] as? String == requestID,
                  let recordRunID = record["run_id"] as? String,
                  UUID(uuidString: recordRunID) != nil else { return false }
            return runID == nil || recordRunID == runID
        }
    }

    private static func manifestIsVerified(_ manifest: [String: Any]?, runID: String) -> Bool {
        guard let manifest, CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID),
              let results = manifest["results"] as? [[String: Any]] else { return false }
        return results.allSatisfy { $0["success"] as? Bool == true }
    }
}

enum V3RefreshAllFailureDiagnostics {
    static func text(requestID: String, runID: String,
                     record: [String: Any]) -> String? {
        guard UUID(uuidString: requestID) != nil, UUID(uuidString: runID) != nil,
              record["request_id"] as? String == requestID,
              record["run_id"] as? String == runID,
              record["state"] as? String == "failed" else { return nil }
        let failure = record["failure"] as? [String: Any]
        let failureMatchesRun = failure?["operation"] as? String == "refresh" &&
            failure?["correlationID"] as? String == runID
        let manifest = record["manifest"] as? [String: Any] ?? [:]
        func safeIDs(_ key: String) -> String {
            guard let values = manifest[key] as? [String] else { return "unknown" }
            let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
            return values.prefix(64).map { value in
                String(value.filter { character in
                    character.unicodeScalars.allSatisfy { allowed.contains($0) }
                }.prefix(160))
            }.joined(separator: ",")
        }
        func recordScalar(_ key: String) -> String {
            let value = (record[key] as? String ?? "unknown")
            return String(value.filter { $0.isASCII && $0 != "\n" && $0 != "\r" }.prefix(80))
        }
        if !failureMatchesRun {
            return [
                "schema=1", "request_id=\(requestID)", "manual_refresh_request=\(requestID)", "run_id=\(runID)",
                "state=failed", "operation=refresh", "stage=refreshVerification",
                "code=staleResult", "correlation=\(runID)",
                "underlying_domain=redacted", "underlying_code=unknown",
                "retryable=unknown", "safe_cause=unknown", "source_step=unknown",
                "source=\(recordScalar("source"))", "origin=\(recordScalar("origin"))",
                "network_preflight=\(recordScalar("network_preflight"))",
                "active_run_id=\(recordScalar("active_run_id"))", "health=\(recordScalar("health"))",
                "terminal_ledger_state=failed", "manifest_run_id=\(recordScalar("manifest_run_id"))",
                "target_app_ids=\(safeIDs("requested_ids"))",
                "requested_app_ids=\(safeIDs("requested_ids"))",
                "attempted_app_ids=\(safeIDs("expected_ids"))",
                "skipped_app_ids=\(safeIDs("skipped_ids"))",
                "safe_message=Refresh failed during refreshVerification, but no safe underlying cause was available."
            ].joined(separator: "\n")
        }
        guard let failure else { return nil }
        func scalar(_ key: String, _ fallback: String) -> String {
            guard let value = failure[key] as? String else { return fallback }
            return value.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ")
        }
        let retryable = (failure["retryable"] as? Bool).map { $0 ? "true" : "false" } ?? "unknown"
        let safeMessage = (record["message"] as? String ?? "Refresh failed during command, but no safe underlying cause was available.")
            .replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ")
        return [
            "schema=1",
            "request_id=\(requestID)",
            "manual_refresh_request=\(requestID)",
            "run_id=\(runID)",
            "state=failed",
            "source=\(recordScalar("source"))",
            "origin=\(recordScalar("origin"))",
            "network_preflight=\(recordScalar("network_preflight"))",
            "active_run_id=\(recordScalar("active_run_id"))",
            "health=\(recordScalar("health"))",
            "terminal_ledger_state=failed",
            "manifest_run_id=\(recordScalar("manifest_run_id"))",
            "target_app_ids=\(safeIDs("requested_ids"))",
            "requested_app_ids=\(safeIDs("requested_ids"))",
            "attempted_app_ids=\(safeIDs("expected_ids"))",
            "skipped_app_ids=\(safeIDs("skipped_ids"))",
            "operation=\(scalar("operation", "refresh"))",
            "stage=\(scalar("stage", "unknown"))",
            "code=\(scalar("code", "unknown"))",
            "correlation=\(scalar("correlationID", runID))",
            "underlying_domain=\(scalar("underlyingDomain", "redacted"))",
            "underlying_code=\((failure["underlyingCode"] as? Int).map { String($0) } ?? "unknown")",
            "retryable=\(retryable)",
            "safe_cause=\(scalar("safeCause", "unknown"))",
            "source_step=\(scalar("sourceStep", "unknown"))",
            "safe_message=\(safeMessage)"
        ].joined(separator: "\n")
    }
}

enum V3RetryDisposition: Equatable {
    case allowed
    case unknown
    case prerequisite
    case blocked
}

struct V3OperationFailureDetails {
    let operation: String
    let stage: String
    let code: String
    let correlation: String
    let underlyingDomain: String
    let underlyingCode: Int
    let retryable: Bool?
    let safeCause: String?
    let sourceStep: String?
    let whatHappened: String
    let whatToDo: String
    let technical: String

    init(_ failure: CombinedFailure) {
        operation = failure.operation
        stage = failure.stage.rawValue
        code = failure.code.rawValue
        correlation = failure.correlationID
        underlyingDomain = failure.underlyingDomain
        underlyingCode = failure.underlyingCode
        retryable = failure.retryable
        safeCause = failure.safeCause?.rawValue
        sourceStep = failure.sourceStep?.rawValue
        whatHappened = failure.safeMessage
        whatToDo = failure.recovery
        technical = failure.technicalDetails
    }

    var retryDisposition: V3RetryDisposition {
        if retryable == false { return .blocked }
        if stage == CombinedFailure.Stage.authentication.rawValue ||
           stage == CombinedFailure.Stage.filePreparation.rawValue ||
           safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue {
            return .prerequisite
        }
        return retryable == true ? .allowed : .unknown
    }

    var recoveryDestination: String? {
        if stage == CombinedFailure.Stage.authentication.rawValue { return "signIn" }
        if stage == CombinedFailure.Stage.filePreparation.rawValue { return "ipa" }
        if safeCause == CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue {
            return "connection"
        }
        if stage == CombinedFailure.Stage.network.rawValue ||
           stage == CombinedFailure.Stage.xpcConnection.rawValue ||
           stage == CombinedFailure.Stage.extensionLaunch.rawValue ||
           stage == CombinedFailure.Stage.serviceReadiness.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.wifiUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.localDevVPNUnavailable.rawValue {
            return "connection"
        }
        if sourceStep == CombinedFailure.SourceStep.provisioningProfileFetch.rawValue {
            return "certificates"
        }
        if sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue ||
           safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue {
            return "certificates"
        }
        return nil
    }

    var recoveryActionTitle: String? {
        switch recoveryDestination {
        case "signIn": return "Open Account & Signing"
        case "ipa": return "Choose IPA Again"
        case "certificates": return "Open Certificates"
        case "connection": return "Open Connection Check"
        default: return nil
        }
    }

    var recommendedAction: String {
        switch safeCause {
        case CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue:
            return "Your current connection may still be healthy. Retry once. If this happens again, open Connection Check."
        case CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue:
            return "The provisioning service timed out for this request. Retry once. If it happens again, open Connection Check."
        case CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue:
            return "The provisioning service could not be reached for this request. Retry once. If it happens again, open Connection Check."
        default: break
        }
        switch recoveryDestination {
        case "signIn": return "Open Account & Signing and complete the required account step."
        case "ipa": return "Choose the IPA again so SideStore can stage a fresh copy."
        case "certificates": return "Open Certificates and review the active certificate and provisioning profile."
        case "setup": return "Open Health Check / Connection and restore the required connection."
        default:
            if retryable == false {
                return "This operation is not marked safe to retry. Check the app and signing status before running it again."
            }
            if retryable == nil {
                return "The service could not determine whether retry is safe. Check the app and signing status, then use Retry (outcome unknown) only if appropriate."
            }
            return whatToDo
        }
    }
}

// Keeps the failed pipeline stage across a Retry transition. A failure while
// creating the next backend session is explicitly separate from pipeline failure.
struct V3OperationRetryContext {
    private(set) var previousFailure: V3OperationFailureDetails?
    private(set) var currentFailure: V3OperationFailureDetails?
    private(set) var retryCouldNotStart = false

    mutating func recordPipelineFailure(_ failure: CombinedFailure) {
        currentFailure = V3OperationFailureDetails(failure)
        retryCouldNotStart = false
    }

    mutating func beginRetry() {
        previousFailure = currentFailure
        currentFailure = nil
        retryCouldNotStart = false
    }

    mutating func operationStarted() {
        previousFailure = nil
        currentFailure = nil
        retryCouldNotStart = false
    }

    mutating func recordStartFailure(_ failure: CombinedFailure) {
        currentFailure = V3OperationFailureDetails(failure)
        retryCouldNotStart = true
    }

    mutating func reset() {
        previousFailure = nil
        currentFailure = nil
        retryCouldNotStart = false
    }

    var whatHappened: String {
        guard let currentFailure else { return "The operation failed." }
        guard retryCouldNotStart else { return currentFailure.whatHappened }
        if let previousFailure {
            if ["timedOut", "interrupted"].contains(currentFailure.code) {
                return "The retry could not be confirmed as started. The previous operation may still be active. Previous attempt: \(previousFailure.whatHappened)"
            }
            return "The retry could not start, so the app operation did not run. Previous attempt: \(previousFailure.whatHappened)"
        }
        if ["timedOut", "interrupted"].contains(currentFailure.code) {
            return "The operation could not be confirmed as started. It may still be active."
        }
        return "The operation could not start, so the app pipeline did not run."
    }

    var whatToDo: String {
        guard let currentFailure else { return "Review the operation and try again only when it is safe." }
        guard retryCouldNotStart else { return currentFailure.recommendedAction }
        return "The retry could not start. Reconnect to SideStore and check the technical details before trying again."
    }

    var technicalDetails: String {
        let current = currentFailure?.technical ?? "No structured failure record was returned."
        guard retryCouldNotStart, let previousFailure else { return current }
        return "retry_start_failure:\n\(current)\nprevious_attempt_failure:\n\(previousFailure.technical)"
    }

    var retryDisposition: V3RetryDisposition {
        guard let currentFailure else { return .unknown }
        return currentFailure.retryDisposition
    }
}
