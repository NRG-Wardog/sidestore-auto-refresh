"""Executable failure behavior plus pinned, transactional adapter regression."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_combined_service_startup as startup


class ExecutableStartupTests(unittest.TestCase):
    def test_actual_startup_state_machine_and_error_wire(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "main.swift"
            source.write_text("\n".join((ROOT / name).read_text(encoding="utf-8") for name in (
                "scripts/templates/combined_failure.swift", "scripts/templates/combined_service_connection.swift",
                "tests/fixtures/combined_startup_harness.swift")), encoding="utf-8")
            exe = root / "startup-tests"
            result = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reconnect PASS", result.stdout)

    def test_actual_shared_storage_initializer_preserves_existing_install(self):
        if sys.platform != "darwin": self.skipTest("Objective-C Foundation requires macOS")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "storage.m"
            source.write_text("#include <assert.h>\n#include <stdio.h>\n" + (ROOT / "scripts/templates/combined_container_storage.h").read_text() + r'''
int main(int argc, char **argv) { @autoreleasepool {
    NSString *root = [NSString stringWithUTF8String:argv[1]];
    NSFileManager *fm = NSFileManager.defaultManager;
    NSError *error = nil;
    assert(LCPrepareContainerDirectories(root, &error));
    NSString *database = [root stringByAppendingPathComponent:@"Library/SideStore.sqlite"];
    NSData *sentinel = [@"existing database and guest state" dataUsingEncoding:NSUTF8StringEncoding];
    assert([sentinel writeToFile:database atomically:YES]);
    assert(LCPrepareContainerDirectories(root, &error));
    assert([[NSData dataWithContentsOfFile:database] isEqual:sentinel]);
    NSString *blocked = [root stringByAppendingPathComponent:@"blocked"];
    assert([sentinel writeToFile:blocked atomically:YES]);
    assert(!LCPrepareContainerDirectories(blocked, &error));
    assert(error != nil);
    puts("storage initialization/preservation/failure PASS");
} return 0; }
''')
            exe = root / "storage-test"
            subprocess.run(["clang", "-fobjc-arc", "-framework", "Foundation", str(source), "-o", str(exe)], check=True, capture_output=True)
            result = subprocess.run([str(exe), str(root / "SideStore")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_baseline_nil_bookmark_reproduces_trap(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift diagnostic build")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "crash.swift"; exe = root / "crash"
            source.write_text("import Foundation\n@inline(never) func bookmarkForURL(_ url: URL) -> Data? { nil }\nlet value = bookmarkForURL(URL(fileURLWithPath: \"/missing\"))!\nprint(value)\n")
            subprocess.run([compiler, "-O", str(source), "-o", str(exe)], check=True, capture_output=True)
            result = subprocess.run([str(exe)], capture_output=True)
            self.assertLess(result.returncode, 0, "the baseline force unwrap must trap")


class StartupPatchTests(unittest.TestCase):
    def fixture(self, directory):
        live_source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not live_source or not side_source: self.skipTest("pinned upstream checkouts required")
        import patch_livecontainer_autorefresh as refresh
        import patch_refresh_result_bridge as results
        roots = (directory / "live", directory / "side")
        paths = (["SideStoreSupport/" + name for name in ("SideStore.swift", "SideStoreClient.swift", "XPCServer.m", "XPCServer.h", "XPCClient.m")] +
                 ["LiveContainer/LCBootstrap.m", "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"],
                 ["AltStore/AppDelegate.swift", "SideStore/Core/Operations/PipelineExecutor.swift"])
        for source, root, pin, files in zip((live_source, side_source), roots, startup.PINS, paths):
            for name in files:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(["git", "-C", source, "show", pin + ":" + name]))
        refresh.patch_support(roots[0]); results.patch(roots[0])
        return roots
    def apply(self, roots):
        with mock.object(startup.subprocess, "check_output", side_effect=lambda args, **kw: startup.PINS[0 if args[2] == str(roots[0]) else 1]):
            startup.patch(*roots, "v2")
    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    def test_pinned_replay_and_no_refresh_sentinel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); roots = self.fixture(root)
            self.apply(roots); first = self.snapshot(root); self.apply(roots)
            self.assertEqual(first, self.snapshot(root))
            source = (roots[0] / "SideStoreSupport/SideStore.swift").read_text()
            self.assertNotIn("__v3_connect", source)
            self.assertNotIn("bookmarkForURL(sideStoreHomeURL)!", source)
            self.assertIn("func ensureServiceConnected()", source)
            self.assertIn("func performRefresh(", source)
    def test_anchor_failure_is_transactional(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); roots = self.fixture(root)
            path = roots[0] / "LiveContainer/LCBootstrap.m"
            path.write_text(path.read_text(encoding="utf-8").replace("NSArray *dirList =", "NSArray *changed ="), encoding="utf-8")
            before = self.snapshot(root)
            with self.assertRaises(SystemExit): self.apply(roots)
            self.assertEqual(before, self.snapshot(root))

    def test_replay_rejects_missing_output_and_patch_revision(self):
        for change in ("missing-output", "patch-revision", "source-pin"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); roots = self.fixture(root)
                self.apply(roots)
                manifest = roots[0] / ".combined-service-startup.json"
                data = json.loads(manifest.read_text())
                if change == "missing-output": data["files"].pop()
                elif change == "source-pin": data["pins"][0] = "0" * 40
                else: data["templates"]["patch_combined_service_startup.py"] = "0" * 64
                manifest.write_text(json.dumps(data))
                before = self.snapshot(root)
                with self.assertRaises(SystemExit): self.apply(roots)
                self.assertEqual(before, self.snapshot(root))
