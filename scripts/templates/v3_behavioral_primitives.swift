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
    // V3_JITLESS_CERT_DISTINCTION_V1: SideStore's active certificate being
    // absent is a different problem from the LiveContainer copy being stale, and
    // neither means the other's certificate is broken.
    case activeCertificateMissing
    case certificateMismatch
    case ready
    case unknown

    var isReady: Bool { self == .ready || self == .notRequired }

    /// True only for a genuinely finished JIT-Less state. Used so a completed
    /// JIT-Less setup is never rendered as an outstanding setup task.
    var isSatisfied: Bool { isReady }
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
        // Distinct from "the copy is missing": the active SideStore certificate
        // itself is absent, which is a SideStore-side prerequisite.
        guard activeCertificateExists else { return .activeCertificateMissing }
        guard hasCopy else { return .setupRequired }
        guard let validationStatus else { return .certificateImported }
        if validationStatus == 1 {
            if activeCertificateExists, identitiesMatch == true { return .activeCertificateRevoked }
            return .revoked
        }
        guard validationStatus == 0, !validationFailed else { return .unknown }
        guard let identitiesMatch else { return .unknown }
        // The copy is valid but SideStore has since moved to a different
        // certificate. Only the copy is stale; SideStore's certificate is fine.
        return identitiesMatch ? .ready : .certificateMismatch
    }
}

// V3_JITLESS_PRESENTATION_V1
// One place that decides how a JIT-Less state is presented, so the Setup
// Assistant, Health and Settings cannot each invent their own treatment. A ready
// state is a completed result, not an outstanding setup task.
struct V3JITLessPresentation: Equatable {
    let readiness: V3JITLessReadiness
    let severity: V3StatusSeverity
    let title: String
    let detail: String
    /// True when this state still requires the user to do something.
    let isOutstandingSetupTask: Bool

    var icon: String { severity.icon }

    static func present(_ readiness: V3JITLessReadiness) -> V3JITLessPresentation {
        switch readiness {
        case .notRequired:
            return V3JITLessPresentation(readiness: .notRequired, severity: .completed,
                                        title: "Not required",
                                        detail: "This iOS version does not require a JIT-Less certificate.",
                                        isOutstandingSetupTask: false)
        case .ready:
            return V3JITLessPresentation(readiness: .ready, severity: .completed,
                                        title: "Configured / Ready",
                                        detail: "The LiveContainer JIT-Less certificate matches the active SideStore certificate.",
                                        isOutstandingSetupTask: false)
        case .certificateMismatch:
            return V3JITLessPresentation(readiness: .certificateMismatch, severity: .warning,
                                        title: "JIT-Less certificate copy is out of date",
                                        detail: "SideStore is using a different or newer signing certificate than the JIT-Less certificate stored by LiveContainer. Refresh the JIT-Less certificate copy.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateMissing:
            return V3JITLessPresentation(readiness: .activeCertificateMissing, severity: .failed,
                                        title: "No active SideStore certificate",
                                        detail: "SideStore has no active signing certificate. Open Certificates and create or select one before configuring JIT-Less.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateRevoked:
            return V3JITLessPresentation(readiness: .activeCertificateRevoked, severity: .failed,
                                        title: "Active certificate revoked",
                                        detail: "SideStore's active signing certificate is reported as revoked. Open Certificates and select or create a current certificate.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateExpired:
            return V3JITLessPresentation(readiness: .activeCertificateExpired, severity: .failed,
                                        title: "Active certificate expired",
                                        detail: "SideStore's active signing certificate has expired. Open Certificates and select or create a current certificate.",
                                        isOutstandingSetupTask: true)
        case .setupRequired:
            return V3JITLessPresentation(readiness: .setupRequired, severity: .warning,
                                        title: "JIT-Less certificate not configured",
                                        detail: "LiveContainer has no JIT-Less certificate copy yet. Import one to launch guest apps on this iOS version.",
                                        isOutstandingSetupTask: true)
        case .revoked:
            return V3JITLessPresentation(readiness: .revoked, severity: .failed,
                                        title: "JIT-Less certificate copy is revoked",
                                        detail: "The certificate stored by LiveContainer is reported as revoked. Import a current copy.",
                                        isOutstandingSetupTask: true)
        case .certificateImported:
            return V3JITLessPresentation(readiness: .certificateImported, severity: .warning,
                                        title: "Certificate imported, validation pending",
                                        detail: "The certificate is stored but could not be validated yet.",
                                        isOutstandingSetupTask: true)
        case .needsCertificateRefresh:
            return V3JITLessPresentation(readiness: .needsCertificateRefresh, severity: .warning,
                                        title: "JIT-Less certificate needs refreshing",
                                        detail: "Refresh the JIT-Less certificate copy from SideStore.",
                                        isOutstandingSetupTask: true)
        case .unknown:
            return V3JITLessPresentation(readiness: .unknown, severity: .unknown,
                                        title: "Validation unknown",
                                        detail: "The JIT-Less certificate state could not be verified.",
                                        isOutstandingSetupTask: true)
        }
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

// V3_STATUS_PRESENTATION_V1
// One reusable semantic status model. Success, warning and failure were drawn
// with almost the same treatment in the operation sheet, Sources, Setup
// Assistant, Health and install flows, so a red failure and a grey informational
// line were hard to tell apart. Every state carries an icon AND a text label so
// the meaning never depends on colour alone.
enum V3StatusSeverity: String, Equatable, CaseIterable {
    case working
    case completed
    case warning
    case failed
    case cancelled
    case unknown

    var icon: String {
        switch self {
        case .working: return "arrow.triangle.2.circlepath"
        case .completed: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .failed: return "xmark.circle.fill"
        case .cancelled: return "slash.circle"
        case .unknown: return "questionmark.circle"
        }
    }

    /// The colour used alongside the icon and the text.
    var severityName: String {
        switch self {
        case .working: return "working"
        case .completed: return "success"
        case .warning: return "warning"
        case .failed: return "failure"
        case .cancelled: return "cancelled"
        case .unknown: return "unknown"
        }
    }

    var isFailure: Bool { self == .failed }
    var isSuccess: Bool { self == .completed }
    /// Only a genuine success is presented as a tick.
    var showsCheckmark: Bool { self == .completed }
}

struct V3StatusPresentation: Equatable {
    let severity: V3StatusSeverity
    let title: String
    let detail: String

    var icon: String { severity.icon }
    var severityName: String { severity.severityName }
    var isFailure: Bool { severity.isFailure }
    var isSuccess: Bool { severity.isSuccess }

    init(severity: V3StatusSeverity, title: String, detail: String = "") {
        self.severity = severity
        self.title = title
        self.detail = detail
    }

    /// Maps a product state word onto the shared severity model.
    static func severity(forState state: String) -> V3StatusSeverity {
        switch state {
        case "complete", "completed", "verified", "ready", "success": return .completed
        case "failed", "error": return .failed
        case "warning", "actionRequired", "needsAttention": return .warning
        case "running", "checking", "working", "loading", "inProgress": return .working
        case "cancelled", "canceled": return .cancelled
        default: return .unknown
        }
    }

    /// V3_RELOAD_STATUS_VISIBILITY_V1: loading wins over connected. The previous
    /// ordering rendered a green "Active & Connected" while a reload was
    /// actively running, so the button appeared to do nothing.
    static func connectionState(connected: Bool, loading: Bool) -> V3StatusPresentation {
        if loading {
            return V3StatusPresentation(severity: .working, title: "Reloading Status...")
        }
        if connected {
            return V3StatusPresentation(severity: .completed, title: "Connected")
        }
        return V3StatusPresentation(severity: .failed, title: "Not Connected")
    }
}

// V3_USER_FACING_ISSUE_V1
// The global alert used to offer "Retry Connection" for essentially every
// failure, which trained users to read every problem as a networking problem.
// A source failure, a certificate failure, an auth failure and a pairing failure
// each get the action that can actually resolve them, and "Retry Connection" is
// only offered when the evidence points at connection or service readiness.
enum V3IssueAction: String, Equatable, CaseIterable {
    case retryConnection
    case retrySource
    case openCertificates
    case openAccount
    case showPairingSetup
    case openConnectionCheck
    case chooseIPA
    case openSetup
    case dismiss

    var title: String {
        switch self {
        case .retryConnection: return "Retry Connection"
        case .retrySource: return "Retry Source"
        case .openCertificates: return "Open Certificates"
        case .openAccount: return "Open Account & Signing"
        case .showPairingSetup: return "Show Pairing Setup"
        case .openConnectionCheck: return "Open Connection Check"
        case .chooseIPA: return "Choose IPA Again"
        case .openSetup: return "Open Setup Assistant"
        case .dismiss: return "OK"
        }
    }

    /// The screen this action opens, or nil for an action that re-requests.
    var destination: String? {
        switch self {
        case .openCertificates: return "certificates"
        case .openAccount: return "signIn"
        case .showPairingSetup: return "pairing"
        case .openConnectionCheck, .retryConnection: return "connection"
        case .chooseIPA: return "ipa"
        case .openSetup: return "setup"
        case .retrySource: return "sources"
        case .dismiss: return nil
        }
    }
}

struct V3UserFacingIssue: Equatable {
    let title: String
    let severity: V3StatusSeverity
    let whatHappened: String
    let whatToDo: String
    let technicalDetails: String
    let primaryAction: V3IssueAction
    let secondaryAction: V3IssueAction
    let recoveryDestination: String?
    let retryDisposition: V3RetryDisposition

    /// The single place that decides which action a failure deserves. Selection
    /// is driven by the typed operation, stage and safe cause, never by a
    /// numeric code or by the mere fact that a request failed.
    static func make(operation: String, stage: String, code: String,
                     safeCause: String?, sourceStep: String?, retryable: Bool?,
                     whatHappened: String, whatToDo: String, technicalDetails: String) -> V3UserFacingIssue {
        let destination: String? = {
            if safeCause == CombinedFailure.SafeCause.pairingRequired.rawValue { return "pairing" }
            if stage == CombinedFailure.Stage.authentication.rawValue { return "signIn" }
            if stage == CombinedFailure.Stage.filePreparation.rawValue { return "ipa" }
            if sourceStep == CombinedFailure.SourceStep.provisioningProfileFetch.rawValue
                || sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue
                || safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue
                || stage == CombinedFailure.Stage.signing.rawValue {
                return "certificates"
            }
            if operation == "source" || sourceStep == CombinedFailure.SourceStep.manifestParsing.rawValue
                || sourceStep == CombinedFailure.SourceStep.sourceDownload.rawValue {
                return "sources"
            }
            // Only these stages actually implicate connectivity or readiness.
            if stage == CombinedFailure.Stage.network.rawValue
                || stage == CombinedFailure.Stage.xpcConnection.rawValue
                || stage == CombinedFailure.Stage.extensionLaunch.rawValue
                || stage == CombinedFailure.Stage.extensionDiscovery.rawValue
                || stage == CombinedFailure.Stage.serviceReadiness.rawValue
                || stage == CombinedFailure.Stage.coreDevice.rawValue
                || stage == CombinedFailure.Stage.cdTunnel.rawValue
                || stage == CombinedFailure.Stage.rsdDiscovery.rawValue
                || stage == CombinedFailure.Stage.rsdService.rawValue
                || stage == CombinedFailure.Stage.lockdownConnection.rawValue
                || stage == CombinedFailure.Stage.uniqueDeviceID.rawValue
                || stage == CombinedFailure.Stage.heartbeat.rawValue
                || stage == CombinedFailure.Stage.endpointSelection.rawValue
                || safeCause == CombinedFailure.SafeCause.networkConnectionLost.rawValue
                || safeCause == CombinedFailure.SafeCause.networkTimedOut.rawValue
                || safeCause == CombinedFailure.SafeCause.networkUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.wifiUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.localDevVPNUnavailable.rawValue {
                return "connection"
            }
            if stage == CombinedFailure.Stage.provisioning.rawValue {
                return "setup"
            }
            return nil
        }()

        let primary: V3IssueAction = {
            switch destination {
            case "certificates": return .openCertificates
            case "signIn": return .openAccount
            case "pairing": return .showPairingSetup
            case "ipa": return .chooseIPA
            case "sources": return .retrySource
            case "setup": return .openSetup
            // A connection destination is the only case that legitimately
            // offers a connection action.
            case "connection": return retryable == true ? .retryConnection : .openConnectionCheck
            default:
                // No evidence points anywhere specific. Never assume networking.
                return .dismiss
            }
        }()

        let disposition: V3RetryDisposition = {
            if retryable == false { return .blocked }
            if destination == "connection" && retryable == true { return .allowed }
            if retryable == true { return .allowed }
            return .unknown
        }()

        return V3UserFacingIssue(
            title: "SideStore",
            severity: .failed,
            whatHappened: whatHappened,
            whatToDo: whatToDo,
            technicalDetails: technicalDetails,
            primaryAction: primary,
            secondaryAction: .dismiss,
            recoveryDestination: destination,
            retryDisposition: disposition)
    }

    /// Builds an issue from a typed failure, preserving its privacy-safe text.
    static func make(_ failure: CombinedFailure) -> V3UserFacingIssue {
        make(operation: failure.operation, stage: failure.stage.rawValue, code: failure.code.rawValue,
             safeCause: failure.safeCause?.rawValue, sourceStep: failure.sourceStep?.rawValue,
             retryable: failure.retryable, whatHappened: failure.safeMessage,
             whatToDo: failure.recovery, technicalDetails: failure.technicalDetails)
    }

    /// One-line summary, kept short enough for a copyable alert body.
    var summary: String { whatHappened }
}

// V3_CATALOG_ROW_POLICY_V1
// The catalog view deduplicated by snapshotting the accumulated IDs before
// filtering a page, so an identifier repeated inside one page passed twice. The
// rule lives here so the real behaviour is executable rather than asserted as
// source text.
enum V3CatalogRowPolicy {
    static func identifier(of row: [String: Any]) -> String? {
        guard let value = row["identifier"] as? String, !value.isEmpty else { return nil }
        return value
    }

    /// Removes duplicates by identifier, preserving first-seen order, across
    /// every page seen so far. Rows without a usable identifier are rejected
    /// rather than silently kept, because they cannot be deduplicated or
    /// installed.
    static func dedupe(_ rows: [[String: Any]]) -> [[String: Any]] {
        var seen = Set<String>()
        var result: [[String: Any]] = []
        result.reserveCapacity(rows.count)
        for row in rows {
            guard let identifier = identifier(of: row) else { continue }
            if seen.insert(identifier).inserted { result.append(row) }
        }
        return result
    }

    /// Folds one page into the rows already displayed.
    static func appending(_ page: [[String: Any]], to rows: [[String: Any]]) -> [[String: Any]] {
        dedupe(rows + page)
    }
}

// V3_RELOAD_GATE_V1
// The reload gate rules, made explicit and executable. The store previously
// inlined this, and callers could not await an authoritative snapshot, so a
// recalculate could read the previous snapshot.
// V3_LOAD_ACTIVITY_OWNERSHIP_V1
// One `loading` flag used to mean two different things: an authoritative status
// snapshot, and a mutation such as refreshSources, signOut, clearCache, syncAppIDs
// or a JIT operation. The reload gate read that flag as "a snapshot is in flight",
// so a caller awaiting an authoritative snapshot could join a mutation instead,
// and the mutation's completion released it with a not-observed outcome before
// any snapshot had been performed. The activity is now named, and the gate can
// tell the two apart.
enum V3LoadActivity: String, Equatable, CaseIterable {
    case idle
    /// An authoritative status snapshot is in flight. This is the only activity
    /// that may resolve a snapshot waiter.
    case snapshot
    /// A mutation is in flight. A snapshot must be requested after it, never
    /// substituted by it.
    case mutation
}

// V3_SNAPSHOT_GATE_V1
// The decision a snapshot request makes. It is a pure function so the ordering
// contract is executable behaviour rather than a comment about a flag.
enum V3SnapshotDecision: String, Equatable, CaseIterable {
    /// The caller owns the snapshot and must perform it now.
    case performSnapshot
    /// A snapshot is genuinely in flight. The caller parks and joins it.
    case joinSnapshot
    /// A mutation is in flight. The caller parks, and a snapshot is owed for
    /// after the mutation. The mutation's completion must not resolve it.
    case awaitMutationThenSnapshot
    /// A presented operation owns the state a snapshot would report. The caller
    /// parks, and a snapshot is owed for when the operation ends.
    case deferForPresentation
    /// Policy forbids a snapshot and none is owed, so the caller is told
    /// truthfully that nothing was observed. No continuation is parked.
    case doNotObserve
}

enum V3SnapshotGate {
    /// A presented operation owns the state, so its snapshot is deferred even
    /// when nothing else is running. This is checked first because a sheet can
    /// be up while a mutation is still settling, and both must be honoured.
    static func decide(activity: V3LoadActivity, presentationActive: Bool,
                       manual: Bool, requiresConnectionRetry: Bool) -> V3SnapshotDecision {
        if presentationActive { return .deferForPresentation }
        switch activity {
        case .snapshot: return .joinSnapshot
        case .mutation: return .awaitMutationThenSnapshot
        case .idle: break
        }
        if !manual && requiresConnectionRetry { return .doNotObserve }
        return .performSnapshot
    }

    /// The result of running an owed snapshot once the blocking activity has
    /// ended. Every case is total: no input leaves a parked continuation
    /// without a resumption, which is what made a non-manual deferred reload a
    /// latent permanent hang.
    static func drain(activity: V3LoadActivity, presentationActive: Bool,
                      owed: Bool, anyWaiterNeedsManual: Bool,
                      requiresConnectionRetry: Bool) -> V3SnapshotDecision {
        guard owed, activity == .idle, !presentationActive else { return .doNotObserve }
        return decide(activity: .idle, presentationActive: false,
                      manual: anyWaiterNeedsManual || !requiresConnectionRetry,
                      requiresConnectionRetry: requiresConnectionRetry)
    }
}

// V3_SOURCE_EDITING_POLICY_V1
// Issue #40: the Add Source field had no focus state and no explicit dismissal,
// so Return was the only way out of the keyboard and read as a submit action.
// The cancel semantics are stated here so they are executable and testable:
// Cancel restores the URL that was present when editing began, and neither
// Cancel nor Done may preview, request, or persist anything.
enum V3SourceEditingOutcome: Equatable {
    case dismissed
    case restored(String)
}

enum V3SourceEditingPolicy {
    /// Done: a pure UI dismissal. The typed value is kept.
    static func done(typed: String) -> V3SourceEditingOutcome { .dismissed }

    /// Cancel: restore the pre-edit value, so a URL is never silently discarded
    /// and a later focus always starts from a predictable value.
    static func cancel(typed: String, beforeEditing: String) -> V3SourceEditingOutcome {
        .restored(beforeEditing)
    }

    /// The value the field should hold after the outcome is applied.
    static func resolved(_ outcome: V3SourceEditingOutcome, typed: String) -> String {
        switch outcome {
        case .dismissed: return typed
        case .restored(let value): return value
        }
    }
}

// V3_SETUP_COMPLETION_POLICY_V1
// One authority for "is setup finished". Home and the Setup Assistant each used
// their own rule, so Home could stop showing "Finish Setup" while the assistant
// still considered setup incomplete. Two authorities for one product state is
// the defect; this type removes the possibility of disagreement by having exactly
// one decision, consumed by both, and by reporting which item is outstanding
// rather than a bare boolean.
enum V3SetupOutstandingItem: String, Equatable, CaseIterable {
    case account
    case provisioning
    case pairing
    case jitless
    case network
    case tunnel
    case backgroundRefresh
    case schedule
    case verifiedRefresh

    /// User-facing label, so the UI can name the outstanding step.
    var title: String {
        switch self {
        case .account: return "Sign in with your Apple ID"
        case .provisioning: return "Finish device provisioning"
        case .pairing: return "Add a pairing file"
        case .jitless: return "Configure the JIT-Less certificate"
        case .network: return "Connect to Wi-Fi"
        case .tunnel: return "Enable LocalDevVPN"
        case .backgroundRefresh: return "Allow Background App Refresh"
        case .schedule: return "Enable scheduled refresh"
        case .verifiedRefresh: return "Run one verified refresh"
        }
    }
}

struct V3SetupCompletionInputs: Equatable {
    var accountComplete = false
    var provisioningIncomplete = false
    var pairingSatisfied = false
    var jitlessRequired = false
    var jitlessComplete = false
    var networkComplete = false
    var tunnelComplete = false
    var backgroundRefreshAvailable = false
    var scheduleEnabled = false
    var verifiedRefreshPresent = false

    /// The only legal way to decide whether setup is finished.
    func outstanding() -> [V3SetupOutstandingItem] {
        var items: [V3SetupOutstandingItem] = []
        if !accountComplete { items.append(.account) }
        if provisioningIncomplete { items.append(.provisioning) }
        if !pairingSatisfied { items.append(.pairing) }
        // JIT-Less is only a prerequisite where the platform requires it.
        if jitlessRequired && !jitlessComplete { items.append(.jitless) }
        if !networkComplete { items.append(.network) }
        if !tunnelComplete { items.append(.tunnel) }
        if !backgroundRefreshAvailable { items.append(.backgroundRefresh) }
        if !scheduleEnabled { items.append(.schedule) }
        if !verifiedRefreshPresent { items.append(.verifiedRefresh) }
        return items
    }

    var isComplete: Bool { outstanding().isEmpty }
}

// V3_FAILURE_GUIDANCE_V1
// A failure that reached a view as an untyped error was displayed as
// error.localizedDescription. That publishes whatever text the service happened
// to attach, which for a bridged NSError includes its numeric domain and code
// and means nothing to a user, and it offered no guidance at all. Every
// user-visible failure message now comes from here.
//
// A typed CombinedFailure keeps its own product recovery copy. An untyped error
// cannot be attributed to a cause, so the guidance deliberately does not guess
// one: it says what is known, and it points at the diagnostics that can identify
// it. The unreadable text is kept out of the interface and offered through
// Copy Diagnostics instead.
enum V3FailureGuidance {
    static func message(_ error: Error) -> String {
        if let combined = error as? CombinedFailure {
            return combined.recovery
        }
        // The earlier wording asserted "and nothing was changed". Nothing
        // supports that: an untyped failure can arrive after the service applied
        // the request, and the same helper is used after settings writes, source
        // confirmation, pairing import and install staging. Claiming a known
        // side-effect from an unknown cause is the same class of error as
        // blaming the network, so the claim is removed and the outcome is stated
        // as unknown.
        return "That action did not complete, and whether it took effect is not known. Reload status to see the current state before trying again. If it keeps failing, copy diagnostics to identify the cause."
    }

    /// Privacy-safe diagnostic text, never shown as guidance.
    static func diagnostics(_ error: Error) -> String {
        if let combined = error as? CombinedFailure {
            return combined.technicalDetails
        }
        let nsError = error as NSError
        return "operation=untyped stage=command code=\(nsError.code) domain=\(nsError.domain) underlying=redacted"
    }
}

// V3_SHARED_JITLESS_FACT_V1
// Home and the Setup Assistant each decided JIT-Less completion separately. Home
// had no access to the certificate facts, so on the platforms that require
// JIT-Less it reported the item as permanently outstanding while the assistant,
// which had the real readiness, showed it complete. One observed readiness is
// now published and both surfaces read it.
//
// A nil readiness means "not observed yet", which counts as outstanding. Guessing
// "fine" there is what produced the original disagreement.
enum V3JITLessCompletionPolicy {
    static func isComplete(_ readiness: V3JITLessReadiness?) -> Bool {
        guard let readiness else { return false }
        return readiness.isReady
    }

    /// True where an unobserved JIT-Less state is still an outstanding item.
    static func isRequired(osMajor: Int) -> Bool { osMajor >= 26 }
}

enum V3RetryDisposition: Equatable {
    case allowed
    case unknown
    case prerequisite
    case blocked
}

// V3_REFRESH_PREREQUISITE_POLICY_V1
// One authoritative prerequisite contract for every refresh entry point. Home
// Refresh All, Setup Assistant Test Refresh, Refresh Manager Manual Refresh,
// and targeted per-app refresh all call this instead of re-deriving rules, so
// a prerequisite the host already knows about can never be reported later as
// "no safe underlying cause was available".
//
// Two invariants are encoded here rather than at each call site.
// 1. The service's pairing status string is interpreted in exactly one place.
// 2. Only an authoritative "Pairing file required" blocks. "Unknown" (before
//    the first snapshot, or after a failed snapshot) does not block, so a host
//    restart can never permanently disable a correctly configured device.
//    Nothing is blocked on Wi-Fi, LocalDevVPN, or an account here: those are
//    not proven required for a refresh, and the scheduler already owns the
//    transport preflight for them.
enum V3RefreshPrerequisiteState: String, Equatable {
    case unknown
    case satisfied
    case unsatisfied
}

enum V3RefreshPrerequisiteKind: String, Equatable {
    case pairing
}

struct V3RefreshPrerequisite: Equatable {
    let state: V3RefreshPrerequisiteState
    let kind: V3RefreshPrerequisiteKind?
    let detail: String

    static let pairingRequiredDetail = "No pairing file yet"

    private init(state: V3RefreshPrerequisiteState, kind: V3RefreshPrerequisiteKind?, detail: String) {
        self.state = state
        self.kind = kind
        self.detail = detail
    }

    static let unknown = V3RefreshPrerequisite(state: .unknown, kind: nil, detail: "")
    static let satisfied = V3RefreshPrerequisite(state: .satisfied, kind: nil, detail: "Pairing file available")
    static let pairingRequired = V3RefreshPrerequisite(state: .unsatisfied, kind: .pairing, detail: pairingRequiredDetail)

    /// The only interpretation of the authoritative pairing snapshot string.
    static func evaluate(pairingStatus: String?) -> V3RefreshPrerequisite {
        switch pairingStatus {
        case "Pairing file available": return .satisfied
        case "Pairing file required": return .pairingRequired
        default: return .unknown
        }
    }

    var blocksRefresh: Bool { state == .unsatisfied }
    var blocksTargetedRefresh: Bool { blocksRefresh }
    var recoveryDestination: String? { kind == .pairing ? "pairing" : nil }
    var recoveryActionTitle: String? { kind == .pairing ? "Show Pairing Setup" : nil }
    var recommendedAction: String {
        kind == .pairing
            ? "Place or import a valid pairing file, then try again."
            : "Reload status, then try again."
    }

    /// The canonical structured failure for a blocked refresh. Minted only on
    /// demand so it can carry the caller's correlation ID.
    func failure(correlationID: String) -> CombinedFailure? {
        guard kind == .pairing else { return nil }
        return CombinedFailure(operation: "refresh", stage: .pairing, code: .notReady,
                               id: correlationID, retryable: false, safeCause: .pairingRequired)
    }
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
