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
        source = '''import Foundation
class Host {
    var c: Int? = 1
    var completions = 0
    var lastError: String?
    func finish(_ error: String?) { completions += 1; lastError = error; c = nil }
''' + bridge.HOST + r'''
}
let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore")!
defaults.removePersistentDomain(forName: "group.com.SideStore.SideStore")
defer { defaults.removePersistentDomain(forName: "group.com.SideStore.SideStore") }
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
print("RESULT_BRIDGE_TESTS_PASSED")
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            executable = Path(directory) / "result-tests"
            file.write_text(source)
            subprocess.run([compiler, str(file), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("RESULT_BRIDGE_TESTS_PASSED", result.stdout)

    def test_result_is_correlated_before_import_and_completion(self):
        self.assertLess(bridge.HOST.index('== runID'), bridge.HOST.index('defaults.set(manifest'))
        self.assertLess(bridge.HOST.index('defaults.set(manifest'), bridge.HOST.rindex('finish(error)'))
        self.assertIn('verification.count <= 262144', bridge.HOST)
        self.assertIn('data.count <= 262144', bridge.CLIENT)
        self.assertNotIn('dictionaryRepresentation', bridge.CLIENT)
        for secret in ('password', 'token', 'Keychain', 'certificate'):
            self.assertNotIn(secret, bridge.CLIENT)

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
            self.assertLess(client.index('setObject:refreshRunID'), client.index('[self performRefreshForRealWithIdentifier:'))
            self.assertIn('removeObjectForKey:@"liveContainerAutoRefreshVerification"', client)


if __name__ == "__main__":
    unittest.main()
