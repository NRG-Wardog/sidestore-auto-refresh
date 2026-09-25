"""Regression coverage for v3.0.3 guided 2FA delivery (issue #32).

Verifies the headless verification-code mapping end to end at the template
level: trusted-device, SMS, voice, phone-number selection, code submission,
and failure propagation. Upstream SideSign owns the actual Apple requests
(makeTwoFactorAuthRequest carries "Connection: close"); the v3 layer must
pass the selected delivery method through without cancelling or resending.
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


class V3TwoFactorTests(unittest.TestCase):
    def test_trusted_device_request(self):
        text = runtime()
        self.assertIn(".trustedDevice", text)
        self.assertIn("return .requestTrustedDevice", text)

    def test_sms_request_with_phone_id(self):
        text = runtime()
        self.assertIn("return method == \"sms\" ? .requestSMS(phoneID: phoneID) : .requestVoice(phoneID: phoneID)", text)
        self.assertIn('phoneID = String(chosen.dropFirst("phone:".count))', text)

    def test_voice_request_with_phone_id(self):
        text = runtime()
        self.assertIn(".requestVoice(phoneID: phoneID)", text)

    def test_phone_number_selection(self):
        text = runtime()
        # Phone options are offered alongside delivery methods.
        self.assertIn('"id": "phone:\\($0.id)"', text)
        self.assertIn('String(chosen.dropFirst("phone:".count))', text)

    def test_code_submission(self):
        text = runtime()
        self.assertIn("return .verificationCode(code)", text)
        self.assertIn('"code"', text)

    def test_delivery_failure_propagates_to_prompt(self):
        text = runtime()
        # SideSign returns a closed typed value. Provider text is not rendered.
        self.assertIn("request.verificationFailure", text)
        self.assertIn("failure?.userMessage", text)

    def test_no_automatic_resend_loop(self):
        text = runtime()
        fn = text[text.index("func verificationCode"):]
        fn = fn[:fn.index("func accountRepair")]
        for forbidden in ("while true", "Timer", "DispatchQueue.main.asyncAfter",
                          "Task.sleep", "resend"):
            self.assertNotIn(forbidden, fn)

    def test_no_secrets_in_diagnostics(self):
        text = runtime()
        fn = text[text.index("func verificationCode"):]
        fn = fn[:fn.index("func accountRepair")]
        logs = "\n".join(line for line in fn.splitlines() if "debugLog" in line)
        for forbidden in ("password", "appleID", "idmsToken", "DSID", "header",
                          "jsonPayload", "answer[\"code\"]"):
            self.assertNotIn(forbidden, logs)

    def test_no_phone_identifiers_in_diagnostics(self):
        # Phone IDs/numbers are user-specific metadata: never logged, even
        # though the phoneID value itself is still forwarded functionally to
        # SideSign (see test_sms_request_with_phone_id). Only the mode and the
        # phone count may appear in diagnostics.
        text = runtime()
        fn = text[text.index("func verificationCode"):]
        fn = fn[:fn.index("func accountRepair")]
        logs = "\n".join(line for line in fn.splitlines() if "debugLog" in line)
        self.assertNotIn("phone_id", logs)
        self.assertNotIn("phoneID=", logs)
        self.assertNotIn("selectedID)", logs)
        # The allowed shape is mode (+ count for selection), nothing else.
        for line in logs.splitlines():
            if "2FA_DELIVERY_SELECTED" in line and ("mode=sms" in line or "mode=voice" in line):
                self.assertIn("phone_count=", line)
            if "2FA_DELIVERY_REQUESTED" in line:
                self.assertNotIn("phone_count=", line)
                self.assertNotIn("phone", line)

    def test_delivery_diagnostics_present(self):
        text = runtime()
        self.assertIn("2FA_DELIVERY_REQUESTED", text)
        self.assertIn("2FA_CODE_SUBMITTED", text)
        self.assertIn("Requesting a verification code by SMS...", shell())

    # --- SideSign integration wiring (verifies the actual upstream call path) ---
    def test_verification_code_maps_to_sidesign_request(self):
        text = runtime()
        # The handler returns SideSign's TwoFactorResponse enum cases.
        # These directly map to SideSign.makeTwoFactorAuthRequest internally.
        self.assertIn("TwoFactorResponse", text)
        self.assertIn(".requestTrustedDevice", text)
        self.assertIn(".requestSMS(phoneID:", text)
        self.assertIn(".requestVoice(phoneID:", text)
        self.assertIn(".verificationCode(", text)

    def test_host_twofactor_ui_renders_delivery_methods(self):
        text = shell()
        # The host prompt renders the delivery method buttons and phone selection
        self.assertIn("Choose how Apple sends your verification code", text)
        self.assertIn("trustedDevice", text)
        self.assertIn("sms", text)
        self.assertIn("voice", text)
        self.assertIn("phoneID", text)

    def test_host_twofactor_ui_renders_code_submission(self):
        text = shell()
        self.assertIn("Change Verification Method", text)
        self.assertIn("Verify Code", text)
        self.assertIn("binding(\"code\")", text)

    def test_delivery_ack_and_typed_wrong_code_remain_in_separate_code_step(self):
        backend = runtime()
        host = shell()
        for acknowledgement in ("Verification request sent to your trusted devices.",
                                "Verification code requested by SMS.",
                                "Verification call requested."):
            self.assertIn(acknowledgement, backend)
        self.assertIn("The verification code was not accepted. Enter a new code and try again.",
                      (ROOT / "scripts/patch_sidesign_2fa_state.py").read_text(encoding="utf-8"))
        self.assertIn("case .enterVerificationCode", host)
        self.assertIn("accepted ? .completed : .enterVerificationCode",
                      (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        self.assertIn("auth.deliveryProgressMessage", host)

    def test_delivery_mode_selected_before_request(self):
        # The prompt includes the active mode in fields, which the handler
        # reads to know which delivery method the user chose.
        text = runtime()
        self.assertIn('"key": "mode"', text)
        self.assertIn('"value": mode.rawValue', text)


if __name__ == "__main__":
    unittest.main()
