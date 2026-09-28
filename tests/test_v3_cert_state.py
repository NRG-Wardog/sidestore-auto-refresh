"""Certificate ownership and canonical LiveContainer JIT-Less routing tests."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"


class JITLessOwnershipTests(unittest.TestCase):
    def test_side_store_exposes_only_public_active_certificate_facts(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        start = runtime.index("static func certificateState() async")
        end = runtime.index("static func accountExport(", start)
        state = runtime[start:end]
        for token in ("CertificateManager.shared.activeCertificate", "certificateIdentitySHA256",
                      "OCSPValidator.validate", 'state["validation"]'):
            self.assertIn(token, state)
        for forbidden in ("signingCertificatePassword", "SecItemCopyMatching", "privateKey", "p12"):
            self.assertNotIn(forbidden, state)

    def test_health_reads_only_the_livecontainer_copy_and_uses_canonical_actions(self):
        shell = SHELL.read_text(encoding="utf-8")
        view = shell[shell.index("struct V3HealthView"):shell.index("struct V3BackupsView")]
        self.assertIn("V3JITLessStatusReader.read", view)
        self.assertIn("livecontainer://jitless-setup", view)
        self.assertIn("livecontainer://jitless-diagnose", view)
        self.assertIn("Open Certificates", view)
        for forbidden in ("SecItemCopyMatching", "signingCertificatePassword", "CFPreferencesSetMultiple",
                          "writeJITLessCertificate", "syncJITLessCertificate"):
            self.assertNotIn(forbidden, view)

    def test_health_reuses_parsed_jitless_certificate_facts(self):
        shell = SHELL.read_text(encoding="utf-8")
        reader = shell[shell.index("private enum V3JITLessStatusReader"):shell.index("struct V3HealthView")]
        health = shell[shell.index("struct V3HealthView"):shell.index("struct V3BackupsView")]
        comparison = health[health.index("private func certComparison("):]
        self.assertEqual(reader.count("parse(bytes, password:"), 1)
        self.assertIn("certificateFacts: facts", reader)
        self.assertIn("localFacts: readiness.certificateFacts", health)
        self.assertNotIn("V3JITLessStatusReader.parse", comparison)

    def test_custom_certificate_sync_engine_is_removed(self):
        shell = SHELL.read_text(encoding="utf-8")
        primitives = PRIMITIVES.read_text(encoding="utf-8")
        self.assertNotIn("V3JITLessCertificateSyncAssessment", shell + primitives)
        self.assertNotIn("V3JITLessCertificateSyncIssue", shell + primitives)
        self.assertNotIn("Sync JIT-Less Certificate from SideStore", shell)
        self.assertIn("importCertificateFromSideStore()", (ROOT / "scripts/patch_v3_unified_shell.py").read_text(encoding="utf-8"))

    def test_quick_setup_gates_ios26_on_jitless_and_rechecks_on_return(self):
        shell = SHELL.read_text(encoding="utf-8")
        setup = shell[shell.index("final class V3SetupStore"):shell.index("struct V3SetupAssistantView")]
        view = shell[shell.index("struct V3SetupAssistantView"):shell.index("struct V3HomeServiceHeader")]
        # V3_SETUP_COMPLETION_POLICY_V1 / V3_SHARED_JITLESS_FACT_V1: the iOS-26
        # JIT-Less requirement is an input from the one shared policy, not a
        # private assistant rule, so Home cannot answer it differently.
        self.assertIn("V3JITLessCompletionPolicy.isRequired(", setup)
        self.assertIn("func isComplete(status: V3SideStoreStatusStore) -> Bool", setup)
        self.assertIn('Section("JIT-Less Mode")', view)
        self.assertIn("status.returnToSetupAfterJITLess", shell)
        self.assertIn("V3CanonicalJITLessCertificateUpdated", shell)

    def test_quick_setup_jitless_actions_match_certificate_state(self):
        shell = SHELL.read_text(encoding="utf-8")
        view_start = shell.index("struct V3SetupAssistantView")
        section_start = shell.index('Section("JIT-Less Mode")', view_start)
        setup = shell[section_start:shell.index('Section("Network")', section_start)]
        primitives = PRIMITIVES.read_text(encoding="utf-8")
        self.assertIn("V3JITLessSetupActionPolicy.action", setup)
        self.assertIn("case .openCertificates:", setup)
        self.assertIn("case .setupRequired: return .setUp", primitives)
        self.assertIn(".activeCertificateMissing, .activeCertificateRevoked, .activeCertificateExpired:", primitives)
        self.assertNotIn("case .setupRequired, .activeCertificateMissing:", setup)

    def test_quick_setup_routes_missing_active_certificate_to_certificates(self):
        shell = SHELL.read_text(encoding="utf-8")
        view_start = shell.index("struct V3SetupAssistantView")
        section_start = shell.index('Section("JIT-Less Mode")', view_start)
        setup = shell[section_start:shell.index('Section("Network")', section_start)]
        self.assertIn("V3JITLessSetupActionPolicy.action", setup)
        self.assertIn("case .openCertificates:", setup)
        self.assertNotIn("case .setupRequired, .activeCertificateMissing:", setup)

    def test_health_unknown_status_keeps_canonical_jitless_setup_action(self):
        shell = SHELL.read_text(encoding="utf-8")
        health_start = shell.index('struct V3HealthView')
        health = shell[health_start:shell.index('.navigationTitle("Health Check")', health_start)]
        self.assertIn("V3JITLessHealthRecoveryPolicy.shouldOfferCanonicalSetup", health)
        self.assertIn('case .unknown:', health)
        unknown = health[health.index('case .unknown:'):health.index('case .activeCertificateMissing', health.index('case .unknown:'))]
        self.assertIn('Button(reloadQueue.isChecking ? "Checking..." : "Re-check")', unknown)
        self.assertIn('.disabled(reloadQueue.isChecking)', unknown)


if __name__ == "__main__":
    unittest.main()
