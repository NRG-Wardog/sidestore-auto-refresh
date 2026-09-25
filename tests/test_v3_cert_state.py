"""Regression coverage for the #35 certificate-state ownership audit.

Two independent stores exist and must never be conflated:

1. SideStore CertificateManager.activeCertificate (+ Keychain p12): the
   certificate the refresh/signing pipeline actually uses.
2. LiveContainer LCCertificateData/Password/UpdateDate (app-group
   defaults): a manually imported p12 copy used ONLY by JIT-Less flows
   (ZSigner guest signing, TestJITLess validation). It only updates through
   the explicit host-owned certificate sync action.

A "Revoked" JIT-Less copy next to an "Active" SideStore certificate means
the copy predates the current certificate until proven otherwise; it can
never explain a refresh failure because the refresh pipeline never reads
the JIT-Less copy.

The v3 Health view therefore shows both sides plus a privacy-safe verdict
(certificate_state_match=yes/no/unknown). Full serials, keys, passwords,
and blobs never cross XPC; a public certificate fingerprint supports identity comparison.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


def shell():
    return SHELL.read_text(encoding="utf-8")


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def service_cert_state():
    text = runtime()
    fn = text[text.index("static func certificateState"):]
    fn = fn[:fn.index("\n    }\n") + 6]
    return fn


class ServiceCertFactsTests(unittest.TestCase):
    def test_health_reports_certificate_state(self):
        self.assertIn('"certificateState": certificateState()', runtime())

    def test_active_cert_is_the_pipeline_authority(self):
        fn = service_cert_state()
        self.assertIn("CertificateManager.shared.activeCertificate", fn)
        self.assertIn("DatabaseManager.shared.activeTeam()?.identifier", fn)

    def test_suffixes_only_no_secrets(self):
        fn = service_cert_state()
        self.assertIn("suffix(4)", fn)
        self.assertIn("certificateIdentitySHA256", fn)
        for forbidden in ("p12Data", "password", "privateKey", "kSecImport",
                          "signingCertificate", "LCCertificate"):
            self.assertNotIn(forbidden, fn)

    def test_missing_cert_is_explicit(self):
        fn = service_cert_state()
        self.assertIn('"active": false', fn)
        self.assertIn('"active": true', fn)


class HostComparisonTests(unittest.TestCase):
    def comparison(self):
        text = shell()
        fn = text[text.index("private func certComparison"):]
        fn = fn[:fn.index("\n    }\n") + 6]
        return fn

    def test_health_shows_certificates_section(self):
        text = shell()
        view = text[text.index("struct V3HealthView"):]
        view = view[:view.index("struct V3BackupsView")]
        self.assertIn('Section("Certificates")', view)
        self.assertIn("certRows", view)
        self.assertIn("Team Match", view)
        self.assertIn("Its revoked state alone does not cause a SideStore refresh failure", view)

    def test_lc_facts_come_from_lcutils(self):
        fn = self.comparison()
        self.assertIn("LCUtils.certificateData()", fn)
        self.assertIn("LCCertificateUpdateDate", fn)
        self.assertIn("getCertTeamId(withKeyData:", fn)
        self.assertIn("LCSharedUtils.certificatePassword()", fn)

    def test_verdict_vocabulary(self):
        fn = self.comparison()
        self.assertIn("unknown: SideStore has no active certificate", fn)
        self.assertIn("unknown: no comparable JIT-Less copy", fn)
        self.assertIn("yes: same team", fn)
        self.assertIn("no: different teams", fn)

    def test_no_key_material_handling(self):
        fn = self.comparison()
        for forbidden in ("p12Data", "privateKey", "kSecImport", "password=",
                          "SecIdentity", "base64"):
            self.assertNotIn(forbidden, fn)

    def test_stale_copy_guidance_present(self):
        text = shell()
        view = text[text.index("struct V3HealthView"):]
        view = view[:view.index("struct V3BackupsView")]
        self.assertIn("SideStore refresh uses its active certificate", view)
        self.assertIn("Sync JIT-Less Certificate from SideStore", view)
        self.assertIn("Its revoked state alone does not cause a SideStore refresh failure", view)


if __name__ == "__main__":
    unittest.main()
