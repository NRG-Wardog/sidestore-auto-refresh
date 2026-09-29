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
TFA_SPEC = importlib.util.spec_from_file_location(
    "sidesign_typed_2fa_patch", ROOT / "scripts/patch_sidesign_2fa_state.py")
tfa_patch = importlib.util.module_from_spec(TFA_SPEC)
TFA_SPEC.loader.exec_module(tfa_patch)
V3_SERVICE_SPEC = importlib.util.spec_from_file_location(
    "v3_service_patch", ROOT / "scripts/patch_v3_service.py")
v3_service_patch = importlib.util.module_from_spec(V3_SERVICE_SPEC)
V3_SERVICE_SPEC.loader.exec_module(v3_service_patch)


def pinned_source():
    value = os.environ.get("SIDESIGN_TEST_SOURCE")
    return Path(value) if value else None


def pinned_sidestore_source():
    value = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
    return Path(value) if value else None


class SideSignPrivacyTests(unittest.TestCase):
    def test_pinned_package_references_match_headless_retained_sources(self):
        source = pinned_sidestore_source()
        if not source or not source.exists():
            self.skipTest("pinned embedded SideStore source is supplied by macOS CI")
        excluded = set(v3_service_patch.HEADLESS_SIDESTORE_VIEW_FILES +
                       v3_service_patch.HEADLESS_SIDESTORE_AUX_UI_FILES)
        retained = []
        for path in source.rglob("*.swift"):
            relative = path.relative_to(source).as_posix()
            source_relative = relative[len("SideStore/"):] if relative.startswith("SideStore/") else relative
            if relative.startswith(".build/") or source_relative in excluded:
                continue
            if "MarkdownKit" in path.read_text(encoding="utf-8", errors="replace"):
                retained.append(relative)
        self.assertEqual(retained, [], "MarkdownKit is used only by excluded CollapsingMarkdownView")
        self.assertIn("Views/Components/UIKit/CollapsingMarkdownView.swift", excluded)

        cache_manager = (source / "SideStore/Core/Storage/CacheManager.swift").read_text(encoding="utf-8")
        clear_cache = (source / "SideStore/Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift").read_text(encoding="utf-8")
        self.assertIn("io.sidestore.Nuke", cache_manager)
        self.assertIn("import Nuke", clear_cache)
        app_delegate = (source / "AltStore/AppDelegate.swift").read_text(encoding="utf-8")
        self.assertIn("prepareImageCache()", app_delegate)

    def test_pinned_typed_2fa_patch_is_idempotent_and_omits_raw_retry_payloads(self):
        source = pinned_source()
        if not source or not source.exists():
            self.skipTest("pinned SideSign source is supplied by macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("Sources/DeveloperPortal/Authentication.swift",
                             "Sources/DeveloperPortal/DeveloperPortalAPI.swift"):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
            tfa_patch.patch(root)
            first = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.swift")}
            tfa_patch.patch(root)
            second = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.swift")}
            self.assertEqual(first, second)
            auth = (root / "Sources/DeveloperPortal/Authentication.swift").read_text(encoding="utf-8")
            api = (root / "Sources/DeveloperPortal/DeveloperPortalAPI.swift").read_text(encoding="utf-8")
            self.assertIn("return .retry(.incorrectCode)", auth)
            self.assertIn("verificationFailure?.userMessage ?? rawError", api)
            self.assertNotIn("2FA verification failed, retrying: ", auth)
            self.assertNotIn("Body: \\(rawStr)", auth)

    def test_generated_typed_incorrect_code_message_executes(self):
        source = pinned_source()
        compiler = shutil.which("swiftc")
        if not source or not source.exists() or not compiler:
            self.skipTest("pinned SideSign source and Swift compiler are supplied by macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api_path = root / "Sources/DeveloperPortal/DeveloperPortalAPI.swift"
            auth_path = root / "Sources/DeveloperPortal/Authentication.swift"
            api_path.parent.mkdir(parents=True)
            shutil.copyfile(source / "Sources/DeveloperPortal/DeveloperPortalAPI.swift", api_path)
            shutil.copyfile(source / "Sources/DeveloperPortal/Authentication.swift", auth_path)
            tfa_patch.patch(root)
            api = api_path.read_text(encoding="utf-8")
            start = api.index("// V3_TFA_TYPED_STATE_V1:")
            end = api.index("public enum TwoFactorRequest", start)
            typed_enum = api[start:end]
            harness = "import Foundation\n" + typed_enum + r'''
@main struct Tests {
    static func main() {
        precondition(TwoFactorVerificationFailure.incorrectCode.userMessage ==
            "The verification code was not accepted. Enter a new code and try again.")
        precondition(TwoFactorVerificationFailure.serviceUnavailable != .incorrectCode)
        print("SIDESIGN_TYPED_2FA_PASS")
    }
}
'''
            swift = root / "main.swift"
            executable = root / "typed-2fa"
            swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SIDESIGN_TYPED_2FA_PASS", result.stdout)

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
            logging_path = root / patch.SIDESTORE_LOGGING
            logging_path.write_text(v3_service_patch.headless_safe_log_format(
                logging_path.read_text(encoding="utf-8")), encoding="utf-8")
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
            logging_path = root / patch.SIDESTORE_LOGGING
            logging_path.write_text(v3_service_patch.headless_safe_log_format(
                logging_path.read_text(encoding="utf-8")), encoding="utf-8")
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
        let operation = OperationFixture()
        // Successful certificate and OCSP messages can contain no generic
        // error marker, so the generated production sink must still omit the
        // full serial from every user-copyable log path.
        debugLog("[CertificateManager] Successfully loaded certificate (serial: 0123456789ABCDEF0123456789ABCDEF).")
        debugLog("[CertificateManager] deleteCertificate: 0123456789ABCDEF0123456789ABCDEF")
        debugLog("[OCSPValidator] Certificate 0123456789ABCDEF0123456789ABCDEF confirmed REVOKED by OCSP.")
        debugLog("[OCSPValidator] Direct Live HTTP OCSP status for 0123456789ABCDEF0123456789ABCDEF: good")
        operation.debugLog("[SignInOperation] Successfully requested certificate (Serial: 0123456789ABCDEF0123456789ABCDEF).")
        operation.debugLog("[SignInOperation] Active certificate (0123456789ABCDEF0123456789ABCDEF) and running bundle certificate (FEDCBA9876543210FEDCBA9876543210) mismatch detected.")
        operation.debugLog("[UpdateAppCertificateOperation] Loaded custom certificate '0123456789ABCDEF0123456789ABCDEF' for app 'Example'.")
        debugLog("SAFE_DIAGNOSTIC_MARKER")
        debugLog("[SignInOperation] error=SECRET_DSID_PHONE_RAW_2FA_BODY")
        verboseLog("authorization header SECRET_SECURITY_CODE_COOKIE")
        operation.debugLog("[SignInOperation] authentication failed: SECRET_GRANDSlam_RESPONSE")
        operation.verboseLog("headers=SECRET_AUTHORIZATION_HEADER")
        operation.debugLog("[PipelineRunner] started for: com.example.privateguest")
        operation.verboseLog("bundleIdentifier=com.spotify.client")
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
            self.assertNotIn("0123456789ABCDEF0123456789ABCDEF", result.stdout)
            self.assertNotIn("FEDCBA9876543210FEDCBA9876543210", result.stdout)
            self.assertNotIn("SECRET_", result.stdout)
            self.assertNotIn("com.example.privateguest", result.stdout)
            self.assertNotIn("com.spotify.client", result.stdout)
            self.assertNotIn("fixture.app", result.stdout)

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
