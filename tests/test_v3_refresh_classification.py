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
    def test_auth_domains_classified_by_domain(self):
        text = template()
        self.assertIn('"com.SideStore.Authentication"', text)
        self.assertIn('"ALTAppleAPIErrorDomain"', text)
        self.assertIn('"GrandSlamErrorDomain"', text)
        self.assertIn('"SideSignErrorDomain"', text)

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

    def test_underlying_domain_preserved_not_redacted(self):
        text = template()
        for domain in ("MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError",
                       "NSPOSIXErrorDomain", "NSURLErrorDomain", "Foundation",
                       "CoreData", "CFNetwork"):
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
        self.assertIn("default:\n                break", fn)

    def test_network_stage_exists_with_copy(self):
        text = template()
        self.assertIn("case network", text)
        self.assertIn("Network error during", text)

    def test_preserving_helper_intact(self):
        text = template()
        self.assertIn("public static func preserving(", text)
        self.assertIn("if let known = error as? CombinedFailure", text)


if __name__ == "__main__":
    unittest.main()
