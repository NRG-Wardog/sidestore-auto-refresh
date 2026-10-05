"""Fast guards for the D08/D10 fixes already present before the staging work.

Executable startup and pinned portal harnesses remain the behavioral authority;
these patch-level guards also run when the upstream trees/Swift are unavailable.
"""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_combined_service_startup as startup


class PreservedLaunchAndQuotaTests(unittest.TestCase):
    def test_missing_request_identifier_producer_is_project_owned(self):
        script = (ROOT / "scripts/patch_combined_service_startup.py").read_text()
        producers = [node.value for node in ast.walk(ast.parse(script))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and "void LCLaunchServiceExtension(NSExtension *extension" in node.value]
        self.assertEqual(len(producers), 1)
        producer = producers[0].split("void LCLaunchServiceExtension", 1)[1]
        self.assertIn('errorWithDomain:@"io.sidestore.LiveContainer.ExtensionLaunch" code:1', producer)
        self.assertIn("completion(identifier, error)", producer)
        self.assertNotIn("NSExecutableLoadError", producer)
        self.assertNotIn("NSCocoaErrorDomain", producer)
        self.assertNotIn("3587", producer)

    def test_quota_patch_is_narrow_idempotent_and_rejects_anchor_drift(self):
        original = """public func addAppID() {
    resultCodeHandler: { resultCode in
        switch resultCode {
                case DeveloperPortalResultCodes.maximumAppIDLimitReached:
                    return DeveloperPortalError.maximumAppIDLimitReached(cause: nil)
                default: return nil
        }
    }
}
// Existing App ID lookup and reuse remain outside this registration handler.
"""
        patched = startup.patch_sidesign_app_id_limit(original)
        self.assertEqual(startup.patch_sidesign_app_id_limit(patched), patched)
        self.assertEqual(patched.replace(
            ", 9120: // LC_APP_ID_LIMIT_9120_V1", ":"), original)
        for changed in [original.replace("maximumAppIDLimitReached:", "unknownLimit:"), original + original]:
            with self.assertRaises(ValueError):
                startup.patch_sidesign_app_id_limit(changed)

    def test_existing_behavioral_harness_keeps_scoped_quota_and_no_replay_cases(self):
        portal = (ROOT / "tests/fixtures/v3_portal_failure_harness.swift").read_text()
        self.assertIn("for resultCode in [37, 9120]", portal)
        self.assertIn('signingContext["http_status"] == "200"', portal)
        self.assertIn("ServerError.underlyingError(code: 9120", portal)
        self.assertIn("quotaCounts[quotaBundle] == 1", portal)
        self.assertIn("retryDisposition == .blocked", portal)


if __name__ == "__main__":
    unittest.main()
