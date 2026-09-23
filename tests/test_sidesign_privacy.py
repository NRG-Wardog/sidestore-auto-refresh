"""Privacy tests against the actual pinned SideSign sources used by the IPA build."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sidesign_privacy_patch", ROOT / "scripts/patch_sidesign_privacy.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


def pinned_source():
    value = os.environ.get("SIDESIGN_TEST_SOURCE")
    return Path(value) if value else None


class SideSignPrivacyTests(unittest.TestCase):
    def test_pinned_source_logging_and_sensitive_call_sites_are_audited(self):
        source = pinned_source()
        if not source or not source.exists():
            self.skipTest("pinned SideSign source is supplied by macOS CI")
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, patch.PIN)
        for relative in (
            "Sources/DeveloperPortal/Authentication.swift",
            "Sources/Logging.swift",
            "Sources/DeveloperPortal/AuthDevices.swift",
            "Sources/DeveloperPortal/DeveloperPortalAPI.swift",
            "Sources/Anisette/AnisetteDataManager.swift",
            "Sources/Anisette/RemoteAnisetteDataProvider.swift",
        ):
            self.assertTrue((source / relative).is_file(), relative)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(source / "Sources", root / "Sources")
            patch.patch_tree(root)
            first = {path.relative_to(root): path.read_bytes() for path in (root / "Sources").rglob("*.swift")}
            patch.patch_tree(root)
            second = {path.relative_to(root): path.read_bytes() for path in (root / "Sources").rglob("*.swift")}
            self.assertEqual(first, second, "privacy patch must be idempotent")
            patch.verify(root)

    def test_actual_patched_logging_sink_never_evaluates_or_emits_values(self):
        source = pinned_source()
        if not source or not source.exists():
            self.skipTest("pinned SideSign source is supplied by macOS CI")
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; executable privacy sink runs in macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(source / "Sources", root / "Sources")
            patch.patch_tree(root)
            logging = (root / "Sources/Logging.swift").read_text(encoding="utf-8")
            logging = logging.replace("import AnisetteKit\n", "")
            harness = r'''
enum AnisetteKitLogging {
    static var enabled = false
    static func setLogging(_ value: Bool) { enabled = value }
}
extension Data {
    func hexEncodedString() -> String { map { String(format: "%02x", $0) }.joined() }
}
'''
            harness += logging + r'''
@main struct Tests {
    static func main() {
        var evaluated = false
        debugLog({ evaluated = true; return "SECRET_DSID_PHONE_TOKEN_RAW_BODY" }())
        verboseLog({ evaluated = true; return "SECRET_AUTHORIZATION_HEADER" }())
        SideSignLogging.setLogging(true)
        precondition(!evaluated, "logging evaluated a sensitive autoclosure")
        precondition(!SideSignLogging.isLoggingEnabled, "SideSign re-enabled verbose output")
        precondition(!AnisetteKitLogging.enabled, "AnisetteKit logging was enabled")
        print("SIDESIGN_LOG_PRIVACY_PASS")
    }
}
'''
            swift = root / "main.swift"
            executable = root / "privacy-test"
            swift.write_text(harness, encoding="utf-8")
            subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("SIDESIGN_LOG_PRIVACY_PASS", result.stdout)
            self.assertNotIn("SECRET_", result.stdout)


if __name__ == "__main__":
    unittest.main()
