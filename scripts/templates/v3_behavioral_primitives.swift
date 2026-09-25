import Foundation

// Operation phases are fed by PipelineExecutor's actual PipelineStep callback.
// Unknown steps intentionally collapse to Working... rather than inferring a
// stage from progress percentages.
enum V3OperationPhase: String, Equatable, CaseIterable {
    case working
    case preparing
    case preparingIPA
    case downloadingIPA
    case verifying
    case preparingSigning
    case fetchingProvisioningProfile
    case signing
    case preparingInstallation
    case transferringToDevice
    case installing
    case refreshing
    case deleting
    case backingUp
    case restoring
    case updating
    case cleaningUp

    var label: String {
        switch self {
        case .working: return "Working..."
        case .preparing: return "Preparing..."
        case .preparingIPA: return "Preparing IPA..."
        case .downloadingIPA: return "Downloading IPA..."
        case .verifying: return "Verifying..."
        case .preparingSigning: return "Preparing signing..."
        case .fetchingProvisioningProfile: return "Fetching provisioning profile..."
        case .signing: return "Signing..."
        case .preparingInstallation: return "Preparing installation..."
        case .transferringToDevice: return "Transferring to device..."
        case .installing: return "Installing..."
        case .refreshing: return "Refreshing..."
        case .deleting: return "Removing app..."
        case .backingUp: return "Backing up..."
        case .restoring: return "Restoring..."
        case .updating: return "Updating app..."
        case .cleaningUp: return "Cleaning up..."
        }
    }

    static func forPipelineStep(_ step: String, downloadUsesNetwork: Bool = false) -> Self? {
        switch step {
        case "userCustomization", "preflightChecks", "cacheApp": return .preparing
        case "downloadApp": return downloadUsesNetwork ? .downloadingIPA : .preparingIPA
        case "verifyApp", "verifyCertificate": return .verifying
        case "updateAppCertificate": return .preparingSigning
        case "fetchProvisioningProfiles": return .fetchingProvisioningProfile
        case "embedSigningCert", "resignApp", "cacheSigningCert": return .signing
        case "stageApp", "stageBackupApp", "changeAppIcon", "removeAppExtensions",
             "prepareAppExtensionBundleIDs", "createIPA", "exportResignedIPA":
            return .preparingInstallation
        case "sendApp": return .transferringToDevice
        case "installApp": return .installing
        case "refreshApp": return .refreshing
        case "uninstallApp", "removeApp": return .deleting
        case "backupAppData": return .backingUp
        case "restoreAppData": return .restoring
        case "deactivateApp", "markAppInactive": return .updating
        case "removeBackupData", "cleanStagedApp": return .cleaningUp
        default: return nil
        }
    }
}

struct V3OperationPhaseTracker: Equatable {
    private(set) var phase: V3OperationPhase = .working

    mutating func recordPipelineStep(_ step: String, downloadUsesNetwork: Bool = false) {
        phase = V3OperationPhase.forPipelineStep(step, downloadUsesNetwork: downloadUsesNetwork) ?? .working
    }

    mutating func record(_ phase: V3OperationPhase) {
        self.phase = phase
    }
}

enum V3NormalizedProgress {
    static func clamp(_ value: Double) -> Double {
        guard value.isFinite else { return 0 }
        return min(max(value, 0), 1)
    }

    static func displayValue(_ value: Double, state: String) -> Double {
        state == "completed" ? 1 : clamp(value)
    }

    static func percent(_ value: Double, state: String) -> Int {
        Int((displayValue(value, state: state) * 100).rounded())
    }
}

enum V3SourceAddDecision: Equatable { case save, alreadyAdded }

enum V3SourceAddPersistencePolicy {
    static func decision(sourceIsPersisted: Bool) -> V3SourceAddDecision {
        sourceIsPersisted ? .alreadyAdded : .save
    }

    static func verifiedResult(identifier: String, alreadyAdded: Bool,
                                authoritativeCount: Int) -> [String: Any]? {
        guard !identifier.isEmpty, authoritativeCount == 1 else { return nil }
        return ["identifier": identifier,
                "added": !alreadyAdded,
                "alreadyAdded": alreadyAdded,
                "persistenceVerified": true]
    }

    static func confirmationMessage(_ result: [String: Any]) -> String? {
        guard result["persistenceVerified"] as? Bool == true,
              let identifier = result["identifier"] as? String, !identifier.isEmpty,
              let added = result["added"] as? Bool,
              let alreadyAdded = result["alreadyAdded"] as? Bool else { return nil }
        if added && !alreadyAdded { return "Source added." }
        if !added && alreadyAdded { return "Source already added." }
        return nil
    }

    static func validatedURL(_ value: String) -> URL? {
        guard let url = URL(string: value),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }
}

enum V3JITLessReadiness: String, Equatable {
    case notRequired
    case setupRequired
    case certificateImported
    case needsCertificateRefresh
    case revoked
    case activeCertificateRevoked
    case activeCertificateExpired
    case ready
    case unknown

    var isReady: Bool { self == .ready || self == .notRequired }
}

// This policy describes only the LiveContainer copy and safe public identity
// facts. Import/repair remains LiveContainer's canonical settings flow.
enum V3JITLessReadinessPolicy {
    static func evaluate(osMajor: Int, hasCopy: Bool, activeCertificateExists: Bool,
                         activeCertificateStatus: String = "unknown", identitiesMatch: Bool?,
                         validationStatus: Int?, validationFailed: Bool) -> V3JITLessReadiness {
        guard osMajor >= 26 else { return .notRequired }
        if activeCertificateExists && activeCertificateStatus == "revoked" { return .activeCertificateRevoked }
        if activeCertificateExists && activeCertificateStatus == "expired" { return .activeCertificateExpired }
        guard activeCertificateExists else { return .unknown }
        guard hasCopy else { return .setupRequired }
        guard let validationStatus else { return .certificateImported }
        if validationStatus == 1 {
            if activeCertificateExists, identitiesMatch == true { return .activeCertificateRevoked }
            return .revoked
        }
        guard validationStatus == 0, !validationFailed else { return .unknown }
        guard let identitiesMatch else { return .unknown }
        return identitiesMatch ? .ready : .needsCertificateRefresh
    }
}

enum V3TwoFactorStep: String, Equatable {
    case chooseDeliveryMethod
    case choosePhoneNumber
    case deliveryRequested
    case enterVerificationCode
    case verifyingCode
    case completed
    case failed
    case cancelled

    var progressLabel: String? {
        switch self {
        case .choosePhoneNumber: return "Choose a phone number for this verification request..."
        case .deliveryRequested: return "Requesting verification..."
        case .verifyingCode: return "Verifying code..."
        default: return nil
        }
    }

    static func afterDeliveryChoice(_ method: String, phoneCount: Int) -> Self? {
        guard ["trustedDevice", "sms", "voice"].contains(method) else { return nil }
        return method == "sms" || method == "voice" ? (phoneCount > 1 ? .choosePhoneNumber : .deliveryRequested) : .deliveryRequested
    }

    static func afterDelivery(_ method: String) -> Self? {
        ["trustedDevice", "sms", "voice"].contains(method) ? .enterVerificationCode : nil
    }

    static func afterVerification(accepted: Bool) -> Self {
        accepted ? .completed : .enterVerificationCode
    }

    static var afterChangeMethod: Self { .chooseDeliveryMethod }
}

enum V3AuthTerminalPolicy {
    static func resolve(authenticationSucceeded: Bool, authoritativeAccountMatches: Bool,
                        provisioningFailed: Bool, cancelled: Bool) -> String {
        if authenticationSucceeded || authoritativeAccountMatches {
            return provisioningFailed || cancelled ? "authenticatedProvisioningIncomplete" : "completed"
        }
        return cancelled ? "cancelled" : "failed"
    }
}

enum V3AuthPromptFailurePolicy {
    static func applying(reply: [String: Any], current: [String: Any]?) -> [String: Any]? {
        (reply["previousFailure"] as? [String: Any]) ?? current
    }

    static func isVisible(_ failure: [String: Any]?, promptKind: String?) -> Bool {
        failure != nil && promptKind == "credentials"
    }

    static func clearingAfterSubmission(_ failure: [String: Any]?, promptKind: String?) -> [String: Any]? {
        promptKind == "credentials" ? nil : failure
    }

    static func clearingOnDismiss(_ failure: [String: Any]?) -> [String: Any]? { nil }
}

// A picker selection survives dismissal and any in-flight snapshot reload.
// The picker and operation occupy one host-owned cover, so SwiftUI never has to
// race two unrelated root presentations.
struct V3InstallPresentationRequest: Equatable {
    let attemptID: UUID
    let operationID: UUID
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

// Coordinates a direct root-owned UIKit picker. If the anchor is not in the
// window hierarchy yet, the attempt remains queued until UIKit reports that
// the anchor appeared; it is never converted into a nested SwiftUI sheet.
final class V3InstallPickerPresentationCoordinator {
    enum Phase: String, Equatable { case idle, queued, presenting, presented, dismissing, awaitingDismissal }
    enum Decision: Equatable {
        case present(UUID)
        case queued
        case dismissed(UUID)
        case rejected(UUID, String)
        case none
    }

    private(set) var phase: Phase = .idle
    private(set) var attemptID: UUID?

    func request(attemptID: UUID, presenterReady: Bool,
                 presenterBusy: Bool) -> Decision {
        guard phase == .idle else { return .rejected(attemptID, "presenter_busy") }
        self.attemptID = attemptID
        guard presenterReady else {
            phase = .queued
            return .queued
        }
        guard !presenterBusy else {
            phase = .queued
            return .rejected(attemptID, "presentation_active")
        }
        phase = .presenting
        return .present(attemptID)
    }

    func presenterBecameReady(isBusy: Bool) -> Decision {
        switch phase {
        case .queued:
            guard let attemptID else { return .none }
            guard !isBusy else {
                return .rejected(attemptID, "presentation_active")
            }
            phase = .presenting
            return .present(attemptID)
        case .awaitingDismissal:
            guard !isBusy, let attemptID else { return .none }
            reset()
            return .dismissed(attemptID)
        default:
            return .none
        }
    }

    @discardableResult
    func didPresent(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .presenting else { return false }
        phase = .presented
        return true
    }

    @discardableResult
    func beginDismissal(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .presenting || phase == .presented else { return false }
        phase = .dismissing
        return true
    }

    @discardableResult
    func didDismiss(attemptID id: UUID, presenterIsClear: Bool) -> Bool {
        guard attemptID == id, phase == .dismissing || phase == .presented else { return false }
        guard presenterIsClear else {
            phase = .awaitingDismissal
            return false
        }
        reset()
        return true
    }

    @discardableResult
    func fail(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase != .idle else { return false }
        reset()
        return true
    }

    private func reset() {
        phase = .idle
        attemptID = nil
    }
}

struct V3InstallAttemptState {
    enum Phase: String, Equatable {
        case idle, pickerPresented, staging, waitingForPickerDismissal, waitingForReload
        case readyToPresentOperation, operationPresented, operationStarted, terminal, cleaningUp
    }

    private(set) var phase: Phase = .idle
    private(set) var attemptID: UUID?
    private(set) var operationID: UUID?
    private(set) var token: String?
    private(set) var title: String?
    private(set) var backendSessionID: String?
    private(set) var terminalOutcome: String?
    private(set) var operationViewDidAppear = false

    var isIdle: Bool { phase == .idle }
    var hasActiveAttempt: Bool { !isIdle }

    mutating func beginPicker() -> UUID? {
        guard isIdle else { return nil }
        reset()
        let id = UUID()
        attemptID = id
        phase = .pickerPresented
        return id
    }

    mutating func beginDirectStaging() -> UUID? {
        guard isIdle else { return nil }
        reset()
        let id = UUID()
        attemptID = id
        phase = .staging
        return id
    }

    @discardableResult
    mutating func beginStaging(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .pickerPresented else { return false }
        phase = .staging
        return true
    }

    @discardableResult
    mutating func staged(attemptID id: UUID, token: String, title: String,
                         waitsForPickerDismissal: Bool, isLoading: Bool) -> Bool {
        guard attemptID == id, phase == .staging, UUID(uuidString: token) != nil,
              !title.isEmpty, title.utf8.count <= 160 else { return false }
        self.token = token
        self.title = title
        if waitsForPickerDismissal { phase = .waitingForPickerDismissal }
        else { phase = isLoading ? .waitingForReload : .readyToPresentOperation }
        return true
    }

    @discardableResult
    mutating func failStaging(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .staging else { return false }
        reset()
        return true
    }

    @discardableResult
    mutating func pickerDidDisappear(attemptID id: UUID, isLoading: Bool) -> Bool {
        guard attemptID == id, phase == .waitingForPickerDismissal else { return false }
        phase = isLoading ? .waitingForReload : .readyToPresentOperation
        return true
    }

    @discardableResult
    mutating func cancelPicker(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .pickerPresented || phase == .staging ||
                phase == .waitingForPickerDismissal else { return false }
        reset()
        return true
    }

    // A presentation can be discarded only before a backend session has been
    // issued, or after the caller has separately confirmed a terminal result.
    @discardableResult
    mutating func resetBeforeBackend(attemptID id: UUID) -> Bool {
        guard attemptID == id else { return false }
        switch phase {
        case .pickerPresented, .staging, .waitingForPickerDismissal,
             .waitingForReload, .readyToPresentOperation:
            reset()
            return true
        case .operationPresented where !operationViewDidAppear && backendSessionID == nil:
            reset()
            return true
        default:
            return false
        }
    }

    mutating func reloadFinished() {
        guard phase == .waitingForReload else { return }
        phase = .readyToPresentOperation
    }

    mutating func takeReadyOperation(isLoading: Bool,
                                     hasActiveOperationPresentation: Bool) -> V3InstallPresentationRequest? {
        guard phase == .readyToPresentOperation, !isLoading, !hasActiveOperationPresentation,
              let attemptID, let token, let title else { return nil }
        let operationID = UUID()
        self.operationID = operationID
        operationViewDidAppear = false
        phase = .operationPresented
        return V3InstallPresentationRequest(attemptID: attemptID, operationID: operationID,
                                            token: token, title: title)
    }

    @discardableResult
    mutating func markOperationViewDidAppear(attemptID id: UUID, operationID: UUID) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented || phase == .operationStarted else { return false }
        operationViewDidAppear = true
        return true
    }

    @discardableResult
    mutating func backendStarted(attemptID id: UUID, operationID: UUID, sessionID: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented, backendSessionID == sessionID,
              UUID(uuidString: sessionID) != nil else { return false }
        backendSessionID = sessionID
        phase = .operationStarted
        return true
    }

    @discardableResult
    mutating func backendStartRequested(attemptID id: UUID, operationID: UUID,
                                        sessionID: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented, UUID(uuidString: sessionID) != nil else { return false }
        backendSessionID = sessionID
        return true
    }

    @discardableResult
    mutating func recordTerminal(attemptID id: UUID, operationID: UUID, outcome: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented || phase == .operationStarted else { return false }
        terminalOutcome = outcome
        phase = .terminal
        return true
    }

    @discardableResult
    mutating func prepareRetry(attemptID id: UUID, operationID: UUID) -> Bool {
        guard attemptID == id, self.operationID == operationID, phase == .terminal else { return false }
        backendSessionID = nil
        terminalOutcome = nil
        operationViewDidAppear = true
        phase = .operationPresented
        return true
    }

    @discardableResult
    mutating func beginCleanup(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .terminal else { return false }
        phase = .cleaningUp
        return true
    }

    @discardableResult
    mutating func finishCleanup(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .cleaningUp else { return false }
        reset()
        return true
    }

    private mutating func reset() {
        phase = .idle
        attemptID = nil
        operationID = nil
        token = nil
        title = nil
        backendSessionID = nil
        terminalOutcome = nil
        operationViewDidAppear = false
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

    mutating func failBeforeStart(message: String) {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = message
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
        if safeCause == CombinedFailure.SafeCause.pairingRequired.rawValue { return "pairing" }
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
        case "pairing": return "Open Pairing File"
        default: return nil
        }
    }

    var recommendedAction: String {
        switch safeCause {
        case CombinedFailure.SafeCause.pairingRequired.rawValue:
            return "Add the pairing file, then start the refresh again."
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
