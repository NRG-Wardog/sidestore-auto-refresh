"""Executable cancellation and stale-callback coverage for canonical JIT-Less import."""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_TEST_SPEC = importlib.util.spec_from_file_location(
    "v3_existing_import_ownership_test", ROOT / "tests/test_v3_jitless_import_ownership.py")
ownership_test = importlib.util.module_from_spec(OWNERSHIP_TEST_SPEC)
OWNERSHIP_TEST_SPEC.loader.exec_module(ownership_test)
patch = ownership_test.patch


class JITLessCancellationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = ownership_test.generated_settings(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_combined_import_uses_only_embedded_keychain_and_decline_is_terminal(self):
        source = self.settings
        importer_start = source.index("func importCertificateFromSideStore() async {")
        importer_end = source.index("private func v3CompleteSideStoreCertificateImport", importer_start)
        importer = source[importer_start:importer_end]

        self.assertIn("if UserDefaults.sideStoreExist()", importer)
        self.assertIn("certificateImportFromBuiltInSideStoreAlert.open()", importer)
        self.assertIn("V3CertificateImportOwnership.isActive(requestID)", importer)
        self.assertIn("V3CertificateImportOwnership.cancel(requestID)", importer)
        self.assertIn("Embedded SideStore is unavailable in this LiveContainer build.", importer)
        self.assertNotIn("storeScheme", importer)
        self.assertNotIn("UIApplication.shared.open(url)", importer)

        prompt = importer.index("certificateImportFromBuiltInSideStoreAlert.open()")
        declined = importer.index("_ = V3CertificateImportOwnership.cancel(requestID)", prompt)
        self.assertLess(prompt, declined)
        self.assertLess(declined, importer.index("return", declined))
        self.assertLess(importer.index("UserDefaults.sideStoreExist()"), importer.index("Embedded SideStore is unavailable"))

    def test_patcher_is_idempotent_for_prepared_livecontainer_sources(self):
        live = self.root / "LiveContainer"
        side = self.root / "SideStore"
        before = {
            path.relative_to(self.root): path.read_bytes()
            for tree in (live, side) for path in tree.rglob("*") if path.is_file()
        }
        patch.patch(live, side)
        after = {
            path.relative_to(self.root): path.read_bytes()
            for tree in (live, side) for path in tree.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc unavailable; Swift ownership harness runs on macOS CI")
    def test_exact_request_cancel_remove_replacement_expiry_and_reload_interleavings(self):
        helper = textwrap.dedent(patch.IMPORT_OWNERSHIP_SWIFT)
        helper = "\n".join(
            line for line in helper.splitlines()
            if "V3_CERTIFICATE_IMPORT_OWNERSHIP_V1" not in line)
        harness = helper + r'''
import Foundation

let suiteName = "V3ImportCancelHarness-" + UUID().uuidString
let defaults = UserDefaults(suiteName: suiteName)!
defer { defaults.removePersistentDomain(forName: suiteName) }
let now = Date(timeIntervalSince1970: 1_800_000_000)

// Declining or dismissing the built-in confirmation cancels the current request.
let declined = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
precondition(V3CertificateImportOwnership.cancel(declined, defaults: defaults, now: now))
precondition(!V3CertificateImportOwnership.isActive(declined, defaults: defaults, now: now))
precondition(!V3CertificateImportOwnership.consume(declined, defaults: defaults, now: now), "late callback after cancel")

// Confirmed removal invalidates every pending owner before the persisted copy is cleared.
let removed = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
V3CertificateImportOwnership.invalidate(defaults: defaults)
precondition(!V3CertificateImportOwnership.consume(removed, defaults: defaults, now: now), "late callback after remove")

// Import B supersedes A. A's late result or late cancel cannot affect B; B writes once.
let requestA = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
let requestB = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
precondition(!V3CertificateImportOwnership.consume(requestA, defaults: defaults, now: now), "A callback after B")
precondition(!V3CertificateImportOwnership.cancel(requestA, defaults: defaults, now: now), "A cancel after B")
precondition(V3CertificateImportOwnership.isActive(requestB, defaults: defaults, now: now), "A must not cancel B")
precondition(V3CertificateImportOwnership.consume(requestB, defaults: defaults, now: now), "B callback")
precondition(!V3CertificateImportOwnership.consume(requestB, defaults: defaults, now: now), "B callback is one-use")

// The owner contains no process-local state: reopening the same defaults suite
// models a fresh process reading the persisted opaque request and expiry.
let persisted = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
let reopenedDefaults = UserDefaults(suiteName: suiteName)!
precondition(V3CertificateImportOwnership.isActive(persisted, defaults: reopenedDefaults, now: now), "reload reads current request")
precondition(V3CertificateImportOwnership.consume(persisted, defaults: reopenedDefaults, now: now), "reloaded request can complete")

let expired = V3CertificateImportOwnership.begin(defaults: defaults, now: now.addingTimeInterval(-301))
precondition(!V3CertificateImportOwnership.isActive(expired, defaults: defaults, now: now), "expired request is inactive")
precondition(!V3CertificateImportOwnership.cancel(expired, defaults: defaults, now: now), "expired cancel is harmless")
precondition(!V3CertificateImportOwnership.consume(expired, defaults: defaults, now: now), "expired callback")
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ownership.swift"
            binary = Path(directory) / "ownership"
            source.write_text(harness, encoding="utf-8")
            subprocess.run([shutil.which("swiftc"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
