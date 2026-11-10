"""Maintained launch error provenance and registration quota guards."""
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from owner_source import owner_source


class PreservedLaunchAndQuotaTests(unittest.TestCase):
    def test_missing_request_identifier_producer_is_project_owned(self):
        source = owner_source("SideStoreSupport/XPCServer.m")
        self.assertEqual(source.count("void LCLaunchServiceExtension("), 1)
        producer = source.split("void LCLaunchServiceExtension", 1)[1]
        self.assertIn('errorWithDomain:@"io.sidestore.LiveContainer.ExtensionLaunch" code:1', producer)
        self.assertIn("completion(identifier, error)", producer)
        self.assertNotIn("NSExecutableLoadError", producer)
        self.assertNotIn("NSCocoaErrorDomain", producer)
        self.assertNotIn("3587", producer)

    def test_current_portal_maps_both_quota_codes_in_registration(self):
        source = owner_source("Sources/DeveloperPortal/AppIDs.swift", "SideStore/Dependencies/SideSign")
        self.assertIn("case DeveloperPortalResultCodes.maximumAppIDLimitReached, 9120:", source)
        self.assertIn("return DeveloperPortalError.maximumAppIDLimitReached(cause: message)", source)

    def test_existing_behavioral_harness_keeps_scoped_quota_and_no_replay_cases(self):
        portal = (ROOT / "tests/fixtures/v3_portal_failure_harness.swift").read_text()
        self.assertIn("for resultCode in [37, 9120]", portal)
        self.assertIn('signingContext["http_status"] == "200"', portal)
        self.assertIn("ServerError.underlyingError(code: 9120", portal)
        self.assertIn("quotaCounts[quotaBundle] == 1", portal)
        self.assertIn("retryDisposition == .blocked", portal)


if __name__ == "__main__":
    unittest.main()
