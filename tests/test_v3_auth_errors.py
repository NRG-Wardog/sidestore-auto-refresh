"""Regression coverage for v3.0.3 authentication error reporting (issue #31).

The v3 headless handler must preserve the real typed authentication failure
instead of inferring "bad password" from a repeated credentials prompt.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def shell():
    return SHELL.read_text(encoding="utf-8")


class V3AuthErrorTests(unittest.TestCase):
    def test_handle_sign_in_result_is_implemented(self):
        text = runtime()
        self.assertIn("func handleSignInResult", text)
        # An empty no-op body would discard the real failure.
        self.assertNotIn(
            "func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {}",
            text)

    def test_failure_preserved_in_session(self):
        text = runtime()
        self.assertIn("previousFailure", text)
        self.assertIn("failure.wire", text)
        self.assertIn("CombinedFailure.capture", text)

    def test_success_clears_previous_failure(self):
        text = runtime()
        self.assertIn("previousFailure = nil", text)

    def test_cancellation_does_not_store_failure(self):
        text = runtime()
        self.assertIn("v3ClassifyAuthError", text)
        # Cancellation-class results clear state instead of displaying it.
        self.assertIn("error is CancellationError", text)
        self.assertIn("userCancelled", text)

    def test_typed_classification_covers_all_kinds(self):
        text = runtime()
        for kind in ("invalidCredentials", "appSpecificPasswordRequired", "invalidCode", "rateLimited",
                     "serviceUnavailable", "anisette", "network",
                     "accountRepairRequired", "unknown"):
            self.assertIn(kind, text)
        # Classification is type-based, not string guessing on server text.
        self.assertIn("as? DeveloperPortalError", text)
        self.assertIn("as? ServerError", text)
        self.assertIn("NSURLErrorDomain", text)

    def test_grandslam_rate_limit_codes_classified(self):
        text = runtime()
        for code in ("-22411", "-20102", "-21668"):
            self.assertIn(code, text)

    def test_attempt_markers_are_safe(self):
        text = runtime()
        self.assertIn("[V3_AUTH] ATTEMPT_FAILED", text)
        # Markers carry kinds/stages/codes, never secrets.
        for forbidden in ("password", "appleID", "verificationCode", "authToken",
                          "dsid", "DSID", "idmsToken", "header", "jsonPayload"):
            segment = text[text.index("[V3_AUTH] ATTEMPT_FAILED") - 200:
                           text.index("[V3_AUTH] ATTEMPT_FAILED") + 300]
            self.assertNotIn(forbidden, segment)

    def test_no_attempts_implies_password_assumption(self):
        text = shell()
        self.assertNotIn("That was not accepted. Check the Apple ID and password", text)
        self.assertNotIn("auth.attempts > 1", text)

    def test_host_renders_structured_failure(self):
        text = shell()
        self.assertIn("previousFailure", text)
        self.assertIn("V3AuthStore.failureMessage", text)
        self.assertIn("V3AuthStore.failureDetails", text)

    def test_top_level_previous_failure_is_preserved_until_credentials_submission(self):
        host = shell()
        self.assertIn("V3AuthPromptFailurePolicy.applying(reply: reply, current: previousFailure)", host)
        self.assertIn("V3AuthPromptFailurePolicy.isVisible(auth.previousFailure", host)
        self.assertNotIn('prompt["previousFailure"]', host)
        self.assertIn("clearingAfterSubmission", host)
        self.assertIn("clearPreviousFailure()", host)

    def test_authentication_and_post_auth_provisioning_have_distinct_terminal_states(self):
        runtime_text = runtime()
        host = shell()
        self.assertIn('"authenticatedProvisioningIncomplete"', runtime_text)
        self.assertIn("V3AuthTerminalPolicy.resolve", runtime_text)
        self.assertIn("Signed in successfully, but provisioning could not be completed.", runtime_text)
        self.assertIn('case "authenticatedProvisioningIncomplete"', host)
        self.assertNotIn('message = "Sign-in failed."', host)

    def test_sign_in_reopening_reconciles_authoritative_side_store_snapshot(self):
        host = shell()
        sign_in = host[host.index("final class V3AuthStore"):host.index("struct V3SignInLink")]
        self.assertIn("func reconcile() async", sign_in)
        self.assertIn('request(operation: "snapshot")', sign_in)
        self.assertIn('snapshot["account"] as? String', sign_in)
        self.assertIn(".task { await auth.reconcile() }", host)
        self.assertNotIn(".task { auth.begin() }", host)

    def test_password_guidance_only_for_proven_credentials(self):
        text = shell()
        self.assertIn('"invalidCredentials"', text)
        self.assertIn("Check them and try again", text)
        self.assertIn('case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."', text)
        self.assertNotIn("appSpecificPasswordRequired: return \"Apple did not accept the Apple ID or password", text)

    def test_no_sensitive_fields_in_prompt(self):
        text = runtime()
        start = text.index("func v3Prompt")
        end = text.index("\n}\n", start) + 3
        prompt_fn = text[start:end]
        for forbidden in ("password", "token", "dsid", "DSID", "header",
                          "jsonPayload", "pairing", "privateKey"):
            self.assertNotIn(forbidden, prompt_fn)

    def test_no_automatic_retry(self):
        text = runtime()
        # The authentication loop belongs to upstream SignInOperation;
        # the v3 handler must not resubmit credentials itself.
        auth_section = text[:text.index("final class V3HeadlessPipelineHandler")]
        self.assertNotIn("while true", auth_section)


if __name__ == "__main__":
    unittest.main()
