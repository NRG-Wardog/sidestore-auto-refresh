import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("patch_refresh_result_bridge")
host = load("patch_livecontainer_autorefresh")


class RefreshResultBridgeTests(unittest.TestCase):
    def test_host_result_import_executes(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; executable XPC result validation requires CI")
        # The combined startup patch replaces the whole RefreshHandler class body
        # with combined_refresh_handler.swift, so the shipped v3 host implements
        # the same correlation in completedRefresh. The fragment below is what
        # the standalone combined pipeline installs, and it is executed here
        # because it is the only way to run it without the whole service target.
        effective = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn("func fileprivate func completedRefresh", effective.replace("fileprivate func completedRefresh", "func fileprivate func completedRefresh"))
        self.assertIn("V3SharedAppGroup.sharedUserDefaults()", effective)
        # The host reads the one runtime App Group the host published. The
        # temporary resolver stands in for it so the executable test stays
        # isolated while the correlation and uncertainty rules run unchanged.
        source = '''import Foundation
''' + (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text(encoding="utf-8") + '''
setenv("LC_V3_INHERITED_APP_GROUP", "group.com.SideStore.SideStore", 1)
let sharedGroup = V3SharedAppGroup.runtimeIdentity()!.identifier
class Host {
    var c: Int? = 1
    var completions = 0
    var lastError: String?
    func finish(_ error: String?) { completions += 1; lastError = error; c = nil }
''' + bridge.HOST + r'''
}
let defaults = UserDefaults(suiteName: sharedGroup)!
defaults.removePersistentDomain(forName: sharedGroup)
defer { defaults.removePersistentDomain(forName: sharedGroup) }
defaults.set("current", forKey: "liveContainerAutoRefreshExpectedRunID")
func payload(_ run: String) throws -> Data {
    try PropertyListSerialization.data(fromPropertyList: ["liveContainerAutoRefreshVerification":
        ["run_id": run, "results": [["bundle_id": "test.app", "success": true]]]], format: .binary, options: 0)
}
let valid = Host()
valid.finishRefresh(nil, runID: "current", verification: try payload("current"))
precondition(valid.completions == 1 && valid.lastError == nil)
precondition(defaults.dictionary(forKey: "liveContainerAutoRefreshVerification")?["run_id"] as? String == "current")
valid.finishRefresh(nil, runID: "current", verification: try payload("current"))
precondition(valid.completions == 1)
let stale = Host()
stale.finishRefresh(nil, runID: "old", verification: try payload("old"))
precondition(stale.completions == 0 && stale.c != nil)
let mismatch = Host()
mismatch.finishRefresh(nil, runID: "current", verification: try payload("old"))
precondition(mismatch.completions == 1 && mismatch.lastError != nil)
for data in [Data([0, 1, 2]), Data(repeating: 0, count: 262145)] {
    let bad = Host()
    bad.finishRefresh(nil, runID: "current", verification: data)
    precondition(bad.completions == 1 && bad.lastError != nil)
}
let failed = Host()
failed.finishRefresh("Signing failed", runID: "current", verification: nil)
precondition(failed.lastError == "Signing failed")
// An unresolved runtime group is an honest unconfirmed result, not a read from
// a store the embedded service can never have written.
unsetenv("LC_V3_INHERITED_APP_GROUP")
let unavailable = Host()
unavailable.finishRefresh(nil, runID: "current", verification: nil)
precondition(unavailable.completions == 1 && unavailable.lastError != nil
             && unavailable.lastError!.contains("refresh-state store"))
print("RESULT_BRIDGE_TESTS_PASSED")
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            executable = Path(directory) / "result-tests"
            file.write_text(source)
            subprocess.run([compiler, str(file), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("RESULT_BRIDGE_TESTS_PASSED", result.stdout)

    def test_the_fragment_and_the_effective_host_agree_on_the_store(self):
        # The bridge fragment is the transport half; the combined handler is what
        # the combined build actually ships and it additionally applies the
        # terminal-completeness gate. The two must not drift apart on the store
        # they read or on the run they accept.
        for text, label in ((bridge.HOST, "bridge fragment"),
                            ((ROOT / "scripts/templates/combined_refresh_handler.swift")
                             .read_text(encoding="utf-8"), "combined handler")):
            self.assertNotIn('UserDefaults(suiteName: "group.com.SideStore.SideStore")', text, label)
            self.assertIn("V3SharedAppGroup.sharedUserDefaults()", text, label)
            self.assertIn('manifest["run_id"] as? String == runID', text, label)
            self.assertIn('defaults.set(manifest, forKey: "liveContainerAutoRefreshVerification")', text, label)
            self.assertIn('"liveContainerAutoRefreshHostHandoffRunID"] as? String == runID', text, label)
        # Only the shipped handler owns the completeness gate, and it must keep it.
        effective = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn("CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID)", effective)
        self.assertIn("CombinedVerification.clearUncertainty(defaults, runID: runID)", effective)

    def test_result_is_correlated_before_import_and_completion(self):
        self.assertLess(bridge.HOST.index('== runID'), bridge.HOST.index('defaults.set(manifest'))
        self.assertLess(bridge.HOST.index('defaults.set(manifest'), bridge.HOST.rindex('finish(error)'))
        self.assertIn('verification.count <= 262144', bridge.HOST)
        self.assertIn('data.count <= 262144', bridge.CLIENT)
        self.assertNotIn('dictionaryRepresentation', bridge.CLIENT)
        # Neither side may name a fixed App Group suite: the contract is the one
        # runtime group the host published.
        self.assertNotIn('group.com.SideStore.SideStore', bridge.HOST + bridge.CLIENT)
        self.assertIn("V3SharedAppGroup.sharedUserDefaults()", bridge.HOST + bridge.CLIENT)
        for secret in ('password', 'token', 'Keychain', 'certificate'):
            self.assertNotIn(secret, bridge.CLIENT)

    def test_direct_dispatch_generation_is_exact_and_repeatable(self):
        legacy_protocol = "@protocol V3CommandService\n+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;\n@end"
        category = "                                     server:(id <RefreshServer> _Nonnull)server;"
        old = legacy_protocol + "\n" + category + "\n" + bridge.LEGACY_HOST_METHOD
        new = bridge.install_direct_host_objc(old)
        self.assertIn(bridge.DIRECT_HOST_METHOD, new)
        self.assertIn(bridge.DIRECT_HOST_PROTOCOL, new)
        self.assertEqual(bridge.install_direct_host_objc(new), new)
        for bad in (old.replace('setObject:refreshRunID', 'setObject:other'),
                    new.replace('[refreshRunID copy]', '[other copy]')):
            with self.assertRaises(SystemExit): bridge.install_direct_host_objc(bad)
        anchor = "    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)"
        client = bridge.install_direct_host_client(anchor)
        self.assertIn(bridge.DIRECT_HOST_FAILURE, client)
        self.assertEqual(bridge.install_direct_host_client(client), client)

    def test_direct_successor_contract_is_strict_and_idempotent(self):
        required = [
            'fileprivate func completedRefresh(_ error: String?, runID: String, verification: Data?, id: UUID)',
            'guard launchID == id, refreshContinuation != nil, refreshRunID == runID else { return }',
            'verification.count <= 262144',
            'CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID)',
            'finishRefreshContinuation(.failure(failure))']
        objc = bridge.DIRECT_HOST_PROTOCOL + "\n" + bridge.DIRECT_HOST_METHOD
        client = bridge.DIRECT_HOST_FAILURE
        receiver = "\n".join(required + list(bridge.callback_admission_module().HOST_FRAGMENTS))
        bridge.validate_direct_host_bridge(objc, receiver, client)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / "SideStoreSupport"; folder.mkdir()
            sources = {'XPCServer.h': bridge.MARKER + '\n' + '\n'.join(bridge.callback_admission_module().HEADER_FRAGMENTS), 'XPCClient.m': objc,
                       'SideStore.swift': receiver, 'SideStoreClient.swift': client}
            for name, value in sources.items(): (folder / name).write_text(value)
            for _ in range(2): bridge.patch(root)
            self.assertEqual(sources, {name: (folder / name).read_text() for name in sources})
        for guard in required:
            with self.subTest(receiver=guard), self.assertRaises(SystemExit):
                bridge.validate_direct_host_bridge(objc, receiver.replace(guard, ''), client)
        for token in ('[refreshRunID copy]', 'runID:runID', 'refreshForHost:runID'):
            with self.subTest(dispatch=token), self.assertRaises(SystemExit):
                bridge.validate_direct_host_bridge(objc.replace(token, 'invalid'), receiver, client)
        with self.assertRaises(SystemExit):
            bridge.validate_direct_host_bridge(objc, receiver, client.replace('verification: nil', 'verification: Data()'))

    def test_pinned_xpc_patch_idempotence(self):
        source = Path(os.environ.get("LIVE_CONTAINER_TEST_SOURCE", ROOT / ".audit/upstream/LiveContainer"))
        if not source.exists():
            self.skipTest("Pinned LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SideStoreSupport").mkdir()
            for name in ('XPCServer.h', 'XPCClient.m', 'SideStore.swift', 'SideStoreClient.swift'):
                shutil.copy2(source / "SideStoreSupport" / name, root / "SideStoreSupport" / name)
            host.patch_support(root)
            bridge.patch(root)
            first = {p.name: p.read_bytes() for p in (root / "SideStoreSupport").iterdir()}
            host.patch_support(root)
            bridge.patch(root)
            self.assertEqual(first, {p.name: p.read_bytes() for p in (root / "SideStoreSupport").iterdir()})
            client = (root / "SideStoreSupport/XPCClient.m").read_text()
            if "V3_DIRECT_HOST_REFRESH_V1" in client:
                bridge.validate_direct_host_bridge(client,
                    (root / "SideStoreSupport/SideStore.swift").read_text(),
                    (root / "SideStoreSupport/SideStoreClient.swift").read_text())
                body = client.split('- (void)refreshAllAppsWithIdentifier:', 1)[1]
                self.assertNotIn('NSUserDefaults', body)
                self.assertNotIn('performRefreshForRealWithIdentifier:', body)
                self.assertIn('[server finishRefresh:error runID:runID verification:verification]', body)
            else:
                self.assertLess(client.index('setObject:refreshRunID'), client.index('[self performRefreshForRealWithIdentifier:'))
                self.assertIn('removeObjectForKey:@"liveContainerAutoRefreshVerification"', client)


if __name__ == "__main__":
    unittest.main()
