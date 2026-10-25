"""Explicit sign-in parity, using generated operation methods and isolated IO.

No Apple requests occur. Swift execution is required on the macOS test gate.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import test_embedded_keychain as keychain
from test_v3_auth_recovery_audit import declaration, swift_source

ROOT = Path(__file__).resolve().parents[1]


def generated_operation():
    source_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE",
                                 "/workspace/shared/sidestore-review/SideStore")
    if not Path(source_root, ".git").exists():
        raise unittest.SkipTest("Pinned SideStore required; supplied in combined macOS CI")
    original = keychain.read_pinned_source(source_root,
        "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift")
    service = keychain.service_module.patch_sign_in_operation(original)
    return keychain.module.patch_sign_in_operation(service)


class ExplicitSignInTests(unittest.TestCase):
    def test_generated_flag_defaults_off_and_only_manual_mode_enables_it(self):
        source = generated_operation()
        self.assertIn("v3RequireInteractiveCredentials: Bool = false", source)
        self.assertIn("self.v3RequireInteractiveCredentials = v3RequireInteractiveCredentials", source)
        start = declaration(source, "private func startAuthentication(")
        self.assertIn("!self.v3RequireInteractiveCredentials && self.v3ReauthenticateAppleID == nil", start)
        execute = declaration(source, "override func execute(")
        self.assertIn("!self.v3RequireInteractiveCredentials", execute)
        # Neither route may clear an account or fall back after submitted auth fails.
        loop = declaration(source, "private func authenticationLoop(")
        self.assertNotIn("silentSignIn", loop)
        self.assertNotIn("clearSignInInfo", loop)
        self.assertIn('v3ClassifyAuthError(error)?.rawValue == "anisetteIdentityStateInvalid" { throw error }', loop)
        silent = declaration(source, "private func silentSignIn(")
        self.assertEqual(silent.count("phase.underlying is LCAnisettePairError { throw error }"), 2)
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        self.assertEqual(runtime.count("v3RequireInteractiveCredentials:"), 1)
        self.assertIn("v3RequireInteractiveCredentials: sessions[id]?.mode == .interactive", runtime)
        self.assertEqual(keychain.module.patch_sign_in_operation(source), source)
        self.assertEqual(keychain.service_module.patch_sign_in_operation(source), source)

    def test_production_credentials_retire_before_immediate_two_factor_prompt(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
        helpers = "\n\n".join(declaration(runtime, sig) for sig in (
            "enum V3PromptAnswerDisposition:", "final class V3PromptCenter:",
            "enum V3PromptResponseStatePolicy {", "func v3Prompt(",
            "enum V3TwoFactorPhoneSelectionPolicy {"))
        helpers += "\n\n" + "\n\n".join(declaration(primitives, sig) for sig in (
            "final class V3TerminalResponse:", "struct V3AuthStartCancellationRegistry {",
            "enum V3TwoFactorStep:", "enum V3TwoFactorRetryPolicy {",
            "enum V3AuthPollResponsePolicy {"))
        center_start = runtime.index("final class V3AuthCenter {")
        center_end = runtime.index("final class V3HeadlessAuthHandler:")
        center_source = runtime[center_start:center_end]
        center_methods = "\n\n".join(declaration(center_source, sig) for sig in (
            "func poll(id:", "func respond(id:"))
        center_methods += "\n\n" + declaration(runtime, "func promptsParked(")
        handler_source = runtime[center_end:]
        handler_methods = "\n\n".join(declaration(handler_source, sig) for sig in (
            "private func center()", "private func ask(", "func credentials()",
            "func verificationCode(", "private func chooseDeliveryMethod(",
            "private func enterVerificationCode("))
        fixture = (ROOT / "tests/fixtures/v3_credentials_prompt_transition_harness.swift").read_text()
        fixture = fixture.replace("__PRODUCTION_CENTER_METHODS__", center_methods)
        fixture = fixture.replace("__PRODUCTION_HANDLER_METHODS__", handler_methods)
        program = swift_source("import Foundation", helpers, fixture)
        self.assertNotIn("__PRODUCTION_", program)
        swift = shutil.which("swiftc")
        if not swift:
            self.skipTest("Swift unavailable; production prompt transition runs in macOS CI")
        with tempfile.TemporaryDirectory(prefix="v3-credentials-transition-") as temp:
            source_path = Path(temp) / "main.swift"
            binary = Path(temp) / "harness"
            source_path.write_text(program)
            result = subprocess.run([swift, "-swift-version", "5", "-parse-as-library",
                str(source_path), "-o", str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("V3_CREDENTIALS_PROMPT_TRANSITION_PASS", result.stdout)

    def test_generated_manual_call_order_and_default_saved_reuse(self):
        source = generated_operation()
        methods = "\n\n".join(declaration(source, sig) for sig in (
            "private func startAuthentication(", "private func silentSignIn(",
            "private func authenticationLoop(", "private func signIn(appleID:",
            "private func v3ValidateReauthenticationIdentity("))
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
        helpers = "\n\n".join(declaration(primitives, sig) for sig in (
            "enum V3ProvisioningResumeExecutionPolicy {", "enum V3TwoFactorRetryPolicy {",
            "enum V3AuthIdentityBindingPolicy {", "enum V3AuthReadStampPolicy {",
            "enum V3ProvisioningReauthenticationIdentityPolicy {"))
        diagnostics = (ROOT / "scripts/templates/combined_failure.swift").read_text()
        phase = diagnostics[diagnostics.index("struct V3AuthenticationPhaseError:"):
                            diagnostics.index("// V3_TYPED_ACCOUNT_DIAGNOSTICS_V1:")]
        doubles = keychain.DOUBLES.replace("\nfinal class Keychain {",
            "\nfinal class Keychain {\n    static var shared: Keychain!", 1)
        fixture = (ROOT / "tests/fixtures/v3_explicit_signin_harness.swift").read_text()
        fixture = fixture.replace("__PRODUCTION_OPERATION_METHODS__", methods)
        program = swift_source(doubles, keychain.TEMPLATE.read_text(),
            keychain.module.KEYCHAIN_ACCESS_ADAPTER, helpers, phase, fixture)
        # Assemble/validate extraction even without a native compiler.
        self.assertNotIn("__PRODUCTION_", program)
        swift = shutil.which("swiftc")
        if not swift:
            self.skipTest("Swift unavailable; generated operation runs in macOS CI")
        with tempfile.TemporaryDirectory(prefix="v3-explicit-signin-") as temp:
            source_path = Path(temp) / "main.swift"
            binary = Path(temp) / "harness"
            source_path.write_text(program)
            result = subprocess.run([swift, "-swift-version", "5", "-parse-as-library",
                str(source_path), "-o", str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in ("manual_saved_valid", "manual_saved_invalid", "manual_different_owner",
                             "manual_cancel", "manual_failed_then_cancel", "manual_repeated",
                             "default_token", "default_password", "default_expired_token_password",
                             "same_owner_reauthentication", "reauthentication_wrong_owner",
                             "reauthentication_wrong_dsid", "default_token_pair_blocked",
                             "default_password_pair_blocked", "manual_pair_blocked"):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario], capture_output=True,
                                            text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("V3_EXPLICIT_SIGNIN_PASS " + scenario, result.stdout)


if __name__ == "__main__":
    unittest.main()
