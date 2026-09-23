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


def pinned_sidestore_source():
    value = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
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

    def test_transitive_side_store_error_logs_are_omitted_from_copy_logs(self):
        source = pinned_sidestore_source()
        if not source or not source.exists():
            self.skipTest("pinned embedded SideStore source is supplied by macOS CI")
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, patch.SIDESTORE_PIN)
        for relative in (
            "SideStore/Core/Logging/SideStoreLogging.swift",
            "SideStore/Core/Logging/OperationLogging.swift",
            "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
            "SideStore/Core/Operations/PipelineRunner.swift",
        ):
            self.assertTrue((source / relative).is_file(), relative)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (patch.SIDESTORE_LOGGING, patch.OPERATION_LOGGING):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
            patch.patch_sidestore_tree(root)
            first = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.swift")}
            patch.patch_sidestore_tree(root)
            second = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.swift")}
            self.assertEqual(first, second, "SideStore transitive log filter must be idempotent")
            patch.verify_sidestore_tree(root)

    def test_actual_pinned_side_store_log_sinks_do_not_emit_error_payloads(self):
        source = pinned_sidestore_source()
        if not source or not source.exists():
            self.skipTest("pinned embedded SideStore source is supplied by macOS CI")
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; SideStore privacy sink runs in macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (patch.SIDESTORE_LOGGING, patch.OPERATION_LOGGING):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
            patch.patch_sidestore_tree(root)
            logging = (root / patch.SIDESTORE_LOGGING).read_text(encoding="utf-8")
            operations = (root / patch.OPERATION_LOGGING).read_text(encoding="utf-8")
            harness = r'''
import Foundation
enum OperationsLoggingControl {
    static func isLoggingEnabled(for type: Any.Type) -> Bool { true }
}
func getOperationsLogTag(level: String) -> String { level + ": " }
'''
            harness += logging + "\n" + operations + r'''
struct OperationFixture: OperationLogging {}
@main struct Tests {
    static func main() {
        SideStoreLogging.setLogging(true)
        debugLog("SAFE_DIAGNOSTIC_MARKER")
        debugLog("[SignInOperation] error=SECRET_DSID_PHONE_RAW_2FA_BODY")
        verboseLog("authorization header SECRET_SECURITY_CODE_COOKIE")
        let operation = OperationFixture()
        operation.debugLog("[SignInOperation] authentication failed: SECRET_GRANDSlam_RESPONSE")
        operation.verboseLog("headers=SECRET_AUTHORIZATION_HEADER")
        logOperationSummary(operation: "signIn", target: "fixture.app", status: "FAILED", elapsed: 0.1,
            error: NSError(domain: "SideSign", code: 20,
                userInfo: [NSLocalizedDescriptionKey: "SECRET_RAW_ERROR_CAUSE_JSON_BODY"]))
        print("SIDESTORE_LOG_PRIVACY_PASS")
    }
}
'''
            swift = root / "main.swift"
            executable = root / "sidestore-privacy-test"
            swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SIDESTORE_LOG_PRIVACY_PASS", result.stdout)
            self.assertIn("SAFE_DIAGNOSTIC_MARKER", result.stdout)
            self.assertNotIn("SECRET_", result.stdout)

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
