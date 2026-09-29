"""Regression coverage for one-use ownership of canonical JIT-Less imports."""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v3_patch_import_ownership", ROOT / "scripts/patch_v3_unified_shell.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


def generated_settings(root: Path) -> str:
    # Reuse the canonical patch test's filesystem fixture, replacing its short
    # importer stub with the upstream method shape the production patch targets.
    spec = importlib.util.spec_from_file_location("v3_shell_test_fixture", ROOT / "tests/test_v3_unified_shell.py")
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    live, side = fixture_module.fixture(root)
    settings = live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    settings.write_text('''import Foundation
struct Settings {
    @State private var certificateDataFound = false
    func v3SharedSideStoreKeychainAccessGroup() -> String? { return "group" }
    func importCertificateFromSideStore() async {
        if UserDefaults.sideStoreExist() {
            if let ans = await certificateImportFromBuiltInSideStoreAlert.open(), ans {
                let query: [String: Any] = [
                    kSecClass as String: kSecClassGenericPassword,
                    kSecAttrAccount as String: "signingCertificate",
                    kSecReturnData as String: true,
                    kSecMatchLimit as String: kSecMatchLimitOne,
                    kSecAttrService as String: "com.kdt.livecontainer",
                    kSecAttrSynchronizable as String: kSecAttrSynchronizableAny
                ]
                let passwordQuery: [String: Any] = [
                    kSecClass as String: kSecClassGenericPassword,
                    kSecAttrAccount as String: "signingCertificatePassword",
                    kSecReturnData as String: true,
                    kSecMatchLimit as String: kSecMatchLimitOne,
                    kSecAttrService as String: "com.kdt.livecontainer",
                    kSecAttrSynchronizable as String: kSecAttrSynchronizableAny
                ]
                onSideStoreCertificateCallback(certificateData: data, password: password)
                return
            }
        }
        let storeScheme: String = "sidestore"
        guard let url = URL(string: "\\(storeScheme.lowercased())://certificate?callback_template=livecontainer%3A%2F%2Fcertificate%3Fcert%3D%24%28BASE64_CERT%29%26password%3D%24%28PASSWORD%29") else { return }
        await UIApplication.shared.open(url)
    }
    func onSideStoreCertificateCallback(certificateData: Data, password: String) {
        LCUtils.appGroupUserDefault.set(certificateData, forKey: "LCCertificateData")
        LCUtils.appGroupUserDefault.set(password, forKey: "LCCertificatePassword")
        LCUtils.appGroupUserDefault.set(NSDate.now, forKey: "LCCertificateUpdateDate")
        certificateDataFound = true
    }
    func removeCertificate() async {
        guard let doRemove = await certificateRemoveAlert.open(), doRemove else { return }
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateData")
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificatePassword")
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateUpdateDate")
        certificateDataFound = false
        UserDefaults.standard.set(nil, forKey: "LCAppGroupID")
    }
    func handleURL(url: URL) {
        if url.host == "certificate" {
            if let components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
                let queryItems = components.queryItems?.reduce(into: [String: String]()) { $0[$1.name.lowercased()] = $1.value } ?? [:]
                guard let encodedCert = queryItems["cert"]?.removingPercentEncoding,
                      let password = queryItems["password"],
                      let certData = Data(base64Encoded: encodedCert) else { return }
                onSideStoreCertificateCallback(certificateData: certData, password: password)
            }
        }
    }
    var body: some View {
        Form {}
            .navigationBarTitle("lc.tabView.settings".loc)
    }
                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
}
''', encoding="utf-8")
    patch.patch(live, side)
    return settings.read_text(encoding="utf-8")


class JITLessImportOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = generated_settings(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_combined_import_uses_exact_one_use_owner_and_never_launches_external_store(self):
        source = self.settings
        self.assertIn(patch.IMPORT_OWNERSHIP_SWIFT, source)
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
        route = self.settings[self.settings.index("func handleURL(url:"):
                                self.settings.index("var body: some View")]
        self.assertIn('queryItems["request_id"]', route)
        self.assertIn("V3CertificateImportOwnership.isActive(requestID)", route)

    def test_removal_invalidates_before_legacy_keys_and_notifies_after_removal(self):
        removal = self.settings[self.settings.index("func removeCertificate() async"):
                                self.settings.index("func handleURL(url:")]
        self.assertLess(removal.index("guard let doRemove"), removal.index("V3CertificateImportOwnership.invalidate()"))
        self.assertLess(removal.index("V3CertificateImportOwnership.invalidate()"), removal.index('forKey: "LCCertificateData"'))
        self.assertLess(removal.index('forKey: "LCAppGroupID"'), removal.index('V3CanonicalJITLessCertificateUpdated"'))
        self.assertIn('NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated")', removal)

    def test_tokenless_callback_is_rejected_and_legacy_writer_stays_three_keys(self):
        route = self.settings[self.settings.index("func handleURL(url:"):
                             self.settings.index("var body: some View")]
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
        helper = textwrap.dedent(patch.IMPORT_OWNERSHIP_SWIFT)
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
