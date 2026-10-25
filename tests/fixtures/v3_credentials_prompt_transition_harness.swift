// Provider request/response types are IO doubles. Prompt creation, answer
// admission, continuation ownership, cleanup, and host revision rules below are
// extracted verbatim from production; this does not exercise SRP or Apple IO.
func debugLog(_ message: String) {}
struct TrustedPhoneNumber { let id: String; let number: String }
enum TwoFactorDeliveryMode: String { case trustedDevice, sms, voice }
enum TwoFactorVerificationFailure {
    case unknown, incorrectCode, serviceUnavailable
    var userMessage: String { "fixture" }
}
enum TwoFactorRequest {
    case selectDeliveryMethod(preferredMode: TwoFactorDeliveryMode, phoneNumbers: [TrustedPhoneNumber])
    case trustedDevice(error: String? = nil)
    case sms(phoneNumbers: [TrustedPhoneNumber], selectedID: String, error: String?)
    case voice(phoneNumbers: [TrustedPhoneNumber], selectedID: String, error: String?)
    var verificationFailure: TwoFactorVerificationFailure? {
        let error: String?
        switch self {
        case .selectDeliveryMethod: error = nil
        case .trustedDevice(let value), .sms(_, _, let value), .voice(_, _, let value): error = value
        }
        return error == nil ? nil : .incorrectCode
    }
}
enum TwoFactorResponse {
    case requestTrustedDevice, requestSMS(phoneID: String), requestVoice(phoneID: String)
    case verificationCode(String), cancel
}
@MainActor final class V3HeadlessRuntime {
    static let shared = V3HeadlessRuntime()
    let auth = V3AuthCenter()
    let prompts = V3PromptCenter()
}
@MainActor final class V3AuthCenter {
    struct Session {
        var terminal = V3TerminalResponse()
        var prompt: [String: Any]?
        var attempts = 0
        var revision = 0
        var cancellationRequested = false
        var acceptedPromptIDs: [String] = []
        var previousFailure: [String: Any]?
        var reauthenticationAppleID: String?
        var submittedAppleID: String?
    }
    var sessions: [String: Session] = [:]
    var cancelledBeforeBegin = V3AuthStartCancellationRegistry()
    func cleanupSessions() {}
    __PRODUCTION_CENTER_METHODS__
}
@MainActor final class V3HeadlessAuthHandler {
    let sessionID: String
    init(sessionID: String) { self.sessionID = sessionID }
    __PRODUCTION_HANDLER_METHODS__
}
@main struct CredentialsPromptTransitionHarness {
    @MainActor static func waitForPrompt(_ id: String, kind: String) async -> [String: Any] {
        for _ in 0..<10_000 {
            if let reply = V3HeadlessRuntime.shared.auth.poll(id: id),
               let prompt = reply["prompt"] as? [String: Any], prompt["kind"] as? String == kind {
                return reply
            }
            await Task.yield()
        }
        preconditionFailure("production handler did not publish expected prompt")
    }
    @MainActor static func nextPrompt(_ id: String, after prior: String? = nil) async -> [String: Any] {
        for _ in 0..<10_000 {
            if let prompt = V3HeadlessRuntime.shared.auth.poll(id: id)?["prompt"] as? [String: Any],
               prompt["id"] as? String != prior { return prompt }
            await Task.yield()
        }
        preconditionFailure("new owned prompt was not published")
    }
    @MainActor static func answer(_ id: String, _ prompt: [String: Any], _ values: [String: String]) {
        let center = V3HeadlessRuntime.shared.auth
        let promptID = prompt["id"] as! String
        precondition(center.respond(id: id, promptID: UUID().uuidString, answer: values) == nil,
                     "unowned prompt answer was accepted")
        precondition(center.respond(id: id, promptID: promptID, answer: values) != nil)
        _ = center.respond(id: id, promptID: promptID, answer: values)
    }
    @MainActor static func checkParity() async throws {
        let center = V3HeadlessRuntime.shared.auth
        for method in ["sms", "voice"] {
            for fromTrusted in [false, true] {
                let id = UUID().uuidString
                center.sessions[id] = V3AuthCenter.Session()
                let handler = V3HeadlessAuthHandler(sessionID: id)
                let task = Task { @MainActor in
                    try await handler.verificationCode(for: fromTrusted ? .trustedDevice() :
                        .selectDeliveryMethod(preferredMode: .sms, phoneNumbers: []))
                }
                var prompt = await nextPrompt(id)
                if fromTrusted {
                    answer(id, prompt, ["action": "changeMethod"])
                    prompt = await nextPrompt(id, after: prompt["id"] as? String)
                }
                let options = prompt["options"] as! [[String: String]]
                precondition(options.contains { $0["id"] == method }, "empty-list phone fallback disappeared")
                answer(id, prompt, ["action": method])
                let result = try await task.value
                switch result {
                case .requestSMS(let phoneID): precondition(method == "sms" && phoneID.isEmpty)
                case .requestVoice(let phoneID): precondition(method == "voice" && phoneID.isEmpty)
                default: preconditionFailure("wrong empty-list delivery response")
                }
                precondition(center.sessions[id]?.prompt == nil)
                center.sessions.removeValue(forKey: id)
            }
        }
        for cancelTask in [false, true] {
            let id = UUID().uuidString
            center.sessions[id] = V3AuthCenter.Session()
            let handler = V3HeadlessAuthHandler(sessionID: id)
            let task = Task { @MainActor in
                try await handler.verificationCode(for: .trustedDevice())
            }
            let prompt = await nextPrompt(id)
            precondition(!(prompt["options"] as! [[String: String]]).contains { $0["id"] == "resend" })
            if cancelTask {
                task.cancel()
                do { _ = try await task.value; preconditionFailure("cancelled task returned") }
                catch is CancellationError {}
            } else {
                answer(id, prompt, ["action": "resend", "mode": "sms", "activeID": "forged"])
                guard case .cancel = try await task.value else {
                    preconditionFailure("forged trusted-device resend dispatched phone IO")
                }
            }
            precondition(center.sessions[id]?.prompt == nil)
            precondition(V3HeadlessRuntime.shared.prompts.pendingCount == 0)
            center.sessions.removeValue(forKey: id)
        }
        let phones = [TrustedPhoneNumber(id: "41", number: "fixture one"),
                      TrustedPhoneNumber(id: "82", number: "fixture two")]
        for method in ["sms", "voice"] {
            let id = UUID().uuidString
            center.sessions[id] = V3AuthCenter.Session()
            let handler = V3HeadlessAuthHandler(sessionID: id)
            let task = Task { @MainActor in
                try await handler.verificationCode(for: .selectDeliveryMethod(preferredMode: .sms, phoneNumbers: phones))
            }
            let methods = await nextPrompt(id)
            answer(id, methods, ["action": method])
            let selection = await nextPrompt(id, after: methods["id"] as? String)
            // A duplicate previous answer cannot select a phone or dispatch a second delivery.
            answer(id, methods, ["action": "trustedDevice"])
            precondition(center.sessions[id]?.prompt?["id"] as? String == selection["id"] as? String)
            answer(id, selection, ["action": "phone:82"])
            switch try await task.value {
            case .requestSMS(let phoneID): precondition(method == "sms" && phoneID == "82")
            case .requestVoice(let phoneID): precondition(method == "voice" && phoneID == "82")
            default: preconditionFailure("selected phone lost")
            }
            center.sessions.removeValue(forKey: id)
        }
        // Original UI checks character count only, without trimming or digit filtering.
        for submittedCode in ["12 456", "abcdef", " 12345"] {
            let id = UUID().uuidString
            center.sessions[id] = V3AuthCenter.Session()
            let handler = V3HeadlessAuthHandler(sessionID: id)
            let task = Task { @MainActor in
                try await handler.verificationCode(for: .trustedDevice())
            }
            let prompt = await nextPrompt(id)
            answer(id, prompt, ["action": "code", "code": submittedCode])
            guard case .verificationCode(let code) = try await task.value else {
                preconditionFailure("original six-character acceptance changed")
            }
            precondition(code == submittedCode, "code was normalized")
            center.sessions.removeValue(forKey: id)
        }
        // Rejected/expired codes stay in the same provider-owned channel. The
        // failure value is an IO double; network classification is tested separately.
        for method in ["sms", "voice"] {
            for action in ["resend", "cancel", "code"] {
                for failure in [nil, "wrong-code", "expired-code"] as [String?] {
                    let id = UUID().uuidString
                    center.sessions[id] = V3AuthCenter.Session()
                    let handler = V3HeadlessAuthHandler(sessionID: id)
                    let task = Task { @MainActor in
                        try await handler.verificationCode(for: method == "sms" ?
                            .sms(phoneNumbers: phones, selectedID: "82", error: failure) :
                            .voice(phoneNumbers: phones, selectedID: "82", error: failure))
                    }
                    var prompt = await nextPrompt(id)
                    precondition((prompt["options"] as! [[String: String]]).contains { $0["id"] == "resend" })
                    if action == "code" {
                        for invalid in ["", "12345", "1234567"] {
                            answer(id, prompt, ["action": "code", "code": invalid])
                            prompt = await nextPrompt(id, after: prompt["id"] as? String)
                        }
                    }
                    answer(id, prompt, ["action": action, "code": "123456"])
                    switch try await task.value {
                    case .requestSMS(let phoneID): precondition(action == "resend" && method == "sms" && phoneID == "82")
                    case .requestVoice(let phoneID): precondition(action == "resend" && method == "voice" && phoneID == "82")
                    case .verificationCode(let code): precondition(action == "code" && code == "123456")
                    case .cancel: precondition(action == "cancel")
                    default: preconditionFailure("wrong response after code prompt")
                    }
                    precondition(center.sessions[id]?.prompt == nil)
                    precondition(V3HeadlessRuntime.shared.prompts.pendingCount == 0)
                    center.sessions.removeValue(forKey: id)
                }
            }
        }
    }
    @MainActor static func main() async throws {
        let center = V3HeadlessRuntime.shared.auth
        for cancellation in ["none", "afterAnswer", "atTwoFactor"] {
            for _ in 0..<32 {
                let id = UUID().uuidString
                center.sessions[id] = V3AuthCenter.Session()
                let handler = V3HeadlessAuthHandler(sessionID: id)
                let operation = Task { @MainActor in
                    _ = try await handler.credentials()
                    precondition(center.sessions[id]?.prompt == nil,
                        "credentials prompt survived into the next verification callback")
                    precondition(V3HeadlessRuntime.shared.prompts.pendingCount == 0,
                        "credentials continuation survived its await/defer boundary")
                    return try await handler.verificationCode(for:
                        .selectDeliveryMethod(preferredMode: .trustedDevice, phoneNumbers: []))
                }
                let initial = await waitForPrompt(id, kind: "credentials")
                let first = initial["prompt"] as! [String: Any]
                let firstID = first["id"] as! String
                let answerReply = center.respond(id: id, promptID: firstID,
                    answer: ["appleID": "fixture@example.invalid", "password": "fixture"])
                precondition(answerReply != nil)
                if cancellation == "afterAnswer" {
                    operation.cancel()
                } else {
                    let next = await waitForPrompt(id, kind: "twoFactor")
                    let second = next["prompt"] as! [String: Any]
                    let secondID = second["id"] as! String
                    precondition(firstID != secondID)
                    precondition((next["revision"] as! Int) > (initial["revision"] as! Int))
                    precondition(V3AuthPollResponsePolicy.mayApply(currentSessionID: id,
                        replySessionID: id, cancellationInProgress: false,
                        currentRevision: answerReply?["revision"] as! Int,
                        replyRevision: next["revision"] as? Int,
                        currentPromptID: firstID, replyPromptID: secondID),
                        "host rejected authoritative next-prompt revision")
                    let delayedDuplicate = center.respond(id: id, promptID: firstID,
                        answer: ["appleID": "wrong@example.invalid", "password": "duplicate"])
                    precondition((delayedDuplicate?["prompt"] as? [String: Any])?["id"] as? String == secondID)
                    precondition(V3HeadlessRuntime.shared.prompts.pendingCount == 1,
                        "late credentials answer consumed the verification continuation")
                    if cancellation == "atTwoFactor" { operation.cancel() }
                    else {
                        _ = center.respond(id: id, promptID: secondID, answer: ["action": "trustedDevice"])
                    }
                }
                do {
                    let response = try await operation.value
                    precondition(cancellation == "none")
                    guard case .requestTrustedDevice = response else {
                        preconditionFailure("delivery choice did not return to the provider")
                    }
                } catch is CancellationError { precondition(cancellation != "none") }
                precondition(center.sessions[id]?.previousFailure == nil)
                precondition(center.sessions[id]?.prompt == nil)
                precondition(V3HeadlessRuntime.shared.prompts.pendingCount == 0)
                center.sessions.removeValue(forKey: id)
            }
        }
        try await checkParity()
        print("V3_CREDENTIALS_PROMPT_TRANSITION_PASS")
    }
}
