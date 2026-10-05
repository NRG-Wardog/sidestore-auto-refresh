from pathlib import Path
import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("combined_contract", ROOT / "scripts/patch_combined_refresh_contract.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)
SERVICE_SPEC = importlib.util.spec_from_file_location("v3_service_patch", ROOT / "scripts/patch_v3_service.py")
service = importlib.util.module_from_spec(SERVICE_SPEC)
SERVICE_SPEC.loader.exec_module(service)
BACKGROUND_SPEC = importlib.util.spec_from_file_location("background_automation", ROOT / "scripts/patch_background_automation.py")
background = importlib.util.module_from_spec(BACKGROUND_SPEC)
BACKGROUND_SPEC.loader.exec_module(background)


class CombinedRefreshContractTests(unittest.TestCase):
    def test_package_verifier_does_not_require_interpolation_fragments_in_binary(self):
        # Optimized Swift can encode short fragments as instruction immediates.
        # Link checks must not assume that a binary contains a rendered log line.
        for diagnostic in (b"[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=shared",
                           b"[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=process",
                           b"[LC_KEYCHAIN] GROUP_SELECTED service="):
            with self.subTest(diagnostic=diagnostic):
                patch.verify_keychain_selection_contract(
                    diagnostic + b"\x00LCSharedKeychainReadyV1")

    def test_package_verifier_requires_selection_and_migration_markers(self):
        cases = (
            (b"[LC_KEYCHAIN] SHARED_GROUP_SELECTED scope=shared\x00LCSharedKeychainReadyV1",
             "Keychain selection diagnostic missing"),
            (b"[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=shared",
             "Legacy Keychain migration contract missing"),
        )
        for executable, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                patch.verify_keychain_selection_contract(executable)

    @unittest.skipUnless(shutil.which("swiftc"), "optimized Swift diagnostic executes on macOS CI")
    def test_optimized_production_diagnostic_emits_scope_and_passes_link_gate(self):
        template = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text()
        diagnostic = next(line.strip() for line in template.splitlines()
                          if 'debugLog("[LC_KEYCHAIN] GROUP_SELECTED' in line)
        # Exercise the shipped interpolation, rather than concatenating an
        # imaginary complete diagnostic into a mock executable.
        source = '''import Foundation
func debugLog(_ text: @autoclosure () -> String) { print(text()) }
let service = CommandLine.arguments[1]
for scope in ["shared", "process"] {
''' + diagnostic + '''
}
print("LCSharedKeychainReadyV1")
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            swift = root / "diagnostic.swift"
            executable = root / "diagnostic"
            swift.write_text(source)
            subprocess.run([shutil.which("swiftc"), "-O", str(swift), "-o", str(executable)],
                           check=True, capture_output=True, text=True, timeout=60)
            output = subprocess.run([str(executable), "storage"], check=True,
                                    capture_output=True, text=True, timeout=10).stdout.splitlines()
            self.assertEqual(output, [
                "[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=shared",
                "[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=process",
                "LCSharedKeychainReadyV1"])
            patch.verify_keychain_selection_contract(executable.read_bytes())

    def test_standalone_manifest_persists_only_safe_failure_fields(self):
        original = r'''    private let refreshIdentifier: String = UUID().uuidString
    private var runningApplications: Set<String> = []
    init(installedApps: [InstalledApp], context: OperationContext) throws {
        self.installedApps = installedApps
        try super.init(context: context)
    }
                self.debugLog("Failed to refresh apps in background. \(error)")
                self.debugLog("Failed to refresh apps in background. \(error.localizedDescription)")
                content.body = error.localizedDescription
        guard !self.installedApps.isEmpty else {
            let error = OperationError.noInstalledApps
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        if UserDefaults.standard.enableEMPforWireguard {
            let filteredApps = await dbContext.perform {
                return self.installedApps.filter { !self.runningApplications.contains($0.bundleIdentifier) }
            }
            let group = AppManager.shared.refresh(apps, presentingViewController: nil)
            group.beginInstallationHandler = { [weak self] (installedApp) in
        }
            group.completionHandler = { (results) in
                self.setProgress(100)
                continuation.resume(returning: results)
            }
    private func startListeningForRunningApps() {
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
            operation.parent.mkdir(parents=True)
            operation.write_text(original)
            background.patch_background_operation(root)
            generated = operation.read_text()
            background.patch_background_operation(root)
            self.assertEqual(generated, operation.read_text(), "standalone patch must be idempotent")
            start = generated.index("    private func automaticRefreshDefaults()")
            end = generated.index("    private func startListeningForRunningApps()", start)
            helper = generated[start:end]
            self.assertIn('"error_category": category, "error_code": (error as NSError).code', helper)
            self.assertIn('AutomaticRefreshFailureCategory.safeMessage(error, event: .failed)', helper)
            self.assertNotIn("error.localizedDescription", helper)
            self.assertNotIn('(error as NSError).domain', helper)

            # Compose the combined-only upgrade over the actual standalone output,
            # then replay it to check both generated output and provenance idempotence.
            patch._patch_verified(root)
            combined = operation.read_text()
            self.assertIn('CombinedFailure.capture(V3HeadlessPairingFailure.tagIfInvalidPairing(error)', combined)
            self.assertIn('"error": failure.message, "failure": failure.wire', combined)
            self.assertIn('content.body = failure.message', combined)
            self.assertNotIn('AutomaticRefreshFailureCategory', combined,
                             'the combined headless target does not include the legacy scheduler classifier')
            self.assertNotIn("error.localizedDescription", combined)
            self.assertNotIn('(error as NSError).domain', combined)
            combined_snapshot = operation.read_bytes()
            patch._patch_verified(root)
            self.assertEqual(combined_snapshot, operation.read_bytes(), "combined overlay must be idempotent")

            compiler = shutil.which("swiftc")
            if not compiler:
                self.skipTest("requires Swift; generated-source checks above passed")
            HARNESS_FILE_MANAGER = r'''

/// A macOS runner has no App Group entitlement, so the real container lookup
/// reports every group unavailable and the refresh helpers would correctly
/// refuse. This answers the same question with a real directory.
final class HarnessContainerFileManager: FileManager {
    static var roots: [String: URL] = [:]
    override func containerURL(forSecurityApplicationGroupIdentifier identifier: String) -> URL? {
        if let existing = HarnessContainerFileManager.roots[identifier] { return existing }
        let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("v3-harness-store-\(getpid())", isDirectory: true)
            .appendingPathComponent(identifier.replacingOccurrences(of: "/", with: "_"),
                                    isDirectory: true)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        HarnessContainerFileManager.roots[identifier] = root
        return root
    }
}
'''
            shared_group = (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text(encoding="utf-8")
            swift = shared_group + "\n" + HARNESS_FILE_MANAGER + r'''
import Foundation
enum AutomaticRefreshEvent { case failed }
enum AutomaticRefreshFailureCategory: String {
    case unknown
    static func classify(_ error: Error) -> Self { .unknown }
    static func safeMessage(_ error: Error, event: AutomaticRefreshEvent) -> String {
        "The refresh failed; no safe underlying cause was available."
    }
}
struct InstalledApp {
    let bundleIdentifier: String
    let name: String
    let refreshedDate: Date
    let expirationDate: Date
}
enum StoreApp { static let altstoreAppID = "com.example.host" }
final class Harness {
    let refreshIdentifier = "safe-run-id"
    var installedApps: [InstalledApp] = []
    func debugLog(_ message: String) {}
    func persist(_ error: Error) {
        persistAutomaticRefreshVerification(results: ["com.example.app": .failure(error)], attemptedAppIDs: ["com.example.app"])
    }
HELPER
}
let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
let manifestKey = "liveContainerAutoRefreshVerification"
let expectedRunKey = "liveContainerAutoRefreshExpectedRunID"
let previousManifest = defaults.object(forKey: manifestKey)
let previousExpectedRunID = defaults.object(forKey: expectedRunKey)
defer {
    if let previousManifest { defaults.set(previousManifest, forKey: manifestKey) }
    else { defaults.removeObject(forKey: manifestKey) }
    if let previousExpectedRunID { defaults.set(previousExpectedRunID, forKey: expectedRunKey) }
    else { defaults.removeObject(forKey: expectedRunKey) }
}
defaults.removeObject(forKey: manifestKey)
defaults.set("safe-run-id", forKey: "liveContainerAutoRefreshExpectedRunID")
let harness = Harness()
let providerError = NSError(domain: "private.invalid/token=SECRET_TOKEN", code: 73,
    userInfo: [NSLocalizedDescriptionKey: "failed at /private/user/path?access_token=SECRET_TOKEN"])
V3SharedAppGroup.containerFileManager = HarnessContainerFileManager()
V3SharedAppGroup.publishRuntimeGroup(V3SharedAppGroup.packagedGroup)
harness.persist(providerError)
let manifest = defaults.dictionary(forKey: manifestKey)!
let rows = manifest["results"] as! [[String: Any]]
let row = rows[0]
precondition(row["error_category"] as? String == "unknown")
precondition(row["error_code"] as? Int == 73)
precondition(row["error"] as? String == "The refresh failed; no safe underlying cause was available.")
precondition(row["error_domain"] == nil)
let data = try PropertyListSerialization.data(fromPropertyList: manifest, format: .xml, options: 0)
let persisted = String(decoding: data, as: UTF8.self)
precondition(!persisted.contains("SECRET_TOKEN") && !persisted.contains("private.invalid") && !persisted.contains("/private/user/path"))
print("standalone manifest privacy PASS")
'''.replace("HELPER", helper)
            with tempfile.TemporaryDirectory() as swift_directory:
                source = Path(swift_directory) / "main.swift"
                executable = Path(swift_directory) / "privacy-test"
                source.write_text(swift)
                compiled = subprocess.run([compiler, str(source), "-o", str(executable)], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("standalone manifest privacy PASS", result.stdout)

    def test_full_patch_composition_is_transactional_on_pinned_source(self):
        source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.getenv("SIDESTORE_TEST_SOURCE")
        if not source: self.skipTest("pinned embedded SideStore source required")
        revision = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, patch.PIN)
        spec = importlib.util.spec_from_file_location("background_automation", ROOT / "scripts/patch_background_automation.py")
        background = importlib.util.module_from_spec(spec); spec.loader.exec_module(background)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "side"
            paths = [
                "AltStore/Core/Components/Keychain.swift",
                "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift",
                "SideStore/Core/Auth/AuthManager.swift",
                "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
                "SideStore/Utils/importexport/ImportExport.swift",
                "SideStore/Core/Certificates/CertificateManager.swift",
            ]
            for relative in paths:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Read the pinned commit blob rather than the checkout file: the
                # test source may be dirty after another patch-generation test.
                pristine = subprocess.check_output(
                    ["git", "-C", source, "show", f"{patch.PIN}:{relative}"]
                )
                target.write_bytes(pristine)
            background.patch_background_operation(root)
            sign_in = root / paths[3]
            sign_in.write_text(service.patch_sign_in_operation(sign_in.read_text(encoding="utf-8")),
                               encoding="utf-8")
            operation = root / paths[1]
            prepared = operation.read_bytes()
            def snapshot():
                return {name: (root / name).read_bytes() for name in paths + [".combined-refresh-contract.json"] if (root / name).exists()}
            # Shared Keychain transforms successfully in staging, then contract rejects
            # the changed privacy-safe anchor. Neither Keychain nor operation nor manifest may leak out.
            operation.write_bytes(prepared.replace(
                b"let category = AutomaticRefreshFailureCategory.classify(error).rawValue",
                b"let changedCategory = AutomaticRefreshFailureCategory.classify(error).rawValue"))
            before = snapshot()
            with mock.object(patch, "verify_pin", return_value=None):
                with self.assertRaises(SystemExit):
                    patch.patch_combined_cli(root)
            self.assertEqual(before, snapshot())
            operation.write_bytes(prepared)
            before = snapshot()
            with mock.object(patch, "verify_pin", side_effect=SystemExit("wrong pin")):
                with self.assertRaises(SystemExit):
                    patch.patch_combined_cli(root)
            self.assertEqual(before, snapshot())
            with mock.object(patch, "verify_pin", return_value=None):
                patch.patch_combined_cli(root)
            applied = snapshot()
            self.assertIn(b"Keychain.shared.embeddedAuthenticationFailure(error)", applied[paths[1]])
            self.assertIn(b"LC_AUTO_REFRESH_CREDENTIAL_SNAPSHOT_V1", applied[paths[1]])
            self.assertIn(b"LC_AUTH_CREDENTIALS_MISSING_V1", applied[paths[1]])
            self.assertIn(b'"failure": failure.wire', applied[paths[1]])
            self.assertIn(b"CombinedFailure.capture(V3HeadlessPairingFailure.tagIfInvalidPairing(error)", applied[paths[1]])
            self.assertIn(b'"error": failure.message', applied[paths[1]])
            generated = applied[paths[1]].decode("utf-8")
            start = generated.index("private func automaticRefreshDefaults()")
            end = generated.index("private func startListeningForRunningApps()", start)
            manifestHelper = generated[start:end]
            self.assertNotIn("error.localizedDescription", manifestHelper,
                             "the final combined manifest and its failure log must not retain raw provider text")
            self.assertNotIn("error_domain=\\(nsError.domain)", manifestHelper)
            with mock.object(patch, "verify_pin", return_value=None):
                patch.patch_combined_cli(root)
            self.assertEqual(applied, snapshot())
            operation.write_bytes(operation.read_bytes() + b"\n// unexpected drift\n")
            drifted = snapshot()
            with mock.object(patch, "verify_pin", return_value=None):
                with self.assertRaises(SystemExit):
                    patch.patch_combined_cli(root)
            self.assertEqual(drifted, snapshot())

    def fixture(self, root):
        tree = ast.parse((ROOT / "scripts/patch_background_automation.py").read_text(encoding="utf-8"))
        helper = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                      and isinstance(node.value, str) and node.value.startswith("\n    private func automaticRefreshDefaults()"))
        path = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
        path.parent.mkdir(parents=True)
        notification = r'''                self.debugLog("[AUTO_REFRESH] NOTIFICATION_FAILURE failure_category=\(AutomaticRefreshFailureCategory.classify(error).rawValue)")
                content.body = AutomaticRefreshFailureCategory.safeMessage(error, event: .failed)
'''
        path.write_text(notification + helper + "\n    private func startListeningForRunningApps() {}\n")
        return path

    def apply(self, root):
        with mock.object(patch.subprocess, "check_output", return_value=patch.PIN):
            patch.patch(root)

    def test_handoff_uses_host_run_and_manifest_counts_expected_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = self.fixture(root)
            self.apply(root)
            result = file.read_text()
            self.assertIn('"expected_ids": expectedIDs, "requested_ids": requestedIDs, "skipped_ids": skippedIDs', result)
            self.assertIn('"version": 2', result)
            self.assertIn('"schema": "LiveContainerRefreshManifestV2"', result)
            self.assertNotIn('"error": error.localizedDescription', result)
            self.assertIn('defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier', result)
            self.assertIn('CombinedFailure.capture(V3HeadlessPairingFailure.tagIfInvalidPairing(error)', result)
            self.assertIn('"error": failure.message, "failure": failure.wire', result)
            self.assertIn('REFRESH_FAILED \\(failure.technicalDetails)', result)
            self.assertNotIn("error.localizedDescription", result,
                             "the composed output must not persist raw provider response text")
            self.assertNotIn(r"\\(refreshIdentifier)", result)
            self.apply(root)
            self.assertEqual(result, file.read_text())

    def test_replay_and_anchor_drift_fail_closed(self):
        for change in ("anchor", "output", "manifest", "pin"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); file = self.fixture(root)
                if change == "anchor":
                    file.write_text(file.read_text().replace(
                        "let category = AutomaticRefreshFailureCategory.classify(error).rawValue",
                        "let changedCategory = AutomaticRefreshFailureCategory.classify(error).rawValue"))
                elif change != "pin":
                    self.apply(root)
                    if change == "output": file.write_text(file.read_text() + "// unexpected drift")
                    else: (root / ".combined-refresh-contract.json").write_text("{}")
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(SystemExit):
                    if change == "pin":
                        with mock.object(patch.subprocess, "check_output", return_value="0" * 40): patch.patch(root)
                    else: self.apply(root)
                self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_post_dispatch_connection_loss_retains_refresh_lease_without_matching_callback(self):
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        start = handler.index("enum V3RefreshAdmissionFailureResolution")
        end = handler.index("\n}\n\n@MainActor\nclass RefreshHandler", start) + 2
        production_policy = handler[start:end]
        production_handler = handler[handler.index("private func performRefresh(identifier: String,", handler.index("class RefreshHandler")):]
        self.assertIn("V3RefreshAdmissionFailureResolution.resolve(", production_handler)
        self.assertIn("terminalCallbackRunID: v3RefreshTerminalCallbackRunID", production_handler)
        self.assertIn("case .retainUnknownOutcome:", production_handler)
        switch_start = production_handler.index("switch resolution {")
        switch_end = production_handler.index("\n            }\n            throw error", switch_start)
        release_switch = production_handler[switch_start:switch_end]
        branches = release_switch.split("case .")
        not_dispatched = next(branch for branch in branches if branch.startswith("releaseNotDispatched:"))
        terminal_failure = next(branch for branch in branches if branch.startswith("releaseTerminalFailure:"))
        unknown_outcome = next(branch for branch in branches if branch.startswith("retainUnknownOutcome:"))
        self.assertIn('terminalState: "notDispatched"', not_dispatched)
        self.assertIn('terminalState: "failed"', terminal_failure)
        self.assertNotIn("releaseRefreshAdmission", unknown_outcome)
        completed = handler[handler.index("fileprivate func completedRefresh("):
                            handler.index("fileprivate func legacyCompletion(")]
        self.assertIn("refreshRunID == runID", completed)
        self.assertIn("v3RefreshTerminalCallbackRunID = runID", completed)
        legacy = handler[handler.index("fileprivate func legacyCompletion("):
                         handler.index("\n    }", handler.index("fileprivate func legacyCompletion("))]
        self.assertNotIn("v3RefreshTerminalCallbackRunID =", legacy,
            "a legacy callback without run correlation cannot settle the refresh lease")
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift unavailable; executable terminal-evidence policy runs in macOS CI")
        fixture = (ROOT / "tests/fixtures/v3_refresh_admission_terminal_evidence_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.swift"
            executable = Path(directory) / "refresh-admission-evidence"
            source.write_text("import Foundation\n" + production_policy + "\n" + fixture, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_REFRESH_ADMISSION_TERMINAL_EVIDENCE_PASS", result.stdout)

    def test_actual_record_to_bridge_keeps_error_stage_and_sanitizes_logs(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); file = self.fixture(root); self.apply(root)
            # Execute the generated verification helper in isolation. The
            # fixture also carries a separate notification catch block so the
            # combined transform can verify both call sites; that fragment is
            # not part of the Operation class body in this harness.
            generated = file.read_text()
            helper_start = generated.index("    private func automaticRefreshDefaults()")
            helper_end = generated.index("    private func startListeningForRunningApps()")
            helper = generated[helper_start:helper_end]
            self.assertNotIn('"group.com.SideStore.SideStore"', helper,
                             "the helper must resolve its suite from the published group, "
                             "not from a literal that a test can rewrite")
            wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text()
            failure = (ROOT / "scripts/templates/combined_failure.swift").read_text()
            primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
            bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text()
            context = bridge[bridge.index("enum V3CatalogRequestContext {"):]
            context = context[:context.index("\n@MainActor")]
            runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
            classifier_start = runtime.index("enum V3HeadlessPairingFailure {")
            classifier_end = runtime.index("\n// V3_HEADLESS_RUNTIME_V1", classifier_start)
            pairing_classifier = runtime[classifier_start:classifier_end]
            # The refresh helpers resolve their store through V3SharedAppGroup,
            # so the shared identity has to be in the program for this to be the
            # production code path rather than a rewritten one.
            HARNESS_FILE_MANAGER = r'''

/// A macOS runner has no App Group entitlement, so the real container lookup
/// reports every group unavailable and the refresh helpers would correctly
/// refuse. This answers the same question with a real directory.
final class HarnessContainerFileManager: FileManager {
    static var roots: [String: URL] = [:]
    override func containerURL(forSecurityApplicationGroupIdentifier identifier: String) -> URL? {
        if let existing = HarnessContainerFileManager.roots[identifier] { return existing }
        let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("v3-harness-store-\(getpid())", isDirectory: true)
            .appendingPathComponent(identifier.replacingOccurrences(of: "/", with: "_"),
                                    isDirectory: true)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        HarnessContainerFileManager.roots[identifier] = root
        return root
    }
}
'''
            shared_group = (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text()
            swift = shared_group + "\n" + HARNESS_FILE_MANAGER + "\n" + wire + "\n" + failure + "\n" + primitives + "\n" + context + r'''
enum OperationError: Error {
    case invalidPairingFile(reason: String)
    case other
}
enum MinimuxerError: Error {
    case invalidPairing(protocol: String, reason: String)
    case other
}
struct MinimuxerServiceError: Error { let error: Error }
struct ALTWrappedError: Error { let wrappedError: Error }
''' + pairing_classifier + r'''
let testSuite = "CombinedRecordTest." + UUID().uuidString
@MainActor var logs: [String] = []
struct InstalledApp {
    var bundleIdentifier = "fixture.app", name = "Fixture"
    var expirationDate = Date(), refreshedDate = Date()
}
enum StoreApp { static let altstoreAppID = "fixture.host" }
@MainActor final class Operation {
    var installedApps = [InstalledApp()]
    var refreshIdentifier = UUID().uuidString
    func debugLog(_ message: String) { logs.append(message) }
    func record(_ error: Error) {
        persistAutomaticRefreshVerification(results: ["fixture.app": .failure(error)],
            attemptedAppIDs: ["fixture.app"])
    }
    func record(_ results: [String: Result<InstalledApp, Error>], attemptedAppIDs: [String]) {
        persistAutomaticRefreshVerification(results: results, attemptedAppIDs: attemptedAppIDs)
    }
''' + helper + r'''
}
@main struct Test {
    @MainActor static func main() throws {
        V3SharedAppGroup.containerFileManager = HarnessContainerFileManager()
        V3SharedAppGroup.publishRuntimeGroup(testSuite)
        let defaults = UserDefaults(suiteName: testSuite)!
        defer { defaults.removePersistentDomain(forName: testSuite) }
        let run = UUID().uuidString
        defaults.set(run, forKey: "liveContainerAutoRefreshExpectedRunID")
        let targetProbe = Operation()
        targetProbe.installedApps.append(InstalledApp(bundleIdentifier: "running.app", name: "Running"))
        targetProbe.record(["fixture.app": .success(InstalledApp())], attemptedAppIDs: ["fixture.app"])
        let skippedManifest = defaults.dictionary(forKey: "liveContainerAutoRefreshVerification")!
        precondition(skippedManifest["expected_ids"] as? [String] == ["fixture.app"])
        precondition(skippedManifest["requested_ids"] as? [String] == ["fixture.app", "running.app"])
        precondition(skippedManifest["skipped_ids"] as? [String] == ["running.app"])
        precondition(CombinedVerification.hasCompleteTerminalResults(skippedManifest, runID: run))
        var incompleteCoverage = skippedManifest
        incompleteCoverage["skipped_ids"] = [String]()
        precondition(!CombinedVerification.hasCompleteTerminalResults(incompleteCoverage, runID: run),
                     "a requested app omitted by the engine was treated as verified")
        defaults.removeObject(forKey: "liveContainerAutoRefreshVerification")
        for stage in [CombinedFailure.Stage.authentication, .signing, .installation, .uniqueDeviceID] {
            logs = []
            let native = NSError(domain: "DeviceGatewayError", code: 77,
                userInfo: [NSLocalizedDescriptionKey: "SECRET_TOKEN private-server-response"])
            let wrapped = NSError(domain: "PipelineWrapper", code: 1,
                userInfo: ["LCStructuredFailureStageV1": stage.rawValue, NSUnderlyingErrorKey: native,
                           NSLocalizedDescriptionKey: "SECRET_TOKEN https://private.invalid/?password=secret"])
            Operation().record(wrapped)
            let stored = defaults.dictionary(forKey: "liveContainerAutoRefreshVerification")!
            let rawStoredBytes = try PropertyListSerialization.data(fromPropertyList: stored, format: .xml, options: 0)
            precondition(!String(decoding: rawStoredBytes, as: UTF8.self).contains("SECRET_TOKEN") &&
                         !String(decoding: rawStoredBytes, as: UTF8.self).contains("private.invalid"),
                         "the composed SideStore patch must not persist raw provider text")
            let storedRows = stored["results"] as! [[String: Any]]
            let embeddedFailure = CombinedFailure.decode(storedRows[0]["failure"] as! [String: Any], expectedID: run)!
            precondition(embeddedFailure.stage == stage && embeddedFailure.underlyingCode == 77)
            let safe = CombinedVerification.sanitized(["liveContainerAutoRefreshVerification": stored], runID: run)
            let encoded = try PropertyListSerialization.data(fromPropertyList: safe, format: .xml, options: 0)
            let decoded = try PropertyListSerialization.propertyList(from: encoded, format: nil) as! [String: Any]
            let manifest = decoded["liveContainerAutoRefreshVerification"] as! [String: Any]
            let rows = manifest["results"] as! [[String: Any]]
            let bridgedFailure = CombinedFailure.decode(rows[0]["failure"] as! [String: Any], expectedID: run)!
            precondition(bridgedFailure.stage == stage && bridgedFailure.underlyingCode == 77)
            precondition(bridgedFailure.operation == "refresh" && bridgedFailure.correlationID == run)
            precondition(!String(decoding: encoded, as: UTF8.self).contains("SECRET_TOKEN"))
            precondition(!logs.joined().contains("SECRET_TOKEN") && !logs.joined().contains("private.invalid"))
            precondition(logs.contains { $0.contains("REFRESH_FAILED") && $0.contains("stage=" + stage.rawValue) })
        }
        logs = []
        let privatePairingReason = "PAIRING_PRIVATE_PARSE_DETAIL"
        Operation().record(OperationError.invalidPairingFile(reason: privatePairingReason))
        let pairingStored = defaults.dictionary(forKey: "liveContainerAutoRefreshVerification")!
        let pairingRows = pairingStored["results"] as! [[String: Any]]
        let pairingFailure = CombinedFailure.decode(pairingRows[0]["failure"] as! [String: Any], expectedID: run)!
        precondition(pairingFailure.stage == .pairing && pairingFailure.safeCause == .invalidPairingFile &&
                     pairingFailure.retryable == false && pairingFailure.correlationID == run,
                     "the refresh recorder must preserve typed invalid-pairing cause and exact run correlation")
        let safePairing = CombinedVerification.sanitized(
            ["liveContainerAutoRefreshVerification": pairingStored], runID: run)
        let pairingBytes = try V3ResponseEncoder.encode(["version": 1, "id": run, "ok": true,
            "result": safePairing], operation: "refresh", limit: V3WireContract.responseLimit)
        let pairingDecoded = try PropertyListSerialization.propertyList(from: pairingBytes, format: nil) as! [String: Any]
        let pairingResult = pairingDecoded["result"] as! [String: Any]
        let pairingManifest = pairingResult["liveContainerAutoRefreshVerification"] as! [String: Any]
        let pairingManifestRows = pairingManifest["results"] as! [[String: Any]]
        let roundTripped = CombinedFailure.decode(pairingManifestRows[0]["failure"] as! [String: Any], expectedID: run)!
        precondition(roundTripped.stage == .pairing && roundTripped.safeCause == .invalidPairingFile &&
                     roundTripped.correlationID == run && roundTripped.retryable == false,
                     "invalid-pairing semantics must survive encoding and decoding of the refresh manifest")
        let callbackFailure = CombinedFailure.fromEncodedString(roundTripped.encodedString, expectedID: run)!
        precondition(callbackFailure.stage == .pairing && callbackFailure.safeCause == .invalidPairingFile &&
                     callbackFailure.correlationID == run && callbackFailure.retryable == false,
                     "the XPC terminal callback must preserve pairing semantics and exact run identity")
        precondition(!String(decoding: pairingBytes, as: UTF8.self).contains(privatePairingReason) &&
                     !logs.joined().contains(privatePairingReason),
                     "private pairing parser details must not cross response or log boundaries")
        let errorReply: [String: Any] = ["version": 1, "id": run, "ok": false,
            "error": "failed", "failure": roundTripped.wire]
        let errorBytes = V3ResponseEncoder.encode(errorReply, operation: "refresh", limit: V3WireContract.responseLimit)
        do {
            _ = try V3CatalogRequestContext.classifyReply(errorBytes, operation: "refresh", id: run)
            preconditionFailure("a failed pairing envelope was accepted as a successful host reply")
        } catch let received as CombinedFailure {
            precondition(received.stage == .pairing && received.safeCause == .invalidPairingFile &&
                         received.correlationID == run && received.retryable == false,
                         "the host classifier must preserve pairing stage, cause, retryability, and request ID")
        } catch {
            preconditionFailure("the pairing failure changed at the host response classifier: \(error)")
        }
        let pairingImportFailure = CombinedFailure(operation: "pairingImportData", stage: .pairing,
            code: .failed, id: run, retryable: false, safeCause: .invalidPairingFile)
        let pairingImportReply = V3ResponseEncoder.encode(["version": 1, "id": run, "ok": false,
            "error": "failed", "failure": pairingImportFailure.wire],
            operation: "pairingImportData", limit: V3WireContract.responseLimit)
        do {
            _ = try V3CatalogRequestContext.classifyReply(pairingImportReply,
                operation: "pairingImportData", id: run)
            preconditionFailure("a typed pairing-import failure was accepted as success")
        } catch let received as CombinedFailure {
            precondition(received.operation == "pairingImportData" && received.stage == .pairing &&
                         received.safeCause == .invalidPairingFile && received.correlationID == run &&
                         V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: received.operation,
                             stage: received.stage.rawValue, safeCause: received.safeCause?.rawValue),
                         "the exact XPC failure must retain its pairing-only file-retry eligibility")
        } catch {
            preconditionFailure("the pairing-import failure changed at the host response classifier: \(error)")
        }
        let stale = CombinedFailure(operation: "refresh", stage: .signing, id: UUID().uuidString).wire
        let legacy: [String: Any] = ["run_id": run, "expected_ids": ["fixture.app"], "results": [
            ["bundle_id": "fixture.app", "success": false, "error_domain": "DeviceGatewayError", "error_code": 84,
             "error": "lc_stage=uniqueDeviceID SECRET_TOKEN", "failure": stale] as [String: Any]]]
        let safe = CombinedVerification.sanitized(["liveContainerAutoRefreshVerification": legacy], runID: run)
        let manifest = safe["liveContainerAutoRefreshVerification"] as! [String: Any]
        let rows = manifest["results"] as! [[String: Any]]
        let failure = CombinedFailure.decode(rows[0]["failure"] as! [String: Any], expectedID: run)!
        precondition(failure.stage == .uniqueDeviceID && failure.underlyingCode == 84)
        precondition(!failure.localizedDescription.contains("SECRET_TOKEN"))
        print("record-to-wire stage/correlation/redaction PASS")
    }
}
'''
            source = root / "main.swift"; executable = root / "record-tests"
            source.write_text(swift)
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("record-to-wire stage/correlation/redaction PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
