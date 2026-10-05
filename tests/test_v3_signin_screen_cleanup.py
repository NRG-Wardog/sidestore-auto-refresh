"""Keep provisioning recovery focused without hiding real account/recovery facts."""
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
        self.assertIn('Section("About")', SHELL)  # Home attribution stays.

    def test_only_redundant_provisioning_prompt_card_is_hidden(self):
        self.assertIn('if shouldShowAccountSection {\n                Section("Apple ID")', VIEW)
        policy = VIEW[VIEW.index("private var isProvisioningRecoveryPrompt"):VIEW.index("private var statusText")]
        self.assertIn('auth.state == "awaitingPrompt" &&', policy)
        self.assertIn('auth.prompt?["kind"] as? String == "provisioningError"', policy)
        self.assertIn('!isProvisioningRecoveryPrompt ||', policy)
        for required in ("auth.isSignedIn", "auth.isCancelling", "auth.cancellationWasAttempted",
                         "!auth.cancellationConfirmed", "!auth.message.isEmpty",
                         "!auth.currentAttemptFailure.message.isEmpty", "auth.hasProvisioningProblem",
                         "auth.provisioningRecoveryRequiresReconciliation",
                         "!auth.deliveryProgressMessage.isEmpty", "auth.twoFactorTransientStep != nil"):
            self.assertIn(required, policy)
        self.assertIn('if !isProvisioningRecoveryPrompt || auth.isCancelling {', VIEW)

    def test_error_diagnostics_are_collapsed_but_copy_and_choices_remain_available(self):
        details = PROMPT[PROMPT.index('if let technical = fieldDefs.first'):PROMPT.index('if isMulti {')]
        self.assertIn('DisclosureGroup("Technical details") {', details)
        self.assertIn('Text(value)', details)
        self.assertIn('.textSelection(.enabled)', details)
        self.assertIn('Button(copiedDetails ? "Copied" : "Copy Details")', details)
        self.assertIn('UIPasteboard.general.string = value', details)
        self.assertIn('ForEach(options, id:', PROMPT)
        self.assertIn('V3PromptSection(prompt: prompt', VIEW)

    def test_terminal_and_cancellation_recovery_remain_available(self):
        for required in ('auth.cancel()', 'auth.retryProvisioning()', 'finishProvisioningLater()',
                         'auth.reloadAuthoritativeAccountStatus()', 'auth.checkProvisioningStorage()',
                         'auth.reauthenticateProvisioning()', 'auth.currentAttemptFailure.message',
                         'auth.provisioningMessage', 'V3AuthFailureDiagnosticsPolicy.shouldShowTerminalDetails'):
            self.assertIn(required, VIEW)


if __name__ == "__main__":
    unittest.main()
