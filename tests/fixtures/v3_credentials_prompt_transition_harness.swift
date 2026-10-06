// Provider request/response types are IO doubles. Prompt creation, answer
// admission, continuation ownership, cleanup, and host revision rules below are
// extracted verbatim from production; this does not exercise SRP or Apple IO.
func debugLog(_ message: String) {}
struct TrustedPhoneNumber { let id: String; let number: String }
enum TwoFactorDeliveryMode: String { case trustedDevice, sms, voice }
enum TwoFactorVerificationFailure {
    case unknown
    var userMessage: String { "fixture" }
}
enum TwoFactorRequest {
    case selectDeliveryMethod(preferredMode: TwoFactorDeliveryMode, phoneNumbers: [TrustedPhoneNumber])
    case trustedDevice
    case sms(phoneNumbers: [TrustedPhoneNumber], selectedID: String, error: String?)
    case voice(phoneNumbers: [TrustedPhoneNumber], selectedID: String, error: String?)
    var verificationFailure: TwoFactorVerificationFailure? { nil }
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
        print("V3_CREDENTIALS_PROMPT_TRANSITION_PASS")
    }
}
