"""Executable startup behavior and current-owner adapter contracts."""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source
from tests.test_refresh_result_bridge import declaration


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
        source = read_product_source("combined_refresh_handler.swift")
        body = source[source.index("        LCLaunchServiceExtension(ext, item) {"):source.index("    fileprivate func accepted(")]
        swift = read_product_source("combined_failure.swift") + "\n" + declaration(source, "public struct V3StartupDiagnosticLog {") + '''
import Foundation
@MainActor var callbacks: [(UUID?, Error?) -> Void] = []
@MainActor final class ExtensionStub {
    var kills = 0
    var observedPID: Int32 = 17
    func _kill(_ signal: Int) { kills += 1 }
    func pid(forRequestIdentifier id: UUID) -> Int32 { observedPID }
}
@MainActor func LCLaunchServiceExtension(_ ext: ExtensionStub, _ item: Int, _ callback: @escaping (UUID?, Error?) -> Void) { callbacks.append(callback) }
@MainActor final class Owner {
    let ext = ExtensionStub()
    var launchID: UUID?
    var launchRequestPending: UUID?
    var retiringRequestPending: UUID?
    var retiringPID: Int32 = 0
    var sideStorePid: Int32 = 0
    var launchRequestIdentifierObserved: Bool?
    var launchPIDObserved: Bool?
    var failureContext: CombinedFailure.LaunchContext?
    var signals = 0; var failures = 0
    var startupDiagnostics = V3StartupDiagnosticLog()
    var service: Owner { self }
    enum Signal { case launched }
    func signal(_ signal: Signal, attempt: UUID) { signals += 1 }
    func confirmLaunchedPeer(_ id: UUID) {}
    func failed(_ id: UUID, stage: CombinedFailure.Stage, underlying: Error? = nil,
                launchSourceStep: CombinedFailure.LaunchContext.Step? = nil,
                requestIdentifierObserved: Bool? = nil, pidObserved: Bool? = nil) {
        failures += 1
        failureContext = CombinedFailure.LaunchContext(error: underlying,
            sourceStep: launchSourceStep ?? .unknown,
            requestIdentifierObserved: requestIdentifierObserved ?? launchRequestIdentifierObserved,
            pidObserved: pidObserved ?? launchPIDObserved)
    }
    func launch(_ id: UUID) {
        launchID = id; launchRequestPending = id
        launchRequestIdentifierObserved = nil; launchPIDObserved = nil
        let ext = self.ext; let item = 0
''' + body + '''
}
@main struct Test {
    @MainActor static func main() async throws {
        let owner = Owner(); let old = UUID(); owner.launch(old)
        callbacks[0](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.signals == 1)
        precondition(owner.launchRequestIdentifierObserved == true && owner.launchPIDObserved == true)
        callbacks[0](nil, NSError(domain: "test", code: 1))
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.failures == 0 && owner.signals == 1)
        precondition(owner.launchRequestIdentifierObserved == true && owner.launchPIDObserved == true,
            "duplicate failure overwrote settled launch observations")
        let next = UUID(); owner.launch(next)
        callbacks[0](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.ext.kills == 0 && owner.launchRequestPending == next)
        precondition(owner.launchRequestIdentifierObserved == nil && owner.launchPIDObserved == nil,
            "late callback overwrote the new attempt's observations")
        callbacks[1](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.signals == 2 && owner.sideStorePid == 17)
        owner.launch(UUID())
        callbacks[2](nil, NSError(domain: CombinedFailure.LaunchContext.bridgeErrorDomain, code: 1))
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.failures == 1 && owner.signals == 2)
        precondition(owner.failureContext?.sourceStep == .requestCallbackNoIdentifier)
        precondition(owner.failureContext?.requestIdentifierObserved == "no" &&
                     owner.failureContext?.pidObserved == "unknown")
        precondition(owner.failureContext?.errorChain == [.init(
            domain: CombinedFailure.LaunchContext.bridgeErrorDomain, code: 1)])
        owner.launch(UUID()); owner.ext.observedPID = 0
        callbacks[3](UUID(), nil)
        try await Task.sleep(nanoseconds: 30_000_000)
        precondition(owner.failures == 2 && owner.signals == 2)
        precondition(owner.failureContext?.sourceStep == .processIdentifierUnavailable)
        precondition(owner.failureContext?.requestIdentifierObserved == "yes" &&
                     owner.failureContext?.pidObserved == "no" && owner.failureContext?.errorChain.isEmpty == true)
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
        adapter = read_product_source("combined_refresh_handler.swift")
        method = adapter[adapter.index("    fileprivate func completedRefresh("):adapter.index("    fileprivate func legacyCompletion(")]
        source = read_product_source("combined_failure.swift") + "\n" + declaration(adapter, "enum V3RefreshCallbackIdentityPolicy {") + "\n" + declaration(adapter, "enum V3RefreshTerminalFailurePolicy {") + '''
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
    var refreshAdmissionID: String?
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
        let renewed: [String: Any] = ["version": 1, "mutationDispatched": true,
            "installAcknowledged": true, "profileStore": "matched", "renewal": "extended", "certificate": "valid"]
        let valid: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": run,
            "expected_ids": ["a", "b"], "host_handoff": false,
            "results": [["bundle_id": "a", "success": true, "verification": renewed], ["bundle_id": "b", "success": true, "verification": renewed]]]
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
        var noEvidence = valid; noEvidence["results"] = [["bundle_id": "a", "success": true], ["bundle_id": "b", "success": true]]
        _ = try receive(noEvidence)
        precondition(defaults.string(forKey: key) == run, "unverified success cannot release uncertainty")
        var failed = valid; failed["results"] = [["bundle_id": "a", "success": true, "verification": renewed],
            ["bundle_id": "b", "success": false, "failure": CombinedFailure(operation: "refresh", stage: .authentication, id: run).wire]]
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
        adapter = read_product_source("combined_refresh_handler.swift")
        method = adapter[adapter.index("    fileprivate func applicationReady("):adapter.index("    private func awaitServiceReady(")]
        source = read_product_source("combined_failure.swift") + "\n" + declaration(adapter, "public struct V3StartupDiagnosticLog {") + '''
import Foundation
@MainActor final class Probe {
    var launchID: UUID? = UUID()
    var readinessTask: Task<Void, Never>?
    var launchApplicationReadyObserved: Bool?
    var readinessError: Error?
    var failureStep: CombinedFailure.LaunchContext.Step?
    var probes = 0; var failures = 0; var signals = 0
    var startupDiagnostics = V3StartupDiagnosticLog()
    var service: Probe { self }
    enum Signal { case ready }
    func signal(_ signal: Signal, attempt: UUID) { signals += 1 }
    func awaitServiceReady(_ id: UUID) async throws {
        probes += 1
        try await Task.sleep(nanoseconds: 30_000_000)
        if let readinessError { throw readinessError }
    }
    func failed(_ id: UUID, stage: CombinedFailure.Stage, underlying: Error,
                launchSourceStep: CombinedFailure.LaunchContext.Step? = nil) {
        failures += 1; failureStep = launchSourceStep
    }
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
        precondition(owner.launchApplicationReadyObserved == true)
        owner.readinessTask = nil; owner.launchID = UUID(); owner.launchApplicationReadyObserved = nil
        owner.applicationReady(id)
        precondition(owner.probes == 1 && owner.launchApplicationReadyObserved == nil,
            "old readiness callback mutated new launch observations")
        owner.applicationReady(owner.launchID!)
        owner.readinessTask?.cancel()
        await owner.readinessTask?.value
        precondition(owner.failures == 0 && owner.signals == 1)
        let failed = Probe(); failed.readinessError = NSError(domain: "ReadinessFixture", code: 1)
        failed.applicationReady(failed.launchID!)
        await failed.readinessTask?.value
        precondition(failed.failures == 1 && failed.signals == 0 && failed.launchApplicationReadyObserved == true)
        precondition(failed.failureStep == .readinessProbe)
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
            source.write_text(read_product_source("combined_failure.swift") + "\n" +
                              read_product_source("combined_service_connection.swift") + "\n" +
                              (ROOT / "tests/fixtures/combined_startup_harness.swift").read_text(), encoding="utf-8")
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
            source.write_text("#include <assert.h>\n#include <stdio.h>\n" + read_product_source("combined_container_storage.h") + r'''
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


class StartupOwnerTests(unittest.TestCase):
    def test_automatic_host_handoff_persists_run_before_manifest_copies_flag(self):
        source = owner_source("SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift", "SideStore")
        persist = source[source.index("    private func persistAutomaticHostHandoff()"):source.index("    private func persistAutomaticRefreshVerification(")]
        manifest = source[source.index("private func persistAutomaticRefreshVerification"):source.index("    private func startListeningForRunningApps()")]
        self.assertLess(persist.index('defaults.set(true, forKey: "liveContainerAutoRefreshHostHandoff")'),
                        persist.index('forKey: "liveContainerAutoRefreshHostHandoffRunID")'))
        self.assertIn('"host_handoff": defaults.bool(forKey: "liveContainerAutoRefreshHostHandoff")', manifest)
        self.assertLess(source.index("self?.persistAutomaticHostHandoff()"),
                        source.index("self.persistAutomaticRefreshVerification(results: results"))

    def test_current_startup_has_no_refresh_sentinel(self):
        source = owner_source("SideStoreSupport/SideStore.swift")
        self.assertNotIn("__v3_connect", source)
        self.assertNotIn("bookmarkForURL(sideStoreHomeURL)!", source)
        self.assertIn("func ensureServiceConnected()", source)
        self.assertIn("func performRefresh(", source)


class ReadinessRegressionTests(unittest.TestCase):
    def test_startup_probe_uses_bounded_backoff_and_terminal_policy(self):
        probe = declaration(owner_source("SideStoreSupport/SideStore.swift"), "private func awaitServiceReady(")
        self.assertIn("while true", probe)
        self.assertIn("V3ServiceReadinessProbeState.resolve", probe)
        self.assertIn("backoff.nextDelay(remaining:", probe)
        self.assertNotIn("lastSnapshotError", probe)

    def test_direct_refresh_rechecks_after_connection_and_uses_service_admission(self):
        generator = owner_source("SideStoreSupport/SideStore.swift")
        handler = read_product_source("combined_refresh_handler.swift")
        self.assertNotIn("/*REFRESH_READINESS*/", generator)
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

    def test_current_pipeline_reports_phase_without_replay(self):
        source = owner_source("SideStore/Core/Operations/PipelineExecutor.swift", "SideStore")
        self.assertIn("V3_PIPELINE_PHASE_REPORTING_V1", source)
        self.assertIn("await headlessHandler.recordPipelinePhase(step,", source)
        self.assertIn("downloadUsesNetwork: downloadingApp.url?.isFileURL == false", source)

    def test_generated_pinned_portal_failures_preserve_request_owned_signing_context(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        errors_text = owner_source("Sources/Models/Errors.swift", "SideStore/Dependencies/SideSign")
        error_start = errors_text.index("public enum DeveloperPortalError")
        error_end = errors_text.index("public enum SignerError", error_start)
        actual_side_sign_errors = errors_text[error_start:error_end]

        response_text = owner_source("Sources/Models/DeveloperPortalResponses.swift", "SideStore/Dependencies/SideSign")
        status_start = response_text.index("struct DeveloperPortalStatusResponse:")
        status_open = response_text.index("{", status_start)
        status_end = _matching_swift_brace(response_text, status_open)
        actual_status_response = response_text[status_start:status_end]

        constants_text = owner_source("Sources/Constants.swift", "SideStore/Dependencies/SideSign")
        codes_start = constants_text.index("public enum DeveloperPortalResultCodes {")
        codes_open = constants_text.index("{", codes_start)
        actual_result_codes = constants_text[codes_start:_matching_swift_brace(constants_text, codes_open)]

        app_ids_text = owner_source("Sources/DeveloperPortal/AppIDs.swift", "SideStore/Dependencies/SideSign")
        handler_open = app_ids_text.index("{", app_ids_text.index("resultCodeHandler: {"))
        actual_app_id_result_handler = app_ids_text[handler_open:_matching_swift_brace(app_ids_text, handler_open)]

        api_text = owner_source("Sources/DeveloperPortal/DeveloperPortalAPI.swift", "SideStore/Dependencies/SideSign")
        patched_api = api_text
        portal_observer = declaration(api_text, "public enum SideSignPortalDiagnostics {")
        pipeline = owner_source("SideStore/Core/Operations/PipelineExecutor.swift", "SideStore")
        signing_helper_source = declaration(owner_source("SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift", "SideStore"), "struct V3RefreshSelectionFailure:") + "\n" + pipeline[pipeline.index("enum V3RefreshFailureBoundary:"):]
        catch_start = pipeline.index("            result = error")
        catch_end = pipeline.index("\n        }\n    }", catch_start)
        pipeline_failure_handler = pipeline[catch_start:catch_end]
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
        self.assertIn('"ENTITY_ERROR.ATTRIBUTE.INVALID"', portal_observer)
        self.assertIn("guard let value, known.contains(value) else { return nil }", portal_observer)
        self.assertIn("ServerError.underlyingError(code: -1, message: detail)", actual_status_decoder)
        self.assertIn("ServerError.underlyingError(code: code, message: message)", actual_status_decoder)
        self.assertIn("lcSafeSigningCause(error, portalResponse: portalResponse)", signing_helper_source)

        generated_proxy = owner_source("SideStore/Core/Auth/DeveloperPortalProxy.swift", "SideStore")
        method_start = generated_proxy.index("public func addAppID(name:")
        method_open = generated_proxy.index("{", method_start)
        actual_add_app_id = generated_proxy[method_start:_matching_swift_brace(generated_proxy, method_open)]
        self.assertIn('sourceStep: "appIDRegistration"', actual_add_app_id)
        self.assertIn("ALTAppleAPI.shared.addAppID", actual_add_app_id)

        failure_model = _without_imports(read_product_source("combined_failure.swift"))
        behavioral_model = _without_imports(read_product_source("v3_behavioral_primitives.swift"))
        template = (ROOT / "tests/fixtures/v3_portal_failure_harness.swift").read_text(encoding="utf-8")

        def build_source(*, mutate_server_code=False, mutate_source_step=False, mutate_quota=False, mutate_portal_type=False, mutate_9120=False):
            signing_helper = signing_helper_source
            if mutate_portal_type:
                original = 'if error is DeveloperPortalError { context["typed_error"] = "sideSignDeveloperPortalError" }'
                self.assertEqual(signing_helper.count(original), 1)
                signing_helper = signing_helper.replace(original, "// targeted mutation: portal type lost", 1)
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
            app_id_handler = actual_app_id_result_handler
            if mutate_9120:
                original = ', 9120: // LC_APP_ID_LIMIT_9120_V1'
                self.assertEqual(app_id_handler.count(original), 1)
                app_id_handler = app_id_handler.replace(original, ':', 1)
            replacements = {
                "$SIDESIGN_ERROR_TYPES$": actual_side_sign_errors,
                "$DEVELOPER_PORTAL_STATUS_RESPONSE$": actual_status_response,
                "$DEVELOPER_PORTAL_RESULT_CODES$": actual_result_codes,
                "$PORTAL_OBSERVER$": portal_observer,
                "$ACTUAL_APP_ID_RESULT_HANDLER$": app_id_handler,
                "$COMBINED_FAILURE$": failure_model,
                "$BEHAVIORAL_POLICIES$": behavioral_model,
                "$SIGNING_CAUSE_HELPER$": signing_helper,
                "$ACTUAL_HTTP_OBSERVER_AND_STATUS_DECODER$": actual_http_observer + actual_status_decoder,
                "$ACTUAL_PATCHED_ADD_APP_ID_METHOD$": generated_method,
                "$ACTUAL_PIPELINE_FAILURE_HANDLER$": pipeline_failure_handler,
            }
            result = template
            for marker, value in replacements.items():
                self.assertEqual(result.count(marker), 1, f"fixture placeholder drift: {marker}")
                result = result.replace(marker, value, 1)
            wire_model = read_product_source("v3_wire_contract.swift")
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
            execute("portal-failure-no-portal-type", build_source(mutate_portal_type=True), False)
            execute("portal-failure-no-9120", build_source(mutate_9120=True), False)

    def test_structured_failures_are_preserved_not_rewrapped(self):
        handler = read_product_source("combined_refresh_handler.swift")
        self.assertIn("CombinedFailure.preserving(underlying", handler)
        self.assertIn("Never double-wrap", handler)

    def test_startup_markers_carry_correlation(self):
        handler = read_product_source("combined_refresh_handler.swift")
        startup = owner_source("SideStoreSupport/SideStore.swift")
        for marker in ("PROCESS_LAUNCH_BEGIN", "PROCESS_LAUNCHED", "XPC_CONNECTED",
                       "APPLICATION_READY", "PROCESS_EXITED", "START_FAILED",
                       "SNAPSHOT_READY", "READINESS_TIMEOUT", "READINESS_INVALID_RESPONSE"):
            self.assertIn(marker, handler + startup)
        self.assertIn("id.uuidString", handler)
        self.assertIn("V3ServiceReadinessReply.decode(response, requestID: requestID)", startup)
        self.assertNotIn('result["ok"] as? Bool', startup)

    def test_reconnect_and_mutation_safety_invariants(self):
        handler = read_product_source("combined_refresh_handler.swift")
        connection = read_product_source("combined_service_connection.swift")
        self.assertIn("if attemptID == nil { begin() }", connection)
        self.assertGreaterEqual(handler.count("launchID == id"), 4)
        self.assertIn("attemptID == id", connection)
        self.assertIn("extensionProcess?._kill(15)", handler)
        self.assertIn("guard v3RefreshToken == nil", handler)
        self.assertIn("v3RefreshToken = token", handler)


if __name__ == "__main__":
    unittest.main()
