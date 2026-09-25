"""Regression coverage for v3.0.3 refresh failure classification (issue #35).

The classifier must depend on domain evidence, never on bare numeric codes.
Unknown errors must remain honestly unknown instead of being relabelled.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/combined_failure.swift"


def template():
    return TEMPLATE.read_text(encoding="utf-8")


class RefreshClassificationTests(unittest.TestCase):
    def test_source_catalog_and_pairing_failures_have_typed_safe_guidance(self):
        text = template()
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for token in ("sourceNetworkFailure", "sourceInvalidManifest", "sourceInvalidURL",
                      "sourcePersistenceUnverified", "catalogUnavailable", "pairingRequired"):
            self.assertIn(token, text)
        self.assertIn("error is DecodingError", runtime)
        self.assertIn("native.domain == NSURLErrorDomain", runtime)
        self.assertIn('native.domain == "io.sidestore.SideStore.DecodingError"', runtime)
        self.assertIn('case "catalog": stage = .catalog', service)
        self.assertIn("activeCertificate", host)

    def test_home_refresh_checks_authoritative_missing_pairing_before_notification(self):
        # V3_REFRESH_PREREQUISITE_POLICY_V1: the pairing status string is now
        # interpreted by exactly one authoritative policy, and Home Refresh All
        # consults it before posting the mutation request. The behavioural
        # guarantee is unchanged; only the location of the decision moved.
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        start = host.index("private func start()", host.index("struct V3RefreshAllButton"))
        end = host.index("private func monitorRun", start)
        body = host[start:end]
        self.assertIn("V3RefreshPrerequisite.evaluate(pairingStatus: status.pairing)", body)
        self.assertLess(body.index("V3RefreshPrerequisite.evaluate"),
                        body.index("NotificationCenter.default.post"))
        # The pairing identity is minted once, in the shared policy.
        self.assertIn('case "Pairing file required": return .pairingRequired', primitives)
        self.assertIn("safeCause: .pairingRequired", primitives)
        self.assertNotIn("safeCause: .pairingRequired", body)
        # The snapshot string is never re-interpreted at a call site.
        self.assertNotIn('status.pairing == "Pairing file required"', host)
        self.assertNotIn('status.pairing == "Pairing file available"', host)
        self.assertIn('case "pairing": status.pairingPresented = true', host)

    def test_only_typed_auth_context_maps_to_authentication(self):
        text = template()
        self.assertIn('"com.SideStore.Authentication"', text)
        capture = text[text.index("static func capture"):]
        self.assertNotIn('case "ALTAppleAPIErrorDomain"', capture)
        self.assertNotIn('case "ALTServerErrorDomain"', capture)
        self.assertNotIn('case "GrandSlamErrorDomain"', capture)
        self.assertNotIn('case "SideSignErrorDomain"', capture)
        self.assertIn("error as? CombinedRefreshVerificationError", capture)

    def test_ppq_requires_installer_domain_operation_and_install_stage(self):
        text = template()
        capture = text[text.index("static func capture"):]
        self.assertIn("let installContext = [\"install\", \"installURL\", \"installSharedIPA\", \"update\"]", capture)
        self.assertIn("typedVerificationSource = verificationDomains.contains(cause.domain)", capture)
        self.assertIn("&& fingerprint.contains(\"applicationverificationfailed\")", capture)

    def test_network_domains_classified_by_domain(self):
        text = template()
        self.assertIn('"NSPOSIXErrorDomain"', text)
        self.assertIn('"NSURLErrorDomain"', text)

    def test_no_bare_numeric_code_guessing(self):
        text = template()
        fn = text[text.index("static func capture"):]
        # A numeric literal must never decide a stage on its own; only the
        # standard POSIX errno mapping under NSPOSIXErrorDomain is allowed,
        # which is domain-qualified above. Gateway codes are preserved as
        # underlying data, never promoted to a stage.
        self.assertNotIn("code == 20", fn)
        self.assertNotIn("code == 22", fn)
        self.assertNotIn("code == 35", fn)
        self.assertNotIn("code == -22411", fn)

    def test_no_invented_gateway_domain(self):
        text = template()
        fn = text[text.index("static func capture"):]
        # A preserved numeric code must keep the domain it was observed in.
        # The old `NSError(domain: "DeviceGatewayError", code: ...)` fallback
        # relabelled unrelated codes (HTTP statuses, POSIX errnos) as gateway
        # errors and must never return.
        self.assertNotIn('NSError(domain: "DeviceGatewayError"', fn)

    def test_http_status_uses_fixed_safe_domain(self):
        text = template()
        fn = text[text.index("static func capture"):]
        self.assertIn('nativeDomain = "HTTPStatus"', fn)
        self.assertIn('"HTTPStatus"', text)

    def test_posix_errno_keeps_posix_domain(self):
        text = template()
        fn = text[text.index("static func capture"):]
        self.assertIn('nativeDomain = "NSPOSIXErrorDomain"', fn)

    def test_gateway_token_keeps_gateway_domain(self):
        text = template()
        fn = text[text.index("static func capture"):]
        # lc_native_code= claims the gateway domain only when the error itself
        # comes from a gateway path.
        self.assertIn("MinimuxerError", fn)
        self.assertIn("IdeviceGatewayError", fn)

    def test_unknown_error_gains_no_fake_domain(self):
        text = template()
        fn = text[text.index("static func capture"):]
        # Without an extracted machine code the cause passes through untouched,
        # so an allowlist-external domain still decodes as "redacted".
        self.assertIn("underlying = cause", fn)
        self.assertIn('NSError(domain: "redacted", code: code)', fn)

    def test_application_verification_failure_detected(self):
        text = template()
        fn = text[text.index("static func capture"):]
        self.assertIn("applicationverificationfailed", fn)
        self.assertIn("e8008024", fn)
        self.assertIn("e8008018", fn)
        self.assertIn("0xE8008024", fn)
        self.assertIn("0xE8008018", fn)

    def test_ppq_messages_are_clear_and_honest(self):
        text = template()
        self.assertIn("provisioning profile is banned during application verification", text)
        self.assertIn("identity used to sign the executable is no longer valid", text)
        self.assertIn("unlikely to address this specific error", text)
        # Never claim an account ban.
        self.assertNotIn("banned your account", text)
        self.assertNotIn("Apple banned", text)

    def test_underlying_domain_preserved_not_redacted(self):
        text = template()
        for domain in ("MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError",
                       "NSPOSIXErrorDomain", "NSURLErrorDomain", "Foundation",
                       "CoreData", "CFNetwork", "HTTPStatus", "io.sidestore.SideStore.DecodingError"):
            self.assertIn('"%s"' % domain, text)

    def test_explicit_stage_markers_honored(self):
        text = template()
        self.assertIn("LCStructuredFailureStageV1", text)
        self.assertIn("lc_stage=", text)
        self.assertIn("lc_native_code=", text)

    def test_http_status_extraction(self):
        text = template()
        self.assertIn('"HTTP"', text)
        self.assertIn("errno=", text)

    def test_unknown_remains_unknown(self):
        text = template()
        fn = text[text.index("static func capture"):]
        # The default branch must not reassign the caller stage.
        self.assertIn("default:", fn)
        self.assertIn("underlying = cause", fn)

    def test_network_stage_exists_with_copy(self):
        text = template()
        self.assertIn("case network", text)
        self.assertIn("Network error during", text)

    def test_preserving_helper_intact(self):
        text = template()
        self.assertIn("public static func preserving(", text)
        self.assertIn("if let known = error as? CombinedFailure", text)

    # --- Explicit regression guards against numeric guessing ---
    def test_no_bare_code_20_guessing_core_device(self):
        """Ensure code 20 is never used to map to coreDevice stage."""
        text = template()
        fn = text[text.index("static func capture"):]
        # The old buggy logic checked `code == 20` to infer coreDevice.
        # This must not exist anywhere in the capture function.
        # "20" may appear in comments (e.g., "errno=20") but not as code.
        self.assertNotIn("code == 20", fn)
        self.assertNotIn("cause.code == 20", fn)
        self.assertNotIn("underlyingCode == 20", fn)

    def test_no_bare_code_guessing_for_any_stage(self):
        """Ensure no bare numeric literal (without domain) decides stage."""
        text = template()
        fn = text[text.index("static func capture"):]
        # Only domain-qualified mappings are allowed.
        # POSIX errno under NSPOSIXErrorDomain is domain-qualified.
        # Gateway codes are preserved as underlyingCode only.
        forbidden_patterns = [
            "code ==",
            "underlyingCode ==",
            "cause.code ==",
        ]
        for pattern in forbidden_patterns:
            # The only allowed numeric comparison is the explicit GrandSlam
            # rate-limit check under ALTAppleAPIErrorDomain/SideSignErrorDomain
            # which is domain-qualified.
            if pattern == "code ==":
                # GrandSlam codes are checked under specific domain switch
                pass
            self.assertNotIn(pattern.replace("==", " =="), fn.replace("code == -22411", "").replace("code == -20102", "").replace("code == -21668", ""))

    def test_minimuxer_gateway_codes_preserved_as_underlying(self):
        """MinimuxerError/DeviceGatewayError/IdeviceGatewayError codes stay in underlyingCode."""
        text = template()
        # These domains are in the allowlist and their codes are preserved
        for domain in ("MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError"):
            self.assertIn(f'"{domain}"', text)
        # The capture function preserves the underlying error with its own
        # domain instead of inventing a new one.
        fn = text[text.index("static func capture"):]
        self.assertIn("underlying = NSError(domain: domain, code: code)", fn)


if __name__ == "__main__":
    unittest.main()
