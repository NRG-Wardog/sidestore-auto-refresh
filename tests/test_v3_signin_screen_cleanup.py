"""A sign-in prompt owns its compact error, copy action and cancellation panel."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
VIEW = SHELL[SHELL.index("struct V3SignInView:"):SHELL.index("struct V3CertificateRow")]
PROMPT = SHELL[SHELL.index("struct V3PromptSection:"):SHELL.index("final class V3AuthStore")]


class SignInScreenCleanupTests(unittest.TestCase):
    def test_unrelated_about_footer_is_removed_only_from_signin(self):
        self.assertNotIn('Section("About")', VIEW)
        self.assertNotIn("Sign-in runs entirely in this screen", SHELL)
        self.assertIn('Section("About")', SHELL)  # No unrelated P1 Home changes.

    def test_every_interactive_prompt_has_only_its_lower_panel(self):
        body = VIEW[:VIEW.index("@ViewBuilder private var accountContent")]
        self.assertIn('if shouldShowAccountSection {\n                Section("Apple ID")', body)
        self.assertIn('private var shouldShowAccountSection: Bool { auth.prompt == nil }', VIEW)
        self.assertNotIn('isProvisioningRecoveryPrompt', VIEW)
        self.assertEqual(body.count('Section("Apple ID")'), 1)
        self.assertNotIn('Section("Verification response")', body)
        self.assertIn('supplementalContent: AnyView(accountContent)', body)
        self.assertIn('if auth.prompt == nil || auth.isCancelling {', VIEW)
        for required in ("auth.isSignedIn", "auth.isCancelling", "auth.cancellationWasAttempted",
                         "auth.currentAttemptFailure.message", "auth.hasProvisioningProblem",
                         "auth.provisioningRecoveryRequiresReconciliation",
                         "auth.deliveryProgressMessage", "auth.twoFactorTransientStep"):
            self.assertIn(required, VIEW)

    def test_current_unknown_failure_has_visible_copy_action_and_collapsed_details(self):
        self.assertIn('previousFailureMessage: promptFailureMessage', VIEW)
        self.assertIn('previousFailureDetails: promptFailureDetails', VIEW)
        self.assertIn('V3AuthPromptFailurePolicy.isVisible(auth.previousFailure,', VIEW)
        self.assertIn('visiblePromptFailure.map { V3AuthStore.failureDetails(from: $0) }', VIEW)
        failure = PROMPT[PROMPT.index('if !previousFailureMessage.isEmpty'):PROMPT.index('if !message.isEmpty')]
        self.assertIn('Text(previousFailureMessage)', failure)
        self.assertIn('DisclosureGroup("Technical details") {', failure)
        self.assertIn('Text(previousFailureDetails)', failure)
        self.assertIn('Button("Copy Details") { UIPasteboard.general.string = previousFailureDetails }', failure)
        # The copy action is a sibling of the disclosure, not hidden inside it.
        self.assertIn('.accessibilityIdentifier("signin.prompt.previous-error-details")\n                    Button("Copy Details")', failure)
        self.assertIn('.accessibilityIdentifier("signin.prompt.copy-details")', failure)
        self.assertIn('.frame(minHeight: 44)', failure)
        self.assertNotIn('UIPasteboard.general.string = fields', PROMPT)
        self.assertNotIn('String(describing: prompt)', PROMPT)

    def test_session_owned_cancel_stays_reachable_without_duplicate_prompt_cancel(self):
        self.assertIn('var onCancel: (() -> Void)? = nil', PROMPT)
        self.assertIn('onCancel: { auth.cancel() }', VIEW)
        self.assertIn('cancellationDisabled: auth.isCancelling', VIEW)
        self.assertIn('Button(cancellationTitle, role: .cancel) { onCancel() }', PROMPT)
        self.assertIn('.disabled(cancellationDisabled)', PROMPT)
        self.assertIn('ForEach(options.filter { onCancel == nil || $0["id"] != "cancel" }', PROMPT)
        self.assertIn('if onCancel == nil && options.contains(where:', PROMPT)
        self.assertIn('if onCancel == nil {\n            Button("Cancel Sign In"', PROMPT)
        operation = SHELL[SHELL.index('struct V3OperationSheet:'):SHELL.index('struct V3PromptSection:')]
        self.assertNotIn('onCancel:', operation)

    def test_error_diagnostics_are_collapsed_but_copy_and_choices_remain_available(self):
        details = PROMPT[PROMPT.index('if let technical = fieldDefs.first'):PROMPT.index('if isMulti {')]
        self.assertIn('DisclosureGroup("Technical details") {', details)
        self.assertIn('Text(value)', details)
        self.assertIn('.textSelection(.enabled)', details)
        self.assertIn('Button(copiedDetails ? "Copied" : "Copy Details")', details)
        self.assertIn('UIPasteboard.general.string = value', details)
        self.assertIn('ForEach(options.filter', PROMPT)
        self.assertIn('V3PromptSection(prompt: prompt', VIEW)

    def test_terminal_and_cancellation_recovery_remain_available(self):
        for required in ('auth.cancel()', 'auth.retryProvisioning()', 'finishProvisioningLater()',
                         'auth.reloadAuthoritativeAccountStatus()', 'auth.checkProvisioningStorage()',
                         'auth.reauthenticateProvisioning()', 'auth.currentAttemptFailure.message',
                         'auth.provisioningMessage', 'V3AuthFailureDiagnosticsPolicy.shouldShowTerminalDetails'):
            self.assertIn(required, VIEW)
        self.assertIn('auth.answer(promptID: prompt["id"] as? String ?? "", answer: answer)', VIEW)
        self.assertIn('.onDisappear {\n            auth.cancel()\n            auth.clearPreviousFailure()', VIEW)


if __name__ == "__main__":
    unittest.main()
