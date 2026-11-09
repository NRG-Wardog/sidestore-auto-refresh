"""Privacy and typed-2FA checks execute the exact maintained owner sources.

Swift harnesses exercise real logging sinks with controlled inputs. They do not
perform authentication or claim full on-device authentication coverage.
"""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tests.owner_source import owner_source, owner_root, owner_pin

ROOT = Path(__file__).resolve().parents[2]
SIGN = "SideStore/Dependencies/SideSign"
SIDE_LOG = "SideStore/Core/Logging/SideStoreLogging.swift"
OPERATIONS_LOG = "SideStore/Core/Logging/OperationLogging.swift"
V3_SERVICE_SPEC = importlib.util.spec_from_file_location("v3_service_patch", ROOT/"scripts/patch_v3_service.py")
v3_service_patch = importlib.util.module_from_spec(V3_SERVICE_SPEC)
V3_SERVICE_SPEC.loader.exec_module(v3_service_patch)


def pinned_sidestore_source():
    owner_source(SIDE_LOG, owner="SideStore")
    return owner_root("SideStore")


def sign_sources():
    root = owner_root(SIGN)
    paths = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "--name-only",
                                     owner_pin(SIGN), "--", "Sources"], text=True).splitlines()
    paths = {path for path in paths if path.endswith(".swift")}
    actual = {path.relative_to(root).as_posix() for path in (root/"Sources").rglob("*.swift")}
    if not paths or actual != paths:
        raise ValueError("SideSign Swift inventory differs from pinned owner")
    return {path: owner_source(path, owner=SIGN) for path in sorted(paths)}


def audit_sign_sinks(sources):
    logging = sources["Sources/Logging.swift"]
    for token in ("SIDESIGN_USER_LOG_PRIVACY_V1", "AnisetteKitLogging.setLogging(false)"):
        if token not in logging:
            raise ValueError("SideSign logging privacy configuration differs")
    for signature in ("public func debugLog", "public func verboseLog"):
        start = logging.index(signature)
        body = logging[start:logging.index("\n}", start)]
        if any(token in body for token in ("print(", "NSLog(", "text()")):
            raise ValueError("SideSign sink emits or evaluates sensitive values")
    for path, source in sources.items():
        if path != "Sources/Logging.swift" and any(token in source for token in ("print(", "debugPrint(", "NSLog(", "os_log(")):
            raise ValueError("SideSign has an unreviewed direct logging sink: " + path)


class SideSignOwnerSourceTests(unittest.TestCase):
    def test_typed_2fa_omits_raw_retry_payloads(self):
        auth = owner_source("Sources/DeveloperPortal/Authentication.swift", owner=SIGN)
        api = owner_source("Sources/DeveloperPortal/DeveloperPortalAPI.swift", owner=SIGN)
        self.assertIn("return .retry(.incorrectCode)", auth)
        self.assertIn("verificationFailure?.userMessage ?? rawError", api)
        self.assertNotIn("2FA verification failed, retrying: ", auth)
        self.assertNotIn(r"Body: \(rawStr)", auth)

    def test_all_sidesign_sources_reject_new_logging_sinks(self):
        sources = sign_sources()
        audit_sign_sinks(sources)
        for path, addition in (("Sources/DeveloperPortal/Authentication.swift", '\nprint("PRIVATE")'),
                               ("Sources/NewLogger.swift", 'NSLog("PRIVATE")')):
            mutated = dict(sources)
            mutated[path] = mutated.get(path, "") + addition
            with self.assertRaisesRegex(ValueError, "unreviewed direct"):
                audit_sign_sinks(mutated)
        mutated = dict(sources)
        mutated["Sources/Logging.swift"] = mutated["Sources/Logging.swift"].replace("_ = text", "_ = text()")
        with self.assertRaisesRegex(ValueError, "evaluates"):
            audit_sign_sinks(mutated)

    def test_sidestore_log_filters_cover_serials_and_raw_errors(self):
        logging = owner_source(SIDE_LOG, owner="SideStore")
        operations = owner_source(OPERATIONS_LOG, owner="SideStore")
        for source in (logging, operations):
            self.assertIn("SIDESTORE_TRANSITIVE_ERROR_LOG_PRIVACY_V1", source)
            self.assertEqual(source.count("shouldOmitUserCopyableSideStoreLog(rawMessage)"), 2)
        self.assertEqual(operations.count("formatLogMessage(rawMessage)"), 2)
        self.assertNotIn("error.localizedDescription", operations)
        for token in ("certificateSerial", "serialHex", "certificateValue", "ocspValue"):
            self.assertIn(token, logging)

    def test_pinned_package_references_match_headless_retained_sources(self):
        source = pinned_sidestore_source()
        if not source or not source.exists():
            self.skipTest("pinned embedded SideStore source is supplied by macOS CI")
        excluded = set(v3_service_patch.HEADLESS_SIDESTORE_VIEW_FILES +
                       v3_service_patch.HEADLESS_SIDESTORE_AUX_UI_FILES)
        # Search immutable tracked owner blobs, not unchecked worktree files
        # or the separately acquired SideSign submodule below this directory.
        pin = owner_pin("SideStore")
        result = subprocess.run(["git", "-C", str(source), "grep", "-l", "MarkdownKit", pin, "--", "*.swift"],
                                capture_output=True, text=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        retained = []
        for row in result.stdout.splitlines():
            self.assertTrue(row.startswith(pin + ":"))
            relative = row[len(pin) + 1:]
            source_relative = relative[len("SideStore/"):] if relative.startswith("SideStore/") else relative
            if source_relative not in excluded:
                retained.append(relative)
        self.assertEqual(retained, [], "MarkdownKit is used only by excluded CollapsingMarkdownView")
        self.assertIn("Views/Components/UIKit/CollapsingMarkdownView.swift", excluded)

        cache_manager = owner_source("SideStore/Core/Storage/CacheManager.swift", owner="SideStore")
        clear_cache = owner_source("SideStore/Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift", owner="SideStore")
        self.assertIn("io.sidestore.Nuke", cache_manager)
        self.assertNotIn("import Nuke", clear_cache)
        self.assertIn("V3LegacyImageCacheCleanup", clear_cache)
        app_delegate = owner_source("AltStore/AppDelegate.swift", owner="SideStore")
        self.assertNotIn("prepareImageCache()", app_delegate)
        self.assertIn("V3_HEADLESS_IMAGE_PIPELINE_REMOVED_V1", app_delegate)

class SideSignOwnerRuntimeTests(unittest.TestCase):
    def run_harness(self, source):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise RuntimeError("Swift compiler required for maintained privacy/2FA harnesses")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            swift, executable = root/"main.swift", root/"probe"
            swift.write_text(source)
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            return subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)

    def test_typed_incorrect_code_message_executes_and_rejects_mutation(self):
        api = owner_source("Sources/DeveloperPortal/DeveloperPortalAPI.swift", owner=SIGN)
        start = api.index("// V3_TFA_TYPED_STATE_V1:")
        typed_enum = api[start:api.index("public enum TwoFactorRequest", start)]
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
        result = self.run_harness(harness)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIDESIGN_TYPED_2FA_PASS", result.stdout)
        # Change only production enum bytes, never the expected assertion.
        changed = typed_enum.replace("The verification code was not accepted.", "Incorrect status.")
        self.assertNotEqual(changed, typed_enum)
        self.assertNotEqual(self.run_harness(harness.replace(typed_enum, changed, 1)).returncode, 0)

    def test_sidestore_sinks_redact_sensitive_values_and_reject_filter_bypass(self):
        logging = owner_source(SIDE_LOG, owner="SideStore")
        operations = owner_source(OPERATIONS_LOG, owner="SideStore")
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
        result = self.run_harness(harness)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIDESTORE_LOG_PRIVACY_PASS", result.stdout)
        self.assertIn("SAFE_DIAGNOSTIC_MARKER", result.stdout)
        for secret in ("0123456789ABCDEF0123456789ABCDEF", "FEDCBA9876543210FEDCBA9876543210",
                       "SECRET_", "com.example.privateguest", "com.spotify.client", "fixture.app"):
            self.assertNotIn(secret, result.stdout)
        changed = logging.replace("guard !shouldOmitUserCopyableSideStoreLog(rawMessage) else { return }", "")
        self.assertNotEqual(changed, logging)
        leaked = self.run_harness(harness.replace(logging, changed, 1))
        self.assertEqual(leaked.returncode, 0, leaked.stderr)
        self.assertIn("0123456789ABCDEF0123456789ABCDEF", leaked.stdout)

    def test_sidesign_sinks_do_not_evaluate_values_and_reject_evaluation(self):
        logging = owner_source("Sources/Logging.swift", owner=SIGN).replace("import AnisetteKit\n", "")
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
        result = self.run_harness(harness)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIDESIGN_LOG_PRIVACY_PASS", result.stdout)
        self.assertNotIn("SECRET_", result.stdout)
        changed = logging.replace("_ = text", "_ = text()")
        self.assertNotEqual(changed, logging)
        self.assertNotEqual(self.run_harness(harness.replace(logging, changed, 1)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
