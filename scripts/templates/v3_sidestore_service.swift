
// V3_SIDESTORE_COMMAND_SERVICE_V1
// Compiled only into SideStore. No managed objects or credentials cross XPC.
import SwiftUI

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
@objc(V3SideStoreService)
final class V3SideStoreService: NSObject {
    static let shared = V3SideStoreService()
    private var tasks: [String: Task<Void, Never>] = [:]
    private var cancellations: [String: () -> Void] = [:]
    private var completed: [String: (data: Data, deadline: Date)] = [:]
    private var mutationID: String?
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
        let mutation = !["snapshot", "catalog", "appIcon", "backupResult", "certificatesSnapshot"].contains(operation)
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
                case "signIn", "signOut", "syncAppIDs": stage = .authentication
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
        case "panel":
            let controller = UIHostingController(rootView: AnyView(EmptyView()))
            let content: AnyView
            switch target {
            case "developerServices": content = AnyView(DeveloperServicesView(presentingViewController: controller))
            case "connection": content = AnyView(ConnectionConfigView())
            case "anisette": content = AnyView(AnisetteServersView(selected: UserDefaults.standard.menuAnisetteURL, onResetAdiPb: {}))
            case "sideSign": content = AnyView(SideSignConfigurationView())
            case "health": content = AnyView(HealthCheckView())
            case "backups": content = AnyView(BackupAndRestoreView())
            case "sideJIT": content = AnyView(SideJITServerConfigView())
            case "customizations": content = AnyView(UserCustomizationsView())
            case "diagnostics": content = AnyView(DeveloperOptionsView())
            case "experimental": content = AnyView(ExperimentalFeaturesView())
            case "releaseTrack": content = AnyView(V3ReleaseTrackView())
            case "logs":
                guard let delegate = UIApplication.shared.delegate as? AppDelegate else { throw ServiceError.notReady }
                content = AnyView(ConsoleLogView(logURL: delegate.consoleLog.logFileURL))
            default: throw ServiceError.invalidRequest
            }
            // SwiftUI links need navigation; UIKit certificate pushes need the
            // actual hosting controller's navigation controller.
            controller.rootView = AnyView(NavigationView { content }.navigationViewStyle(StackNavigationViewStyle()))
            try await callback { done in
                controller.navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(closePanel))
                let navigation = UINavigationController(rootViewController: controller)
                navigation.isModalInPresentation = true
                self.finishPanel = { done(.success(())) }
                self.cancellations[id] = { self.closePanel() }
                Self.presenter.present(navigation, animated: true)
            }
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
        case "addSource":
            guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                  url.host != nil, url.user == nil, url.password == nil else { throw ServiceError.invalidRequest }
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
            try await AppManager.shared.add(source, presentingViewController: Self.presenter)
        case "removeSource":
            let query = NSFetchRequest<Source>(entityName: "Source")
            query.predicate = NSPredicate(format: "identifier == %@", target)
            guard let source = try context.fetch(query).first else { throw ServiceError.notFound }
            try await AppManager.shared.remove(source, presentingViewController: Self.presenter)
        case "signIn":
            try await callback { done in
                AppManager.shared.signIn(presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
            }
        case "signOut":
            // Preserve reusable certificate and anisette state, matching upgrade preservation.
            AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)
        case "syncAppIDs":
            if !AuthManager.shared.isAuthenticated {
                _ = try await AuthManager.shared.signIn(presentingViewController: Self.presenter)
            }
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
        case "install", "installURL", "installSharedIPA":
            let installTarget: InstallTarget
            var scopedURL: URL?
            defer { scopedURL?.stopAccessingSecurityScopedResource() }
            if operation == "install" {
                let app: StoreApp = try object(target)
                guard app.latestSupportedVersion != nil else { throw ServiceError.unsupported }
                installTarget = .app(app)
            } else if operation == "installSharedIPA" {
                guard UUID(uuidString: target) != nil, let group = Bundle.main.altstoreAppGroup,
                      let defaults = UserDefaults(suiteName: group),
                      let bookmark = defaults.data(forKey: "V3SharedIPA." + target) else { throw ServiceError.invalidRequest }
                defaults.removeObject(forKey: "V3SharedIPA." + target)
                var stale = false
                let url = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
                guard !stale, url.isFileURL, url.pathExtension.lowercased() == "ipa" else { throw ServiceError.invalidRequest }
                if url.startAccessingSecurityScopedResource() { scopedURL = url }
                installTarget = .url(url)
            } else {
                guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                      url.host != nil, url.user == nil, url.password == nil else { throw ServiceError.invalidRequest }
                installTarget = .url(url)
            }
            try await callback { done in
                let group = AppManager.shared.install(installTarget, presentingViewController: Self.presenter) { result in done(result.map { _ in () }) }
                cancellations[id] = { group.cancel(); group.progress.cancel() }
            }
        case "refreshApp":
            let app: InstalledApp = try object(target)
            guard app.isActive, app.bundleIdentifier != StoreApp.altstoreAppID else { throw ServiceError.unsupported }
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let group = RefreshGroup(context: StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background))
            try await callback { done in
                group.completionHandler = { results in
                    guard let result = results[app.bundleIdentifier] else { done(.failure(ServiceError.notFound)); return }
                    done(result.map { _ in () })
                }
                cancellations[id] = { group.cancel(); group.progress.cancel() }
                AppManager.shared.refresh([app], presentingViewController: Self.presenter, group: group)
            }
        case "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "jit":
            let app: InstalledApp = try object(target)
            if ["deactivate", "remove", "delete"].contains(operation), app.bundleIdentifier == StoreApp.altstoreAppID { throw ServiceError.unsupported }
            try await callback { done in
                let finished: (Result<InstalledApp, Error>) -> Void = { result in done(result.map { _ in () }) }
                switch operation {
                case "update":
                    let progress = AppManager.shared.update(app, presentingViewController: Self.presenter, completionHandler: finished)
                    cancellations[id] = { progress.cancel() }
                case "activate": AppManager.shared.activate(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "deactivate": AppManager.shared.deactivate(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "remove": AppManager.shared.removeApp(app, presentingViewController: Self.presenter, completionHandler: done)
                case "delete": AppManager.shared.deleteApp(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "backup": AppManager.shared.backup(app, presentingViewController: Self.presenter, completionHandler: finished)
                case "restore": AppManager.shared.restore(app, presentingViewController: Self.presenter, completionHandler: finished)
                default: AppManager.shared.enableJIT(for: app, completionHandler: done)
                }
            }
        default: throw ServiceError.invalidRequest
        }
        return try snapshot()
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
