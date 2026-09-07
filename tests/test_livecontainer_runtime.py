"""Compile the actual scheduler against OS doubles; do not call this device proof."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LiveContainerRuntimeTests(unittest.TestCase):
    def test_scheduler_typechecks_and_executes_failure_paths(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        paths = ["tests/fixtures/livecontainer_scheduler_stubs.swift", "scripts/templates/livecontainer_refresh_policy.swift",
                 "scripts/templates/livecontainer_refresh_scheduler.swift", "tests/fixtures/livecontainer_scheduler_harness.swift"]
        source = "\n".join((ROOT / p).read_text() for p in paths)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "Test.swift"
            exe = Path(directory) / "scheduler-test"
            file.write_text(source)
            build = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(file), "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SCHEDULER_BEHAVIOR_TESTS_PASSED", result.stdout)
            self.assertIn("error_code=42", result.stdout)
            self.assertNotIn(r"\(runID", result.stdout)

    def test_installed_profile_identity_and_expiration(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        source = (ROOT / "scripts/templates/livecontainer_refresh_policy.swift").read_text() + r'''
let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: directory) }
let url = directory.appendingPathComponent("profile.fixture")
let expiry = Date(timeIntervalSince1970: 1900000000)
let plist: [String: Any] = ["UUID": "TEST-PROFILE-UUID", "ApplicationIdentifierPrefix": ["TESTTEAM"],
    "Entitlements": ["application-identifier": "TESTTEAM.com.kdt.livecontainer.TESTTEAM"], "ExpirationDate": expiry]
let xml = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
try (Data([0x30, 0x82, 0x01, 0x02]) + xml + Data([0x01, 0x02])).write(to: url)
let profile = try LiveContainerHostProfile.read(at: url, expectedBundleID: "com.kdt.livecontainer.TESTTEAM")
precondition(profile.expiration == expiry && profile.uuid == "TEST-PROFILE-UUID")
do { _ = try LiveContainerHostProfile.read(at: url, expectedBundleID: "another.app"); fatalError("wrong identity accepted") } catch {}
try Data("not a provisioning profile".utf8).write(to: url)
do { _ = try LiveContainerHostProfile.read(at: url, expectedBundleID: "com.kdt.livecontainer.TESTTEAM"); fatalError("invalid profile accepted") } catch {}
print("INSTALLED_PROFILE_TESTS_PASSED")
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            exe = Path(directory) / "profile-test"
            file.write_text(source)
            build = subprocess.run([compiler, "-swift-version", "5", str(file), "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INSTALLED_PROFILE_TESTS_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
