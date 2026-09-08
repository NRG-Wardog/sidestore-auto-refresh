"""Execute the shipped migration/adapter with isolated Keychain and Security doubles.

These tests verify routing, persistence semantics and OSStatus handling, not
real iOS securityd entitlement enforcement. The full iOS build typechecks it.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/embedded_shared_keychain.swift"
SPEC = importlib.util.spec_from_file_location("embedded_keychain_patch", ROOT / "scripts/patch_embedded_keychain.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

DOUBLES = r'''
import Foundation
// In-memory namespace isolation; each client pins its group at construction.
enum Store {
    static var group: String? = "group.example.shared"
    static var processGroup = "TEAM.host.default"
    static var data: [String: [String: Data]] = [:]
    static var failure = 0
    static var writes = 0
    static var logs: [String] = []
}
func debugLog(_ message: String) { Store.logs.append(message) }
extension Bundle {
    enum Info { static var appbundleIdentifier = "com.kdt.livecontainer" }
    var altstoreAppGroup: String? { Store.group }
}
enum KeychainAccess {
    final class Keychain {
        enum Accessibility { case afterFirstUnlock }
        let group: String
        init(service: String) { group = Store.processGroup }
        init(service: String, accessGroup: String) { group = accessGroup }
        func accessibility(_ value: Accessibility) -> Keychain { self }
        func synchronizable(_ value: Bool) -> Keychain { self }
        func getData(_ key: String) throws -> Data? {
            if Store.failure != 0 { throw NSError(domain: NSOSStatusErrorDomain, code: Store.failure) }
            return Store.data[group]?[key]
        }
        func set(_ value: Data, key: String) throws {
            if Store.failure != 0 { throw NSError(domain: NSOSStatusErrorDomain, code: Store.failure) }
            Store.data[group, default: [:]][key] = value; Store.writes += 1
        }
        func remove(_ key: String) throws {
            if Store.failure != 0 { throw NSError(domain: NSOSStatusErrorDomain, code: Store.failure) }
            Store.data[group]?.removeValue(forKey: key); Store.writes += 1
        }
        func allKeys() -> [String] { Array(Store.data[group, default: [:]].keys) }
    }
}
typealias CFTypeRef = AnyObject
typealias CFDictionary = [String: Any]
let kSecClass = "class", kSecClassGenericPassword = "genp", kSecAttrService = "svce"
let kSecAttrSynchronizable = "sync", kSecAttrSynchronizableAny = "any"
let kSecMatchLimit = "limit", kSecMatchLimitAll = "all", kSecReturnAttributes = "attrs"
let kSecReturnData = "r_Data", kSecUseAuthenticationUI = "ui", kSecUseAuthenticationUIFail = "fail"
let kSecAttrAccessGroup = "agrp", kSecAttrAccount = "acct", kSecValueData = "v_Data"
let errSecItemNotFound = -25300, errSecSuccess = 0
func SecItemCopyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<CFTypeRef?>) -> Int {
    precondition(query[kSecAttrService] as? String == "com.kdt.livecontainer")
    precondition(query[kSecClass] as? String == kSecClassGenericPassword)
    precondition(query[kSecUseAuthenticationUI] as? String == kSecUseAuthenticationUIFail)
    if Store.failure != 0 { return Store.failure }
    let visible = [Store.processGroup, Store.group ?? ""]
    var rows: [[String: Any]] = []
    for group in visible {
        for (key, value) in Store.data[group, default: [:]] {
            rows.append([kSecAttrAccessGroup: group, kSecAttrAccount: key, kSecValueData: value])
        }
    }
    if rows.isEmpty { return errSecItemNotFound }
    result.pointee = rows as NSArray
    return 0
}
'''

HARNESS = r'''
@main struct Tests {
    static func main() throws {
        let scenario = CommandLine.arguments[1]
        let group = Store.group!
        let login = ["appleIDAdsid": Data("test-account-id".utf8), "appleIDXcodeToken": Data("sensitive-test-token".utf8)]
        func seed() { Store.data[Store.processGroup] = login }
        switch scenario {
        case "shared_route":
            seed()
            let ui = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(ui)
            precondition(ui.group == group)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: ui) == login["appleIDXcodeToken"])
            Store.processGroup = "TEAM.LiveProcess.default"
            let refresh = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(refresh)
            precondition(refresh.group == group)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == login["appleIDXcodeToken"])
            precondition(Store.data["TEAM.host.default"] == login) // original unchanged
        case "extension_first":
            Store.data["TEAM.host.default"] = login
            Store.processGroup = "TEAM.LiveProcess.default"
            let refresh = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(refresh)
            precondition(Store.writes == 0)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == nil)
            Store.processGroup = "TEAM.host.default"
            let ui = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.prepare(ui)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: refresh) == login["appleIDXcodeToken"])
        case "no_password_or_token_logging":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            _ = LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client)
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("private-test-password".utf8), client: client)
            let logs = Store.logs.joined(separator: "\n")
            for secret in ["sensitive-test-token", "private-test-password", "test-account-id"] { precondition(!logs.contains(secret)) }
        case "locked":
            let client = LCEmbeddedSharedKeychain.makeClient(); Store.failure = -25308
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil)
            let error = LCEmbeddedSharedKeychain.authenticationFailure()
            precondition(error.domain == "com.SideStore.Keychain" && error.code == 1005)
            precondition(Store.writes == 0)
        case "missing_entitlement":
            let client = LCEmbeddedSharedKeychain.makeClient(); Store.failure = -34018
            LCEmbeddedSharedKeychain.prepare(client)
            _ = LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client)
            precondition(LCEmbeddedSharedKeychain.authenticationFailure().code == 1006)
        case "missing_group":
            Store.group = nil
            let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("secret".utf8), client: client)
            precondition(Store.writes == 0 && LCEmbeddedSharedKeychain.authenticationFailure().code == 1006)
        case "wrong_identity":
            Bundle.Info.appbundleIdentifier = "com.SideStore.SideStore"
            let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDPassword", data: Data("secret".utf8), client: client)
            precondition(Store.writes == 0 && LCEmbeddedSharedKeychain.authenticationFailure().code == 1006)
        case "signout_no_resurrection":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            for key in LCSharedKeychainMigration.authKeys { LCEmbeddedSharedKeychain.write(key, data: nil, client: client) }
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == nil)
            precondition(Store.data[Store.processGroup] == login)
        case "clear_all_no_resurrection":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            LCEmbeddedSharedKeychain.clearAll(client); LCEmbeddedSharedKeychain.prepare(client)
            precondition(Store.data[group] == [LCSharedKeychainMigration.marker: LCSharedKeychainMigration.ready])
            precondition(Store.data[Store.processGroup] == login)
        case "unchanged_no_writes":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.writes = 0
            for _ in 0..<50 { LCEmbeddedSharedKeychain.prepare(client); _ = LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) }
            precondition(Store.writes == 0)
        case "partial_retry":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let items = login.map { LCLegacyKeychainItem(group: "old", key: $0.key, data: $0.value) }
            var fail = true
            do {
                _ = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { key, data in
                    if fail && key == "appleIDXcodeToken" { throw NSError(domain: NSOSStatusErrorDomain, code: -25308) }
                    try client.set(data, key: key)
                })
                fatalError("failure was swallowed")
            } catch {}
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == nil) // uncommitted, not a partial login
            let uncommittedMarker = try client.getData(LCSharedKeychainMigration.marker)
            precondition(uncommittedMarker == nil)
            fail = false
            let ready = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
            precondition(ready && LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == login["appleIDXcodeToken"])
        case "conflicts_fail_before_writes":
            let client = LCEmbeddedSharedKeychain.makeClient()
            var items = login.map { LCLegacyKeychainItem(group: "old1", key: $0.key, data: $0.value) }
            items += [LCLegacyKeychainItem(group: "old2", key: "appleIDAdsid", data: Data("another".utf8)), LCLegacyKeychainItem(group: "old2", key: "appleIDXcodeToken", data: Data("token2".utf8))]
            do {
                _ = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
                fatalError("mixed legacy accounts")
            } catch { precondition((error as NSError).code == 1008) }
            precondition(Store.writes == 0)
        case "no_cross_group_pair":
            let client = LCEmbeddedSharedKeychain.makeClient()
            let items = [LCLegacyKeychainItem(group: "A", key: "appleIDAdsid", data: Data("id".utf8)), LCLegacyKeychainItem(group: "B", key: "appleIDXcodeToken", data: Data("token".utf8))]
            let ready = try LCSharedKeychainMigration.prepare(group: group, items: { items }, read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
            precondition(!ready && Store.writes == 0)
        case "certificate_only":
            let cert = Data("test-imported-cert".utf8)
            Store.data[Store.processGroup] = ["importedCert_test": cert]
            let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("importedCert_test", client: client) == cert)
            precondition(LCEmbeddedSharedKeychain.read("appleIDXcodeToken", client: client) == nil)
            precondition(Store.writes == 0)
        case "invalid_utf8":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient(); LCEmbeddedSharedKeychain.prepare(client)
            Store.data[group]?["appleIDXcodeToken"] = Data([0xff])
            precondition(LCEmbeddedSharedKeychain.readString("appleIDXcodeToken", client: client) == nil)
            precondition(LCEmbeddedSharedKeychain.authenticationFailure().code == 1009)
        case "preserve_new_login":
            seed(); let client = LCEmbeddedSharedKeychain.makeClient()
            LCEmbeddedSharedKeychain.write("appleIDAdsid", data: Data("new-id".utf8), client: client)
            LCEmbeddedSharedKeychain.write("appleIDXcodeToken", data: Data("new-token".utf8), client: client)
            LCEmbeddedSharedKeychain.prepare(client)
            precondition(LCEmbeddedSharedKeychain.read("appleIDAdsid", client: client) == Data("new-id".utf8))
        default: fatalError("unknown scenario")
        }
        print("PASSED: " + scenario)
    }
}
'''

class EmbeddedKeychainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise unittest.SkipTest("swiftc unavailable")
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-keychain-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "KeychainTests.swift"
        source.write_text(DOUBLES + TEMPLATE.read_text() + HARNESS)
        cls.executable = Path(cls.temp.name) / "keychain-tests"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", "-O", str(source), "-o", str(cls.executable)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_execution_scenarios(self):
        for scenario in ("shared_route", "extension_first", "no_password_or_token_logging", "locked", "missing_entitlement", "missing_group", "wrong_identity", "signout_no_resurrection", "clear_all_no_resurrection", "unchanged_no_writes", "partial_retry", "conflicts_fail_before_writes", "no_cross_group_pair", "preserve_new_login", "certificate_only", "invalid_utf8"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PASSED: " + scenario, result.stdout)

    def test_source_safety(self):
        text = TEMPLATE.read_text()
        for forbidden in ("UserDefaults.", "write(to:", "NSLog(", "Timer(", "Task.sleep", "removePersistentDomain", "signOut("):
            self.assertNotIn(forbidden, text)
        self.assertIn("accessGroup: group", text)
        self.assertIn("kSecAttrService as String: service", text)
        self.assertIn("kSecUseAuthenticationUIFail", text)
        self.assertNotIn("\\(error)", text)
        self.assertIn(".afterFirstUnlock", text)

    def test_pinned_patch_and_idempotence(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("pinned SideStore source unavailable locally; required in combined CI")
        relative = "AltStore/Core/Components/Keychain.swift"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / relative
            target.parent.mkdir(parents=True)
            shutil.copy2(Path(source) / relative, target)
            op = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
            op.parent.mkdir(parents=True)
            op.write_text('''func preflight() throws {
        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {
            let error = NSError(domain: "com.SideStore.Authentication", code: 1004)
            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL reason=no_accessible_authentication_path")
            throw error
        }
}''')
            module.patch(root)
            first = (target.read_bytes(), op.read_bytes())
            module.patch(root)
            self.assertEqual(first, (target.read_bytes(), op.read_bytes()))
            self.assertIn("Keychain.shared.embeddedAuthenticationFailure()", op.read_text())
            self.assertNotIn("try? Keychain.shared.keychain.get", target.read_text())

if __name__ == "__main__":
    unittest.main()
