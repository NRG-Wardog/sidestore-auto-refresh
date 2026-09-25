import Foundation
import CoreData
import CryptoKit
import UIKit
import SideSign

// V3_HEADLESS_RUNTIME_V1: SideStore executes as a headless backend. No window,
// presenter, view controller, picker, alert, or remotely rendered view exists
// on any normal path below. Every human decision crosses the bridge as data.

// Parked continuations resume from cancellation callbacks that run off-actor,
// so this center stays non-isolated and guards its boxes with a lock.
final class V3PromptCenter: @unchecked Sendable {
    private let lock = NSLock()
    private final class Pending: @unchecked Sendable {
        var continuation: CheckedContinuation<[String: String], Error>?
        var result: Result<[String: String], Error>?
    }
    private var boxes: [String: Pending] = [:]
    var pendingCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return boxes.count
    }

    func park(promptID: String, onReady: (@MainActor () -> Void)? = nil) async throws -> [String: String] {
        try Task.checkCancellation()
        let pending = Pending()
        let installed = lock.withLock { () -> Bool in
            guard boxes[promptID] == nil else { return false }
            boxes[promptID] = pending
            return true
        }
        guard installed else { throw NSError(domain: "V3Prompt", code: 1) }
        defer {
            lock.withLock {
                if boxes[promptID] === pending { boxes.removeValue(forKey: promptID) }
            }
        }
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<[String: String], Error>) in
                let result = self.lock.withLock { () -> Result<[String: String], Error>? in
                    if case nil = pending.result { pending.continuation = continuation }
                    return pending.result
                }
                if let result { continuation.resume(with: result) }
                else if let onReady {
                    Task { @MainActor in
                        guard self.isWaiting(promptID: promptID, pending: pending) else { return }
                        onReady()
                    }
                }
            }
        }, onCancel: {
            self.settle(promptID: promptID, pending: pending, result: .failure(CancellationError()))
        })
    }

    func answer(promptID: String, answer: [String: String]) -> Bool {
        lock.lock()
        let pending = boxes[promptID]
        lock.unlock()
        guard let pending else { return false }
        return settle(promptID: promptID, pending: pending, result: .success(answer))
    }

    @discardableResult
    private func settle(promptID: String, pending: Pending, result: Result<[String: String], Error>) -> Bool {
        lock.lock()
        guard boxes[promptID] === pending, case nil = pending.result else {
            lock.unlock()
            return false
        }
        pending.result = result
        let continuation = pending.continuation
        pending.continuation = nil
        lock.unlock()
        continuation?.resume(with: result)
        return true
    }

    private func isWaiting(promptID: String, pending: Pending) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard boxes[promptID] === pending else { return false }
        if case nil = pending.result { return true }
        return false
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

// MARK: - Authentication failure classification (typed, no string guessing)

// Privacy-safe display kind for the previous authentication attempt failure.
// Only the kind string plus CombinedFailure scalar fields cross the bridge.
// Never credentials, tokens, 2FA codes, DSID, headers, or response bodies.
enum V3AuthFailureKind: String, Equatable {
    case invalidCredentials
    case appSpecificPasswordRequired
    case invalidCode
    case rateLimited
    case serviceUnavailable
    case anisette
    case network
    case accountRepairRequired
    case unknown
}

// Classifies the actual typed error from SignInOperation.authenticationLoop().
// Returns nil for cancellation-class results, which must clear any stored
// failure instead of being displayed. Evidence for each mapping is the pinned
// SideSign source (Sources/DeveloperPortal/Authentication.swift,
// Sources/Models/Errors.swift, Sources/Constants.swift):
// - incorrectCredentials: GrandSlam ec -22406
// - appSpecificPasswordRequired: GrandSlam ec -20101 / -20209
// - tooManyAttempts: GrandSlam ec -21668 / -20102 / -22411, or HTTP 429
// - incorrectVerificationCode: wrong 2FA code (returns to credentials prompt)
// - invalidAnisetteData: Anisette infrastructure failure
// - accountRepairRequired: Apple requires account attention
// - ServerError.badServerResponse / invalidResponseFormat / missingKey: the
//   Apple endpoint did not return a valid auth response (e.g. HTTP 5xx with an
//   empty body, which SideSign reports without a status code)
// - ServerError.underlyingError with a GrandSlam rate-limit code: rateLimited
// - URLError (any code): network reachability failure
// Anything else is honestly reported as unknown.
func v3ClassifyAuthError(_ error: Error) -> V3AuthFailureKind? {
    if error is CancellationError { return nil }
    if let portal = error as? DeveloperPortalError {
        switch portal {
        case .incorrectCredentials: return .invalidCredentials
        case .appSpecificPasswordRequired: return .appSpecificPasswordRequired
        case .tooManyAttempts: return .rateLimited
        case .incorrectVerificationCode: return .invalidCode
        case .invalidAnisetteData: return .anisette
        case .accountRepairRequired: return .accountRepairRequired
        case .userCancelled: return nil
        default: return .unknown
        }
    }
    if let server = error as? ServerError {
        switch server {
        case .badServerResponse, .invalidResponseFormat, .missingKey:
            return .serviceUnavailable
        case .underlyingError(let code, _):
            // GrandSlam rate-limit codes (Sources/Constants.swift).
            if code == -22411 || code == -20102 || code == -21668 {
                return .rateLimited
            }
            return .unknown
        }
    }
    if (error as NSError).domain == NSURLErrorDomain { return .network }
    return .unknown
}

// MARK: - Provisioning failure guidance (typed, never numeric)

// User-facing message plus what Retry means for a concrete
// DeveloperPortalError. The bridged NSError integer (e.g.
// SideSign.DeveloperPortalError 20) is never a stable semantic identifier,
// so classification switches on the typed cases only; the numeric code
// travels exclusively inside the separate technical details. Associated
// values are never forwarded (they can carry raw portal payloads).
// The switch is compiler-checked: @unknown default stays honest instead of
// inventing a cause.
func v3ProvisioningGuidance(_ error: DeveloperPortalError) -> (message: String, hint: String) {
    switch error {
    case .unknown:
        return ("The developer portal request failed for an unknown reason.",
                "You can retry; if it keeps failing, check the connection and try again later.")
    case .invalidParameters:
        return ("The provisioning request was malformed.",
                "Retry will repeat the same failure. Check the app configuration before trying again.")
    case .incorrectCredentials:
        return ("Apple did not accept the Apple ID or password.",
                "Signing in again with the correct credentials is required before retrying.")
    case .noTeams:
        return ("No Apple Developer team is available for this account.",
                "Join or create a developer team for this Apple ID before retrying.")
    case .appSpecificPasswordRequired:
        return ("Apple requires an app-specific password for this authentication path.",
                "Create an app-specific password for this Apple ID, then use it for this sign-in path.")
    case .invalidDeviceID:
        return ("This device could not be identified for registration.",
                "Retry will repeat the same failure until the device identifier issue is resolved.")
    case .deviceAlreadyRegistered:
        return ("This device is already registered with the selected developer team.",
                "No action is needed for the device itself; retry continues provisioning.")
    case .invalidCertificateRequest:
        return ("Apple rejected the development certificate request.",
                "Check the team certificates before retrying.")
    case .certificateDoesNotExist:
        return ("The selected development certificate no longer exists on the Apple Developer account.",
                "Choose or create a current certificate before retrying.")
    case .invalidAppIDName:
        return ("An App ID name was rejected as invalid.",
                "Fix the app identifier configuration before retrying.")
    case .invalidBundleIdentifier:
        return ("An app bundle identifier was rejected as invalid.",
                "Fix the bundle identifier before retrying.")
    case .bundleIdentifierUnavailable:
        return ("Apple could not register this app identifier for the selected team.",
                "Use a different identifier or team before retrying.")
    case .appIDDoesNotExist:
        return ("A required App ID no longer exists on the developer team.",
                "Recreate the App ID or sync app data before retrying.")
    case .maximumAppIDLimitReached:
        return ("The Apple Developer account has reached its App ID limit.",
                "Remove an unused App ID before retrying.")
    case .invalidAppGroup:
        return ("An app group value was rejected as invalid.",
                "Fix the app group configuration before retrying.")
    case .appGroupDoesNotExist:
        return ("A required app group does not exist on the developer team.",
                "Recreate the app group before retrying.")
    case .invalidProvisioningProfileIdentifier:
        return ("Apple rejected the provisioning profile identifier.",
                "Check the provisioning configuration before retrying.")
    case .provisioningProfileDoesNotExist:
        return ("The required provisioning profile no longer exists.",
                "Create the missing provisioning profile before retrying.")
    case .requiresTwoFactorAuthentication:
        return ("Two-factor authentication is required to continue.",
                "Complete two-factor authentication, then retry.")
    case .userCancelled:
        return ("Provisioning was cancelled.",
                "Run the operation again when ready.")
    case .incorrectVerificationCode:
        return ("The verification code was not accepted.",
                "Enter a fresh verification code when asked, then retry.")
    case .authenticationHandshakeFailed:
        return ("The authentication handshake with Apple failed.",
                "Check the account sign-in state before retrying.")
    case .invalidAnisetteData:
        return ("Valid Anisette data could not be obtained.",
                "You can retry; if it keeps failing, check the Anisette servers.")
    case .tooManyCertificates:
        return ("The developer team has reached its development certificate limit.",
                "Revoke an unused certificate under Certificates before retrying.")
    case .tooManyAttempts:
        return ("Apple is temporarily limiting authentication or developer portal requests.",
                "Wait before trying again.")
    case .accountRepairRequired:
        return ("Apple requires attention on this account before provisioning can continue.",
                "Resolve the account issue with Apple before retrying.")
    case .invalid2FAResponse:
        return ("The two-factor authentication response was not valid.",
                "Start sign-in again so a fresh verification can complete.")
    @unknown default:
        return ("The developer portal request failed for an unknown reason.",
                "You can retry; if it keeps failing, check the connection and try again later.")
    }
}

// MARK: - Authentication state machine

@MainActor
final class V3AuthCenter {
    struct Session {
        var task: Task<Void, Never>?
        var watchdog: Task<Void, Never>?
        var prompt: [String: Any]?
        var attempts = 0
        var terminal = V3TerminalResponse()
        var deadline = Date.distantFuture
        var previousFailure: [String: Any]?
        var terminalAt: Date?
        var cancellationRequested = false
        var submittedAppleID: String?
        var authenticatedAppleID: String?
    }

    var sessions: [String: Session] = [:]
    private var activeID: String?

    func begin(deadline: Date) async -> [String: Any] {
        cleanupSessions()
        if let current = activeID {
            let oldTask = sessions[current]?.task
            _ = cancel(id: current)
            if let oldTask { await oldTask.value }
        }
        let id = UUID().uuidString
        sessions[id] = Session(deadline: deadline)
        activeID = id
        sessions[id]?.task = Task { @MainActor in await V3HeadlessRuntime.shared.auth.run(id: id) }
        sessions[id]?.watchdog = Task { @MainActor in
            let interval = deadline.timeIntervalSinceNow
            if interval > 0 { try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000)) }
            V3HeadlessRuntime.shared.auth.expire(id: id)
        }
        debugLog("[V3_AUTH] BEGIN session=\(id)")
        return ["session": id, "state": "working"]
    }

    func run(id: String) async {
        defer {
            sessions[id]?.task = nil
            sessions[id]?.watchdog?.cancel()
            sessions[id]?.watchdog = nil
            if activeID == id { activeID = nil }
            cleanupSessions()
        }
        do {
            let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let context = StandaloneOperationContext(steps: .signIn, dbBackgroundContext: background)
            let handler = V3HeadlessAuthHandler(sessionID: id)
            let operation = try SignInOperation(context: context, signInHandler: handler, anisetteServerHandler: handler)
            let result = try await operation.execute()
            let account = result.team.account ?? ALTAccount(appleID: "", identifier: result.team.identifier)
            await handler.handleSignInResult(.success((account, result.session)))
            sessions[id]?.prompt = nil
            finish(id: id, response: ["state": "completed", "team": result.team.name,
                                      "teamID": result.team.identifier, "authenticated": true])
            debugLog("[V3_AUTH] TERMINAL session=\(id) state=completed")
        } catch {
            sessions[id]?.prompt = nil
            let session = sessions[id]
            let activeAppleID = DatabaseManager.shared.activeAccount()?.appleID
            let submitted = session?.submittedAppleID?.lowercased()
            let accountMatches = submitted != nil && submitted == activeAppleID?.lowercased()
            let authenticationSucceeded = session?.authenticatedAppleID != nil
            let cancelled = error is CancellationError || session?.cancellationRequested == true
            let authenticatedOutcome = V3AuthTerminalPolicy.resolve(
                authenticationSucceeded: authenticationSucceeded,
                authoritativeAccountMatches: accountMatches,
                provisioningFailed: !cancelled,
                cancelled: cancelled)

            if authenticatedOutcome == "authenticatedProvisioningIncomplete" {
                var response: [String: Any] = ["state": authenticatedOutcome,
                    "authenticated": true,
                    "message": cancelled ? "Signed in successfully. Provisioning was cancelled before setup finished." :
                        "Signed in successfully, but provisioning could not be completed."]
                if !cancelled {
                    let failure = CombinedFailure.capture(error, operation: "signIn", stage: .provisioning, id: id)
                    response["stage"] = failure.stage.rawValue
                    response["code"] = failure.code.rawValue
                    response["failure"] = failure.wire
                    response["technicalDetails"] = failure.technicalDetails
                }
                finish(id: id, response: response)
                debugLog("[V3_AUTH] TERMINAL session=\(id) state=authenticatedProvisioningIncomplete")
            } else if cancelled {
                finish(id: id, response: ["state": "cancelled", "authenticated": false])
                debugLog("[V3_AUTH] TERMINAL session=\(id) state=cancelled")
            } else {
                let failure = CombinedFailure.capture(error, operation: "signIn", stage: .authentication, id: id)
                var wire = failure.wire
                if let kind = v3ClassifyAuthError(error) { wire["kind"] = kind.rawValue }
                let message = (wire["kind"] as? String).map { V3AuthFailureDisplay.message(for: $0) } ?? failure.safeMessage
                let response: [String: Any] = ["state": "failed", "stage": failure.stage.rawValue,
                    "code": failure.code.rawValue, "failure": wire, "message": message,
                    "technicalDetails": failure.technicalDetails]
                finish(id: id, response: response)
                debugLog("[V3_AUTH] TERMINAL session=\(id) state=failed stage=\(failure.stage.rawValue) code=\(failure.code.rawValue)")
            }
        }
    }

    func poll(id: String) -> [String: Any]? {
        cleanupSessions()
        guard let session = sessions[id] else { return nil }
        if let terminal = session.terminal.value { return terminal.merging(["session": id]) { current, _ in current } }
        if let prompt = session.prompt, !session.cancellationRequested {
            var reply: [String: Any] = ["session": id, "state": "awaitingPrompt", "attempts": session.attempts, "prompt": prompt]
            if let previousFailure = session.previousFailure {
                reply["previousFailure"] = previousFailure
            }
            return reply
        }
        return ["session": id, "state": "working", "attempts": session.attempts,
                "cancellationRequested": session.cancellationRequested]
    }

    func respond(id: String, promptID: String, answer: [String: String]) -> [String: Any]? {
        guard let session = sessions[id], case nil = session.terminal.value,
              session.prompt?["id"] as? String == promptID else { return nil }
        guard V3HeadlessRuntime.shared.prompts.answer(promptID: promptID, answer: answer) else {
            return ["session": id, "state": "promptExpired"]
        }
        sessions[id]?.attempts += 1
        // Clear previous failure on successful response to credentials prompt
        if let prompt = sessions[id]?.prompt,
           prompt["kind"] as? String == "credentials" {
            sessions[id]?.previousFailure = nil
        }
        return poll(id: id)
    }

    func expire(id: String) {
        guard let session = sessions[id], case nil = session.terminal.value else { return }
        cancel(id: id)
    }

    @discardableResult
    func cancel(id: String) -> Bool {
        guard var session = sessions[id] else { return false }
        if !session.terminal.isEmpty { return true }
        session.cancellationRequested = true
        session.task?.cancel()
        session.watchdog?.cancel()
        session.prompt = nil
        sessions[id] = session
        debugLog("[V3_AUTH] CANCEL session=\(id)")
        return true
    }

    func cancelAndWait(id: String) async -> Bool {
        guard let parsed = UUID(uuidString: id), parsed.uuidString == id,
              let session = sessions[id] else { return false }
        let task = session.task
        guard cancel(id: id) else { return false }
        if let task { await task.value }
        return true
    }

    @discardableResult
    private func finish(id: String, response: [String: Any]) -> Bool {
        guard var session = sessions[id], session.terminal.setIfEmpty(response) else { return false }
        session.terminalAt = Date()
        sessions[id] = session
        cleanupSessions()
        return true
    }

    private func cleanupSessions(now: Date = Date()) {
        let expired = sessions.compactMap { id, session in
            session.task == nil && session.terminal.value != nil &&
                session.terminalAt.map { now.timeIntervalSince($0) > 600 } == true ? id : nil
        }
        for id in expired where id != activeID { sessions.removeValue(forKey: id) }
        let completed = sessions.filter { $0.value.task == nil && $0.value.terminal.value != nil && $0.key != activeID }
            .sorted { ($0.value.terminalAt ?? .distantPast) < ($1.value.terminalAt ?? .distantPast) }
        if completed.count > 256 {
            for (id, _) in completed.prefix(completed.count - 256) { sessions.removeValue(forKey: id) }
        }
    }
}

enum V3AuthFailureDisplay {
    static func message(for kind: String) -> String {
        switch kind {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "invalidCode": return "The verification code was not accepted. Enter a new code and try again."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service is temporarily unavailable. Try again later."
        case "anisette": return "Authentication could not obtain valid Anisette data."
        case "network": return "Authentication could not reach the required Apple service. Check the connection and try again."
        case "accountRepairRequired": return "Apple requires attention on this account before signing in."
        default: return "Apple sign-in failed for an unknown typed reason."
        }
    }
}

@MainActor
final class V3HeadlessAuthHandler: SignInHandler, AnisetteServerHandler {
    let sessionID: String
    init(sessionID: String) { self.sessionID = sessionID }

    private func center() throws -> V3AuthCenter {
        let center = V3HeadlessRuntime.shared.auth
        guard let session = center.sessions[sessionID], session.terminal.isEmpty else { throw CancellationError() }
        return center
    }

    private func ask(kind: String, title: String, message: String,
                     fields: [[String: String]] = [], options: [[String: String]] = [],
                     destructive: Bool = false) async throws -> [String: String] {
        let center = try center()
        let prompt = v3Prompt(kind: kind, title: title, message: message,
                              fields: fields, options: options, destructive: destructive)
        guard let promptID = prompt["id"] as? String else { throw CancellationError() }
        defer {
            if center.sessions[sessionID]?.prompt?["id"] as? String == promptID {
                center.sessions[sessionID]?.prompt = nil
            }
        }
        return try await center.promptsParked(promptID: promptID) {
            guard center.sessions[self.sessionID]?.terminal.isEmpty == true else { return }
            center.sessions[self.sessionID]?.prompt = prompt
            debugLog("[V3_AUTH] PROMPT session=\(self.sessionID) kind=\(kind) attempts=\(center.sessions[self.sessionID]?.attempts ?? 0)")
        }
    }

    func credentials() async throws -> (String, String) {
        let answer = try await ask(kind: "credentials", title: "Apple ID Sign In",
                                   message: "Enter the Apple ID and password used for signing.",
                                   fields: [["key": "appleID", "label": "Apple ID", "secure": "false"],
                                            ["key": "password", "label": "Password", "secure": "true"]])
        guard let appleID = answer["appleID"], !appleID.isEmpty,
              let password = answer["password"], !password.isEmpty else { throw CancellationError() }
        V3HeadlessRuntime.shared.auth.sessions[sessionID]?.submittedAppleID = appleID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return (appleID, password)
    }

    func verificationCode(for request: TwoFactorRequest) async throws -> TwoFactorResponse {
        switch request {
        case .selectDeliveryMethod(let preferredMode, let phoneNumbers):
            _ = preferredMode
            return try await chooseDeliveryMethod(phoneNumbers: phoneNumbers)
        case .trustedDevice:
            return try await enterVerificationCode(mode: .trustedDevice, phoneNumbers: [], activeID: "",
                                                  failure: request.verificationFailure)
        case .sms(let phoneNumbers, let selectedID, _):
            return try await enterVerificationCode(mode: .sms, phoneNumbers: phoneNumbers, activeID: selectedID,
                                                  failure: request.verificationFailure)
        case .voice(let phoneNumbers, let selectedID, _):
            return try await enterVerificationCode(mode: .voice, phoneNumbers: phoneNumbers, activeID: selectedID,
                                                  failure: request.verificationFailure)
        }
    }

    private func chooseDeliveryMethod(phoneNumbers: [TrustedPhoneNumber]) async throws -> TwoFactorResponse {
        var methods: [[String: String]] = [
            ["id": "trustedDevice", "label": "Use Trusted Device"],
            ["id": "sms", "label": "Send SMS"],
            ["id": "voice", "label": "Request Voice Call"],
            ["id": "cancel", "label": "Cancel Sign In"]
        ]
        if phoneNumbers.isEmpty { methods.removeAll { ["sms", "voice"].contains($0["id"] ?? "") } }
        let answer = try await ask(kind: "twoFactor", title: "Choose Verification Method",
            message: "Choose how Apple should send your verification code.",
            fields: [["key": "step", "label": "step", "secure": "false", "value": V3TwoFactorStep.chooseDeliveryMethod.rawValue]],
            options: methods)
        switch answer["action"] {
        case "trustedDevice":
            debugLog("[V3_AUTH] 2FA_DELIVERY_REQUESTED mode=trustedDevice")
            return .requestTrustedDevice
        case "sms", "voice":
            let method = answer["action"] ?? "sms"
            var phoneID = phoneNumbers.first?.id ?? ""
            if phoneNumbers.count > 1 {
                let phoneChoices = phoneNumbers.map { ["id": "phone:\($0.id)", "label": $0.number] }
                    + [["id": "cancel", "label": "Cancel Sign In"]]
                let selection = try await ask(kind: "twoFactor", title: "Choose Phone Number",
                    message: "Select where Apple should send the verification code.",
                    fields: [["key": "step", "label": "step", "secure": "false", "value": V3TwoFactorStep.afterDeliveryChoice(method, phoneCount: phoneNumbers.count)?.rawValue ?? V3TwoFactorStep.choosePhoneNumber.rawValue],
                             ["key": "mode", "label": "mode", "secure": "false", "value": method]],
                    options: phoneChoices)
                guard let chosen = selection["action"], chosen.hasPrefix("phone:") else { return .cancel }
                phoneID = String(chosen.dropFirst("phone:".count))
            }
            debugLog("[V3_AUTH] 2FA_DELIVERY_REQUESTED mode=\(method)")
            return method == "sms" ? .requestSMS(phoneID: phoneID) : .requestVoice(phoneID: phoneID)
        default:
            return .cancel
        }
    }

    private func enterVerificationCode(mode: TwoFactorDeliveryMode, phoneNumbers: [TrustedPhoneNumber],
                                       activeID: String, failure: TwoFactorVerificationFailure?) async throws -> TwoFactorResponse {
        let acknowledgement: String
        switch mode {
        case .trustedDevice: acknowledgement = "Verification request sent to your trusted devices."
        case .sms: acknowledgement = "Verification code requested by SMS."
        case .voice: acknowledgement = "Verification call requested."
        }
        let message = failure?.userMessage ?? acknowledgement
        let answer = try await ask(kind: "twoFactor", title: "Enter Verification Code", message: message,
            fields: [["key": "step", "label": "step", "secure": "false", "value": V3TwoFactorStep.afterDelivery(mode.rawValue)?.rawValue ?? V3TwoFactorStep.enterVerificationCode.rawValue],
                     ["key": "mode", "label": "mode", "secure": "false", "value": mode.rawValue],
                     ["key": "activeID", "label": "activeID", "secure": "false", "value": activeID],
                     ["key": "code", "label": "Verification code", "secure": "false"]],
            options: [["id": "changeMethod", "label": "Change Verification Method"],
                      ["id": "cancel", "label": "Cancel Sign In"]])
        switch answer["action"] {
        case "code":
            guard let code = answer["code"], !code.isEmpty else { return try await enterVerificationCode(
                mode: mode, phoneNumbers: phoneNumbers, activeID: activeID, failure: .unknown) }
            debugLog("[V3_AUTH] 2FA_CODE_SUBMITTED")
            return .verificationCode(code)
        case "changeMethod": return try await chooseDeliveryMethod(phoneNumbers: phoneNumbers)
        default: return .cancel
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

    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {
        guard V3HeadlessRuntime.shared.auth.sessions[sessionID]?.terminal.isEmpty == true else { return }
        switch result {
        case .success(let (account, _)):
            V3HeadlessRuntime.shared.auth.sessions[sessionID]?.previousFailure = nil
            V3HeadlessRuntime.shared.auth.sessions[sessionID]?.authenticatedAppleID =
                account.appleID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        case .failure(let error):
            guard let kind = v3ClassifyAuthError(error) else {
                V3HeadlessRuntime.shared.auth.sessions[sessionID]?.previousFailure = nil
                return
            }
            let failure = CombinedFailure.capture(error, operation: "signIn", stage: .authentication, id: sessionID)
            var wire = failure.wire
            wire["kind"] = kind.rawValue
            V3HeadlessRuntime.shared.auth.sessions[sessionID]?.previousFailure = wire
            debugLog("[V3_AUTH] ATTEMPT_FAILED session=\(sessionID) kind=\(kind.rawValue) stage=\(failure.stage.rawValue) code=\(failure.code.rawValue) correlation=\(failure.correlationID)")
        }
    }

    func resolveTeam(_ teams: [ALTTeam]) async throws -> ALTTeam {
        let answer = try await ask(kind: "team", title: "Select Team", message: "Choose the development team used for signing.",
                                   options: teams.map { ["id": $0.identifier, "label": "\($0.name) (\($0.identifier))"] })
        guard let identifier = answer["choice"], let team = teams.first(where: { $0.identifier == identifier }) else {
            throw CancellationError()
        }
        return team
    }

    func resolveProvisioningError(_ error: Error) async -> ProvisioningErrorDecision {
        // Cancellation is terminal, never a prompt. Everything else is
        // classified from the actual typed error: the message comes from the
        // concrete DeveloperPortalError case; the bridged domain/code travel
        // only inside the separate technical details.
        if error is CancellationError { return .cancel }
        if let portal = error as? DeveloperPortalError {
            if case .userCancelled = portal { return .cancel }
            let guidance = v3ProvisioningGuidance(portal)
            return await askProvisioningRetry(message: guidance.message, hint: guidance.hint, error: error)
        }
        return await askProvisioningRetry(
            message: "Provisioning could not be completed because of an unexpected failure.",
            hint: "You can retry; if it keeps failing, check the account, team, and certificates before trying again.",
            error: error)
    }

    private func askProvisioningRetry(message: String, hint: String, error: Error) async -> ProvisioningErrorDecision {
        let native = error as NSError
        let technical = "domain=\(native.domain) code=\(native.code) area=provisioning correlation=\(sessionID)"
        do {
            let answer = try await ask(kind: "provisioningError", title: "Provisioning Needs Attention",
                                       message: message + "\n\n" + hint,
                                       fields: [["key": "technical", "label": "Technical details", "secure": "false", "value": technical]],
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
    func promptsParked(promptID: String, onReady: @escaping @MainActor () -> Void) async throws -> [String: String] {
        try await V3HeadlessRuntime.shared.prompts.park(promptID: promptID, onReady: onReady)
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
        guard let session = center.sessions[sessionID], session.terminal.isEmpty else { throw CancellationError() }
        return center
    }

    private func ask(kind: String, title: String, message: String,
                     fields: [[String: String]] = [], options: [[String: String]] = [],
                     destructive: Bool = false) async throws -> [String: String] {
        let center = try center()
        let prompt = v3Prompt(kind: kind, title: title, message: message,
                              fields: fields, options: options, destructive: destructive)
        guard let promptID = prompt["id"] as? String else { throw CancellationError() }
        defer {
            if center.sessions[sessionID]?.prompt?["id"] as? String == promptID {
                center.sessions[sessionID]?.prompt = nil
            }
        }
        return try await V3HeadlessRuntime.shared.prompts.park(promptID: promptID) {
            guard center.sessions[self.sessionID]?.terminal.isEmpty == true else { return }
            center.sessions[self.sessionID]?.prompt = prompt
            debugLog("[V3_OP] PROMPT session=\(self.sessionID) kind=\(kind)")
        }
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
        return try await V3ExtensionRemovalPromptPolicy.decide(
            excessExtensions: excessExtensions,
            whenEmpty: .keepAll(useMainProfile: false)
        ) {
            let sorted = excessExtensions.sorted { $0.bundleIdentifier < $1.bundleIdentifier }
            let answer = try await self.ask(kind: "extensions", title: "App Extensions",
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

    func recordNativeUninstallSucceeded() {
        V3DeleteNativeSuccessRegistry.shared.record(sessionID: sessionID)
        debugLog("[V3_OP] DELETE_NATIVE_UNINSTALL_SUCCEEDED session=\(sessionID)")
    }

    // Called by PipelineExecutor immediately before it executes the concrete
    // pipeline step. This reflects backend state, never progress ranges.
    func recordPipelinePhase(_ step: PipelineStep, downloadUsesNetwork: Bool) {
        guard let center = try? center() else { return }
        center.recordPipelineStep(sessionID: sessionID, step: String(describing: step),
                                  downloadUsesNetwork: downloadUsesNetwork)
    }

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
        var phase = V3OperationPhaseTracker()
        var terminal = V3TerminalResponse()
        var deadline = Date.distantFuture
        var terminalAt: Date?
        var ipaToken: String?
    }

    var sessions: [String: Session] = [:]
    private var mutationRegistry = V3OperationMutationRegistry()

    func start(kind: String, target: String, value: Bool?, sessionID requestedID: String,
               deadline: Date) async -> [String: Any] {
        _ = value
        cleanupSessions()
        guard let parsedID = UUID(uuidString: requestedID), parsedID.uuidString == requestedID else {
            return ["state": "failed", "failedToStart": true, "code": "invalidConfiguration",
                    "message": "The operation attempt identifier is invalid."]
        }
        let id = requestedID
        if sessions[id] != nil { return terminalReply(id: id) }
        sessions[id] = Session(deadline: deadline)
        switch mutationRegistry.begin(id) {
        case .cancelledBeforeStart:
            finish(id: id, response: ["state": "cancelled"])
            return terminalReply(id: id)
        case .busy:
            let failure = CombinedFailure(operation: kind, stage: .command, code: .busy, id: id, retryable: true)
            finish(id: id, response: ["state": "failed", "failedToStart": true, "stage": failure.stage.rawValue,
                "code": failure.code.rawValue, "message": failure.message,
                "technical": failure.technicalDetails, "failure": failure.wire, "retryable": true])
            return terminalReply(id: id)
        case .started:
            break
        }
        if kind == "installSharedIPA" { sessions[id]?.ipaToken = target }
        guard AuthManager.shared.isAuthenticated else {
            finish(id: id, response: ["state": "waitingForAuthentication"])
            mutationRegistry.finish(id)
            return terminalReply(id: id)
        }
        do {
            let driver = try await makeDriver(id: id, kind: kind, target: target)
            guard sessions[id]?.terminal.isEmpty == true, mutationRegistry.activeID == id else {
                return terminalReply(id: id)
            }
            sessions[id]?.task = Task { @MainActor in await self.drive(id: id, driver: driver) }
            sessions[id]?.watchdog = Task { @MainActor in
                let interval = deadline.timeIntervalSinceNow
                if interval > 0 { try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000)) }
                self.expire(id: id)
            }
        } catch {
            var failure = terminalFailure(id: id, kind: kind, error: error)
            failure["failedToStart"] = true
            finish(id: id, response: failure)
            mutationRegistry.finish(id)
            return terminalReply(id: id)
        }
        return ["session": id, "state": "working",
                "phase": V3OperationPhase.working.rawValue,
                "phaseLabel": V3OperationPhase.working.label]
    }

    private func drive(id: String, driver: V3OpDriver) async {
        defer {
            sessions[id]?.task = nil
            sessions[id]?.watchdog?.cancel()
            sessions[id]?.watchdog = nil
            mutationRegistry.finish(id)
            cleanupSessions()
        }
        do {
            try await driver.run()
            sessions[id]?.prompt = nil
            finish(id: id, response: ["state": "completed"])
            debugLog("[V3_OP] TERMINAL session=\(id) kind=\(driver.kind) state=completed")
        } catch {
            sessions[id]?.prompt = nil
            if error is CancellationError {
                finish(id: id, response: ["state": "cancelled"])
                debugLog("[V3_OP] TERMINAL session=\(id) kind=\(driver.kind) state=cancelled")
            } else {
                let terminal = terminalFailure(id: id, kind: driver.kind, error: error)
                finish(id: id, response: terminal)
                debugLog("[V3_OP] TERMINAL session=\(id) kind=\(driver.kind) state=\(terminal["state"] as? String ?? "") stage=\(terminal["stage"] as? String ?? "") code=\(terminal["code"] as? String ?? "")")
            }
        }
    }

    func poll(id: String) -> [String: Any]? {
        cleanupSessions()
        guard let session = sessions[id] else { return nil }
        if let terminal = session.terminal.value { return terminal.merging(["session": id]) { current, _ in current } }
        let phase = session.phase.phase
        var reply: [String: Any] = ["session": id, "state": "working",
                                    "phase": phase.rawValue, "phaseLabel": phase.label]
        if let progress = session.group?.progress.fractionCompleted, progress.isFinite {
            let normalized = V3NormalizedProgress.clamp(progress)
            if normalized != progress {
                debugLog("[V3_OP] PROGRESS_CLAMP session=\(id) out_of_range=1")
            }
            reply["progress"] = normalized
        }
        if let prompt = session.prompt {
            reply["state"] = "awaitingPrompt"
            reply["prompt"] = prompt
        }
        return reply
    }

    func recordPipelineStep(sessionID: String, step: String, downloadUsesNetwork: Bool) {
        guard var session = sessions[sessionID], session.terminal.isEmpty else { return }
        session.phase.recordPipelineStep(step, downloadUsesNetwork: downloadUsesNetwork)
        sessions[sessionID] = session
    }

    func setPhase(sessionID: String, phase: V3OperationPhase) {
        guard var session = sessions[sessionID], session.terminal.isEmpty else { return }
        session.phase.record(phase)
        sessions[sessionID] = session
    }

    func answer(id: String, promptID: String, answer: [String: String]) -> [String: Any]? {
        guard let session = sessions[id], case nil = session.terminal.value,
              session.prompt?["id"] as? String == promptID else { return nil }
        guard V3HeadlessRuntime.shared.prompts.answer(promptID: promptID, answer: answer) else {
            return ["session": id, "state": "promptExpired"]
        }
        return poll(id: id)
    }

    func expire(id: String) {
        guard let session = sessions[id], case nil = session.terminal.value else { return }
        _ = cancel(id: id)
    }

    @discardableResult
    func cancel(id: String) -> Bool {
        guard var session = sessions[id] else { return false }
        guard case nil = session.terminal.value else { return true }
        session.task?.cancel()
        session.watchdog?.cancel()
        session.group?.cancel()
        session.terminal.setIfEmpty(["state": "cancelled"])
        session.terminalAt = Date()
        session.prompt = nil
        sessions[id] = session
        V3SideStoreService.shared.cancellations[id]?()
        if session.task == nil { mutationRegistry.finish(id) }
        cleanupSessions()
        return true
    }

    func cancelAndWait(id: String) async -> Bool {
        guard let parsedID = UUID(uuidString: id), parsedID.uuidString == id else { return false }
        guard let session = sessions[id] else {
            _ = mutationRegistry.cancel(id)
            cleanupSessions()
            return true
        }
        let task = session.task
        guard cancel(id: id) else { return false }
        if let task { await task.value }
        return true
    }

    func cleanupIPA(token: String) throws {
        let canonical = try V3IPAStaging.canonicalToken(token)
        guard !sessions.values.contains(where: {
            $0.ipaToken == canonical && $0.terminal.isEmpty
        }) else { throw V3SideStoreServiceError.busy }
        guard let group = Bundle.main.altstoreAppGroup,
              let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else {
            throw CombinedIPAFileError(.fileAccess)
        }
        try V3IPAStaging.cleanup(token: canonical, containerRoot: root)
    }

    private func finish(id: String, response: [String: Any]) {
        guard var session = sessions[id] else { return }
        if session.terminal.setIfEmpty(response) {
            session.terminalAt = Date()
            sessions[id] = session
            cleanupSessions()
        }
    }

    private func cleanupSessions(now: Date = Date()) {
        let expired = sessions.compactMap { id, session in
            id != mutationRegistry.activeID && session.task == nil && session.terminal.value != nil &&
                session.terminalAt.map { now.timeIntervalSince($0) > 600 } == true ? id : nil
        }
        for id in expired { sessions.removeValue(forKey: id) }
        let completed = sessions.filter { $0.key != mutationRegistry.activeID && $0.value.task == nil && $0.value.terminal.value != nil }
            .sorted { ($0.value.terminalAt ?? .distantPast) < ($1.value.terminalAt ?? .distantPast) }
        if completed.count > 256 {
            for (id, _) in completed.prefix(completed.count - 256) { sessions.removeValue(forKey: id) }
        }
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
        // The structured failure crosses XPC as scalars only: the user-facing
        // message plus the fixed wire vocabulary (stage, code, correlation,
        // underlying domain/code, retryable). No arbitrary userInfo, file
        // paths, or auth secrets ever leave the SideStore process.
        // The session id is the end-to-end correlation identifier.
        let failure = CombinedFailure.capture(error, operation: kind, stage: stage, id: id)
        var terminal: [String: Any] = ["state": "failed", "stage": failure.stage.rawValue,
            "code": failure.code.rawValue, "message": failure.message,
            "technical": failure.technicalDetails, "failure": failure.wire]
        if let retryable = failure.retryable { terminal["retryable"] = retryable }
        return terminal
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
            let installTarget = try await resolveInstallTarget(id: id, kind: kind, target: target)
            let app: AppProtocol
            switch installTarget {
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
            let route: V3InstallInputRoute = kind == "installSharedIPA" ? .localIPA :
                (kind == "installURL" ? .remoteURL : .catalog)
            return makeInstallDriver(id: id, kind: kind, route: route, app: app,
                                     handler: handler, context: baseContext)
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
                V3SideStoreService.shared.cancellations[id] = { group.cancel(); group.progress.cancel() }
                do {
                    try await AppManager.shared.pipelineRunner.perform([.refresh(app)], handler: handler, group: group)
                } catch {
                    group.context.error = error
                    group.set(.failure(error), forAppWithBundleIdentifier: app.bundleIdentifier)
                    throw error
                }
                // PipelineRunner.perform returns only after every app result is
                // recorded. Its completion callback is an observer and cannot
                // race drive() into writing a second terminal result.
                _ = try V3RefreshResultVerifier.verified(expectedBundleID: app.bundleIdentifier,
                    results: group.results, bundleIdentifier: { $0.bundleIdentifier })
                try Task.checkCancellation()
            }
        case "delete":
            let app: InstalledApp = try v3Resolve(target)
            guard app.bundleIdentifier != StoreApp.altstoreAppID else { throw V3SideStoreServiceError.unsupported }
            return V3OpDriver(kind: kind) {
                try await self.deleteAndReconcile(id: id, app: app, handler: handler, context: baseContext)
            }
        case "activate", "deactivate", "backup", "restore":
            let app: InstalledApp = try v3Resolve(target)
            if kind == "deactivate", app.bundleIdentifier == StoreApp.altstoreAppID {
                throw V3SideStoreServiceError.unsupported
            }
            let operation: AppOperation
            switch kind {
            case "activate": operation = .activate(app)
            case "deactivate": operation = .deactivate(app)
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

    private func deleteAndReconcile(id: String, app: InstalledApp,
                                    handler: V3HeadlessPipelineHandler,
                                    context: StandaloneOperationContext) async throws {
        let callback = V3DeleteBackendResultBox()
        let bundleIdentifier = app.bundleIdentifier
        let group = AppManager.shared.pipelineRunner.performSingleOperation(
            .deleteApp(app), handler: handler, context: context
        ) { result in
            switch result {
            case .success: callback.record(.success(()))
            case .failure(let error): callback.record(.failure(error))
            }
        }
        guard var session = sessions[id], session.terminal.isEmpty else {
            group.cancel()
            group.progress.cancel()
            throw CancellationError()
        }
        session.group = group
        sessions[id] = session
        V3SideStoreService.shared.cancellations[id] = { group.cancel(); group.progress.cancel() }
        defer {
            V3DeleteNativeSuccessRegistry.shared.remove(sessionID: id)
            V3SideStoreService.shared.cancellations[id] = nil
        }

        let deadline = Date().addingTimeInterval(30)
        var missingCallbackReconcileDeadline: Date?
        var lastLibraryPresence: Bool?
        var contract = V3DeleteCompletionContract()
        debugLog("[V3_OP] DELETE_RECONCILE_START session=\(id)")
        while !Task.isCancelled {
            try Task.checkCancellation()
            let backendResult = callback.result
            if case .failure(let error)? = backendResult { throw error }
            let appIsPresent = try await authoritativeLibraryContains(bundleIdentifier: bundleIdentifier)
            let nativeUninstallSucceeded = V3DeleteNativeSuccessRegistry.shared.contains(sessionID: id)
            if lastLibraryPresence != appIsPresent {
                lastLibraryPresence = appIsPresent
                debugLog("[V3_OP] DELETE_LIBRARY_RECONCILE session=\(id) app_present=\(appIsPresent)")
            }
            let backendState: V3DeleteCompletionContract.BackendResult
            switch backendResult {
            case .success?: backendState = .succeeded
            case .failure?: backendState = .failed
            case nil: backendState = .pending
            }
            let now = Date()
            if !appIsPresent, backendState == .pending, nativeUninstallSucceeded,
               missingCallbackReconcileDeadline == nil {
                missingCallbackReconcileDeadline = now.addingTimeInterval(5)
            }
            let reconciliationExpired = now >= deadline ||
                (missingCallbackReconcileDeadline.map { now >= $0 } ?? false)
            let terminal = contract.resolve(
                backend: backendState,
                nativeUninstallSucceeded: nativeUninstallSucceeded,
                appStillInAuthoritativeLibrary: appIsPresent,
                deadlineExpired: reconciliationExpired,
                progress: group.progress.fractionCompleted
            )
            switch terminal {
            case .completed?:
                let resolution = backendState == .succeeded ? "pipeline_callback" : "native_success_reconciled"
                debugLog("[V3_OP] DELETE_RECONCILE_COMPLETED session=\(id) evidence=\(resolution)+library_absent")
                return
            case .failed?:
                debugLog("[V3_OP] DELETE_RECONCILE_FAILED session=\(id) backend=\(backendState) native_uninstall=\(nativeUninstallSucceeded) library_present=\(appIsPresent)")
                throw CombinedFailure(operation: "delete", stage: .command, code: .timedOut,
                                      id: id, retryable: false)
            case nil:
                break
            }
            if reconciliationExpired { continue }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw CancellationError()
    }

    private func authoritativeLibraryContains(bundleIdentifier: String) async throws -> Bool {
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        return try await context.perform {
            let request = NSFetchRequest<NSFetchRequestResult>(entityName: "InstalledApp")
            request.predicate = NSPredicate(format: "bundleIdentifier == %@", bundleIdentifier)
            request.fetchLimit = 1
            return try context.count(for: request) > 0
        }
    }

    // The first convergence point for local IPA and URL installs is a resolved
    // AppProtocol. Both then create the same .install operation and use the
    // same PipelineRunner callback, prompt handler, and terminal result path.
    private func makeInstallDriver(id: String, kind: String, route: V3InstallInputRoute,
                                   app: AppProtocol, handler: V3HeadlessPipelineHandler,
                                   context: StandaloneOperationContext) -> V3OpDriver {
        let built = V3InstallPipelineParity.makeOperation(route: route, app) {
            AppOperation.install($0)
        }
        debugLog("[V3_INSTALL_ROUTE] input=\(built.route.rawValue) convergence=AppProtocol pipeline=.install")
        return V3OpDriver(kind: kind) {
            try await self.single(id: id, operation: built.operation, handler: handler, context: context)
        }
    }

    private final class V3DeleteBackendResultBox: @unchecked Sendable {
        private let lock = NSLock()
        private var stored: Result<Void, Error>?

        func record(_ result: Result<Void, Error>) {
            lock.lock()
            if stored == nil { stored = result }
            lock.unlock()
        }

        var result: Result<Void, Error>? {
            lock.lock()
            defer { lock.unlock() }
            return stored
        }
    }

    private func resolveInstallTarget(id: String, kind: String, target: String) async throws -> InstallTarget {
        if kind == "install" {
            let app: StoreApp = try v3Resolve(target)
            guard app.latestSupportedVersion != nil else { throw V3SideStoreServiceError.unsupported }
            return .app(app)
        }
        if kind == "installSharedIPA" {
            let token = try V3IPAStaging.canonicalToken(target)
            guard let group = Bundle.main.altstoreAppGroup,
                  let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else {
                throw CombinedIPAFileError(.fileAccess)
            }
            let metadata = try V3IPAStaging.inspect(token: token, containerRoot: root) { url in
                try Self.readAppMetadata(from: url, packageType: .ipa)
            }
            let file = try V3IPAStaging.resolve(token: token, containerRoot: root)
            return .app(AnyApp(name: metadata.name, bundleIdentifier: metadata.bundleIdentifier,
                               url: file, storeApp: nil))
        }
        guard let url = URL(string: target), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else {
            throw V3SideStoreServiceError.invalidRequest
        }
        return try await ipaTarget(url: url, scoped: false, sessionID: id)
    }

    private func ipaTarget(url: URL, scoped: Bool, sessionID: String) async throws -> InstallTarget {
        var localURL = url
        var scopedURL: URL?
        defer { scopedURL?.stopAccessingSecurityScopedResource() }
        if !url.isFileURL {
            guard let packageType = PackageType(url: url), packageType == .ipa else {
                throw OperationError.invalidApp(reason: "Unsupported package format '.\(url.pathExtension)'. Expected '.ipa'.")
            }
            let temporaryDirectory = FileManager.default.uniqueTemporaryURL()
            try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
            V3HeadlessRuntime.shared.operations.setPhase(sessionID: sessionID, phase: .downloadingIPA)
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
        let (bundleIdentifier, appName): (String, String)
        do { (bundleIdentifier, appName) = try Self.readAppMetadata(from: localURL, packageType: packageType) }
        catch { throw CombinedIPAFileError(.invalidPackage) }
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
    case notReady, invalidRequest, notFound, unsupported, busy, authRequired, persistenceUnverified
}

struct V3SourceCommandError: Error {
    enum Kind { case network, invalidManifest }
    let kind: Kind
    let domain: String
    let code: Int

    static func classify(_ error: Error) -> V3SourceCommandError? {
        let native = error as NSError
        if error is URLError || native.domain == NSURLErrorDomain {
            return V3SourceCommandError(kind: .network, domain: NSURLErrorDomain, code: native.code)
        }
        if error is DecodingError || native.domain == "io.sidestore.SideStore.DecodingError" ||
            ((error as? SourceError)?.code == .unsupported) {
            return V3SourceCommandError(kind: .invalidManifest, domain: native.domain, code: native.code)
        }
        return nil
    }
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
    private static var activeCertificateValidationCache: (fingerprint: String, result: String, checkedAt: Date)?

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
        guard let url = V3SourceAddPersistencePolicy.validatedURL(urlString) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let source: Source
        do {
            source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
        } catch {
            if let classified = V3SourceCommandError.classify(error) { throw classified }
            throw error
        }
        let name = try await background.performAsync { source.name }
        let identifier = try await background.performAsync { source.identifier }
        let added = try await source.isAdded()
        let title = "Would you like to add the source \"\(name)\"?"
        return ["identifier": identifier, "name": name, "alreadyAdded": added,
                "title": title, "message": "Make sure to only add sources that you trust."]
    }

    static func sourceAddConfirmed(urlString: String) async throws -> [String: Any] {
        guard let url = V3SourceAddPersistencePolicy.validatedURL(urlString) else {
            throw V3SideStoreServiceError.invalidRequest
        }
        let background = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let source: Source
        do {
            source = try await AppManager.shared.fetchSource(sourceURL: url, managedObjectContext: background)
        } catch {
            if let classified = V3SourceCommandError.classify(error) { throw classified }
            throw error
        }
        let identifier = try await background.performAsync { source.identifier }
        let wasPersisted = try await source.isAdded()
        let decision = V3SourceAddPersistencePolicy.decision(sourceIsPersisted: wasPersisted)
        if decision == .save {
            // `fetchSource` inserts into this context. Do not use a fetch from
            // the same context as a duplicate check: it sees that unsaved row.
            try await background.performAsync { try background.save() }
        }
        let verificationContext = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        let query = NSFetchRequest<Source>(entityName: "Source")
        query.predicate = NSPredicate(format: "%K == %@", #keyPath(Source.identifier), identifier)
        let authoritativeCount = try await verificationContext.performAsync {
            try verificationContext.count(for: query)
        }
        guard let result = V3SourceAddPersistencePolicy.verifiedResult(
            identifier: identifier, alreadyAdded: decision == .alreadyAdded,
            authoritativeCount: authoritativeCount) else {
            throw V3SideStoreServiceError.persistenceUnverified
        }
        if decision == .save {
            let viewContext = DatabaseManager.shared.viewContext
            let query = NSFetchRequest<Source>(entityName: "Source")
            query.predicate = NSPredicate(format: "%K == %@", #keyPath(Source.identifier), identifier)
            guard let persistedSource = try viewContext.performAndWait({ try viewContext.fetch(query).first }) else {
                throw V3SideStoreServiceError.persistenceUnverified
            }
            NotificationCenter.default.post(name: AppManager.didAddSourceNotification, object: persistedSource)
        }
        return result
    }

    static func authoritativeSourceRows() async throws -> [[String: Any]] {
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        return try await context.performAsync {
            try context.fetch(NSFetchRequest<Source>(entityName: "Source")).map { source in
                ["identifier": source.identifier, "name": source.name,
                 "subtitle": source.subtitle ?? "", "url": source.sourceURL.absoluteString,
                 "appCount": source.apps.count,
                 "canRemove": source.identifier != Source.altStoreIdentifier] as [String: Any]
            }
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
                "service": ["ready": DatabaseManager.shared.isStarted],
                "certificateState": await certificateState()]
    }

    // Facts about the certificate the refresh/signing pipeline actually uses
    // (CertificateManager.activeCertificate). Only the serial suffix and a
    // public certificate DER fingerprint cross XPC; no private key, p12, or
    // password is returned. The LiveContainer JIT-Less copy stays host-side.
    static func certificateState() async -> [String: Any] {
        guard let active = CertificateManager.shared.activeCertificate else {
            return ["active": false]
        }
        var state: [String: Any] = [
            "active": true,
            "serialSuffix": String(active.serialNumber.suffix(4)),
            "team": DatabaseManager.shared.activeTeam()?.identifier ?? "",
            "expiry": active.certificate.x509.expiryDate,
        ]
        let fingerprint: String
        if let certificateDER = active.certificate.x509.data {
            fingerprint = SHA256.hash(data: certificateDER).map { String(format: "%02x", $0) }.joined()
            state["certificateIdentitySHA256"] = fingerprint
        } else {
            fingerprint = ""
        }
        if let cached = activeCertificateValidationCache,
           cached.fingerprint == fingerprint, Date().timeIntervalSince(cached.checkedAt) < 60 {
            state["validation"] = cached.result
        } else {
            let validation: String
            do {
                try await OCSPValidator.validate(active.certificate.x509)
                validation = "valid"
            } catch let error as OCSPValidationError {
                switch error {
                case .revoked: validation = "revoked"
                case .expired: validation = "expired"
                default: validation = "unknown"
                }
            } catch {
                validation = "unknown"
            }
            activeCertificateValidationCache = (fingerprint, validation, Date())
            state["validation"] = validation
        }
        return state
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
