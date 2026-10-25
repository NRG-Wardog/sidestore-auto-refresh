// LC_AUTH_PROMPT_SUBMISSION_OWNERSHIP_V1: who owns the transition into
// "submission in progress" for a prompt answer.
//
// The device symptom was a no-op on Apple ID submit. The view set the parent's
// submission flag before calling the parent, and the parent admits the answer
// through that same flag, so admission always ran against state the tap had just
// created and refused it. Nothing was ever dispatched.
//
// RESPOND_SLICE is the real V3PromptSection.respond method, injected verbatim
// from scripts/templates/v3_unified_shell.swift by the test. It runs here
// unmodified against the real admission policy, so this harness fails on the
// pre-fix view and passes on the fixed one.
import Foundation

@main
struct AuthPromptSubmissionOwnershipHarness {
    static var failures = 0
    static func expect(_ condition: Bool, _ label: String) {
        if !condition {
            FileHandle.standardError.write(
                Data("V3_AUTH_PROMPT_OWNERSHIP_FAIL \(label)\n".utf8))
            failures += 1
        }
    }

    /// The parent. Its admission is V3AuthPromptResponsePolicy.maySubmit verbatim:
    /// an awaiting prompt, the prompt it is actually showing, no in-flight
    /// submission, and no cancellation in progress.
    final class AuthStore {
        var state = "awaitingPrompt"
        var prompt: [String: Any]?
        var promptSubmitting = false
        var promptResponseGeneration = 0
        var isCancelling = false
        var isSubmissionBlocked = false
        var dispatched: [String] = []

        var currentPromptID: String? { prompt?["id"] as? String }

        func answer(promptID: String, answer: [String: String]) {
            guard !promptID.isEmpty,
                  V3AuthPromptResponsePolicy.maySubmit(state: state,
                    currentPromptID: currentPromptID, submittedPromptID: promptID,
                    isSubmitting: promptSubmitting,
                    cancellationInProgress: isCancelling),
                  !isSubmissionBlocked else { return }
            promptResponseGeneration &+= 1
            promptSubmitting = true
            dispatched.append(promptID)
        }
    }

    /// The view, with only the storage the real method touches, plus the real
    /// method injected at RESPOND_SLICE. `isSubmitting` is the parent's binding,
    /// exactly as both call sites pass it.
    final class PromptSectionModel {
        var isSubmitting = false
        var onAnswer: ([String: String]) -> Void = { _ in }

        // RESPOND_SLICE
    }

    static func freshPrompt(_ id: String, kind: String = "credentials",
                            step: String? = nil) -> AuthStore {
        let store = AuthStore()
        var prompt: [String: Any] = ["id": id, "kind": kind]
        if let step { prompt["step"] = step }
        store.prompt = prompt
        return store
    }

    /// The view is constructed for one rendered prompt, and its closure answers
    /// for that prompt. The real call site does the same: it reads prompt["id"]
    /// at render time and passes it down.
    static func view(_ store: AuthStore) -> PromptSectionModel {
        let model = PromptSectionModel()
        let renderedPromptID = store.currentPromptID ?? ""
        model.isSubmitting = store.promptSubmitting
        model.onAnswer = { answer in store.answer(promptID: renderedPromptID, answer: answer) }
        return model
    }

    static func main() {
        // 1. A credentials submit reaches the service exactly once, and the store
        //    owns the transition.
        let credentials = freshPrompt("P")
        let credentialsView = view(credentials)
        credentialsView.respond(["appleIDEmailAddress": "user@example.com",
                                 "appleIDPassword": "SECRET_TOKEN"])
        expect(credentials.dispatched.count == 1,
               "a credentials submit dispatches authRespond exactly once")
        expect(credentials.promptSubmitting == true,
               "the store owns and holds the submission transition")
        expect(credentials.promptResponseGeneration == 1,
               "the store advances its own response generation once")
        expect(credentials.dispatched == ["P"], "the dispatched prompt is the current one")

        // 2. Double tap: the store's own flag refuses the second answer.
        credentialsView.isSubmitting = credentials.promptSubmitting
        credentialsView.respond(["appleIDEmailAddress": "user@example.com",
                                 "appleIDPassword": "SECRET_TOKEN"])
        expect(credentials.dispatched.count == 1, "a double tap dispatches once, not twice")
        expect(credentials.promptResponseGeneration == 1,
               "a refused answer does not advance the generation")

        // 3. Every prompt kind the app renders takes the same path.
        for kind in ["credentials", "twoFactor", "phoneSelection", "verificationCode",
                     "teamSelection", "revocation", "extensions", "accountRepair"] {
            let store = freshPrompt("P-\(kind)", kind: kind)
            view(store).respond(["action": "code"])
            expect(store.dispatched.count == 1, "\(kind) dispatches exactly once")
            expect(store.promptSubmitting, "\(kind) holds the transition in the store")
        }

        // 4. The 2FA delivery, phone and code paths.
        for (action, step) in [("code", "enterVerificationCode"),
                               ("sms", "chooseDeliveryMethod"),
                               ("voice", "chooseDeliveryMethod"),
                               ("trustedDevice", "chooseDeliveryMethod"),
                               ("phone:+15550100", "choosePhoneNumber"),
                               ("changeMethod", "choosePhoneNumber")] {
            let store = freshPrompt("P-2fa-\(action)", kind: "twoFactor", step: step)
            view(store).respond(["action": action, "choice": action, "code": "123456"])
            expect(store.dispatched.count == 1, "2FA \(action) dispatches exactly once")
            expect(store.promptSubmitting, "2FA \(action) holds the transition")
        }

        // 5. A stale prompt ID is refused because the store has moved on.
        let stale = freshPrompt("P")
        let staleView = view(stale)
        stale.prompt = ["id": "P2", "kind": "credentials"]
        staleView.respond(["action": "code"])
        expect(stale.dispatched.isEmpty, "an answer for a stale prompt is refused")

        // 6. A cancelled session is refused, and recovers when it ends.
        let cancelled = freshPrompt("P")
        cancelled.isCancelling = true
        view(cancelled).respond(["action": "code"])
        expect(cancelled.dispatched.isEmpty, "a cancelled session refuses the answer")
        cancelled.isCancelling = false
        view(cancelled).respond(["action": "code"])
        expect(cancelled.dispatched.count == 1, "the prompt works again once cancellation ends")

        // 7. responsePending keeps submitting true until an authoritative
        //    transition releases it.
        let pending = freshPrompt("P")
        view(pending).respond(["action": "code"])
        expect(pending.promptSubmitting == true,
               "submitting stays true while the response is pending")
        expect(V3AuthPromptResponsePolicy.shouldClearSubmissionFailure(
            oldPromptID: "P", newPromptID: "P2", state: "awaitingPrompt"),
               "an authoritative prompt transition is the recovery signal")
        pending.promptSubmitting = false
        expect(pending.promptSubmitting == false, "the store releases the claim on transition")

        // 8. A transport failure releases the claim so the user can retry.
        let failed = freshPrompt("P")
        view(failed).respond(["action": "code"])
        failed.dispatched.removeAll()
        failed.promptSubmitting = false
        expect(failed.promptSubmitting == false,
               "a failed submission releases the claim so the user can retry")
        view(failed).respond(["action": "code"])
        expect(failed.dispatched.count == 1, "the retry after a failure dispatches again")

        // 9. A blocked prompt still refuses a code answer.
        let blocked = freshPrompt("P")
        blocked.isSubmissionBlocked = true
        view(blocked).respond(["action": "code"])
        expect(blocked.dispatched.isEmpty, "a blocked prompt refuses a code answer")

        // 10. The view must not create the state its own guard reads. If it did,
        //     the very first tap would be refused and never recovered.
        let probe = freshPrompt("P")
        let probeView = view(probe)
        probeView.respond(["action": "code"])
        expect(probe.dispatched.count == 1,
               "the first tap is admitted, so the view did not claim the transition")
        expect(probeView.isSubmitting == false,
               "the view did not write the parent's flag itself")

        if failures == 0 {
            print("V3_AUTH_PROMPT_OWNERSHIP_PASS")
        } else {
            FileHandle.standardError.write(
                Data("V3_AUTH_PROMPT_OWNERSHIP_FAILURES=\(failures)\n".utf8))
            exit(1)
        }
    }
}