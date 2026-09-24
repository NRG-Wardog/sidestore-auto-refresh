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
    def test_actual_native_launch_callback_ignores_settled_old_attempt(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        source = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text()
        body = source[source.index("        LCLaunchServiceExtension(ext, item) {"):source.index("    fileprivate func accepted(")]
        swift = '''import Foundation
@MainActor var callbacks: [(UUID?, Error?) -> Void] = []
@MainActor final class ExtensionStub {
    var kills = 0
    func _kill(_ signal: Int) { kills += 1 }
    func pid(forRequestIdentifier id: UUID) -> Int32 { 17 }
}
@MainActor func LCLaunchServiceExtension(_ ext: ExtensionStub, _ item: Int, _ callback: @escaping (UUID?, Error?) -> Void) { callbacks.append(callback) }
@MainActor final class Owner {
    let ext = ExtensionStub()
    var launchID: UUID?
    var launchRequestPending: UUID?
    var retiringRequestPending: UUID?
    var retiringPID: Int32 = 0
    var sideStorePid: Int32 = 0
    var signals = 0; var failures = 0
    var service: Owner { self }
    enum Signal { case launched }; enum Stage { case extensionLaunch }
    func signal(_ signal: Signal, attempt: UUID) { signals += 1 }
    func failed(_ id: UUID, stage: Stage, underlying: Error? = nil) { failures += 1 }
    func launch(_ id: UUID) {
        launchID = id; launchRequestPending = id
        let ext = self.ext; let item = 0
''' + body + '''
}
@main struct Test {
    @MainActor static func main() async throws {
        let owner = Owner(); let old = UUID(); owner.launch(old)
        callbacks[0](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.signals == 1)
        callbacks[0](nil, NSError(domain: "test", code: 1))
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.failures == 0 && owner.signals == 1)
        let next = UUID(); owner.launch(next)
        callbacks[0](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.ext.kills == 0 && owner.launchRequestPending == next)
        callbacks[1](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.signals == 2 && owner.sideStorePid == 17)
        print("native duplicate/late launch identity PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main.swift"; exe = Path(temp) / "native-launch"
            path.write_text(swift)
            result = subprocess.run([compiler, "-parse-as-library", str(path), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_refresh_adapter_rejects_incomplete_and_stale_completion(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        adapter = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text()
        method = adapter[adapter.index("    fileprivate func completedRefresh("):adapter.index("    fileprivate func legacyCompletion(")]
        # Inject only the UserDefaults suite to keep the executable test isolated.
        method = method.replace('UserDefaults(suiteName: "group.com.SideStore.SideStore")', 'UserDefaults(suiteName: testSuite)')
        source = (ROOT / "scripts/templates/combined_failure.swift").read_text() + '''
let testSuite = "CombinedCompletionTest." + UUID().uuidString
@MainActor final class Probe {
    var launchID: UUID? = UUID()
    var refreshRunID: String?
    var refreshContinuation: Int? = 1
    var completions: [Result<Void, Error>] = []
    func finishRefreshContinuation(_ result: Result<Void, Error>) { completions.append(result); refreshContinuation = nil }
''' + method + r'''
}
@main struct Test {
    @MainActor static func main() throws {
        let defaults = UserDefaults(suiteName: testSuite)!
        defer { defaults.removePersistentDomain(forName: testSuite) }
        let run = UUID().uuidString, newer = UUID().uuidString
        let key = CombinedVerification.uncertainMutationKey
        let valid: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": run,
            "expected_ids": ["a", "b"], "results": [["bundle_id": "a", "success": true], ["bundle_id": "b", "success": true]]]
        func receive(_ manifest: [String: Any], marker: String? = nil, error: String? = nil) throws -> Probe {
            let owner = Probe(); owner.refreshRunID = run
            defaults.set(marker ?? run, forKey: key)
            defaults.removeObject(forKey: "liveContainerAutoRefreshHostHandoff")
            let payload = try PropertyListSerialization.data(fromPropertyList: ["liveContainerAutoRefreshVerification": manifest], format: .binary, options: 0)
            owner.completedRefresh(error, runID: run, verification: payload, id: owner.launchID!)
            precondition(owner.completions.count == 1)
            owner.completedRefresh(error, runID: run, verification: payload, id: owner.launchID!)
            precondition(owner.completions.count == 1, "duplicate completion settled twice")
            return owner
        }
        var invalids: [[String: Any]] = []
        let invalidEntries: [[[String: Any]]] = [[], [["bundle_id": "a", "success": true]],
            [["bundle_id": "a", "success": true], ["bundle_id": "a", "success": true]],
            [["bundle_id": "a", "success": true], ["bundle_id": "other", "success": true]],
            [["bundle_id": "a", "success": true], ["bundle_id": "b", "success": 1]]]
        for entries in invalidEntries {
            var invalid = valid; invalid["results"] = entries; invalids.append(invalid)
        }
        var wrongRun = valid; wrongRun["run_id"] = newer; invalids.append(wrongRun)
        var duplicates = valid; duplicates["expected_ids"] = ["a", "a"]; invalids.append(duplicates)
        for invalid in invalids {
            let owner = try receive(invalid)
            guard case .failure(let error) = owner.completions[0], let failure = error as? CombinedFailure else { preconditionFailure("incomplete manifest accepted") }
            precondition(failure.stage == .refreshVerification)
            precondition(defaults.string(forKey: key) == run, "unconfirmed mutation became retryable")
        }
        let complete = try receive(valid)
        guard case .success = complete.completions[0] else { preconditionFailure("complete terminal results rejected") }
        precondition(defaults.string(forKey: key) == nil)
        var failed = valid; failed["results"] = [["bundle_id": "a", "success": true], ["bundle_id": "b", "success": false]]
        _ = try receive(failed)
        precondition(defaults.string(forKey: key) == nil, "known terminal failure should allow policy evaluation")
        var handoff = valid; handoff["host_handoff"] = true
        _ = try receive(handoff)
        precondition(defaults.string(forKey: key) == run, "host replacement is not yet verified")
        _ = try receive(valid, marker: newer)
        precondition(defaults.string(forKey: key) == newer, "old completion erased a newer uncertainty marker")
        _ = try receive(valid, marker: newer, error: CombinedFailure(operation: "refresh", stage: .signing, id: run).encodedString)
        precondition(defaults.string(forKey: key) == newer, "old failure erased a newer uncertainty marker")
        print("actual refresh completeness/correlation PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main.swift"; exe = Path(temp) / "completion"
            path.write_text(source)
            result = subprocess.run([compiler, "-parse-as-library", str(path), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_adapter_duplicate_readiness_is_idempotent(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        adapter = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text()
        method = adapter[adapter.index("    fileprivate func applicationReady("):adapter.index("    private func awaitServiceReady(")]
        source = '''import Foundation
enum Stage { case serviceReadiness }
@MainActor final class Probe {
    var launchID: UUID? = UUID()
    var readinessTask: Task<Void, Never>?
    var probes = 0; var failures = 0; var signals = 0
    var service: Probe { self }
    enum Signal { case ready }
    func signal(_ signal: Signal, attempt: UUID) { signals += 1 }
    func awaitServiceReady(_ id: UUID) async throws { probes += 1; try await Task.sleep(nanoseconds: 30_000_000) }
    func failed(_ id: UUID, stage: Stage, underlying: Error) { failures += 1 }
''' + method + '''
}
@main struct Test {
    @MainActor static func main() async throws {
        let owner = Probe(); let id = owner.launchID!
        owner.applicationReady(id)
        owner.applicationReady(id)
        await owner.readinessTask?.value
        owner.applicationReady(id)
        precondition(owner.probes == 1 && owner.signals == 1 && owner.failures == 0)
        owner.readinessTask = nil; owner.launchID = UUID()
        owner.applicationReady(id)
        precondition(owner.probes == 1)
        owner.applicationReady(owner.launchID!)
        owner.readinessTask?.cancel()
        await owner.readinessTask?.value
        precondition(owner.failures == 0 && owner.signals == 1)
        print("adapter duplicate readiness/cancellation PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main.swift"; exe = Path(temp) / "probe"
            path.write_text(source)
            result = subprocess.run([compiler, "-parse-as-library", str(path), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

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
                 ["AltStore/AppDelegate.swift", "SideStore/Core/Operations/PipelineExecutor.swift",
                  "SideStore/Core/Operations/PipelineRunner.swift"])
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


class ReadinessRegressionTests(unittest.TestCase):
    def test_pipeline_phase_hook_is_v3_only(self):
        source = """        do {
            switch step {
            default: break
            }
            result = error
            throw error"""
        generated_v2 = startup.patch_pipeline_executor(source, product="v2")
        generated_v3 = startup.patch_pipeline_executor(source, product="v3")
        self.assertNotIn("V3_PIPELINE_PHASE_REPORTING_V1", generated_v2)
        self.assertIn("V3_PIPELINE_PHASE_REPORTING_V1", generated_v3)
        self.assertIn("await headlessHandler.recordPipelinePhase(step,", generated_v3)
        self.assertIn("downloadUsesNetwork: downloadingApp.url?.isFileURL == false", generated_v3)

    def test_generated_pinned_sidesign_errors_keep_typed_signing_semantics(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        side_sign = os.getenv("SIDESIGN_TEST_SOURCE")
        embedded = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_sign or not embedded:
            self.skipTest("pinned SideSign and SideStore sources are supplied by macOS CI")
        errors_path = Path(side_sign) / "Sources/Models/Errors.swift"
        pinned_errors = errors_path.read_text(encoding="utf-8")
        self.assertIn("public enum ServerError", pinned_errors)
        self.assertIn("case underlyingError(code: Int, message: String)", pinned_errors)
        self.assertIn("public enum DeveloperPortalError", pinned_errors)
        enum_start = pinned_errors.index("public enum DeveloperPortalError")
        enum_end = pinned_errors.index("public enum SignerError", enum_start)
        actual_side_sign_types = pinned_errors[enum_start:enum_end]

        original_pipeline = subprocess.check_output([
            "git", "-C", embedded, "show",
            startup.PINS[1] + ":SideStore/Core/Operations/PipelineExecutor.swift"], text=True)
        generated_pipeline = startup.patch_pipeline_executor(original_pipeline)
        self.assertIn("lcSafeSigningCause(error)", generated_pipeline)
        self.assertIn('sourceStep = "provisioningProfileFetch"', generated_pipeline)
        self.assertIn("V3_PIPELINE_PHASE_REPORTING_V1", generated_pipeline)
        self.assertIn("await headlessHandler.recordPipelinePhase(step,", generated_pipeline)
        self.assertIn("downloadUsesNetwork: downloadingApp.url?.isFileURL == false", generated_pipeline)
        original_runner = subprocess.check_output([
            "git", "-C", embedded, "show",
            startup.PINS[1] + ":SideStore/Core/Operations/PipelineRunner.swift"], text=True)
        generated_runner = startup.patch_pipeline_runner(original_runner)
        self.assertIn("V3_PROGRESS_BASELINE_FIX_V1", generated_runner)
        self.assertIn("group.progress.completedUnitCount = 0", generated_runner)
        self.assertNotIn("group.progress.completedUnitCount = 1", generated_runner)
        helper = generated_pipeline[generated_pipeline.index("// LC_SIGNING_CAUSE_CLASSIFIER_V1"):]
        failure_model = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        behavioral_model = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure_model = "\n".join(line for line in failure_model.splitlines()
                                    if not line.startswith("import "))
        behavioral_model = "\n".join(line for line in behavioral_model.splitlines()
                                       if not line.startswith("import "))
        source = """import Foundation
import CoreFoundation
enum Constants { static let defaultAccountRepairMessage = "" }
""" + actual_side_sign_types + failure_model + behavioral_model + """
@main struct SigningCauseTest {
    static func main() {
        precondition(lcSafeSigningCause(URLError(.networkConnectionLost)) == "signingNetworkConnectionLost")
        precondition(lcSafeSigningCause(ServerError.underlyingError(code: -1005, message: "provider")) == "developerPortalRejectedRequest")
        precondition(lcSafeSigningCause(DeveloperPortalError.certificateDoesNotExist(serial: "private")) == "certificateUnavailable")
        precondition(lcSafeSigningCause(DeveloperPortalError.provisioningProfileDoesNotExist(identifier: "private")) == "provisioningProfileUnavailable")
        precondition(lcSafeSigningCause(NSError(domain: "redacted", code: -1005)) == "unknownSigningCause",
                     "numeric -1005 alone was classified as a network failure")
        let runID = UUID().uuidString
        let providerCause = lcSafeSigningCause(ServerError.underlyingError(code: -1005, message: "private response"))
        let wrapped = NSError(domain: "PrivateSideSignDomain", code: -1005, userInfo: [
            "LCStructuredFailureStageV1": "signing",
            "LCStructuredFailureCauseV1": providerCause,
            "LCStructuredFailureSourceV1": "provisioningProfileFetch",
            NSUnderlyingErrorKey: NSError(domain: "PrivateProviderDomain", code: -1005)
        ])
        let captured = CombinedFailure.capture(wrapped, operation: "install", stage: .installation, id: runID)
        let bridged = CombinedFailure.decode(captured.wire, expectedID: runID)!
        let details = V3OperationFailureDetails(bridged)
        precondition(bridged.stage == .signing && bridged.safeCause == .developerPortalRejectedRequest)
        precondition(bridged.sourceStep == .provisioningProfileFetch && bridged.underlyingDomain == "redacted")
        precondition(bridged.underlyingCode == -1005 && details.recoveryDestination == "certificates")
        print("PINNED_SIDESIGN_TYPED_SIGNING_CAUSE_PASS")
    }
}
""" + helper
        with tempfile.TemporaryDirectory() as directory:
            swift = Path(directory) / "main.swift"
            executable = Path(directory) / "signing-cause"
            swift.write_text(source, encoding="utf-8")
            built = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PINNED_SIDESIGN_TYPED_SIGNING_CAUSE_PASS", result.stdout)

    def test_structured_failures_are_preserved_not_rewrapped(self):
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn("CombinedFailure.preserving(underlying", handler)
        self.assertIn("Never double-wrap", handler)

    def test_startup_markers_carry_correlation(self):
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        startup = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        for marker in ("PROCESS_LAUNCH_BEGIN", "PROCESS_LAUNCHED", "XPC_CONNECTED",
                       "APPLICATION_READY", "PROCESS_EXITED", "START_FAILED",
                       "SNAPSHOT_READY", "READINESS_TIMEOUT", "READINESS_INVALID_RESPONSE"):
            self.assertIn(marker, handler + startup)
        self.assertIn("id.uuidString", handler)

    def test_reconnect_and_mutation_safety_invariants(self):
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        connection = (ROOT / "scripts/templates/combined_service_connection.swift").read_text(encoding="utf-8")
        self.assertIn("if attemptID == nil { begin() }", connection)
        self.assertGreaterEqual(handler.count("launchID == id"), 4)
        self.assertIn("attemptID == id", connection)
        self.assertIn("extensionProcess?._kill(15)", handler)
        self.assertIn("guard v3RefreshToken == nil", handler)
        self.assertIn("v3RefreshToken = token", handler)


if __name__ == "__main__":
    unittest.main()
