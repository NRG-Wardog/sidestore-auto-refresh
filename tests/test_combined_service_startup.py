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
import patch_v3_service as service_patch


def _matching_swift_brace(text, open_brace):
    """Return the offset after a Swift brace block, ignoring strings/comments."""
    depth = 0
    index = open_brace
    state = "code"
    block_depth = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if char == "\n": state = "code"
        elif state == "block_comment":
            if char == "/" and following == "*":
                block_depth += 1; index += 1
            elif char == "*" and following == "/":
                block_depth -= 1; index += 1
                if block_depth == 0: state = "code"
        elif state == "string":
            if char == "\\": index += 1
            elif char == '"': state = "code"
        else:
            if char == "/" and following == "/":
                state = "line_comment"; index += 1
            elif char == "/" and following == "*":
                state = "block_comment"; block_depth = 1; index += 1
            elif char == '"': state = "string"
            elif char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0: return index + 1
        index += 1
    raise AssertionError("unbalanced Swift brace block")


def _without_imports(text):
    return "\n".join(line for line in text.splitlines() if not line.startswith("import "))


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
    func confirmLaunchedPeer(_ id: UUID) {}
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
        source = (ROOT / "scripts/templates/combined_failure.swift").read_text() + '''
let testSuite = "CombinedCompletionTest." + UUID().uuidString
// The completion path reads the one runtime App Group the host published. The
// harness stands in for that resolver so the executable test stays isolated
// while the production correlation and uncertainty rules run unchanged.
enum V3SharedAppGroup {
    static func sharedUserDefaults() -> UserDefaults? { UserDefaults(suiteName: testSuite) }
}
@MainActor final class Probe {
    var launchID: UUID? = UUID()
    var refreshRunID: String?
    var v3RefreshTerminalCallbackRunID: String?
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
            "expected_ids": ["a", "b"], "host_handoff": false,
            "results": [["bundle_id": "a", "success": true], ["bundle_id": "b", "success": true]]]
        func receive(_ manifest: [String: Any], marker: String? = nil,
                     handoffRunID: Any? = nil, outerHandoff: Any? = nil,
                     includeOuterHandoff: Bool = false, error: String? = nil) throws -> Probe {
            let owner = Probe(); owner.refreshRunID = run
            defaults.set(marker ?? run, forKey: key)
            defaults.removeObject(forKey: "liveContainerAutoRefreshHostHandoff")
            var rawPayload: [String: Any] = ["liveContainerAutoRefreshVerification": manifest]
            if let handoffRunID { rawPayload["liveContainerAutoRefreshHostHandoffRunID"] = handoffRunID }
            if includeOuterHandoff, let outerHandoff { rawPayload["liveContainerAutoRefreshHostHandoff"] = outerHandoff }
            // Exercise the exact production sanitizer before passing its plist
            // output to the exact production completion consumer below.
            let safePayload = CombinedVerification.sanitized(rawPayload, runID: run)
            let payload = try PropertyListSerialization.data(fromPropertyList: safePayload, format: .binary, options: 0)
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
        // The real producer persists true and this run ID before the manifest
        // copies the persisted Boolean into host_handoff.
        var handoff = valid; handoff["host_handoff"] = true
        let currentHandoff = try receive(handoff, handoffRunID: run,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .success = currentHandoff.completions[0] else { preconditionFailure("matching true handoff rejected") }
        precondition(defaults.string(forKey: key) == run, "host replacement is not yet verified")
        let missingOuter = try receive(handoff, handoffRunID: run)
        guard case .failure = missingOuter.completions[0] else { preconditionFailure("missing current-run outer flag accepted") }
        precondition(defaults.string(forKey: key) == run, "missing handoff marker cleared uncertainty")
        var missingManifestFlag = valid; missingManifestFlag.removeValue(forKey: "host_handoff")
        let missingManifest = try receive(missingManifestFlag, handoffRunID: run,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = missingManifest.completions[0] else { preconditionFailure("missing current-run manifest flag accepted") }
        precondition(defaults.string(forKey: key) == run, "missing manifest marker cleared uncertainty")
        let outerOnly = try receive(valid, handoffRunID: run,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = outerOnly.completions[0] else { preconditionFailure("outer true / manifest false contradiction accepted") }
        precondition(defaults.string(forKey: key) == run, "contradictory outer marker cleared uncertainty")
        let manifestOnly = try receive(handoff, handoffRunID: run,
            outerHandoff: false, includeOuterHandoff: true)
        guard case .failure = manifestOnly.completions[0] else { preconditionFailure("manifest true / outer false contradiction accepted") }
        precondition(defaults.string(forKey: key) == run, "contradictory manifest marker cleared uncertainty")
        var malformedMarker = handoff
        malformedMarker["host_handoff"] = "true"
        let malformed = try receive(malformedMarker, handoffRunID: run,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = malformed.completions[0] else { preconditionFailure("wrong-typed manifest handoff accepted") }
        precondition(defaults.string(forKey: key) == run, "malformed handoff cleared uncertainty")
        let malformedOuter = try receive(handoff, handoffRunID: run,
            outerHandoff: "true", includeOuterHandoff: true)
        guard case .failure = malformedOuter.completions[0] else { preconditionFailure("wrong-typed outer handoff accepted") }
        precondition(defaults.string(forKey: key) == run, "malformed outer marker cleared uncertainty")
        let malformedRunID = try receive(handoff, handoffRunID: 7,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = malformedRunID.completions[0] else { preconditionFailure("wrong-typed handoff run ID accepted") }
        precondition(defaults.string(forKey: key) == run, "malformed handoff run ID cleared uncertainty")
        let staleRunID = try receive(handoff, handoffRunID: newer,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = staleRunID.completions[0] else { preconditionFailure("handoff flags with stale run ID accepted") }
        precondition(defaults.string(forKey: key) == run, "stale handoff run ID cleared uncertainty")
        let manifestTrueWithoutOuterRunID = try receive(handoff,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = manifestTrueWithoutOuterRunID.completions[0] else {
            preconditionFailure("successful manifest handoff without outer run ID accepted")
        }
        precondition(defaults.string(forKey: key) == run,
            "manifest handoff without outer run ID cleared uncertainty")
        let outerTrueWithoutRunID = try receive(valid,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = outerTrueWithoutRunID.completions[0] else {
            preconditionFailure("outer true handoff without run ID accepted")
        }
        precondition(defaults.string(forKey: key) == run,
            "outer true handoff without run ID cleared uncertainty")
        let outerTrueWithStaleRunID = try receive(valid, handoffRunID: newer,
            outerHandoff: true, includeOuterHandoff: true)
        guard case .failure = outerTrueWithStaleRunID.completions[0] else {
            preconditionFailure("outer true handoff with stale run ID accepted")
        }
        precondition(defaults.string(forKey: key) == run,
            "outer true handoff with stale run ID cleared uncertainty")
        var noRunRefresh = valid
        noRunRefresh["host_handoff"] = false
        let normalRefresh = try receive(noRunRefresh)
        guard case .success = normalRefresh.completions[0] else { preconditionFailure("no-run normal refresh rejected") }
        precondition(defaults.string(forKey: key) == nil, "normal refresh did not clear confirmed uncertainty")
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
    def test_automatic_host_handoff_persists_run_before_manifest_copies_flag(self):
        source = (ROOT / "scripts/patch_background_automation.py").read_text(encoding="utf-8")
        persist = source[source.index("    private func persistAutomaticHostHandoff()"):source.index("    private func persistAutomaticRefreshVerification(")]
        manifest = source[source.index("private func persistAutomaticRefreshVerification"):source.rindex("VERIFICATION_MANIFEST_V1")]
        self.assertLess(persist.index('defaults.set(true, forKey: "liveContainerAutoRefreshHostHandoff")'),
                        persist.index('defaults.set(refreshIdentifier, forKey: "liveContainerAutoRefreshHostHandoffRunID")'))
        self.assertIn('"host_handoff": defaults.bool(forKey: "liveContainerAutoRefreshHostHandoff")', manifest)
        self.assertLess(source.index("self?.persistAutomaticHostHandoff()"),
                        source.index("self.persistAutomaticRefreshVerification(results: results"))

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
    def test_startup_probe_uses_bounded_backoff_and_terminal_policy(self):
        patcher = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        probe = patcher[patcher.index('handler = handler.replace("/*SERVICE_PROBE*/",'):]
        probe = probe[:probe.index("''' if product == \"v3\" else")]
        self.assertIn("while true", probe)
        self.assertIn("V3ServiceReadinessProbeState.resolve", probe)
        self.assertIn("backoff.nextDelay(remaining:", probe)
        self.assertNotIn("lastSnapshotError", probe)

    def test_direct_refresh_rechecks_after_connection_and_uses_service_admission(self):
        generator = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn('handler = handler.replace("/*REFRESH_READINESS*/", "")', generator)
        perform = handler[handler.index("private func performRefresh(identifier:"):]
        perform = perform[:perform.index("private func releaseRefreshAdmission")]
        preflights = [index for index in range(len(perform))
                      if perform.startswith("V3DirectRefreshPreflightPolicy.isBlocked", index)]
        self.assertEqual(len(preflights), 2,
                         "a fast local check and a post-connect recheck must guard the non-suspending claim")
        connected = perform.index("try await ensureServiceConnected()")
        token = perform.index("let token = UUID()")
        self.assertLess(preflights[0], connected)
        self.assertLess(connected, preflights[1])
        self.assertLess(preflights[1], token)
        self.assertLess(token, perform.index('operation: "refreshAdmissionBegin"'))

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

    def test_generated_pinned_portal_failures_preserve_request_owned_signing_context(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        side_sign = os.getenv("SIDESIGN_TEST_SOURCE")
        embedded = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_sign or not embedded:
            self.skipTest("pinned SideSign and SideStore sources are supplied by macOS CI")
        side_sign_ref = subprocess.check_output(["git", "-C", side_sign, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(side_sign_ref, "a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d")
        side_store_ref = subprocess.check_output(["git", "-C", embedded, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(side_store_ref, startup.PINS[1])

        errors_text = (Path(side_sign) / "Sources/Models/Errors.swift").read_text(encoding="utf-8")
        error_start = errors_text.index("public enum DeveloperPortalError")
        error_end = errors_text.index("public enum SignerError", error_start)
        actual_side_sign_errors = errors_text[error_start:error_end]

        response_text = (Path(side_sign) / "Sources/Models/DeveloperPortalResponses.swift").read_text(encoding="utf-8")
        status_start = response_text.index("struct DeveloperPortalStatusResponse:")
        status_open = response_text.index("{", status_start)
        status_end = _matching_swift_brace(response_text, status_open)
        actual_status_response = response_text[status_start:status_end]

        constants_text = (Path(side_sign) / "Sources/Constants.swift").read_text(encoding="utf-8")
        codes_start = constants_text.index("public enum DeveloperPortalResultCodes {")
        codes_open = constants_text.index("{", codes_start)
        actual_result_codes = constants_text[codes_start:_matching_swift_brace(constants_text, codes_open)]

        app_ids_text = (Path(side_sign) / "Sources/DeveloperPortal/AppIDs.swift").read_text(encoding="utf-8")
        handler_open = app_ids_text.index("{", app_ids_text.index("resultCodeHandler: {"))
        actual_app_id_result_handler = app_ids_text[handler_open:_matching_swift_brace(app_ids_text, handler_open)]

        api_text = (Path(side_sign) / "Sources/DeveloperPortal/DeveloperPortalAPI.swift").read_text(encoding="utf-8")
        patched_api = startup.patch_sidesign_portal_observer(api_text)
        status_parser = "if let status = (try? PropertyListDecoder().decode(DeveloperPortalStatusResponse.self, from: data))"
        parser_position = patched_api.index(status_parser)
        response_position = patched_api.rfind("let httpResponse = response as? HTTPURLResponse", 0, parser_position)
        self.assertGreaterEqual(response_position, 0)
        status_code_end = patched_api.index("\n", patched_api.index("let statusCode = httpResponse?.statusCode ?? 0", response_position)) + 1
        actual_http_observer = patched_api[response_position:status_code_end]
        parser_open = patched_api.index("{", parser_position)
        actual_status_decoder = patched_api[parser_position:_matching_swift_brace(patched_api, parser_open)]
        self.assertIn("SideSignPortalDiagnostics.responseObserver?(httpResponse?.statusCode, nil)", actual_http_observer)
        self.assertIn("SideSignPortalDiagnostics.safeProviderCode(firstError.code)", actual_status_decoder)
        self.assertIn('"ENTITY_ERROR.ATTRIBUTE.INVALID"', startup.SIDESIGN_PORTAL_OBSERVER)
        self.assertIn("guard let value, known.contains(value) else { return nil }", startup.SIDESIGN_PORTAL_OBSERVER)
        self.assertIn("ServerError.underlyingError(code: -1, message: detail)", actual_status_decoder)
        self.assertIn("ServerError.underlyingError(code: code, message: message)", actual_status_decoder)
        self.assertIn("lcSafeSigningCause(error, portalResponse: portalResponse)", startup.SIGNING_CAUSE_HELPER)

        original_proxy = subprocess.check_output([
            "git", "-C", embedded, "show",
            startup.PINS[1] + ":SideStore/Core/Auth/DeveloperPortalProxy.swift"], text=True)
        generated_proxy = service_patch.patch_developer_portal_proxy(original_proxy)
        method_start = generated_proxy.index("public func addAppID(name:")
        method_open = generated_proxy.index("{", method_start)
        actual_add_app_id = generated_proxy[method_start:_matching_swift_brace(generated_proxy, method_open)]
        self.assertIn('sourceStep: "appIDRegistration"', actual_add_app_id)
        self.assertIn("ALTAppleAPI.shared.addAppID", actual_add_app_id)

        failure_model = _without_imports((ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8"))
        behavioral_model = _without_imports((ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        template = (ROOT / "tests/fixtures/v3_portal_failure_harness.swift").read_text(encoding="utf-8")

        def build_source(*, mutate_server_code=False, mutate_source_step=False, mutate_quota=False):
            signing_helper = startup.SIGNING_CAUSE_HELPER
            if mutate_quota:
                original = 'case .maximumAppIDLimitReached: return "appIDLimitReached"'
                self.assertEqual(signing_helper.count(original), 1)
                signing_helper = signing_helper.replace(original, "// targeted mutation: typed quota lost", 1)
            if mutate_server_code:
                original = 'context["server_code"] = code == -1 ? "unknown" : String(code)'
                self.assertEqual(signing_helper.count(original), 1)
                signing_helper = signing_helper.replace(original, "// targeted mutation: server_code omitted", 1)
            generated_method = actual_add_app_id
            if mutate_source_step:
                original = 'sourceStep: "appIDRegistration"'
                self.assertEqual(generated_method.count(original), 1)
                generated_method = generated_method.replace(original, 'sourceStep: "appIDLookup"', 1)
            replacements = {
                "$SIDESIGN_ERROR_TYPES$": actual_side_sign_errors,
                "$DEVELOPER_PORTAL_STATUS_RESPONSE$": actual_status_response,
                "$DEVELOPER_PORTAL_RESULT_CODES$": actual_result_codes,
                "$PORTAL_OBSERVER$": startup.SIDESIGN_PORTAL_OBSERVER,
                "$ACTUAL_APP_ID_RESULT_HANDLER$": actual_app_id_result_handler,
                "$COMBINED_FAILURE$": failure_model,
                "$BEHAVIORAL_POLICIES$": behavioral_model,
                "$SIGNING_CAUSE_HELPER$": signing_helper,
                "$ACTUAL_HTTP_OBSERVER_AND_STATUS_DECODER$": actual_http_observer + actual_status_decoder,
                "$ACTUAL_PATCHED_ADD_APP_ID_METHOD$": generated_method,
                "$ACTUAL_PIPELINE_FAILURE_HANDLER$": startup.PIPELINE_FAILURE_HANDLER,
            }
            result = template
            for marker, value in replacements.items():
                self.assertEqual(result.count(marker), 1, f"fixture placeholder drift: {marker}")
                result = result.replace(marker, value, 1)
            wire_model = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
            return wire_model + "\n" + result

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)

            def execute(label, source, expect_success):
                swift = directory / f"{label}.swift"
                executable = directory / label
                swift.write_text(source, encoding="utf-8")
                built = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                       capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, f"{label} did not compile:\n{built.stderr}")
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
                if expect_success:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("PINNED_PORTAL_FAILURE_CONTEXT_PASS", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0,
                        f"{label} mutation did not fail the behavioral harness")

            execute("portal-failure", build_source(), True)
            execute("portal-failure-no-server-code", build_source(mutate_server_code=True), False)
            execute("portal-failure-wrong-source-step", build_source(mutate_source_step=True), False)
            execute("portal-failure-no-quota", build_source(mutate_quota=True), False)

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
        self.assertIn("V3ServiceReadinessReply.decode(response, requestID: requestID)", startup)
        self.assertNotIn('result["ok"] as? Bool', startup)

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
