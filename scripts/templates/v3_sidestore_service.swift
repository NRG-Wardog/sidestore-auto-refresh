
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
import SwiftUI
import SideSign
import Minimuxer

// V3_NATIVE_CALLBACK_GATE_V1: native completions can arrive on arbitrary queues.
// Cancellation does not manufacture a native completion or release the mutation gate.
// The owning service retains it until the real callback returns or the process retires.
private final class V3ServiceCallbackGate: @unchecked Sendable {
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


private enum V3HeadlessFlowError: Error {
    case invalidResponse
    case staleResponse
}

@MainActor
private final class V3HeadlessInteractionSession {
    let id = UUID().uuidString
    private(set) var revision = 0
    private(set) var phase = "starting"
    private(set) var isTerminal = false
    private var state: [String: Any] = [:]
    private var challengeID: String?
    private var responder: (([String: Any]) -> Void)?
    private var canceller: (() -> Void)?

    init(title: String) {
        state = ["phase": "starting", "title": title, "message": "Starting…"]
    }

    func snapshot() -> [String: Any] {
        var value = state
        value["sessionID"] = id
        value["revision"] = revision
        value["phase"] = phase
        return value
    }

    func setRunning(_ message: String) {
        guard !isTerminal else { return }
        revision += 1
        phase = "running"
        state = ["phase": phase, "message": bounded(message)]
    }

    func setProgress(_ fraction: Double, message: String) {
        guard !isTerminal, phase == "running" else { return }
        revision += 1
        let boundedFraction = min(max(fraction, 0), 1)
        state = [
            "phase": phase,
            "message": bounded(message),
            "progress": boundedFraction
        ]
    }

    func complete(_ fields: [String: Any] = [:]) {
        guard !isTerminal else { return }
        revision += 1
        phase = "completed"
        isTerminal = true
        state = ["phase": phase, "message": "Completed", "fields": fields]
        clearPending()
    }

    func fail(_ error: Error) {
        guard !isTerminal else { return }
        revision += 1
        phase = "failed"
        isTerminal = true
        state = [
            "phase": phase,
            "message": bounded((error as NSError).localizedDescription, max: 2_048)
        ]
        clearPending()
    }

    func cancel() {
        guard !isTerminal else { return }
        let cancel = canceller
        clearPending()
        revision += 1
        phase = "cancelled"
        isTerminal = true
        state = ["phase": phase, "message": "Cancelled"]
        cancel?()
    }

    func respond(_ payload: [String: Any]) throws {
        guard !isTerminal,
              phase == "requiresInput",
              let expected = challengeID,
              payload["challengeID"] as? String == expected,
              let responder else {
            throw V3HeadlessFlowError.staleResponse
        }

        self.responder = nil
        self.canceller = nil
        self.challengeID = nil
        setRunning("Continuing…")
        responder(payload)
    }

    func ask<T>(
        kind: String,
        title: String,
        message: String = "",
        fields: [String: Any] = [:],
        decode: @escaping ([String: Any]) throws -> T
    ) async throws -> T {
        guard !isTerminal, responder == nil else { throw V3HeadlessFlowError.invalidResponse }

        return try await withCheckedThrowingContinuation { continuation in
            let token = UUID().uuidString
            challengeID = token
            responder = { payload in
                do { continuation.resume(returning: try decode(payload)) }
                catch { continuation.resume(throwing: error) }
            }
            canceller = { continuation.resume(throwing: OperationError.cancelled) }
            revision += 1
            phase = "requiresInput"
            var next: [String: Any] = [
                "phase": phase,
                "kind": kind,
                "title": bounded(title),
                "message": bounded(message),
                "challengeID": token,
                "fields": fields
            ]
            next["sessionID"] = id
            state = next
        }
    }

    private func clearPending() {
        challengeID = nil
        responder = nil
        canceller = nil
    }

    private func bounded(_ value: String, max: Int = 4_096) -> String {
        String(value.prefix(max))
    }
}

private final class V3HeadlessPipelineHandler: PipelineExecutionHandler,
                                               PreflightChecksHandler,
                                               EntitlementsReviewHandler,
                                               ExtensionRemovalHandler,
                                               UnsupportedVersionHandler,
                                               InstallAppHandler,
                                               UserCustomizationHandler,
                                               @unchecked Sendable {
    var preflightChecksHandler: PreflightChecksHandler { self }
    var entitlementsReviewHandler: EntitlementsReviewHandler { self }
    var extensionRemovalHandler: ExtensionRemovalHandler { self }
    var unsupportedVersionHandler: UnsupportedVersionHandler { self }
    var installAppHandler: InstallAppHandler { self }
    var userCustomizationHandler: UserCustomizationHandler { self }

    let isResignActive: Bool
    private let session: V3HeadlessInteractionSession

    init(session: V3HeadlessInteractionSession, isResignActive: Bool = false) {
        self.session = session
        self.isResignActive = isResignActive
    }

    @MainActor
    func resolveBundleIDMismatch(targetID: String, activeEffectiveID: String) async -> Bool {
        (try? await session.ask(
            kind: "bundleIDMismatch",
            title: "Bundle ID Mismatch",
            message: "The app bundle identifier differs from the active app.",
            fields: ["targetID": targetID, "activeEffectiveID": activeEffectiveID]
        ) { payload in
            switch payload["action"] as? String {
            case "proceed": return true
            case "cancel": return false
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }) ?? false
    }

    @MainActor
    func reviewPermissions(_ permissions: [ALTEntitlement], for app: AppProtocol, mode: PermissionReviewMode) async throws {
        let permissionNames = permissions.map { String(describing: $0) }
        let accepted: Bool = try await session.ask(
            kind: "permissionsReview",
            title: "Review Permissions",
            message: "Review the permissions requested by \(app.name).",
            fields: [
                "appName": app.name,
                "bundleID": app.bundleIdentifier,
                "mode": String(describing: mode),
                "permissions": permissionNames
            ]
        ) { payload in
            switch payload["action"] as? String {
            case "continue": return true
            case "cancel": throw OperationError.cancelled
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
        if !accepted { throw OperationError.cancelled }
    }

    @MainActor
    func selectAppExtensionsToRemove(
        appBundle: ALTApplication,
        localAppExtensions: [ALTApplication],
        excessExtensions: Set<ALTApplication>
    ) async throws -> ExtensionRemovalDecision {
        let extensions = appBundle.appExtensions.map {
            ["bundleID": $0.bundleIdentifier, "name": $0.name]
        }
        let excess = excessExtensions.map(\.bundleIdentifier)
        return try await session.ask(
            kind: "extensionRemoval",
            title: "App Extensions",
            message: "Choose how SideStore should handle this app's extensions.",
            fields: [
                "appName": appBundle.name,
                "extensions": extensions,
                "excessBundleIDs": excess,
                "activeLimitIncludesExtensions": UserDefaults.standard.activeAppLimitIncludesExtensions
            ]
        ) { payload in
            switch payload["action"] as? String {
            case "keepMainProfile": return .keepAll(useMainProfile: true)
            case "keepSeparateProfiles": return .keepAll(useMainProfile: false)
            case "removeAll": return .removeAll
            case "removeSelected":
                let ids = Set(payload["bundleIDs"] as? [String] ?? [])
                return .removeSelected(Set(appBundle.appExtensions.filter { ids.contains($0.bundleIdentifier) }))
            case "cancel": throw OperationError.cancelled
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
    }

    @MainActor
    func resolveUnsupportediOSVersion(errorDescription: String, appName: String, compatibleVersion: String) async throws -> Bool {
        try await session.ask(
            kind: "unsupportedVersion",
            title: "Unsupported iOS Version",
            message: errorDescription,
            fields: ["appName": appName, "compatibleVersion": compatibleVersion]
        ) { payload in
            switch payload["action"] as? String {
            case "useCompatible": return true
            case "cancel": return false
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
    }

    func requestBackgroundSuspension() async {
        _ = try? await session.ask(
            kind: "backgroundSuspension",
            title: "Finish Operation",
            message: "SideStore must briefly finish its backend installation step.",
            fields: [:]
        ) { payload in
            guard payload["action"] as? String == "continue" else { throw OperationError.cancelled }
            return true
        }
    }

    func suspendToHomeScreen() async {
        await CellularRefreshManager.shared.turnOnDataIfNeeded()
        await MainActor.run {
            _ = UIApplication.shared.perform(#selector(NSXPCConnection.suspend))
        }
    }

    func isAppInForeground() async -> Bool {
        await MainActor.run { UIApplication.shared.applicationState == .active }
    }

    @MainActor
    func resolveBundleIDOverride(initialBundleID: String) async throws -> (customID: String, appendTeamID: Bool)? {
        try await session.ask(
            kind: "bundleIDCustomization",
            title: "App ID Customization",
            message: "Confirm or edit the bundle identifier.",
            fields: ["bundleID": initialBundleID, "appendTeamID": true]
        ) { payload in
            switch payload["action"] as? String {
            case "confirm":
                let raw = (payload["bundleID"] as? String ?? initialBundleID).trimmingCharacters(in: .whitespacesAndNewlines)
                return (raw.isEmpty ? initialBundleID : raw, payload["appendTeamID"] as? Bool ?? true)
            case "cancel": return nil
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
    }

    @MainActor
    func resolveAppGroupMismatch(originalGroup: String, correctedGroup: String) async throws -> AppGroupResolution {
        try await session.ask(
            kind: "appGroupMismatch",
            title: "App Group Discrepancy",
            message: "Choose which app-group identifier to use.",
            fields: ["originalGroup": originalGroup, "correctedGroup": correctedGroup]
        ) { payload in
            switch payload["action"] as? String {
            case "correct": return .correctAndProceed(correctedGroup)
            case "keep": return .keepOriginal(originalGroup)
            case "cancel": throw OperationError.cancelled
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
    }
}

@MainActor
private final class V3HeadlessSignInFlow: NSObject, SignInHandler, AnisetteServerHandler {
    let session = V3HeadlessInteractionSession(title: "Sign In")
    private var task: Task<Void, Never>?
    private var operation: SignInOperation?
    private var activePipelineProgress: Progress?
    private var lastCredentialError: String?

    var id: String { session.id }
    var isTerminal: Bool { session.isTerminal }
    func snapshot() -> [String: Any] { session.snapshot() }

    func start() {
        guard task == nil else { return }
        session.setRunning("Checking saved authentication…")
        task = Task { @MainActor in
            do {
                let context = StandaloneOperationContext(
                    steps: .signIn,
                    dbBackgroundContext: DatabaseManager.shared.persistentContainer.newBackgroundContext()
                )
                let operation = try SignInOperation(
                    context: context,
                    signInHandler: self,
                    anisetteServerHandler: self
                )
                self.operation = operation
                let result = try await operation.execute()
                self.operation = nil
                session.complete([
                    "teamID": result.team.identifier,
                    "teamName": result.team.name,
                    "teamType": String(describing: result.team.type)
                ])
            } catch {
                self.operation = nil
                if Task.isCancelled || error is CancellationError {
                    session.cancel()
                } else {
                    session.fail(error)
                }
            }
        }
    }

    func cancel() {
        operation?.cancel()
        activePipelineProgress?.cancel()
        task?.cancel()
        session.cancel()
    }

    @MainActor
    func credentials() async throws -> (String, String) {
        var fields: [String: Any] = [
            "appleID": AuthManager.shared.currentAppleID ?? ""
        ]
        if let lastCredentialError, !lastCredentialError.isEmpty {
            fields["error"] = String(lastCredentialError.prefix(2_048))
        }
        self.lastCredentialError = nil
        return try await session.ask(
            kind: "credentials",
            title: "Sign In",
            message: "Enter the Apple ID credentials SideStore should use.",
            fields: fields
        ) { payload in
            if payload["action"] as? String == "cancel" { throw OperationError.cancelled }
            guard payload["action"] as? String == "submitCredentials",
                  let appleID = payload["appleID"] as? String,
                  let password = payload["password"] as? String,
                  !appleID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !password.isEmpty else { throw V3HeadlessFlowError.invalidResponse }
            return (appleID.trimmingCharacters(in: .whitespacesAndNewlines), password)
        }
    }

    @MainActor
    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {
        switch result {
        case .success:
            lastCredentialError = nil
        case .failure(let error):
            lastCredentialError = (error as NSError).localizedDescription
        }
    }

    @MainActor
    func verificationCode(for request: TwoFactorRequest) async throws -> TwoFactorResponse {
        var fields: [String: Any] = [:]
        var kind = "verificationCode"
        var title = "Two-Factor Authentication"

        switch request {
        case .selectDeliveryMethod(let preferredMode, let phoneNumbers):
            kind = "verificationMethod"
            fields["preferredMode"] = preferredMode.rawValue
            fields["phoneNumbers"] = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
        case .trustedDevice(let error):
            fields["mode"] = TwoFactorDeliveryMode.trustedDevice.rawValue
            if let error, !error.isEmpty { fields["error"] = String(error.prefix(2_048)) }
        case .sms(let phoneNumbers, let activeID, let error):
            fields["mode"] = TwoFactorDeliveryMode.sms.rawValue
            fields["phoneNumbers"] = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
            fields["activeID"] = activeID
            if let error, !error.isEmpty { fields["error"] = String(error.prefix(2_048)) }
        case .voice(let phoneNumbers, let activeID, let error):
            fields["mode"] = TwoFactorDeliveryMode.voice.rawValue
            fields["phoneNumbers"] = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
            fields["activeID"] = activeID
            if let error, !error.isEmpty { fields["error"] = String(error.prefix(2_048)) }
        }

        if kind == "verificationCode" { title = "Verification Code" }
        return try await session.ask(
            kind: kind,
            title: title,
            message: kind == "verificationMethod"
                ? "Choose how to receive the verification code."
                : "Enter the six-digit verification code or choose another delivery option.",
            fields: fields
        ) { payload in
            switch payload["action"] as? String {
            case "submitCode":
                guard let code = payload["code"] as? String, code.count == 6 else {
                    throw V3HeadlessFlowError.invalidResponse
                }
                return .verificationCode(code)
            case "trustedDevice":
                return .requestTrustedDevice
            case "sms":
                guard let phoneID = payload["phoneID"] as? String else { throw V3HeadlessFlowError.invalidResponse }
                return .requestSMS(phoneID: phoneID)
            case "voice":
                guard let phoneID = payload["phoneID"] as? String else { throw V3HeadlessFlowError.invalidResponse }
                return .requestVoice(phoneID: phoneID)
            case "cancel":
                return .cancel
            default:
                throw V3HeadlessFlowError.invalidResponse
            }
        }
    }

    @MainActor
    func accountRepair(url: URL, message: String) async -> AccountRepairDecision {
        (try? await session.ask(
            kind: "accountRepair",
            title: "Account Repair Required",
            message: message,
            fields: [
                "developerURL": url.absoluteString,
                "appleAccountURL": AppConstants.URLs.appleAccount.absoluteString
            ]
        ) { payload in
            switch payload["action"] as? String {
            case "proceed": return AccountRepairDecision.proceed
            case "cancel": return AccountRepairDecision.cancel
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }) ?? .cancel
    }

    @MainActor
    func resolveTeam(_ teams: [ALTTeam]) async throws -> ALTTeam {
        try await session.ask(
            kind: "teamSelection",
            title: "Select Developer Team",
            message: "Choose the Apple Developer team to use.",
            fields: [
                "teams": teams.map {
                    ["id": $0.identifier, "name": $0.name, "type": String(describing: $0.type)]
                }
            ]
        ) { payload in
            if payload["action"] as? String == "cancel" { throw OperationError.cancelled }
            guard payload["action"] as? String == "selectTeam",
                  let teamID = payload["teamID"] as? String,
                  let team = teams.first(where: { $0.identifier == teamID }) else {
                throw V3HeadlessFlowError.invalidResponse
            }
            return team
        }
    }

    @MainActor
    func resolveProvisioningError(_ error: Error) async -> ProvisioningErrorDecision {
        (try? await session.ask(
            kind: "provisioningDecision",
            title: "Developer Portal Error",
            message: (error as NSError).localizedDescription,
            fields: [:]
        ) { payload in
            switch payload["action"] as? String {
            case "retry": return ProvisioningErrorDecision.retry
            case "cancel": return ProvisioningErrorDecision.cancel
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }) ?? .cancel
    }

    @MainActor
    func resolvePostAuth() async {
        _ = try? await session.ask(
            kind: "postAuth",
            title: "Authentication Complete",
            message: "Continue to finish SideStore account setup.",
            fields: [:]
        ) { payload in
            guard payload["action"] as? String == "continue" else { throw OperationError.cancelled }
            return true
        }
    }

    @MainActor
    func resolveRevocation(certificates: [ALTX509Certificate], teamType: ALTTeamType) async throws -> RevokeDecision {
        try await session.ask(
            kind: "revocationDecision",
            title: "Signing Certificates",
            message: "Choose whether SideStore should revoke existing iOS development certificates.",
            fields: [
                "teamType": String(describing: teamType),
                "certificates": certificates.map {
                    [
                        "serial": $0.serialNumber,
                        "name": $0.name,
                        "machineName": $0.machineName ?? ""
                    ]
                }
            ]
        ) { payload in
            switch payload["action"] as? String {
            case "keepExisting":
                return .keepExisting
            case "revokeSelected":
                let serials = Set(payload["serials"] as? [String] ?? [])
                return .revokeSelected(certificates.filter { serials.contains($0.serialNumber) })
            case "cancel":
                throw OperationError.cancelled
            default:
                throw V3HeadlessFlowError.invalidResponse
            }
        }
    }

    @MainActor
    func resolveResign(mismatchReason: CodeSignValidationReason, context: StandaloneOperationContext) async throws -> Bool {
        let shouldResign: Bool = try await session.ask(
            kind: "resignDecision",
            title: "Resign SideStore",
            message: "The running SideStore signature no longer matches the active signing state.",
            fields: ["reason": String(describing: mismatchReason)]
        ) { payload in
            switch payload["action"] as? String {
            case "resignNow": return true
            case "later": return false
            case "cancel": throw OperationError.cancelled
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
        guard shouldResign else { return false }
        guard let app = InstalledApp.fetchAltStore(in: DatabaseManager.shared.viewContext) else {
            throw V3HeadlessFlowError.invalidResponse
        }

        let handler = V3HeadlessPipelineHandler(session: session, isResignActive: true)
        return try await withCheckedThrowingContinuation { continuation in
            let group = AppManager.shared.pipelineRunner.performSingleOperation(
                .install(app),
                handler: handler,
                context: context
            ) { result in
                switch result {
                case .success:
                    continuation.resume(returning: true)
                case .failure(let error):
                    continuation.resume(throwing: error)
                }
            }
            self.activePipelineProgress = group.progress
        }
    }

    @MainActor
    func complete() async {
        activePipelineProgress = nil
    }

    @MainActor
    func warnOutdatedAnisetteServer() async throws -> Bool {
        try await session.ask(
            kind: "anisetteWarning",
            title: "Outdated Anisette Server",
            message: "This Anisette server is outdated and may increase account risk.",
            fields: [:]
        ) { payload in
            switch payload["action"] as? String {
            case "continue": return true
            case "cancel": return false
            default: throw V3HeadlessFlowError.invalidResponse
            }
        }
    }
}


@MainActor
private final class V3HeadlessOperationFlow {
    let session: V3HeadlessInteractionSession
    private var task: Task<Void, Never>?
    private var progressTask: Task<Void, Never>?
    private var progress: Progress?
    private var cancelAction: (() -> Void)?
    private var scopedURL: URL?

    init(title: String) {
        session = V3HeadlessInteractionSession(title: title)
    }

    var id: String { session.id }
    var isTerminal: Bool { session.isTerminal }
    func snapshot() -> [String: Any] { session.snapshot() }

    func start(_ work: @escaping @MainActor (V3HeadlessPipelineHandler, V3HeadlessOperationFlow) async throws -> [String: Any]) {
        guard task == nil else { return }
        session.setRunning("Starting…")
        let handler = V3HeadlessPipelineHandler(session: session)
        task = Task { @MainActor in
            do {
                let result = try await work(handler, self)
                stopTrackingProgress()
                releaseScopedURL()
                session.complete(result)
            } catch {
                stopTrackingProgress()
                releaseScopedURL()
                if Task.isCancelled || error is CancellationError {
                    session.cancel()
                } else {
                    session.fail(error)
                }
            }
        }
    }

    func track(_ progress: Progress, cancel: (() -> Void)? = nil) {
        self.progress = progress
        self.cancelAction = cancel
        progressTask?.cancel()
        progressTask = Task { @MainActor in
            while !Task.isCancelled, !session.isTerminal {
                if !progress.isIndeterminate {
                    session.setProgress(progress.fractionCompleted, message: "Working…")
                }
                do { try await Task.sleep(nanoseconds: 250_000_000) }
                catch { return }
            }
        }
    }

    func retainSecurityScopedURL(_ url: URL) {
        releaseScopedURL()
        if url.startAccessingSecurityScopedResource() {
            scopedURL = url
        }
    }

    func cancel() {
        cancelAction?()
        progress?.cancel()
        task?.cancel()
        stopTrackingProgress()
        releaseScopedURL()
        session.cancel()
    }

    private func stopTrackingProgress() {
        progressTask?.cancel()
        progressTask = nil
        progress = nil
        cancelAction = nil
    }

    private func releaseScopedURL() {
        scopedURL?.stopAccessingSecurityScopedResource()
        scopedURL = nil
    }

    deinit {
        scopedURL?.stopAccessingSecurityScopedResource()
    }
}

@MainActor
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    private var tasks: [String: Task<Void, Never>] = [:]
    private var cancellations: [String: () -> Void] = [:]
    private var completed: [String: (data: Data, deadline: Date)] = [:]
    private var mutationID: String?
    private var signInFlow: V3HeadlessSignInFlow?
    private var operationFlow: V3HeadlessOperationFlow?
    private var finishPanel: (() -> Void)?
    static var presenter: UIViewController {
        if let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first(where: { $0.activationState == .foregroundActive || $0.activationState == .foregroundInactive }),
           let window = scene.windows.first(where: { $0.isKeyWindow }) ?? scene.windows.first,
           let root = window.rootViewController {
            return topViewController(root)
        }
        if let window = UIApplication.shared.windows.first(where: { $0.isKeyWindow }) ?? UIApplication.shared.windows.first,
           let root = window.rootViewController {
            return topViewController(root)
        }
        return fallbackPresenter
    }
    private static let fallbackPresenter = UIViewController()

    private static func topViewController(_ root: UIViewController) -> UIViewController {
        if let presented = root.presentedViewController, !presented.isBeingDismissed {
            return topViewController(presented)
        }
        if let nav = root as? UINavigationController, let visible = nav.visibleViewController {
            return topViewController(visible)
        }
        if let tab = root as? UITabBarController, let selected = tab.selectedViewController {
            return topViewController(selected)
        }
        return root
    }

    @objc(execute:reply:)
    nonisolated static func execute(_ data: Data, reply: @escaping (Data) -> Void) {
        Task { @MainActor in shared.receive(data, reply: reply) }
    }

    private func receive(_ data: Data, reply: @escaping (Data) -> Void) {
        guard let request = V3WireContract.decodeRequest(data),
              let id = request["id"] as? String,
              let operation = request["operation"] as? String,
              let deadline = request["deadline"] as? Date,
              deadline > Date(), deadline.timeIntervalSinceNow <= 610 else {
            reply(encode(["error": "invalidRequest"]))
            return
        }
        completed = completed.filter { $0.value.deadline > Date() }
        if let previous = completed[id] { reply(previous.data); return }
        if operation == "cancel" {
            let target = request["target"] as? String ?? ""
            tasks[target]?.cancel()
            cancellations[target]?()
            if mutationID == target { Self.presenter.dismiss(animated: true) }
            reply(encode(["id": id, "version": 1, "ok": true]))
            return
        }
        guard tasks[id] == nil else { reply(encode(["version": 1, "id": id, "error": "busy",
            "failure": CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true).wire])); return }
        let nonMutatingOperations: Set<String> = [
            "snapshot", "catalog", "appIcon", "backupResult", "certificatesSnapshot",
            "signInState", "signInRespond", "cancelSignIn",
            "operationState", "operationRespond", "cancelOperation"
        ]
        let mutation = !nonMutatingOperations.contains(operation)
        if mutation, operation != "beginSignIn", let flow = signInFlow, !flow.isTerminal {
            reply(encode(["version": 1, "id": id, "error": "busy",
                "failure": CombinedFailure(operation: operation, stage: .authentication, code: .busy, id: id, retryable: true).wire]))
            return
        }
        if mutation, let flow = operationFlow, !flow.isTerminal {
            reply(encode(["version": 1, "id": id, "error": "busy",
                "failure": CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true).wire]))
            return
        }
        guard !mutation || (mutationID == nil && completed.count < 512) else {
            reply(encode(["version": 1, "id": id, "error": "busy",
                "failure": CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true).wire])); return
        }
        if mutation { mutationID = id }
        tasks[id] = Task { @MainActor in
            defer {
                tasks[id] = nil
                cancellations[id] = nil
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
                // Raw framework errors can contain URLs, authentication data or server responses.
                // Detailed errors remain inside the SideStore process.
                if let serviceError = error as? ServiceError { response["error"] = serviceError.rawValue }
                else if error is CancellationError { response["error"] = "cancelled" }
                else { response["error"] = "operationFailed" }
                let stage: CombinedFailure.Stage
                switch operation {
                case "snapshot": stage = .serviceReadiness
                case "beginSignIn", "signInState", "signInRespond", "cancelSignIn", "signOut", "syncAppIDs": stage = .authentication
                case "operationState", "operationRespond", "cancelOperation": stage = .command
                case "install", "installURL", "installSharedIPA", "update", "activate": stage = .installation
                case "refreshApp": stage = .refreshVerification
                default: stage = .command
                }
                if let serviceError = error as? ServiceError {
                    let code: CombinedFailure.Code
                    switch serviceError {
                    case .notReady: code = .notReady
                    case .busy: code = .busy
                    case .unsupported: code = .unsupported
                    case .notFound: code = .unavailable
                    case .invalidRequest: code = .invalidConfiguration
                    }
                    response["failure"] = CombinedFailure(operation: operation, stage: stage, code: code, id: id).wire
                } else {
                    response["failure"] = CombinedFailure.capture(error, operation: operation, stage: stage, id: id).wire
                }
            }
            let encoded = encode(response)
            if mutation { completed[id] = (encoded, deadline) }
            reply(encoded)
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(max(0, deadline.timeIntervalSinceNow) * 1_000_000_000))
            if tasks[id] != nil { tasks[id]?.cancel(); cancellations[id]?() }
        }
    }

    enum ServiceError: String, Error { case notReady, invalidRequest, notFound, unsupported, busy }

    private func encode(_ value: [String: Any]) -> Data {
        guard let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0),
              data.count <= 4_194_304 else {
            return try! PropertyListSerialization.data(fromPropertyList: ["id": value["id"] ?? "", "error": "responseTooLarge"], format: .binary, options: 0)
        }
        return data
    }

    private func run(_ operation: String, request: [String: Any], id: String) async throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let target = request["target"] as? String ?? ""
        switch operation {
        case "snapshot": return try snapshot()
        case "beginSignIn":
            if signInFlow == nil || signInFlow?.isTerminal == true {
                let flow = V3HeadlessSignInFlow()
                signInFlow = flow
                flow.start()
            }
            guard let flow = signInFlow else { throw ServiceError.notReady }
            return flow.snapshot()
        case "signInState":
            guard let flow = signInFlow, flow.id == target else { throw ServiceError.notFound }
            return flow.snapshot()
        case "signInRespond":
            guard let flow = signInFlow, flow.id == target,
                  let data = request["payload"] as? Data,
                  let payload = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            try flow.session.respond(payload)
            return flow.snapshot()
        case "cancelSignIn":
            guard let flow = signInFlow, flow.id == target else { throw ServiceError.notFound }
            flow.cancel()
            return flow.snapshot()
        case "operationState":
            guard let flow = operationFlow, flow.id == target else { throw ServiceError.notFound }
            return flow.snapshot()
        case "operationRespond":
            guard let flow = operationFlow, flow.id == target,
                  let data = request["payload"] as? Data,
                  let payload = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            try flow.session.respond(payload)
            return flow.snapshot()
        case "cancelOperation":
            guard let flow = operationFlow, flow.id == target else { throw ServiceError.notFound }
            flow.cancel()
            return flow.snapshot()
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
        case "settingsPanelSnapshot":
            return try await settingsPanelSnapshot(target)
        case "settingsPanelCommand":
            guard let payload = try decodePayload(request) else { throw ServiceError.invalidRequest }
            return try await settingsPanelCommand(target, payload: payload)
        case "developerServicesSnapshot":
            return try await developerServicesSnapshot()
        case "developerServicesCommand":
            guard let payload = try decodePayload(request) else { throw ServiceError.invalidRequest }
            try await developerServicesCommand(payload)
            return try await developerServicesSnapshot()
        case "backupAccountExport":
            guard let payload = try decodePayload(request),
                  let password = payload["password"] as? String, !password.isEmpty else {
                throw ServiceError.invalidRequest
            }
            let includeApplePassword = payload["includeApplePassword"] as? Bool ?? false
            let data = try ImportExport.exportAccount(password: password, includeApplePassword: includeApplePassword)
            guard data.count <= 1_048_576 else { throw ServiceError.unsupported }
            return ["fileName": AppConstants.accountConfigurationFileName, "data": data]
        case "backupAccountImportSharedFile":
            guard let payload = try decodePayload(request),
                  let password = payload["password"] as? String, !password.isEmpty,
                  UUID(uuidString: target) != nil,
                  let group = Bundle.main.altstoreAppGroup,
                  let defaults = UserDefaults(suiteName: group),
                  let bookmark = defaults.data(forKey: "V3SharedAccountBackup." + target) else {
                throw ServiceError.invalidRequest
            }
            defaults.removeObject(forKey: "V3SharedAccountBackup." + target)
            var stale = false
            let url = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
            guard !stale, url.isFileURL else { throw ServiceError.invalidRequest }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            guard !data.isEmpty, data.count <= 1_048_576 else { throw ServiceError.invalidRequest }
            let account = try ImportExport.importAccount(data, filePassword: password)
            if let applePassword = payload["applePassword"] as? String, !applePassword.isEmpty {
                AuthManager.shared.password = applePassword
            }
            return [
                "email": account.email,
                "needsApplePassword": (account.password?.isEmpty ?? true) && ((payload["applePassword"] as? String)?.isEmpty ?? true)
            ]
        case "importPairingSharedFile":
            guard UUID(uuidString: target) != nil, let group = Bundle.main.altstoreAppGroup,
                  let defaults = UserDefaults(suiteName: group),
                  let bookmark = defaults.data(forKey: "V3SharedPairing." + target) else { throw ServiceError.invalidRequest }
            defaults.removeObject(forKey: "V3SharedPairing." + target)
            var stale = false
            let url = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
            let allowedExtensions = Set(["mobiledevicepairing", "plist", "xml"])
            guard !stale, url.isFileURL, allowedExtensions.contains(url.pathExtension.lowercased()) else {
                throw ServiceError.invalidRequest
            }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            guard !data.isEmpty, data.count <= 1_048_576,
                  let contents = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
                throw ServiceError.invalidRequest
            }
            try PairingFileManager.shared.savePairingFile(contents: contents)
        case "certificatesSnapshot":
            let activeSerial = CertificateManager.shared.activeCertificate?.serialNumber
            let certificates: [[String: Any]] = CertificateManager.shared.getAllLocalCertificates().map { certificate in
                ["serialNumber": certificate.serialNumber,
                 "name": certificate.name,
                 "expirationDate": certificate.x509.expiryDate,
                 "active": certificate.serialNumber == activeSerial]
            }
            return ["certificates": certificates]
        case "activateLocalCertificate":
            guard let certificate = CertificateManager.shared.getLocalCertificate(serialNumber: target) else {
                throw ServiceError.notFound
            }
            try CertificateManager.shared.setActiveCertificate(certificate)
        case "deleteLocalCertificate":
            guard !target.isEmpty else { throw ServiceError.invalidRequest }
            CertificateManager.shared.deleteCertificate(serialNumber: target)
        case "catalog":
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
            return ["apps": apps.map { app in
                ["identifier": app.objectID.uriRepresentation().absoluteString,
                 "bundleID": app.bundleIdentifier, "name": app.name,
                 "version": app.latestSupportedVersion?.version ?? "Unavailable",
                 "developer": app.developerName, "description": app.localizedDescription,
                 "iconURL": app.iconURL.absoluteString,
                 "downloadURL": app.latestSupportedVersion?.downloadURL.absoluteString ?? "",
                 "canInstall": app.latestSupportedVersion != nil,
                 "installedID": app.installedApp?.objectID.uriRepresentation().absoluteString ?? ""] as [String: Any]
            }, "nextCursor": fetched.count > 50 ? offset + 50 : -1]
        case "refreshSources":
            try await callback { done in AppManager.shared.updateAllSources(completion: done) }
        case "addSource", "removeSource":
            return try await beginHeadlessOperation(operation, target: target)
        case "signOut":
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
        case "syncAppIDs":
            try await callback { done in AppManager.shared.syncAppIDs(completionHandler: done) }
        case "clearCache":
            try await callback { done in AppManager.shared.clearAppCache(completion: done) }
        case "setSetting":
            guard let value = request["value"] as? Bool else { throw ServiceError.invalidRequest }
            switch target {
            case "betaUpdates": UserDefaults.standard.isBetaUpdatesEnabled = value
            case "idleTimeoutDisabled": UserDefaults.standard.isIdleTimeoutDisableEnabled = value
            case "responseCachingDisabled": UserDefaults.standard.responseCachingDisabled = value
            case "verboseOperations": UserDefaults.standard.isVerboseOperationsLoggingEnabled = value
            default: throw ServiceError.invalidRequest
            }
        case "install", "installURL", "installSharedIPA", "refreshApp",
             "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "jit":
            return try await beginHeadlessOperation(operation, target: target)
        default: throw ServiceError.invalidRequest
        }
        return try snapshot()
    }


    private func decodePayload(_ request: [String: Any]) throws -> [String: Any]? {
        guard let data = request["payload"] as? Data, data.count <= 32_768 else { return nil }
        return try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
    }

    private func settingRow(
        _ type: String,
        key: String,
        title: String,
        value: Any,
        subtitle: String? = nil,
        options: [String]? = nil,
        destructive: Bool = false
    ) -> [String: Any] {
        var row: [String: Any] = [
            "type": type,
            "key": key,
            "title": title,
            "value": value
        ]
        if let subtitle { row["subtitle"] = subtitle }
        if let options { row["options"] = options }
        if destructive { row["destructive"] = true }
        return row
    }

    private func settingSection(_ title: String, rows: [[String: Any]]) -> [String: Any] {
        ["title": title, "rows": rows]
    }

    private func settingsPanelSnapshot(_ target: String) async throws -> [String: Any] {
        switch target {
        case "connection":
            let config = ConnectionConfig.shared
            return [
                "title": "Connection",
                "mode": "form",
                "sections": [
                    settingSection("Connection Mode", rows: [
                        settingRow("bool", key: "useLocalVPN", title: "Use Local VPN", value: config.useLocalVPN),
                        settingRow("text", key: "overrideTunnelPeerIp", title: "Device IP Override", value: config.overrideTunnelPeerIp),
                        settingRow("text", key: "remoteServerIp", title: "Remote Device IP", value: config.remoteServerIp),
                        settingRow("integer", key: "remotePairingPortOverride", title: "RemotePair Port Override", value: UserDefaults.standard.remotePairingPortOverride)
                    ]),
                    settingSection("WireGuard", rows: [
                        settingRow("text", key: "wireguardServerHost", title: "Bind Host / IP", value: config.wireguardServerHost),
                        settingRow("integer", key: "wireguardServerPort", title: "Bind Port", value: Int(config.wireguardServerPort)),
                        settingRow("bool", key: "alwaysShowWireGuardConfig", title: "Always Show WireGuard Config", value: UserDefaults.standard.alwaysShowWireGuardConfig),
                        settingRow("bool", key: "acceptIPv6ConnectionConfig", title: "Accept IPv6 Config", value: UserDefaults.standard.acceptIPv6ConnectionConfig)
                    ]),
                    settingSection("Live Status", rows: [
                        settingRow("info", key: "tunnelInterface", title: "Tunnel Interface", value: config.formattedTunnelIface ?? "Unavailable"),
                        settingRow("info", key: "tunnelPeer", title: "Tunnel Peer", value: config.formattedTunnelPeer ?? "Unavailable"),
                        settingRow("info", key: "remotePeer", title: "Remote Peer", value: config.remotePeerIp ?? "Unavailable"),
                        settingRow("info", key: "remoteReachable", title: "Remote Reachable", value: config.remoteReachable ? "Yes" : "No")
                    ])
                ],
                "saveAction": "save"
            ]

        case "anisette":
            let manager = AnisetteServersManager.shared
            let servers = await manager.loadLocalServers()
            let active = UserDefaults.standard.menuAnisetteURL
            let offline = await manager.isOfflineMode
            let importedName = await manager.importedFileName ?? ""
            return [
                "title": "Anisette Servers",
                "mode": "anisette",
                "active": active,
                "source": UserDefaults.standard.menuAnisetteList,
                "offline": offline,
                "importedFileName": importedName,
                "servers": servers.map {
                    ["name": $0.name, "address": $0.address, "hidden": $0.isHidden]
                }
            ]

        case "sideSign":
            let headers = await SideSignConfigManager.shared.loadConfig()
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            let data = try encoder.encode(headers)
            return [
                "title": "SideSign Configuration",
                "mode": "json",
                "json": String(data: data, encoding: .utf8) ?? "{}"
            ]

        case "customizations":
            let backend = UserDefaults.standard.minimuxerGatewayBackend
            return [
                "title": "Installation and Signing Options",
                "mode": "form",
                "sections": [
                    settingSection("Anisette", rows: [
                        settingRow("bool", key: "useOnDeviceAnisette", title: "On-Device Anisette", value: UserDefaults.standard.useOnDeviceAnisette,
                                   subtitle: "Changing this signs out the backend and requires a SideStore service restart.")
                    ]),
                    settingSection("General", rows: [
                        settingRow("bool", key: "customizeAppId", title: "Customize AppID", value: UserDefaults.standard.customizeAppId),
                        settingRow("bool", key: "customizeAppExtensions", title: "Customize App Extensions", value: UserDefaults.standard.customizeAppExtensions),
                        settingRow("bool", key: "autoFixAppGroupIDs", title: "Auto-Fix AppGroup IDs", value: UserDefaults.standard.autoFixAppGroupIDs),
                        settingRow("bool", key: "preferResignedIPA", title: "Prefer Resigned IPA", value: UserDefaults.standard.preferResignedIPA),
                        settingRow("bool", key: "isExportResignedAppEnabled", title: "Export Resigned IPAs", value: UserDefaults.standard.isExportResignedAppEnabled),
                        settingRow("bool", key: "skipNonCopyableBackupFiles", title: "Skip Uncopyable Backup Files", value: UserDefaults.standard.skipNonCopyableBackupFiles)
                    ]),
                    settingSection("App Verification", rows: [
                        settingRow("bool", key: "appVerificationDisabled", title: "Disable All Verifications", value: UserDefaults.standard.appVerificationDisabled),
                        settingRow("bool", key: "isBundleIDVerificationEnabled", title: "Bundle Identifier Check", value: UserDefaults.standard.isBundleIDVerificationEnabled),
                        settingRow("bool", key: "isiOSVersionVerificationEnabled", title: "iOS Version Check", value: UserDefaults.standard.isiOSVersionVerificationEnabled),
                        settingRow("bool", key: "isAppVersionVerificationEnabled", title: "App Version Check", value: UserDefaults.standard.isAppVersionVerificationEnabled),
                        settingRow("bool", key: "isChecksumVerificationEnabled", title: "Checksum Check", value: UserDefaults.standard.isChecksumVerificationEnabled),
                        settingRow("bool", key: "isFileSizeVerificationEnabled", title: "App File Size Check", value: UserDefaults.standard.isFileSizeVerificationEnabled),
                        settingRow("bool", key: "permissionCheckingEnabled", title: "Permission Checks", value: !UserDefaults.standard.permissionCheckingDisabled)
                    ]),
                    settingSection("EMProxy & Minimuxer", rows: [
                        settingRow("bool", key: "enableEMPforWireguard", title: "EMProxy (WireGuard) Server", value: UserDefaults.standard.enableEMPforWireguard,
                                   subtitle: "Requires backend restart."),
                        settingRow("option", key: "minimuxerGatewayBackend", title: "Minimuxer Backend", value: backend,
                                   options: GatewayBackend.allCases.map(\.rawValue))
                    ])
                ],
                "saveAction": "save"
            ]

        case "health":
            let config = ConnectionConfig.shared
            let readiness = await minimuxer.core.isReady(withDDIMountCheck: true)
            return [
                "title": "Health Check",
                "mode": "info",
                "sections": [
                    settingSection("Core Requirements", rows: [
                        settingRow("info", key: "readiness", title: "Minimuxer", value: String(describing: readiness)),
                        settingRow("info", key: "pairing", title: "Pairing File", value: PairingFileManager.shared.fetchPairingFile() == nil ? "Missing" : "Loaded"),
                        settingRow("info", key: "tunnelPeer", title: "Tunnel Peer", value: config.formattedTunnelPeer ?? "Unavailable"),
                        settingRow("info", key: "remoteReachable", title: "Remote Reachable", value: config.remoteReachable ? "Yes" : "No")
                    ])
                ]
            ]

        case "sideJIT":
            return [
                "title": "SideJIT Server",
                "mode": "form",
                "sections": [
                    settingSection("Server", rows: [
                        settingRow("bool", key: "isSideJITServerEnabled", title: "Enable SideJITServer", value: UserDefaults.standard.isSideJITServerEnabled),
                        settingRow("text", key: "textInputSideJITServerurl", title: "Server Address", value: UserDefaults.standard.textInputSideJITServerurl ?? "",
                                   subtitle: "Leave empty for Bonjour discovery.")
                    ])
                ],
                "saveAction": "save"
            ]

        case "releaseTrack":
            let current = UserDefaults.standard.betaUdpatesTrack ?? UserDefaults.defaultBetaUpdatesTrack
            let tracks = [current] + ReleaseTrackType.betaTracks.map(\.rawValue).filter { $0 != current }
            return [
                "title": "Update Channel",
                "mode": "form",
                "sections": [
                    settingSection("Updates", rows: [
                        settingRow("option", key: "betaUpdatesTrack", title: "Beta Update Channel", value: current, options: tracks)
                    ])
                ],
                "saveAction": "save"
            ]

        case "diagnostics":
            return [
                "title": "SideStore Diagnostics",
                "mode": "form",
                "sections": [
                    settingSection("Logging", rows: [
                        settingRow("bool", key: "responseCachingDisabled", title: "Disable URL Response Caching", value: UserDefaults.standard.responseCachingDisabled),
                        settingRow("bool", key: "isRotateLogsOnStartupEnabled", title: "Rotate Logs on Startup", value: UserDefaults.standard.isRotateLogsOnStartupEnabled),
                        settingRow("bool", key: "isSideStoreVerboseLoggingEnabled", title: "SideStore Verbose Logging", value: UserDefaults.standard.isSideStoreVerboseLoggingEnabled),
                        settingRow("bool", key: "isAltSignVerboseLoggingEnabled", title: "SideSign Verbose Logging", value: UserDefaults.standard.isAltSignVerboseLoggingEnabled),
                        settingRow("bool", key: "isMinimuxerVerboseLoggingEnabled", title: "Minimuxer Verbose Logging", value: UserDefaults.standard.isMinimuxerVerboseLoggingEnabled),
                        settingRow("bool", key: "isVerboseOperationsLoggingEnabled", title: "Operations Verbose Logging", value: UserDefaults.standard.isVerboseOperationsLoggingEnabled)
                    ]),
                    settingSection("Database & Connection", rows: [
                        settingRow("bool", key: "recreateDatabaseOnNextStart", title: "Recreate Database on Next Start", value: UserDefaults.standard.recreateDatabaseOnNextStart),
                        settingRow("bool", key: "alwaysShowWireGuardConfig", title: "Always Show WireGuard Config", value: UserDefaults.standard.alwaysShowWireGuardConfig),
                        settingRow("bool", key: "acceptIPv6ConnectionConfig", title: "Accept IPv6 Connection Config", value: UserDefaults.standard.acceptIPv6ConnectionConfig)
                    ])
                ],
                "saveAction": "save"
            ]

        case "experimental":
            return [
                "title": "Experimental Features",
                "mode": "form",
                "sections": [
                    settingSection("Feature Flags", rows: [
                        settingRow("bool", key: "isCellularRefreshEnabled", title: "Cellular Refresh", value: UserDefaults.standard.isCellularRefreshEnabled)
                    ])
                ],
                "saveAction": "save"
            ]

        case "logs":
            guard let delegate = UIApplication.shared.delegate as? AppDelegate else { throw ServiceError.notReady }
            let url = delegate.consoleLog.logFileURL
            let data = (try? Data(contentsOf: url)) ?? Data()
            let tail = data.suffix(262_144)
            let text = String(data: tail, encoding: .utf8) ?? String(decoding: tail, as: UTF8.self)
            return [
                "title": "Operation Logs",
                "mode": "log",
                "text": text
            ]

        case "backups":
            return [
                "title": "SideStore Backups",
                "mode": "backup",
                "account": AuthManager.shared.currentAppleID ?? ""
            ]

        default:
            throw ServiceError.invalidRequest
        }
    }

    private func settingsPanelCommand(_ target: String, payload: [String: Any]) async throws -> [String: Any] {
        guard let action = payload["action"] as? String else { throw ServiceError.invalidRequest }

        switch target {
        case "connection":
            guard action == "save", let values = payload["values"] as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            let config = ConnectionConfig.shared
            if let value = values["useLocalVPN"] as? Bool { config.useLocalVPN = value }
            if let value = values["overrideTunnelPeerIp"] as? String { config.overrideTunnelPeerIp = value.trimmingCharacters(in: .whitespacesAndNewlines) }
            if let value = values["remoteServerIp"] as? String { config.remoteServerIp = value.trimmingCharacters(in: .whitespacesAndNewlines) }
            if let value = values["wireguardServerHost"] as? String, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                config.wireguardServerHost = value.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            if let number = values["wireguardServerPort"] as? NSNumber {
                let port = number.intValue
                guard (1...65535).contains(port) else { throw ServiceError.invalidRequest }
                config.wireguardServerPort = UInt16(port)
            }
            if let number = values["remotePairingPortOverride"] as? NSNumber {
                let port = number.intValue
                guard port == 0 || (1...65535).contains(port) else { throw ServiceError.invalidRequest }
                UserDefaults.standard.remotePairingPortOverride = port
            }
            if let value = values["alwaysShowWireGuardConfig"] as? Bool {
                UserDefaults.standard.alwaysShowWireGuardConfig = value
            }
            if let value = values["acceptIPv6ConnectionConfig"] as? Bool {
                UserDefaults.standard.acceptIPv6ConnectionConfig = value
            }
            syncMinimuxerBackendFromUserDefaults()
            await bindConnectionConfig()

        case "anisette":
            let manager = AnisetteServersManager.shared
            switch action {
            case "select":
                guard let address = payload["address"] as? String else { throw ServiceError.invalidRequest }
                let servers = await manager.loadLocalServers()
                guard servers.contains(where: { !$0.isHidden && $0.address == address }) else { throw ServiceError.invalidRequest }
                UserDefaults.standard.menuAnisetteURL = address
            case "toggleHidden":
                guard let address = payload["address"] as? String,
                      let hidden = payload["hidden"] as? Bool else { throw ServiceError.invalidRequest }
                var servers = await manager.loadLocalServers()
                guard let index = servers.firstIndex(where: { $0.address == address }) else { throw ServiceError.notFound }
                servers[index].isHidden = hidden
                await manager.saveLocalServers(servers)
            case "sync":
                _ = try await manager.syncWithRemote(forceRemote: true)
            case "reset":
                _ = try await manager.resetToOriginalState()
            case "setSource":
                guard let source = payload["source"] as? String, let url = URL(string: source),
                      ["https", "http"].contains(url.scheme?.lowercased() ?? "") else { throw ServiceError.invalidRequest }
                UserDefaults.standard.menuAnisetteList = source
                _ = try await manager.syncWithRemote(sourceURLString: source, forceRemote: true)
            case "resetAdi":
                let keepHeaders = payload["keepHeaders"] as? Bool ?? true
                AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: false, keepAnisetteHeaders: keepHeaders)
            default:
                throw ServiceError.invalidRequest
            }

        case "sideSign":
            switch action {
            case "saveRaw":
                guard let json = payload["json"] as? String,
                      json.utf8.count <= 24_000,
                      let data = json.data(using: .utf8) else { throw ServiceError.invalidRequest }
                let headers = try JSONDecoder().decode(SideSignHeaders.self, from: data)
                await SideSignConfigManager.shared.saveConfig(headers)
            case "reset":
                _ = await SideSignConfigManager.shared.resetToDefaults()
            default:
                throw ServiceError.invalidRequest
            }

        case "customizations":
            guard action == "save", let values = payload["values"] as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            if let value = values["customizeAppId"] as? Bool { UserDefaults.standard.customizeAppId = value }
            if let value = values["customizeAppExtensions"] as? Bool { UserDefaults.standard.customizeAppExtensions = value }
            if let value = values["autoFixAppGroupIDs"] as? Bool { UserDefaults.standard.autoFixAppGroupIDs = value }
            if let value = values["preferResignedIPA"] as? Bool { UserDefaults.standard.preferResignedIPA = value }
            if let value = values["isExportResignedAppEnabled"] as? Bool { UserDefaults.standard.isExportResignedAppEnabled = value }
            if let value = values["skipNonCopyableBackupFiles"] as? Bool { UserDefaults.standard.skipNonCopyableBackupFiles = value }
            if let value = values["appVerificationDisabled"] as? Bool { UserDefaults.standard.appVerificationDisabled = value }
            if let value = values["isBundleIDVerificationEnabled"] as? Bool { UserDefaults.standard.isBundleIDVerificationEnabled = value }
            if let value = values["isiOSVersionVerificationEnabled"] as? Bool { UserDefaults.standard.isiOSVersionVerificationEnabled = value }
            if let value = values["isAppVersionVerificationEnabled"] as? Bool { UserDefaults.standard.isAppVersionVerificationEnabled = value }
            if let value = values["isChecksumVerificationEnabled"] as? Bool { UserDefaults.standard.isChecksumVerificationEnabled = value }
            if let value = values["isFileSizeVerificationEnabled"] as? Bool { UserDefaults.standard.isFileSizeVerificationEnabled = value }
            if let value = values["permissionCheckingEnabled"] as? Bool { UserDefaults.standard.permissionCheckingDisabled = !value }
            if let value = values["enableEMPforWireguard"] as? Bool { UserDefaults.standard.enableEMPforWireguard = value }
            if let value = values["minimuxerGatewayBackend"] as? String,
               GatewayBackend.allCases.map(\.rawValue).contains(value) {
                UserDefaults.standard.minimuxerGatewayBackend = value
            }
            if let value = values["useOnDeviceAnisette"] as? Bool,
               value != UserDefaults.standard.useOnDeviceAnisette {
                AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: false)
                UserDefaults.standard.useOnDeviceAnisette = value
            }
            UserDefaults.standard.synchronize()

        case "sideJIT":
            guard action == "save", let values = payload["values"] as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            if let value = values["isSideJITServerEnabled"] as? Bool {
                UserDefaults.standard.isSideJITServerEnabled = value
            }
            if let value = values["textInputSideJITServerurl"] as? String {
                let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
                UserDefaults.standard.textInputSideJITServerurl = trimmed.isEmpty ? nil : trimmed
            }

        case "releaseTrack":
            guard action == "save", let values = payload["values"] as? [String: Any],
                  let value = values["betaUpdatesTrack"] as? String else { throw ServiceError.invalidRequest }
            let allowed = Set(ReleaseTrackType.betaTracks.map(\.rawValue) + [UserDefaults.defaultBetaUpdatesTrack])
            guard allowed.contains(value) else { throw ServiceError.invalidRequest }
            UserDefaults.standard.betaUdpatesTrack = value

        case "diagnostics":
            guard action == "save", let values = payload["values"] as? [String: Any] else {
                throw ServiceError.invalidRequest
            }
            if let value = values["responseCachingDisabled"] as? Bool { UserDefaults.standard.responseCachingDisabled = value }
            if let value = values["isRotateLogsOnStartupEnabled"] as? Bool { UserDefaults.standard.isRotateLogsOnStartupEnabled = value }
            if let value = values["isSideStoreVerboseLoggingEnabled"] as? Bool {
                UserDefaults.standard.isSideStoreVerboseLoggingEnabled = value
                SideStoreLogging.setLogging(value)
            }
            if let value = values["isAltSignVerboseLoggingEnabled"] as? Bool {
                UserDefaults.standard.isAltSignVerboseLoggingEnabled = value
                SideSignLogging.setLogging(value)
            }
            if let value = values["isMinimuxerVerboseLoggingEnabled"] as? Bool {
                UserDefaults.standard.isMinimuxerVerboseLoggingEnabled = value
                minimuxerSetLogging(value)
            }
            if let value = values["isVerboseOperationsLoggingEnabled"] as? Bool {
                UserDefaults.standard.isVerboseOperationsLoggingEnabled = value
            }
            if let value = values["recreateDatabaseOnNextStart"] as? Bool {
                UserDefaults.standard.recreateDatabaseOnNextStart = value
            }
            if let value = values["alwaysShowWireGuardConfig"] as? Bool {
                UserDefaults.standard.alwaysShowWireGuardConfig = value
            }
            if let value = values["acceptIPv6ConnectionConfig"] as? Bool {
                UserDefaults.standard.acceptIPv6ConnectionConfig = value
            }

        case "experimental":
            guard action == "save", let values = payload["values"] as? [String: Any],
                  let enabled = values["isCellularRefreshEnabled"] as? Bool else {
                throw ServiceError.invalidRequest
            }
            CellularRefreshManager.shared.setEnabled(enabled)

        case "backups":
            switch action {
            case "refresh":
                break
            case "setApplePassword":
                guard let password = payload["password"] as? String, !password.isEmpty else {
                    throw ServiceError.invalidRequest
                }
                AuthManager.shared.password = password
            default:
                throw ServiceError.invalidRequest
            }

        case "logs", "health":
            guard action == "refresh" else { throw ServiceError.invalidRequest }

        default:
            throw ServiceError.invalidRequest
        }

        return try await settingsPanelSnapshot(target)
    }

    private func developerServicesSnapshot() async throws -> [String: Any] {
        guard let team = AuthManager.shared.team else { throw ServiceError.notReady }

        async let appIDsTask = DeveloperPortalProxy.shared.fetchAppIDs()
        async let profilesTask = DeveloperPortalProxy.shared.listProvisioningProfiles()
        async let groupsTask = DeveloperPortalProxy.shared.fetchAppGroups()
        async let devicesTask = DeveloperPortalProxy.shared.fetchDevices(types: .all)
        async let certsTask = DeveloperPortalProxy.shared.fetchCertificates()

        let (appIDs, profiles, groups, devices, certificates) =
            try await (appIDsTask, profilesTask, groupsTask, devicesTask, certsTask)

        return [
            "team": [
                "id": team.identifier,
                "name": team.name,
                "type": team.type.displayName,
                "paid": team.isPaid
            ],
            "appIDs": appIDs.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map {
                [
                    "id": $0.identifier,
                    "name": $0.name,
                    "bundleID": $0.bundleIdentifier,
                    "featureCount": $0.features.count
                ] as [String: Any]
            },
            "profiles": profiles.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map {
                [
                    "id": $0.identifier ?? "",
                    "uuid": $0.uuid.uuidString,
                    "name": $0.name,
                    "bundleID": $0.bundleIdentifier ?? "",
                    "expires": $0.dateExpire
                ] as [String: Any]
            },
            "appGroups": groups.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map {
                [
                    "id": $0.identifier,
                    "name": $0.name,
                    "groupIdentifier": $0.groupIdentifier
                ] as [String: Any]
            },
            "devices": devices.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map {
                [
                    "id": $0.identifier,
                    "name": $0.name,
                    "type": $0.type.displayName,
                    "status": $0.status
                ] as [String: Any]
            },
            "certificates": certificates.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }.map {
                [
                    "serial": $0.serialNumber,
                    "name": $0.name,
                    "expires": $0.expiryDate,
                    "machineName": $0.machineName ?? ""
                ] as [String: Any]
            }
        ]
    }

    private func developerServicesCommand(_ payload: [String: Any]) async throws {
        guard let action = payload["action"] as? String else { throw ServiceError.invalidRequest }

        switch action {
        case "refresh":
            AuthManager.shared.session = nil

        case "createAppID":
            guard let name = payload["name"] as? String,
                  let bundleID = payload["bundleID"] as? String,
                  !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !bundleID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw ServiceError.invalidRequest
            }
            _ = try await DeveloperPortalProxy.shared.addAppID(
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                bundleIdentifier: bundleID.trimmingCharacters(in: .whitespacesAndNewlines)
            )

        case "deleteAppID":
            guard let id = payload["id"] as? String else { throw ServiceError.invalidRequest }
            let items = try await DeveloperPortalProxy.shared.fetchAppIDs()
            guard let item = items.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.deleteAppID(item)

        case "generateProfile":
            guard let id = payload["appID"] as? String else { throw ServiceError.invalidRequest }
            let items = try await DeveloperPortalProxy.shared.fetchAppIDs()
            guard let item = items.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.downloadProvisioningProfile(
                for: item,
                deviceType: DeveloperPortalProxy.currentDeviceType
            )

        case "deleteProfile":
            guard let id = payload["id"] as? String, !id.isEmpty else { throw ServiceError.invalidRequest }
            _ = try await DeveloperPortalProxy.shared.deleteProvisioningProfile(profileID: id)

        case "deleteAllProfiles":
            let profiles = try await DeveloperPortalProxy.shared.listProvisioningProfiles()
            for profile in profiles {
                if let id = profile.identifier {
                    _ = try await DeveloperPortalProxy.shared.deleteProvisioningProfile(profileID: id)
                }
            }

        case "revokeCertificate":
            guard let serial = payload["serial"] as? String else { throw ServiceError.invalidRequest }
            let certs = try await DeveloperPortalProxy.shared.fetchCertificates()
            guard let cert = certs.first(where: { $0.serialNumber == serial }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.revokeCertificate(cert)

        case "createAppGroup":
            guard let name = payload["name"] as? String,
                  let identifier = payload["groupIdentifier"] as? String else { throw ServiceError.invalidRequest }
            _ = try await DeveloperPortalProxy.shared.addAppGroup(name: name, groupIdentifier: identifier)

        case "renameAppGroup":
            guard let id = payload["id"] as? String,
                  let name = payload["name"] as? String else { throw ServiceError.invalidRequest }
            let groups = try await DeveloperPortalProxy.shared.fetchAppGroups()
            guard var group = groups.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            group.name = name
            _ = try await DeveloperPortalProxy.shared.updateAppGroup(group)

        case "deleteAppGroup":
            guard let id = payload["id"] as? String else { throw ServiceError.invalidRequest }
            let groups = try await DeveloperPortalProxy.shared.fetchAppGroups()
            guard let group = groups.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.deleteAppGroup(group)

        case "registerDevice":
            guard let name = payload["name"] as? String,
                  let identifier = payload["identifier"] as? String,
                  let typeName = payload["type"] as? String else { throw ServiceError.invalidRequest }
            let type: ALTDeviceType
            switch typeName.lowercased() {
            case "iphone": type = .iphone
            case "ipad": type = .ipad
            case "apple tv", "appletv": type = .appleTV
            case "apple watch", "applewatch": type = .appleWatch
            case "mac": type = .mac
            case "vision pro", "visionpro": type = .visionPro
            default: throw ServiceError.invalidRequest
            }
            _ = try await DeveloperPortalProxy.shared.registerDevice(name: name, identifier: identifier, type: type)

        case "renameDevice":
            guard let id = payload["id"] as? String,
                  let name = payload["name"] as? String else { throw ServiceError.invalidRequest }
            let devices = try await DeveloperPortalProxy.shared.fetchDevices(types: .all)
            guard var device = devices.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            device.name = name
            _ = try await DeveloperPortalProxy.shared.updateDevice(device)

        case "disableDevice":
            guard let id = payload["id"] as? String else { throw ServiceError.invalidRequest }
            let devices = try await DeveloperPortalProxy.shared.fetchDevices(types: .all)
            guard let device = devices.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.disableDevice(device)

        case "deleteDevice":
            guard let id = payload["id"] as? String else { throw ServiceError.invalidRequest }
            let devices = try await DeveloperPortalProxy.shared.fetchDevices(types: .all)
            guard let device = devices.first(where: { $0.identifier == id }) else { throw ServiceError.notFound }
            _ = try await DeveloperPortalProxy.shared.deleteDevice(device)

        default:
            throw ServiceError.invalidRequest
        }
    }

    private func beginHeadlessOperation(_ operation: String, target: String) async throws -> [String: Any] {
        guard operationFlow == nil || operationFlow?.isTerminal == true else { throw ServiceError.busy }

        let titles: [String: String] = [
            "addSource": "Add Source",
            "removeSource": "Remove Source",
            "install": "Install App",
            "installURL": "Install App",
            "installSharedIPA": "Install / Sideload App",
            "refreshApp": "Refresh App",
            "update": "Update App",
            "activate": "Activate App",
            "deactivate": "Deactivate App",
            "remove": "Remove from Library",
            "delete": "Delete App",
            "backup": "Backup App",
            "restore": "Restore Backup",
            "jit": "Enable JIT"
        ]
        let flow = V3HeadlessOperationFlow(title: titles[operation] ?? "SideStore Operation")
        operationFlow = flow

        switch operation {
        case "addSource":
            guard let url = URL(string: target),
                  ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                  url.host != nil, url.user == nil, url.password == nil else {
                throw ServiceError.invalidRequest
            }
            flow.start { _, flow in
                let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
                let source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
                let sourceInfo = try await background.perform {
                    (source.name, source.identifier, source.sourceURL.absoluteString)
                }
                let confirmed: Bool = try await flow.session.ask(
                    kind: "sourceAddConfirmation",
                    title: "Add Source",
                    message: "Only add sources that you trust.",
                    fields: [
                        "name": sourceInfo.0,
                        "identifier": sourceInfo.1,
                        "url": sourceInfo.2
                    ]
                ) { payload in
                    switch payload["action"] as? String {
                    case "confirm": return true
                    case "cancel": throw OperationError.cancelled
                    default: throw V3HeadlessFlowError.invalidResponse
                    }
                }
                guard confirmed else { throw OperationError.cancelled }
                try await AppManager.shared.add(source, presentingViewController: nil, confirmed: true)
                return ["operation": operation]
            }

        case "removeSource":
            let query = NSFetchRequest<Source>(entityName: "Source")
            query.predicate = NSPredicate(format: "identifier == %@", target)
            guard let source = try DatabaseManager.shared.viewContext.fetch(query).first else {
                throw ServiceError.notFound
            }
            let sourceName = source.name
            let sourceID = source.identifier
            guard sourceID != Source.altStoreIdentifier else { throw ServiceError.unsupported }
            flow.start { _, flow in
                _ = try await flow.session.ask(
                    kind: "sourceRemoveConfirmation",
                    title: "Remove Source",
                    message: "Installed apps will remain, but they will no longer receive updates from this source.",
                    fields: ["name": sourceName, "identifier": sourceID]
                ) { payload in
                    guard payload["action"] as? String == "confirm" else {
                        throw OperationError.cancelled
                    }
                    return true
                }
                try await AppManager.shared.remove(source, presentingViewController: nil, confirmed: true)
                return ["operation": operation]
            }

        case "install", "installURL", "installSharedIPA":
            let installTarget: InstallTarget
            if operation == "install" {
                let app: StoreApp = try object(target)
                guard app.latestSupportedVersion != nil else { throw ServiceError.unsupported }
                installTarget = .app(app)
            } else if operation == "installSharedIPA" {
                guard UUID(uuidString: target) != nil,
                      let group = Bundle.main.altstoreAppGroup,
                      let defaults = UserDefaults(suiteName: group),
                      let bookmark = defaults.data(forKey: "V3SharedIPA." + target) else {
                    throw ServiceError.invalidRequest
                }
                defaults.removeObject(forKey: "V3SharedIPA." + target)
                var stale = false
                let url = try URL(
                    resolvingBookmarkData: bookmark,
                    options: .withoutUI,
                    relativeTo: nil,
                    bookmarkDataIsStale: &stale
                )
                guard !stale, url.isFileURL, url.pathExtension.lowercased() == "ipa" else {
                    throw ServiceError.invalidRequest
                }
                flow.retainSecurityScopedURL(url)
                installTarget = .url(url)
            } else {
                guard let url = URL(string: target),
                      ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                      url.host != nil, url.user == nil, url.password == nil else {
                    throw ServiceError.invalidRequest
                }
                installTarget = .url(url)
            }

            flow.start { handler, flow in
                if case .app(let app) = installTarget,
                   let storeApp = app as? StoreApp,
                   let source = storeApp.source,
                   try await !source.isAdded() {
                    let sourceName = source.name
                    let sourceID = source.identifier
                    _ = try await flow.session.ask(
                        kind: "sourceAddConfirmation",
                        title: "Add Required Source",
                        message: "This source must be added before the app can be installed.",
                        fields: [
                            "name": sourceName,
                            "identifier": sourceID,
                            "url": source.sourceURL.absoluteString
                        ]
                    ) { payload in
                        guard payload["action"] as? String == "confirm" else {
                            throw OperationError.cancelled
                        }
                        return true
                    }
                    try await AppManager.shared.add(source, presentingViewController: nil, confirmed: true)
                }

                try await self.callback { done in
                    let group = AppManager.shared.install(
                        installTarget,
                        presentingViewController: nil,
                        pipelineHandler: handler
                    ) { result in
                        done(result.map { _ in () })
                    }
                    flow.track(group.progress) {
                        group.cancel()
                        group.progress.cancel()
                    }
                }
                return ["operation": operation]
            }

        case "refreshApp", "update", "activate", "deactivate", "remove", "delete", "backup", "restore":
            let app: InstalledApp = try object(target)
            if operation == "refreshApp" {
                guard app.isActive, app.bundleIdentifier != StoreApp.altstoreAppID else {
                    throw ServiceError.unsupported
                }
            }
            if ["deactivate", "remove", "delete"].contains(operation),
               app.bundleIdentifier == StoreApp.altstoreAppID {
                throw ServiceError.unsupported
            }

            let pipelineOperation: AppOperation
            switch operation {
            case "refreshApp":
                pipelineOperation = .refresh(app)
            case "update":
                guard let version = app.storeApp?.latestSupportedVersion else {
                    throw ServiceError.notFound
                }
                pipelineOperation = .update(version, customBundleIdentifier: app.customBundleIdentifier)
            case "activate":
                pipelineOperation = .activate(app)
            case "deactivate":
                pipelineOperation = .deactivate(app)
            case "remove":
                pipelineOperation = .removeApp(app)
            case "delete":
                pipelineOperation = .deleteApp(app)
            case "backup":
                pipelineOperation = .backup(app)
            case "restore":
                pipelineOperation = .restore(app)
            default:
                throw ServiceError.invalidRequest
            }

            flow.start { handler, flow in
                let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
                let operationContext = StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background)
                try await self.callback { done in
                    let group = AppManager.shared.pipelineRunner.performSingleOperation(
                        pipelineOperation,
                        handler: handler,
                        context: operationContext
                    ) { result in
                        done(result.map { _ in () })
                    }
                    flow.track(group.progress) {
                        group.cancel()
                        group.progress.cancel()
                    }
                }
                return ["operation": operation]
            }

        case "jit":
            let app: InstalledApp = try object(target)
            flow.start { _, _ in
                try await self.callback { done in
                    AppManager.shared.enableJIT(for: app, completionHandler: done)
                }
                return ["operation": operation]
            }

        default:
            throw ServiceError.invalidRequest
        }

        return flow.snapshot()
    }

    private func object<T: NSManagedObject>(_ identifier: String) throws -> T {
        guard let url = URL(string: identifier),
              let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
              let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else { throw ServiceError.notFound }
        return object
    }

    @objc private func closePanel() {
        let finish = finishPanel
        finishPanel = nil
        Self.presenter.dismiss(animated: true) { finish?() }
    }

    private func callback(_ start: (@escaping (Result<Void, Error>) -> Void) -> Void) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let gate = V3ServiceCallbackGate(continuation)
            start { result in gate.settle(result) }
        }
    }

    private func snapshot() throws -> [String: Any] {
        let context = DatabaseManager.shared.viewContext
        let apps = InstalledApp.all(in: context)
        let sources = try context.fetch(NSFetchRequest<Source>(entityName: "Source"))
        let team = DatabaseManager.shared.activeTeam()
        let certificate = CertificateManager.shared.activeCertificate?.certificate.x509
        return ["updatedAt": Date(), "busy": mutationID != nil,
                "account": DatabaseManager.shared.activeAccount()?.appleID ?? "Not signed in",
                "team": team?.name ?? "No active team", "teamID": team?.identifier ?? "",
                "signing": team == nil ? "Sign in required" : "Team selected",
                "certificate": CertificateManager.shared.activeCertificate == nil ? "No active certificate" : "Active certificate available",
                "certificateExpiration": certificate?.expiryDate ?? Date.distantPast,
                "pairing": PairingFileManager.shared.fetchPairingFile() == nil ? "Pairing file required" : "Pairing file available",
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
    }
}

private struct V3ReleaseTrackView: View {
    @State private var track = UserDefaults.standard.betaUdpatesTrack ?? UserDefaults.defaultBetaUpdatesTrack
    private var tracks: [String] {
        [track] + ReleaseTrackType.betaTracks.map(\.rawValue).filter { $0 != track }
    }
    var body: some View {
        Form {
            Picker("Beta update channel", selection: $track) {
                ForEach(tracks, id: \.self) { Text($0).tag($0) }
            }
        }.navigationTitle("Update Channel")
            .onChange(of: track) { UserDefaults.standard.betaUdpatesTrack = $0 }
    }
}
