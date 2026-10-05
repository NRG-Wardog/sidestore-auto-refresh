"""Source contracts for the Anisette namespace used across Apple auth commit.

The executable Swift scenarios are in test_embedded_keychain.py. These checks
also run without swiftc, but are not a substitute for native or device tests.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text()


class AnisetteKeychainContinuitySourceTests(unittest.TestCase):
    def test_legacy_fallback_is_explicitly_certificate_only(self):
        read = SOURCE.split("    static func read(_ key:", 1)[1].split("    static func write(", 1)[0]
        gate = re.search(r"if data == nil && !ready([^\{]*)\{", read)
        self.assertIsNotNone(gate, "the certificate compatibility path must remain explicit")
        self.assertIn("LCSharedKeychainMigration.supportsLegacyCertificateFallback(key)", gate[1],
                      "identifier/adiPb must not borrow a legacy value only until auth becomes ready")
        start = SOURCE.index("    static func supportsLegacyCertificateFallback(")
        end = SOURCE.index("\n    }", start)
        helper = SOURCE[start:end]
        # This is an allowlist, not a denylist that silently grants fallback to
        # the next sensitive key added by upstream.
        literals = set(re.findall(r'"([^"\\]*)"', helper))
        self.assertEqual(literals, {"signingCertificatePrivateKey", "signingCertificateSerialNumber", "importedCert_"})
        self.assertIn("key.count <= 256", helper)

    def test_coherent_migration_remains_the_anisette_transfer_path(self):
        self.assertIn('"signingCertificatePrivateKey", "signingCertificateSerialNumber", "identifier", "adiPb"', SOURCE)
        migration = SOURCE.split("    static func prepare(group:", 1)[1].split("// LC_SHARED_MIGRATION_POLICY_END", 1)[0]
        self.assertLess(migration.index("guard let source = completeSets.first"),
                        migration.index("for key in source.keys.sorted()"))
        self.assertIn("guard completeSets.allSatisfy({ $0 == source })", migration)
        self.assertIn("if let existing = try read(key), existing != source[key]", migration)
        self.assertIn("guard try read(key) == value", migration)
        self.assertLess(migration.index("guard try read(key) == value"),
                        migration.index("try write(marker, ready)"))

    def test_native_regressions_cover_commit_and_migration_states(self):
        harness = (ROOT / "tests/test_embedded_keychain.py").read_text()
        for scenario in ("anisette_markerless_login_continuity", "anisette_signed_out_login_continuity",
                         "anisette_partial_selected_identity", "anisette_partial_selected_blob",
                         "anisette_complete_migration_preserved"):
            self.assertGreaterEqual(harness.count('"' + scenario + '"'), 2, scenario)
        self.assertIn("committing Apple login must not switch Anisette device identity before fetchTeams", harness)
        self.assertIn("never combine an Anisette identifier and blob from different keychain namespaces", harness)


if __name__ == "__main__":
    unittest.main()
