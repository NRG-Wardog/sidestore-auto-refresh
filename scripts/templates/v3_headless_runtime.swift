import Foundation
import CoreData
import UIKit
import SideSign

// V3_HEADLESS_RUNTIME_V1: SideStore executes as a headless backend. No window,
// presenter, view controller, picker, alert, or remotely rendered view exists
// on any normal path below. Every human decision crosses the bridge as data.

// Parked continuations resume from cancellation callbacks that run off-actor,
// so this center stays non-isolated and guards its boxes with a lock.
final class V3PromptCenter {
    private let lock = NSLock()
    private var boxes: [String: CheckedContinuation<[String: String], Error>] = [:]

    func park(promptID: String) async throws -> [String: String] {
        try Task.checkCancellation()
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<[String: String], Error>) in
                self.lock.withLock { self.boxes[promptID] = continuation }
            }
        }, onCancel: {
            self.lock.withLock { self.boxes.removeValue(forKey: promptID) }?.resume(throwing: CancellationError())
        })
    }

    func answer(promptID: String, answer: [String: String]) -> Bool {
        guard let continuation = lock.withLock({ boxes.removeValue(forKey: promptID) }) else { return false }
        continuation.resume(returning: answer)
        return true
    }
}

@MainActor
final class V3HeadlessRuntime {
    static let shared = V3HeadlessRuntime()
    let prompts = V3PromptCenter()
    let auth = V3AuthCenter()
    let operations = V3OperationCenter()

    func cancelSession(_ id: String) -> Bool {
        if auth.cancel(id: id) { return true }
        return operations.cancel(id: id)
    }
}

// MARK: - Prompt construction (plist-safe dictionaries only)

func v3Prompt(id: String = UUID().uuidString, kind: String, title: String, message: String,
              fields: [[String: String]] = [], options: [[String: String]] = [],
              destructive: Bool = false) -> [String: Any] {
    var prompt: [String: Any] = ["id": id, "kind": kind, "title": title, "message": message,
                                 "fields": fields, "options": options]
    if destructive { prompt["destructive"] = true }
    return prompt
}

// MARK: - Authentication state machine

@MainActor
final class V3AuthCenter {
    struct Session {
        var task: Task<Void, Never>?
        var watchdog: Task<Void, Never>?
        var prompt: [String: Any]?
        var attempts = 0
        var terminal: [String: Any]?
        var deadline = Date.distantFuture
    }

    var sessions: [String: Session] = [:]
    private var activeID: String?

    func begin(deadline: Date) -> [String: Any] {
        if let current = activeID { cancel(id: current) }
        let id = UUID().uuidString
        sessions[id] = Session(deadline: deadline)
        activeID = id
        sessions[id]?.task = Task { @MainActor in await V3HeadlessRuntime.shared.auth.run(id: id) }
        sessions[id]?.watchdog = Task { @MainActor in
            let interval = deadline.timeIntervalSinceNow
            if interval > 0 { try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000)) }
            V3HeadlessRuntime.shared.auth.expire(id: id)
        }
        return ["session": id, "state": "working"]
    }

    func run(id: String) async {
        defer {
            sessions[id]?.task = nil
            sessions[id]?.watchdog?.cancel()
            sessions[id]?.watchdog = nil
            if activeID == id { activeID = nil }
        }
        do {
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let context = StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background)
            let handler = V3HeadlessAuthHandler(sessionID: id)
            let operation = try SignInOperation(context: context, signInHandler: handler, anisetteServerHandler: handler)
            let result = try await operation.execute()
            sessions[id]?.prompt = nil
            sessions[id]?.terminal = ["state": "completed", "team": result.team.name, "teamID": result.team.identifier]
        } catch {
            sessions[id]?.prompt = nil
            if error is CancellationError {
                sessions[id]?.terminal = ["state": "cancelled"]
            } else {
                let failure = CombinedFailure.capture(error, operation: "signIn", stage: .authentication, id: id)
                sessions[id]?.terminal = ["state": "failed", "stage": failure.stage.rawValue, "code": failure.code.rawValue]
            }
        }
    }

    func poll(id: String) -> [String: Any]? {
        guard let session = sessions[id] else { return nil }
        if let terminal = session.terminal { return terminal.merging(["session": id]) { current, _ in current } }
        if let prompt = session.prompt {
            return ["session": id, "state": "awaitingPrompt", "attempts": session.attempts, "prompt": prompt]
        }
        return ["session": id, "state": "working", "attempts": session.attempts]
    }

    func respond(id: String, promptID: String, answer: [String: String]) -> [String: Any]? {
        guard sessions[id] != nil else { return nil }
        sessions[id]?.attempts += 1
        guard V3HeadlessRuntime.shared.prompts.answer(promptID: promptID, answer: answer) else {
            return ["session": id, "state": "promptExpired"]
        }
        return poll(id: id)
    }

    func expire(id: String) {
        guard sessions[id]?.terminal == nil else { return }
        cancel(id: id)
    }

    @discardableResult
    func cancel(id: String) -> Bool {
        guard var session = sessions[id] else { return false }
        session.task?.cancel()
        session.watchdog?.cancel()
        if session.terminal == nil { session.terminal = ["state": "cancelled"] }
        session.prompt = nil
        sessions[id] = session
        if activeID == id { activeID = nil }
        return true
    }
}

@MainActor
final class V3HeadlessAuthHandler: SignInHandler, AnisetteServerHandler {
    let sessionID: String
    init(sessionID: String) { self.sessionID = sessionID }

    private func center() throws -> V3AuthCenter {
        let center = V3HeadlessRuntime.shared.auth
        guard center.sessions[sessionID] != nil else { throw CancellationError() }
        return center
    }

    private func ask(kind: String, title: String, message: String,
                     fields: [[String: String]] = [], options: [[String: String]] = [],
                     destructive: Bool = false) async throws -> [String: String] {
        let center = try center()
        let prompt = v3Prompt(kind: kind, title: title, message: message,
                              fields: fields, options: options, destructive: destructive)
        guard let promptID = prompt["id"] as? String else { throw CancellationError() }
        center.sessions[sessionID]?.prompt = prompt
        defer { center.sessions[sessionID]?.prompt = nil }
        return try await center.promptsParked(promptID: promptID)
    }

    func credentials() async throws -> (String, String) {
        let answer = try await ask(kind: "credentials", title: "Apple ID Sign In",
                                   message: "Enter the Apple ID and password used for signing.",
                                   fields: [["key": "appleID", "label": "Apple ID", "secure": "false"],
                                            ["key": "password", "label": "Password", "secure": "true"]])
        guard let appleID = answer["appleID"], !appleID.isEmpty,
              let password = answer["password"], !password.isEmpty else { throw CancellationError() }
        return (appleID, password)
    }

    func verificationCode(for request: TwoFactorRequest) async throws -> TwoFactorResponse {
        var phones: [[String: String]] = []
        var activeID = ""
        var mode = ""
        var failure = ""
        switch request {
        case .selectDeliveryMethod(let preferredMode, let phoneNumbers):
            mode = preferredMode.rawValue
            phones = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
        case .trustedDevice(let error):
            mode = TwoFactorDeliveryMode.trustedDevice.rawValue
            failure = error ?? ""
        case .sms(let phoneNumbers, let selectedID, let error):
            mode = TwoFactorDeliveryMode.sms.rawValue
            phones = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
            activeID = selectedID
            failure = error ?? ""
        case .voice(let phoneNumbers, let selectedID, let error):
            mode = TwoFactorDeliveryMode.voice.rawValue
            phones = phoneNumbers.map { ["id": $0.id, "number": $0.number] }
            activeID = selectedID
            failure = error ?? ""
        }
        if let requestError = request.error, !requestError.isEmpty { failure = requestError }
        var actionOptions: [[String: String]] = [["id": "code", "label": "Submit Code"],
                                                      ["id": "trustedDevice", "label": "Use Trusted Device"],
                                                      ["id": "sms", "label": "Send SMS"],
                                                      ["id": "voice", "label": "Voice Call"],
                                                      ["id": "cancel", "label": "Cancel"]]
        for phone in phones {
            actionOptions.append(["id": "phone:\(phone["id"] ?? "")", "label": phone["number"] ?? ""])
        }
        let answer = try await ask(kind: "twoFactor", title: "Two-Factor Authentication",
                                   message: failure.isEmpty ? "Approve the sign-in or enter the verification code." : failure,
                                   fields: [["key": "mode", "label": "mode", "secure": "false", "value": mode],
                                            ["key": "activeID", "label": "activeID", "secure": "false", "value": activeID],
                                            ["key": "code", "label": "Verification code", "secure": "false"],
                                            ["key": "phoneID", "label": "phoneID", "secure": "false", "value": activeID]],
                                   options: actionOptions)
        switch answer["action"] {
        case "code":
            guard let code = answer["code"], !code.isEmpty else { throw CancellationError() }
            return .verificationCode(code)
        case "trustedDevice": return .requestTrustedDevice
        case "sms": return .requestSMS(phoneID: answer["phoneID"] ?? activeID)
        case "voice": return .requestVoice(phoneID: answer["phoneID"] ?? activeID)
        default: throw CancellationError()
        }
    }

    func accountRepair(url: URL, message: String) async -> AccountRepairDecision {
        do {
            let answer = try await ask(kind: "accountRepair", title: "Account Attention Needed", message: message,
                                       fields: [["key": "url", "label": "Details", "secure": "false", "value": url.absoluteString]],
                                       options: [["id": "proceed", "label": "Continue"], ["id": "cancel", "label": "Cancel"]])
            return answer["choice"] == "proceed" ? .proceed : .cancel
        } catch { return .cancel }
    }

    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {}

    func resolveTeam(_ teams: [ALTTeam]) async throws -> ALTTeam {
        let answer = try await ask(kind: "team", title: "Select Team", message: "Choose the development team used for signing.",
                                   options: teams.map { ["id": $0.identifier, "label": "\($0.name) (\($0.identifier))"] })
        guard let identifier = answer["choice"], let team = teams.first(where: { $0.identifier == identifier }) else {
            throw CancellationError()
        }
        return team
    }

    func resolveProvisioningError(_ error: Error) async -> ProvisioningErrorDecision {
        let native = error as NSError
        do {
            let answer = try await ask(kind: "provisioningError", title: "Provisioning Needs Attention",
                                       message: "Provisioning reported an issue (\(native.domain) \(native.code)). Retry or cancel.",
                                       options: [["id": "retry", "label": "Retry"], ["id": "cancel", "label": "Cancel"]])
            return answer["choice"] == "retry" ? .retry : .cancel
        } catch { return .cancel }
    }

    func resolvePostAuth() async {
        _ = try? await ask(kind: "postAuth", title: "Almost Done",
                           message: "Authentication succeeded. Continue to finish provisioning this device.",
                           options: [["id": "continue", "label": "Continue"]])
    }

    func resolveRevocation(certificates: [ALTX509Certificate], teamType: ALTTeamType) async throws -> RevokeDecision {
        let answer = try await ask(kind: "revocation", title: "Certificates Need Attention",
                                   message: "The portal holds certificates that block provisioning for a \("\(teamType)") team. Keep the existing certificates or revoke the selected ones.",
                                   fields: [["key": "serials", "label": "serials", "secure": "false", "value": ""]],
                                   options: [["id": "keep", "label": "Keep Existing"]] +
                                       certificates.map { ["id": "revoke:\($0.serialNumber)", "label": "\($0.name) (\($0.serialNumber))"] })
        if answer["choice"] == "keep" { return .keepExisting }
        let serials = Set((answer["serials"] ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) })
        let selected = certificates.filter { serials.contains($0.serialNumber) }
        guard !selected.isEmpty else { throw CancellationError() }
        return .revokeSelected(selected)
    }

    func resolveResign(mismatchReason: CodeSignValidationReason, context: StandaloneOperationContext) async throws -> Bool {
        let answer = try await ask(kind: "resign", title: "Re-sign Required",
                                   message: "The installed app must be re-signed (\("\(mismatchReason)"). Proceed?",
                                   options: [["id": "proceed", "label": "Re-sign"], ["id": "cancel", "label": "Cancel"]])
        return answer["choice"] == "proceed"
    }

    func complete() async {}

    func warnOutdatedAnisetteServer() async throws -> Bool {
        let answer = try await ask(kind: "anisetteOutdated", title: "Outdated Anisette Server",
                                   message: "The configured anisette server is outdated, which increases the risk of locking the account. Continue anyway?",
                                   options: [["id": "continue", "label": "Continue"], ["id": "cancel", "label": "Cancel"]],
                                   destructive: true)
        return answer["choice"] == "continue"
    }
}

extension V3AuthCenter {
    func promptsParked(promptID: String) async throws -> [String: String] {
        try await V3HeadlessRuntime.shared.prompts.park(promptID: promptID)
    }
}

// MARK: - Headless pipeline decisions (every confirmation renders in the host)

@MainActor
final class V3HeadlessPipelineHandler: PipelineExecutionHandler, PreflightChecksHandler,
    EntitlementsReviewHandler, ExtensionRemovalHandler, UnsupportedVersionHandler,
    InstallAppHandler, UserCustomizationHandler {
    let sessionID: String
    init(sessionID: String) { self.sessionID = sessionID }

    var preflightChecksHandler: PreflightChecksHandler { self }
    var entitlementsReviewHandler: EntitlementsReviewHandler { self }
    var extensionRemovalHandler: ExtensionRemovalHandler { self }
    var unsupportedVersionHandler: UnsupportedVersionHandler { self }
    var installAppHandler: InstallAppHandler { self }
    var userCustomizationHandler: UserCustomizationHandler { self }
    var isResignActive: Bool { false }

    private func center() throws -> V3OperationCenter {
        let center = V3HeadlessRuntime.shared.operations
        guard center.sessions[sessionID] != nil else { throw CancellationError() }
        return center
    }

    private func ask(kind: String, title: String, message: String,
                     fields: [[String: String]] = [], options: [[String: String]] = [],
                     destructive: Bool = false) async throws -> [String: String] {
        let center = try center()
        let prompt = v3Prompt(kind: kind, title: title, message: message,
                              fields: fields, options: options, destructive: destructive)
        guard let promptID = prompt["id"] as? String else { throw CancellationError() }
        center.sessions[sessionID]?.prompt = prompt
        defer { center.sessions[sessionID]?.prompt = nil }
        return try await V3HeadlessRuntime.shared.prompts.park(promptID: promptID)
    }

    func resolveBundleIDMismatch(targetID: String, activeEffectiveID: String) async -> Bool {
        let answer = try? await ask(kind: "bundleIDMismatch", title: "Bundle ID Mismatch",
                                    message: "The app reports \(targetID) but the active signing identity expects \(activeEffectiveID). Proceed anyway?",
                                    options: [["id": "proceed", "label": "Proceed"], ["id": "cancel", "label": "Cancel"]])
        return answer?["choice"] == "proceed"
    }

    func reviewPermissions(_ permissions: [ALTEntitlement], for app: AppProtocol, mode: PermissionReviewMode) async throws {
        let list = permissions.map(\.rawValue).sorted().joined(separator: "\n")
        let answer = try await ask(kind: "permissions", title: "Review Permissions",
                                   message: "\(app.name) requests \(permissions.count) permission(s):\n\(list)",
                                   options: [["id": "approve", "label": "Approve"], ["id": "deny", "label": "Deny"]])
        guard answer["choice"] == "approve" else { throw CancellationError() }
    }

    func selectAppExtensionsToRemove(appBundle: ALTApplication, localAppExtensions: [ALTApplication],
                                     excessExtensions: Set<ALTApplication>) async throws -> ExtensionRemovalDecision {
        let sorted = excessExtensions.sorted { $0.bundleIdentifier < $1.bundleIdentifier }
        let answer = try await ask(kind: "extensions", title: "App Extensions",
                                   message: "\(appBundle.bundleIdentifier) contains \(sorted.count) extension(s) that do not fit the active profile. Choose which to remove.",
                                   options: [["id": "keepAll", "label": "Keep All"]] +
                                       sorted.map { ["id": "remove:\($0.bundleIdentifier)", "label": "Remove \($0.bundleIdentifier)"] } +
                                       [["id": "removeAll", "label": "Remove All"]])
        switch answer["choice"] {
        case "keepAll": return .keepAll(useMainProfile: false)
        case "removeAll": return .removeAll
        default:
            let wanted = Set((answer["ids"] ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) })
            let selected = Set(sorted.filter { wanted.contains($0.bundleIdentifier) })
            guard !selected.isEmpty else { throw CancellationError() }
            return .removeSelected(selected)
        }
    }

    func resolveUnsupportediOSVersion(errorDescription: String, appName: String, compatibleVersion: String) async throws -> Bool {
        let answer = try await ask(kind: "unsupportedVersion", title: "Unsupported iOS Version",
                                   message: "\(appName): \(errorDescription) Compatible version: \(compatibleVersion). Proceed anyway?",
                                   options: [["id": "proceed", "label": "Proceed"], ["id": "cancel", "label": "Cancel"]])
        return answer["choice"] == "proceed"
    }

    func requestBackgroundSuspension() async {}
    func suspendToHomeScreen() async {}
    func isAppInForeground() async -> Bool { false }

    func resolveBundleIDOverride(initialBundleID: String) async throws -> (customID: String, appendTeamID: Bool)? {
        let answer = try await ask(kind: "bundleIDOverride", title: "Customize Bundle ID",
                                   message: "Optionally customize the bundle identifier used for signing.",
                                   fields: [["key": "customID", "label": "Bundle ID", "secure": "false", "value": initialBundleID],
                                            ["key": "appendTeamID", "label": "appendTeamID", "secure": "false", "value": "true"]],
                                   options: [["id": "custom", "label": "Use Custom ID"],
                                             ["id": "default", "label": "Use Default"],
                                             ["id": "cancel", "label": "Cancel"]])
        switch answer["choice"] {
        case "custom":
            guard let customID = answer["customID"], !customID.isEmpty else { throw CancellationError() }
            return (customID, answer["appendTeamID"] != "false")
        case "default": return nil
        default: throw CancellationError()
        }
    }

    func resolveAppGroupMismatch(originalGroup: String, correctedGroup: String) async throws -> AppGroupResolution {
        let answer = try await ask(kind: "appGroupMismatch", title: "App Group Mismatch",
                                   message: "The app group \(originalGroup) does not match the expected \(correctedGroup).",
                                   options: [["id": "correct", "label": "Use \(correctedGroup)"],
                                             ["id": "keep", "label": "Keep \(originalGroup)"]])
        switch answer["choice"] {
        case "correct": return .correctAndProceed(correctedGroup)
        case "keep": return .keepOriginal(originalGroup)
        default: throw CancellationError()
        }
    }
}

// MARK: - Headless operation sessions (install/update/refresh/activate/...)

@MainActor
final class V3OperationCenter {
    struct Session {
        var task: Task<Void, Never>?
        var watchdog: Task<Void, Never>?
        var prompt: [String: Any]?
        var group: RefreshGroup?
        var terminal: [String: Any]?
        var deadline = Date.distantFuture
    }

    var sessions: [String: Session] = [:]

    func start(kind: String, target: String, value: Bool?, deadline: Date) async -> [String: Any] {
        _ = value
        let id = UUID().uuidString
        sessions[id] = Session(deadline: deadline)
        guard AuthManager.shared.isAuthenticated else {
            sessions[id]?.terminal = ["state": "waitingForAuthentication"]
            return ["session": id, "state": "waitingForAuthentication"]
        }
        do {
            let driver = try await makeDriver(id: id, kind: kind, target: target)
            sessions[id]?.task = Task { @MainActor in await self.drive(id: id, driver: driver) }
            sessions[id]?.watchdog = Task { @MainActor in
                let interval = deadline.timeIntervalSinceNow
                if interval > 0 { try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000)) }
                self.expire(id: id)
            }
        } catch {
            sessions[id]?.terminal = terminalFailure(id: id, kind: kind, error: error)
            return terminalReply(id: id)
        }
        return ["session": id, "state": "working"]
    }

    private func drive(id: String, driver: V3OpDriver) async {
        defer {
            sessions[id]?.task = nil
            sessions[id]?.watchdog?.cancel()
            sessions[id]?.watchdog = nil
        }
        do {
            try await driver.run()
            sessions[id]?.prompt = nil
            sessions[id]?.terminal = ["state": "completed"]
        } catch {
            sessions[id]?.prompt = nil
            if error is CancellationError {
                sessions[id]?.terminal = ["state": "cancelled"]
            } else {
                sessions[id]?.terminal = terminalFailure(id: id, kind: driver.kind, error: error)
            }
        }
    }

    func poll(id: String) -> [String: Any]? {
        guard let session = sessions[id] else { return nil }
        if let terminal = session.terminal { return terminal.merging(["session": id]) { current, _ in current } }
        var reply: [String: Any] = ["session": id, "state": "working"]
        if let progress = session.group?.progress.fractionCompleted, progress.isFinite {
            reply["progress"] = progress
        }
        if let prompt = session.prompt {
            reply["state"] = "awaitingPrompt"
            reply["prompt"] = prompt
        }
        return reply
    }

    func answer(id: String, promptID: String, answer: [String: String]) -> [String: Any]? {
        guard sessions[id] != nil else { return nil }
        guard V3HeadlessRuntime.shared.prompts.answer(promptID: promptID, answer: answer) else {
            return ["session": id, "state": "promptExpired"]
        }
        return poll(id: id)
    }

    func expire(id: String) {
        guard sessions[id]?.terminal == nil else { return }
        _ = cancel(id: id)
    }

    @discardableResult
    func cancel(id: String) -> Bool {
        guard var session = sessions[id] else { return false }
        session.task?.cancel()
        session.watchdog?.cancel()
        session.group?.cancel()
        if session.terminal == nil { session.terminal = ["state": "cancelled"] }
        session.prompt = nil
        sessions[id] = session
        return true
    }

    private func terminalReply(id: String) -> [String: Any] {
        poll(id: id) ?? ["session": id, "state": "failed"]
    }

    private func terminalFailure(id: String, kind: String, error: Error) -> [String: Any] {
        if let required = error as? V3RequiresSourceError {
            return ["state": "requiresSource", "sourceID": required.sourceID, "sourceName": required.sourceName]
        }
        let stage: CombinedFailure.Stage
        switch kind {
        case "install", "installURL", "installSharedIPA", "update": stage = .installation
        case "refreshApp": stage = .refreshVerification
        default: stage = .command
        }
        let failure = CombinedFailure.capture(error, operation: kind, stage: stage, id: id)
        return ["state": "failed", "stage": failure.stage.rawValue, "code": failure.code.rawValue]
    }

    private struct V3OpDriver: @unchecked Sendable {
        let kind: String
        let run: @MainActor () async throws -> Void
    }

    private func makeDriver(id: String, kind: String, target: String) async throws -> V3OpDriver {
        let handler = V3HeadlessPipelineHandler(sessionID: id)
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let baseContext = StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background)
        switch kind {
        case "install", "installURL", "installSharedIPA":
            let target = try await resolveInstallTarget(kind: kind, target: target)
            let app: AppProtocol
            switch target {
            case .app(let protocolApp):
                app = protocolApp
                if let storeApp = protocolApp.storeApp, let source = storeApp.source {
                    guard try await source.isAdded() else {
                        throw V3RequiresSourceError(sourceID: source.identifier, sourceName: source.name)
                    }
                }
            case .url(_):
                throw V3SideStoreServiceError.invalidRequest
            }
            return V3OpDriver(kind: kind) {
                try await self.single(id: id, operation: .install(app), handler: handler, context: baseContext)
            }
        case "update":
            let app: InstalledApp = try v3Resolve(target)
            guard app.bundleIdentifier != StoreApp.altstoreAppID else { throw V3SideStoreServiceError.unsupported }
            guard let appVersion = app.storeApp?.latestSupportedVersion else { throw V3SideStoreServiceError.unsupported }
            guard appVersion as AnyObject !== app else {
                throw OperationError.invalidParameters("Make sure we never accidentally 'update' to already installed app.")
            }
            return V3OpDriver(kind: kind) {
                try await self.single(id: id, operation: .update(appVersion, customBundleIdentifier: app.customBundleIdentifier),
                                      handler: handler, context: baseContext)
            }
        case "refreshApp":
            let app: InstalledApp = try v3Resolve(target)
            guard app.isActive, app.bundleIdentifier != StoreApp.altstoreAppID else { throw V3SideStoreServiceError.unsupported }
            return V3OpDriver(kind: kind) {
                let group = RefreshGroup(context: baseContext)
                self.sessions[id]?.group = group
                group.completionHandler = { [weak self] results in
                    Task { @MainActor in
                        guard let self, self.sessions[id] != nil else { return }
                        guard let result = results[app.bundleIdentifier] else {
                            self.sessions[id]?.terminal = ["state": "failed", "stage": "refreshVerification", "code": "unavailable"]
                            return
                        }
                        if case .failure(let error) = result {
                            self.sessions[id]?.terminal = self.terminalFailure(id: id, kind: kind, error: error)
                        }
                    }
                }
                V3SideStoreService.shared.cancellations[id] = { group.cancel(); group.progress.cancel() }
                do {
                    try await AppManager.shared.pipelineRunner.perform([.refresh(app)], handler: handler, group: group)
                } catch {
                    group.context.error = error
                    group.set(.failure(error), forAppWithBundleIdentifier: app.bundleIdentifier)
                    group.completionHandler?([app.bundleIdentifier: .failure(error)])
                    throw error
                }
            }
        case "activate", "deactivate", "delete", "backup", "restore":
            let app: InstalledApp = try v3Resolve(target)
            if ["deactivate", "delete"].contains(kind), app.bundleIdentifier == StoreApp.altstoreAppID {
                throw V3SideStoreServiceError.unsupported
            }
            let operation: AppOperation
            switch kind {
            case "activate": operation = .activate(app)
            case "deactivate": operation = .deactivate(app)
            case "delete": operation = .deleteApp(app)
            case "backup": operation = .backup(app)
            default: operation = .restore(app)
            }
            return V3OpDriver(kind: kind) {
                try await self.single(id: id, operation: operation, handler: handler, context: baseContext)
            }
        case "remove":
            let app: InstalledApp = try v3Resolve(target)
            guard app.bundleIdentifier != StoreApp.altstoreAppID else { throw V3SideStoreServiceError.unsupported }
            return V3OpDriver(kind: kind) {
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                    let gate = V3ServiceCallbackGate(continuation)
                    AppManager.shared.pipelineRunner.performVoidOperation(.removeApp(app), handler: handler, context: baseContext) { result in
                        gate.settle(result)
                    }
                }
            }
        default:
            throw V3SideStoreServiceError.invalidRequest
        }
    }

    private func single(id: String, operation: AppOperation, handler: V3HeadlessPipelineHandler,
                        context: StandaloneOperationContext) async throws {
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let gate = V3ServiceCallbackGate(continuation)
                let group = AppManager.shared.pipelineRunner.performSingleOperation(operation, handler: handler, context: context) { result in
                    gate.settle(result.map { _ in () })
                }
                Task { @MainActor in
                    self.sessions[id]?.group = group
                    V3SideStoreService.shared.cancellations[id] = { group.cancel(); group.progress.cancel() }
                }
            }
        }, onCancel: {
            Task { @MainActor in
                self.sessions[id]?.group?.cancel()
                if let cancel = V3SideStoreService.shared.cancellations[id] { cancel() }
            }
        })
        try Task.checkCancellation()
    }

    private func resolveInstallTarget(kind: String, target: String) async throws -> InstallTarget {
        if kind == "install" {
            let app: StoreApp = try v3Resolve(target)
            guard app.latestSupportedVersion != nil else { throw V3SideStoreServiceError.unsupported }
            return .app(app)
        }
        if kind == "installSharedIPA" {
            guard UUID(uuidString: target) != nil, let group = Bundle.main.altstoreAppGroup,
                  let defaults = UserDefaults(suiteName: group),
                  let bookmark = defaults.data(forKey: "V3SharedIPA." + target) else {
                throw V3SideStoreServiceError.invalidRequest
            }
            defaults.removeObject(forKey: "V3SharedIPA." + target)
            var stale = false
            let url = try URL(resolvingBookmarkData: bookmark, options: .withoutUI, relativeTo: nil, bookmarkDataIsStale: &stale)
            guard !stale, url.isFileURL, url.pathExtension.lowercased() == "ipa" else {
                throw V3SideStoreServiceError.invalidRequest
            }
            return try await ipaTarget(url: url, scoped: true)
        }
        guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else {
            throw V3SideStoreServiceError.invalidRequest
        }
        return try await ipaTarget(url: url, scoped: false)
    }

    private func ipaTarget(url: URL, scoped: Bool) async throws -> InstallTarget {
        var localURL = url
        var scopedURL: URL?
        defer { scopedURL?.stopAccessingSecurityScopedResource() }
        if !url.isFileURL {
            guard let packageType = PackageType(url: url), packageType == .ipa else {
                throw OperationError.invalidApp(reason: "Unsupported package format '.\(url.pathExtension)'. Expected '.ipa'.")
            }
            let temporaryDirectory = FileManager.default.uniqueTemporaryURL()
            try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
            localURL = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
                let downloadTask = URLSession.shared.downloadTask(with: url) { (fileURL, response, error) in
                    do {
                        let (fileURL, _) = try Result((fileURL, response), error).get()
                        let dest = temporaryDirectory.appendingPathComponent(url.lastPathComponent)
                        try FileManager.default.moveItem(at: fileURL, to: dest)
                        continuation.resume(returning: dest)
                    } catch {
                        continuation.resume(throwing: error)
                    }
                }
                downloadTask.resume()
            }
        }
        if scoped, localURL.startAccessingSecurityScopedResource() { scopedURL = localURL }
        let packageType = PackageType(url: localURL) ?? .ipa
        let (bundleIdentifier, appName) = try Self.readAppMetadata(from: localURL, packageType: packageType)
        return .app(AnyApp(name: appName, bundleIdentifier: bundleIdentifier, url: localURL, storeApp: nil))
    }

    static func readAppMetadata(from url: URL, packageType: PackageType) throws -> (bundleIdentifier: String, name: String) {
        switch packageType {
        case .ipa:
            let reader = try Archive.Reader.open(at: url)
            try reader.goToFirstFile()
            var plistData: Data?
            repeat {
                let filename = try reader.currentFilename()
                let components = filename.components(separatedBy: "/")
                if components.count == 3 && components[0] == "Payload" && components[1].hasSuffix(".app") && components[2] == "Info.plist" {
                    plistData = try reader.readCurrentFile()
                    break
                }
            } while reader.goToNextFile()
            guard let data = plistData,
                  let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
                  let bundleIdentifier = (plist["CFBundleIdentifier"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !bundleIdentifier.isEmpty else {
                throw OperationError.invalidApp(reason: "Archive missing valid Payload/*.app/Info.plist")
            }
            let appName = (plist["CFBundleDisplayName"] as? String) ?? (plist["CFBundleName"] as? String) ?? url.deletingPathExtension().lastPathComponent
            return (bundleIdentifier, appName)
        case .app:
            let plistURL = url.appendingPathComponent("Info.plist")
            let data = try Data(contentsOf: plistURL)
            guard let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
                  let bundleIdentifier = (plist["CFBundleIdentifier"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !bundleIdentifier.isEmpty else {
                throw OperationError.invalidApp(reason: "Invalid Info.plist in app directory")
            }
            let appName = (plist["CFBundleDisplayName"] as? String) ?? (plist["CFBundleName"] as? String) ?? url.lastPathComponent
            return (bundleIdentifier, appName)
        }
    }
}

struct V3RequiresSourceError: Error {
    let sourceID: String
    let sourceName: String
}

enum V3SideStoreServiceError: String, Error {
    case notReady, invalidRequest, notFound, unsupported, busy, authRequired
}

func v3Resolve<T: NSManagedObject>(_ identifier: String) throws -> T {
    guard let url = URL(string: identifier),
          let id = DatabaseManager.shared.persistentContainer.persistentStoreCoordinator.managedObjectID(forURIRepresentation: url),
          let object = try DatabaseManager.shared.viewContext.existingObject(with: id) as? T else {
        throw V3SideStoreServiceError.notFound
    }
    return object
}

// MARK: - Backend data commands (certificates, developer services, sources,
// pairing, settings, anisette, SideSign, logs, health, account backup)

@MainActor
enum V3BackendCommands {
    static func certificateRow(_ x509: ALTX509Certificate, activeSerial: String?) -> [String: Any] {
        var row: [String: Any] = ["serial": x509.serialNumber, "name": x509.name,
                                  "active": x509.serialNumber == activeSerial]
        row["machineName"] = x509.machineName ?? ""
        row["machineID"] = x509.machineIdentifier ?? ""
        row["requesterEmail"] = x509.requesterEmail ?? ""
        row["created"] = x509.creationDate
        row["expiry"] = x509.expiryDate
        return row
    }

    static func certificates() -> [[String: Any]] {
        let activeSerial = CertificateManager.shared.activeCertificate?.certificate.serialNumber
        return CertificateManager.shared.getAllLocalX509Certificates().map { certificateRow($0, activeSerial: activeSerial) }
    }

    static func portalAccount() async throws -> ALTAccount {
        guard let appleID = DatabaseManager.shared.activeAccount()?.appleID else {
            throw V3SideStoreServiceError.authRequired
        }
        return ALTAccount(appleID: appleID, identifier: appleID, firstName: "", lastName: "")
    }

    static func portalCertificates() async throws -> [[String: Any]] {
        _ = try await AuthManager.shared.getAuthenticatedSession()
        let team = try await AuthManager.shared.getAuthenticatedTeam()
        let certificates = try await DeveloperPortalProxy.shared.fetchCertificates(team: team)
        let activeSerial = CertificateManager.shared.activeCertificate?.certificate.serialNumber
        return certificates.map { certificateRow($0, activeSerial: activeSerial) }
    }

    static func teamRow(_ team: ALTTeam) -> [String: Any] {
        ["identifier": team.identifier, "name": team.name, "type": "\(team.type)"]
    }

    static func developerTeams() async throws -> [[String: Any]] {
        let account = try await portalAccount()
        return try await DeveloperPortalProxy.shared.fetchTeams(for: account).map(teamRow)
    }

    static func developerDevices() async throws -> [[String: Any]] {
        _ = try await AuthManager.shared.getAuthenticatedSession()
        return try await DeveloperPortalProxy.shared.fetchDevices().map {
            ["identifier": $0.identifier, "name": $0.name, "type": "\($0.type)"]
        }
    }

    static func developerAppIDs() async throws -> [[String: Any]] {
        _ = try await AuthManager.shared.getAuthenticatedSession()
        return try await DeveloperPortalProxy.shared.fetchAppIDs().map {
            ["identifier": $0.identifier, "name": $0.name, "bundleID": $0.bundleIdentifier]
        }
    }

    static func developerGroups() async throws -> [[String: Any]] {
        _ = try await AuthManager.shared.getAuthenticatedSession()
        return try await DeveloperPortalProxy.shared.fetchAppGroups().map {
            ["identifier": $0.identifier, "name": $0.name]
        }
    }

    static func profileRow(_ profile: ALTListedProvisioningProfile) -> [String: Any] {
        // The portal list shape is upstream-owned; reflect scalar members instead
        // of hard-coding them so portal changes cannot break compilation.
        var row: [String: Any] = [:]
        for child in Mirror(reflecting: profile).children {
            guard let label = child.label else { continue }
            switch child.value {
            case let value as String: row[label] = value
            case let value as Bool: row[label] = value
            case let value as Int: row[label] = value
            case let value as Date: row[label] = value
            case let value as UUID: row[label] = value.uuidString
            default: row[label] = String(describing: child.value)
            }
        }
        return row
    }

    static func developerProfiles() async throws -> [[String: Any]] {
        _ = try await AuthManager.shared.getAuthenticatedSession()
        return try await DeveloperPortalProxy.shared.listProvisioningProfiles().map(profileRow)
    }

    static func sourcePreview(urlString: String) async throws -> [String: Any] {
        guard let url = URL(string: urlString), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else {
            throw V3SideStoreServiceError.invalidRequest
        }
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
        let name = try await background.performAsync { source.name }
        let identifier = try await background.performAsync { source.identifier }
        let added = try await source.isAdded()
        let title = "Would you like to add the source \"\(name)\"?"
        return ["identifier": identifier, "name": name, "alreadyAdded": added,
                "title": title, "message": "Make sure to only add sources that you trust."]
    }

    static func sourceAddConfirmed(urlString: String) async throws {
        guard let url = URL(string: urlString) else { throw V3SideStoreServiceError.invalidRequest }
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
        guard try await !source.isAdded() else { return }
        let identifier = try await background.performAsync { source.identifier }
        let existing = try await background.performAsync {
            try background.fetch(NSFetchRequest<Source>(entityName: "Source")).contains { $0.identifier == identifier }
        }
        guard !existing else { return }
        try await background.performAsync { try background.save() }
        await MainActor.run {
            NotificationCenter.default.post(name: AppManager.didAddSourceNotification, object: source)
        }
    }

    static func sourceRemoveConfirmed(identifier: String) async throws {
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        try await background.performAsync {
            let query = NSFetchRequest<Source>(entityName: "Source")
            query.predicate = NSPredicate(format: "%K == %@", #keyPath(Source.identifier), identifier)
            guard let source = try background.fetch(query).first else { return }
            guard source.identifier != Source.altStoreIdentifier else { return }
            background.delete(source)
            try background.save()
        }
        await MainActor.run {
            NotificationCenter.default.post(name: AppManager.didRemoveSourceNotification, object: nil)
        }
    }

    static func pairingImportData(token: String) throws {
        guard UUID(uuidString: token) != nil, let group = Bundle.main.altstoreAppGroup,
              let defaults = UserDefaults(suiteName: group),
              let data = defaults.data(forKey: "V3SharedFile." + token) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        defaults.removeObject(forKey: "V3SharedFile." + token)
        guard let contents = String(data: data, encoding: .utf8), !contents.isEmpty,
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil),
              (plist as? [String: Any]) != nil || (plist as? [Any]) != nil else {
            throw OperationError.invalidPairingFile(reason: "not a readable pairing property list")
        }
        try PairingFileManager.shared.savePairingFile(contents: contents)
    }

    static let boolSettings: Set<String> = ["isCellularRefreshEnabled", "isSideJITServerEnabled",
        "alwaysShowWireGuardConfig", "acceptIPv6ConnectionConfig", "enableEMPforWireguard",
        "useOnDeviceAnisette", "customizeAppId", "customizeAppExtensions", "autoFixAppGroupIDs",
        "preferResignedIPA", "isExportResignedAppEnabled", "skipNonCopyableBackupFiles",
        "appVerificationDisabled", "isBundleIDVerificationEnabled", "isiOSVersionVerificationEnabled",
        "isAppVersionVerificationEnabled", "isChecksumVerificationEnabled", "isFileSizeVerificationEnabled",
        "permissionCheckingDisabled", "responseCachingDisabled", "isVerboseOperationsLoggingEnabled",
        "isSideStoreVerboseLoggingEnabled", "isAltSignVerboseLoggingEnabled", "isMinimuxerVerboseLoggingEnabled",
        "isRotateLogsOnStartupEnabled", "recreateDatabaseOnNextStart", "isAnisetteOfflineMode",
        "disableAnisetteRotation", "useLocalVPN", "isBetaUpdatesEnabled", "isIdleTimeoutDisableEnabled",
        "isBackgroundRefreshEnabled", "keepSigningCertsAfterLogout", "keepAnisetteDataAfterLogout",
        "keepAnisetteHeadersAfterLogout", "keepSideSignHeadersAfterLogout"]
    static let stringSettings: Set<String> = ["textInputSideJITServerurl", "menuAnisetteURL", "menuAnisetteList",
        "betaUdpatesTrack", "minimuxerGatewayBackend", "textInputAnisetteURL"]
    static let intSettings: Set<String> = ["remotePairingPortOverride", "deviceProbeTimeoutOverride"]

    static func settingsGet() -> [String: Any] {
        var bools: [String: Bool] = [:]
        for key in boolSettings { bools[key] = UserDefaults.standard.bool(forKey: key) }
        bools["widgetVerboseLogging"] = WidgetDataManager.shared.isVerboseLoggingEnabled
        var strings: [String: String] = [:]
        for key in stringSettings { strings[key] = UserDefaults.standard.string(forKey: key) ?? "" }
        var ints: [String: Int] = [:]
        for key in intSettings { ints[key] = UserDefaults.standard.integer(forKey: key) }
        return ["bools": bools, "strings": strings, "ints": ints]
    }

    static func settingsSet(payload: [String: Any]) throws {
        guard let key = payload["key"] as? String else { throw V3SideStoreServiceError.invalidRequest }
        if boolSettings.contains(key) {
            guard let value = payload["bool"] as? Bool else { throw V3SideStoreServiceError.invalidRequest }
            UserDefaults.standard.set(value, forKey: key)
        } else if key == "widgetVerboseLogging" {
            guard let value = payload["bool"] as? Bool else { throw V3SideStoreServiceError.invalidRequest }
            WidgetDataManager.shared.isVerboseLoggingEnabled = value
        } else if stringSettings.contains(key) {
            guard let value = payload["string"] as? String else { throw V3SideStoreServiceError.invalidRequest }
            if value.isEmpty { UserDefaults.standard.removeObject(forKey: key) }
            else { UserDefaults.standard.set(value, forKey: key) }
        } else if intSettings.contains(key) {
            guard let value = payload["int"] as? Int else { throw V3SideStoreServiceError.invalidRequest }
            UserDefaults.standard.set(value, forKey: key)
        } else {
            throw V3SideStoreServiceError.invalidRequest
        }
    }

    static func anisetteList() async -> [[String: Any]] {
        let items = await AnisetteServersManager.shared.loadLocalServers()
        let active = await AnisetteServersManager.shared.getActiveServerURLs()
        return items.map { ["id": $0.id, "name": $0.name, "address": $0.address,
                            "hidden": $0.isHidden, "active": active.contains($0.address)] }
    }

    static func sidesignJSON() async -> String {
        let config = await SideSignConfigManager.shared.loadConfig()
        guard let data = try? JSONEncoder().encode(config),
              let text = String(data: data, encoding: .utf8) else { return "{}" }
        return text
    }

    static func sidesignSet(json: String) async throws {
        guard let data = json.data(using: .utf8),
              let config = try? JSONDecoder().decode(SideSignHeaders.self, from: data) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        await SideSignConfigManager.shared.saveConfig(config)
    }

    static func sidesignImport(token: String) async throws {
        guard UUID(uuidString: token) != nil, let group = Bundle.main.altstoreAppGroup,
              let defaults = UserDefaults(suiteName: group),
              let data = defaults.data(forKey: "V3SharedFile." + token) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        defaults.removeObject(forKey: "V3SharedFile." + token)
        guard let config = try? JSONDecoder().decode(SideSignHeaders.self, from: data) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        await SideSignConfigManager.shared.saveConfig(config)
    }

    static func sidesignExport() async -> String {
        guard let data = await SideSignConfigManager.shared.exportConfigData(),
              let text = String(data: data, encoding: .utf8) else { return "{}" }
        return text
    }

    static func stagedFile(token: String) throws -> Data {
        guard UUID(uuidString: token) != nil, let group = Bundle.main.altstoreAppGroup,
              let defaults = UserDefaults(suiteName: group),
              let data = defaults.data(forKey: "V3SharedFile." + token) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        return data
    }

    static func consumeStagedFile(token: String) throws -> Data {
        let data = try stagedFile(token: token)
        if let group = Bundle.main.altstoreAppGroup {
            UserDefaults(suiteName: group)?.removeObject(forKey: "V3SharedFile." + token)
        }
        return data
    }

    static func logTail(limit: Int = 262_144) -> [String: Any] {
        guard let delegate = UIApplication.shared.delegate as? AppDelegate else { return ["tail": ""] }
        let url = delegate.consoleLog.logFileURL
        guard let handle = try? FileHandle(forReadingFrom: url) else { return ["tail": ""] }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        let start = size > UInt64(limit) ? size - UInt64(limit) : 0
        try? handle.seek(toOffset: start)
        let data = (try? handle.readToEnd()) ?? Data()
        return ["tail": String(decoding: data, as: UTF8.self)]
    }

    static func health() async -> [String: Any] {
        let account = DatabaseManager.shared.activeAccount()?.appleID ?? "Not signed in"
        let team = DatabaseManager.shared.activeTeam()
        var anisette: [String: Any] = ["servers": 0, "offline": UserDefaults.standard.bool(forKey: "isAnisetteOfflineMode")]
        let servers = await AnisetteServersManager.shared.loadLocalServers()
        anisette["servers"] = servers.count
        anisette["active"] = await AnisetteServersManager.shared.getActiveServerURLs()
        return ["account": account, "team": team?.name ?? "No active team",
                "certificate": CertificateManager.shared.activeCertificate == nil ? "No active certificate" : "Active certificate available",
                "pairing": PairingFileManager.shared.fetchPairingFile() == nil ? "Pairing file required" : "Pairing file available",
                "anisette": anisette,
                "sidesign": ["configured": SideSignConfigManager.shared.hasConfigFile()],
                "service": ["ready": DatabaseManager.shared.isStarted]]
    }

    static func accountExport(password: String, includeApplePassword: Bool) throws -> String {
        guard !password.isEmpty else { throw V3SideStoreServiceError.invalidRequest }
        let data = try ImportExport.exportAccount(password: password, includeApplePassword: includeApplePassword)
        return data.base64EncodedString()
    }

    static func accountImport(token: String, password: String) throws -> [String: Any] {
        let data = try consumeStagedFile(token: token)
        let account = try ImportExport.importAccount(data, filePassword: password)
        return ["email": account.email]
    }
}
