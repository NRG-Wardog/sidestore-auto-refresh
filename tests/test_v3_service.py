"""Exercise pinned patch transactions and the actual shipped wire decoder."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


service = module("patch_v3_service")
shell = module("patch_v3_unified_shell")
refresh = module("patch_livecontainer_autorefresh")
results = module("patch_refresh_result_bridge")


class ServicePatchTests(unittest.TestCase):
    def fixture(self, directory):
        live_source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not live_source or not side_source:
            self.skipTest("Set pinned source environment variables")
        roots = (directory / "live", directory / "side")
        files = (
            ["SideStoreSupport/" + name for name in ("XPCServer.h", "XPCClient.m", "SideStore.swift", "SideStoreClient.swift")] +
            ["LiveContainerSwiftUI/" + name for name in ("Views/LCTabView.swift", "Views/AppList/LCAppListView.swift",
             "Views/Settings/LCSettingsView.swift", "Views/Settings/LCMultiLCManagementView.swift",
             "Utilities/Shared.swift", "App/LiveContainerSwiftUIApp.swift", "App/AppDelegate.swift")] +
            ["MultitaskSupport/AppSceneViewController." + suffix for suffix in ("h", "m")] +
            ["LiveContainer/LCBootstrap.m", "ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"],
            ["AltStore/AppDelegate.swift", "AltStore/SceneDelegate.swift"])
        for source, root, pin, names in zip((live_source, side_source), roots, service.PINS, files):
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(["git", "-C", source, "show", pin + ":" + name]))
        refresh.patch_support(roots[0])
        refresh.patch_host_delegate(roots[0])
        refresh.patch_settings(roots[0])
        results.patch(roots[0])
        shell.patch(*roots)
        return roots

    def apply(self, roots):
        def revision(args, **kwargs):
            return service.PINS[0 if str(roots[0]) == args[2] else 1]
        with mock.object(service.subprocess, "check_output", side_effect=revision):
            service.patch(*roots)

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    def test_pinned_patch_replay_and_tamper(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            first = self.snapshot(directory)
            self.apply(roots)
            self.assertEqual(first, self.snapshot(directory))
            path = roots[0] / "SideStoreSupport/XPCClient.m"
            path.write_text(path.read_text() + "\n// unexpected drift\n")
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertIn(".downloadAlert", (roots[0] / "LiveContainerSwiftUI/Views/LCTabView.swift").read_text())
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift").read_text(encoding="utf-8"))
            self.assertIn("!isLiveProcess && sideStoreExist", (roots[0] / "LiveContainer/LCBootstrap.m").read_text(encoding="utf-8"))
            for name in ("ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"):
                self.assertNotIn('set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")', (roots[0] / name).read_text(encoding="utf-8"))

    def test_anchor_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            path = roots[1] / "AltStore/SceneDelegate.swift"
            path.write_text(path.read_text().replace("guard let _ = (scene as? UIWindowScene)", "guard let changed = (scene as? UIWindowScene)"))
            before = self.snapshot(directory)
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_wrong_revision_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with mock.object(service.subprocess, "check_output", return_value="unknown"):
                with self.assertRaises(SystemExit):
                    service.patch(directory, directory)
            self.assertEqual({}, self.snapshot(directory))

    def test_owner_boundary(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        for token in ("CoreData", "Keychain", "NSManagedObject", "signingCertificatePassword", "appleIDXcodeToken"):
            self.assertNotIn(token, host + bridge)
        self.assertNotIn("v3SideStoreStatusSnapshot", host)
        self.assertIn("pending.removeValue", bridge)
        self.assertIn("decoded[\"id\"] as? String == id", bridge)


class WireExecutionTests(unittest.TestCase):
    def test_shipped_bridge_lifecycle(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "tests/fixtures/v3_bridge_harness.swift").read_text() +
                               (ROOT / "scripts/templates/v3_service_bridge.swift").read_text())
            executable = directory / "bridge-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 lifecycle PASS", result.stdout)

    def test_shipped_decoder_rejects_secrets_stale_and_malformed_requests(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
let valid: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": "snapshot",
                          "target": "", "deadline": now.addingTimeInterval(30)]
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
precondition(V3WireContract.decodeRequest(encode(valid), now: now) != nil)
var booleanVersion = valid; booleanVersion["version"] = true
precondition(V3WireContract.decodeRequest(encode(booleanVersion), now: now) == nil)
var page = valid; page["operation"] = "catalog"; page["cursor"] = 50
precondition(V3WireContract.decodeRequest(encode(page), now: now) != nil)
for cursor in [-1, 1_000_001, true, "50", 1.5] as [Any] {
    page["cursor"] = cursor
    precondition(V3WireContract.decodeRequest(encode(page), now: now) == nil)
}
var nonCatalog = valid; nonCatalog["cursor"] = 0
precondition(V3WireContract.decodeRequest(encode(nonCatalog), now: now) == nil)
for (key, value) in [("password", "secret"), ("token", "secret"), ("certificate", "secret"),
                     ("operation", "arbitrarySelector"), ("id", "bad"), ("target", String(repeating: "a", count: 4097))] {
    var request = valid
    request[key] = value
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
for date in [now.addingTimeInterval(-1), now, now.addingTimeInterval(611)] {
    var request = valid; request["deadline"] = date
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
var setting = valid; setting["operation"] = "setSetting"; setting["target"] = "betaUpdates"
setting["value"] = true
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
setting["value"] = 1
precondition(V3WireContract.decodeRequest(encode(setting), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data(repeating: 0, count: 16385), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data([1, 2, 3]), now: now) == nil)
print("V3 wire contract PASS")
''')
            executable = directory / "wire-tests"
            subprocess.run([compiler, str(program), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("PASS", result.stdout)
