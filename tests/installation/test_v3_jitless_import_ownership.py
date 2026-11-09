"""Regression coverage for one-use ownership of canonical JIT-Less imports."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "tests"))
from owner_source import owner_source
from support.product_sources import declaration_end


def swift_function(text: str, signature: str) -> str:
    if text.count(signature) != 1:
        raise AssertionError(f"Expected one owner declaration: {signature}")
    start = text.index(signature)
    return text[start:declaration_end(text, start)]


def current_settings() -> str:
    return owner_source("LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift")


def import_ownership() -> str:
    return swift_function(current_settings(), "    private enum V3CertificateImportOwnership {")


class JITLessImportOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = current_settings()

    def tearDown(self):
        self.temporary.cleanup()

    def test_combined_import_uses_exact_one_use_owner_and_never_launches_external_store(self):
        source = self.settings
        self.assertIn(import_ownership(), source)
        importer = source[source.index("func importCertificateFromSideStore() async"):
                          source.index("private func v3CompleteSideStoreCertificateImport")]
        self.assertIn("let requestID = V3CertificateImportOwnership.begin()", importer)
        self.assertLess(importer.index("V3CertificateImportOwnership.begin()"), importer.index("await certificateImportFromBuiltInSideStoreAlert.open()"))
        self.assertIn("V3CertificateImportOwnership.isActive(requestID) else { return }", importer)
        self.assertIn("V3CertificateImportOwnership.cancel(requestID)", importer)
        self.assertIn("Embedded SideStore is unavailable in this LiveContainer build.", importer)
        self.assertNotIn("storeScheme", importer)
        self.assertNotIn("UIApplication.shared.open(url)", importer)
        self.assertIn("V3CertificateImportOwnership.consume(requestID)", source)
        self.assertIn("onSideStoreCertificateCallback(certificateData: certificateData, password: password)", source)
        self.assertIn("V3CertificateImportOwnership.isActive(requestID) else { return }", source)

    def test_legacy_external_callback_stays_exact_id_gated_but_is_not_generated(self):
        importer = self.settings[self.settings.index("func importCertificateFromSideStore() async"):
                                self.settings.index("private func v3CompleteSideStoreCertificateImport")]
        self.assertNotIn("callback_template", importer)
        self.assertNotIn("storeScheme", importer)
        route = swift_function(self.settings, "    func handleURL(url: URL) {")
        self.assertIn('queryItems["request_id"]', route)
        self.assertIn("V3CertificateImportOwnership.isActive(requestID)", route)

    def test_removal_invalidates_before_legacy_keys_and_notifies_after_removal(self):
        removal = self.settings[self.settings.index("func removeCertificate() async"):
                                self.settings.index("func handleURL(url:")]
        self.assertLess(removal.index("guard let doRemove"), removal.index("V3CertificateImportOwnership.invalidate()"))
        self.assertLess(removal.index("V3CertificateImportOwnership.invalidate()"), removal.index('forKey: "LCCertificateData"'))
        self.assertLess(removal.index('forKey: "LCAppGroupID"'), removal.index('V3CanonicalJITLessCertificateUpdated"'))
        self.assertIn('NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated")', removal)

    def test_manual_import_notification_is_one_post_after_canonical_state_writes(self):
        manual = self.settings[self.settings.index("func importCertificate() async {"):
                               self.settings.index("func importCertificateFromSideStore() async {")]
        event = 'NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)'
        self.assertEqual(manual.count("V3_CANONICAL_JITLESS_MANUAL_IMPORT_EVENT_V1"), 1)
        self.assertEqual(manual.count("V3_CANONICAL_JITLESS_MANUAL_IMPORT_INVALIDATES_PENDING_V1"), 1)
        self.assertEqual(manual.count(event), 1)
        self.assertLess(manual.index("getCertTeamId(withKeyData: certificateData"),
                        manual.index("V3CertificateImportOwnership.invalidate()"))
        self.assertLess(manual.index("V3CertificateImportOwnership.invalidate()"),
                        manual.index('forKey: "LCCertificateData"'))
        self.assertLess(manual.index('forKey: "LCCertificateData"'), manual.index(event))
        self.assertLess(manual.index('forKey: "LCCertificatePassword"'), manual.index(event))
        self.assertLess(manual.index('forKey: "LCAppGroupID"'), manual.index(event))

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc unavailable; production extraction harness runs on macOS CI")
    def test_pinned_manual_import_success_and_failures_drive_canonical_observation(self):
        generated = self.settings
        method = swift_function(generated, "func importCertificate() async {")
        self.assertIn("V3_CANONICAL_JITLESS_MANUAL_IMPORT_EVENT_V1", method)
        self.assertIn("V3_CANONICAL_JITLESS_MANUAL_IMPORT_INVALIDATES_PENDING_V1", method)
        harness_template = (ROOT / "tests/fixtures/v3_jitless_manual_import_behavior_harness.swift").read_text(encoding="utf-8")
        self.assertEqual(harness_template.count("$MANUAL_IMPORT_FUNCTION$"), 1)
        self.assertEqual(harness_template.count("$IMPORT_OWNERSHIP_HELPER$"), 1)
        harness = harness_template.replace("$IMPORT_OWNERSHIP_HELPER$", import_ownership())
        harness = harness.replace("$MANUAL_IMPORT_FUNCTION$", method)
        harness += "\n" + owner_source("LiveContainerSwiftUI/Utilities/V3Localization.swift")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "manual-import.swift"
            binary = directory / "manual-import"
            source.write_text(harness, encoding="utf-8")
            built = subprocess.run([shutil.which("swiftc"), "-parse-as-library", str(source), "-o", str(binary)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, f"pinned import harness did not compile:\n{built.stderr}")
            for mode in ("success", "cancel-import", "cancel-file", "cancel-password", "file-error", "validation-fail"):
                result = subprocess.run([str(binary), mode, str(directory / f"{mode}.p12")],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, f"{mode} import contract failed:\n{result.stderr}")

    def test_tokenless_callback_is_rejected_and_legacy_writer_stays_three_keys(self):
        route = swift_function(self.settings, "    func handleURL(url: URL) {")
        self.assertIn('queryItems["request_id"]', route)
        self.assertIn("else { return }", route)
        callback = self.settings[self.settings.index("func onSideStoreCertificateCallback"):
                                 self.settings.index("func removeCertificate()")]
        keys = ("LCCertificateData", "LCCertificatePassword", "LCCertificateUpdateDate")
        for key in keys:
            self.assertIn(f'forKey: "{key}"', callback)
        self.assertEqual(sum(callback.count(f'forKey: "{key}"') for key in keys), 3)

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc unavailable; exact Swift ownership harness runs on macOS CI")
    def test_production_helper_remove_during_await_late_duplicate_expiry_and_new_import(self):
        helper = textwrap.dedent(import_ownership())
        helper = "\n".join(line for line in helper.splitlines() if "V3_CERTIFICATE_IMPORT_OWNERSHIP_V1" not in line)
        harness = helper + r'''
import Foundation

let suiteName = "V3ImportOwnershipHarness-" + UUID().uuidString
let defaults = UserDefaults(suiteName: suiteName)!
defer { defaults.removePersistentDomain(forName: suiteName) }
let now = Date(timeIntervalSince1970: 1_800_000_000)

// Manual, built-in prompt, and external app-open completions all retain this
// token while suspended. Removal invalidates before their awaited work resumes.
for route in ["manual", "built-in", "external"] {
    let id = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
    precondition(V3CertificateImportOwnership.isActive(id, defaults: defaults, now: now), route)
    V3CertificateImportOwnership.invalidate(defaults: defaults)
    precondition(!V3CertificateImportOwnership.consume(id, defaults: defaults, now: now), route + " stale completion")
}

// A cold view/process can re-open the same persisted defaults and accept only
// the exact unexpired request. Consumption is one-use.
let persisted = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
precondition(V3CertificateImportOwnership.isActive(persisted, defaults: defaults, now: now))
precondition(V3CertificateImportOwnership.consume(persisted, defaults: defaults, now: now))
precondition(!V3CertificateImportOwnership.consume(persisted, defaults: defaults, now: now), "duplicate")
precondition(!V3CertificateImportOwnership.consume("", defaults: defaults, now: now), "tokenless")

let expired = V3CertificateImportOwnership.begin(defaults: defaults, now: now.addingTimeInterval(-301))
precondition(!V3CertificateImportOwnership.consume(expired, defaults: defaults, now: now), "expired")

let old = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
V3CertificateImportOwnership.invalidate(defaults: defaults)
let fresh = V3CertificateImportOwnership.begin(defaults: defaults, now: now)
precondition(old != fresh)
precondition(!V3CertificateImportOwnership.consume(old, defaults: defaults, now: now), "stale after new import")
precondition(V3CertificateImportOwnership.consume(fresh, defaults: defaults, now: now), "new import")
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ownership.swift"
            binary = Path(directory) / "ownership"
            source.write_text(harness, encoding="utf-8")
            subprocess.run([shutil.which("swiftc"), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
