from pathlib import Path
import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.owner_source import owner_source
from scripts.headless_artifact_contract import verify_keychain_selection_contract
from tests.support.product_sources import read_product_source

class CombinedRefreshContractTests(unittest.TestCase):
    def test_package_verifier_does_not_require_interpolation_fragments_in_binary(self):
        # Optimized Swift can encode short fragments as instruction immediates.
        # Link checks must not assume that a binary contains a rendered log line.
        for diagnostic in (b"[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=shared",
                           b"[LC_KEYCHAIN] GROUP_SELECTED service=storage scope=process",
                           b"[LC_KEYCHAIN] GROUP_SELECTED service="):
            with self.subTest(diagnostic=diagnostic):
                verify_keychain_selection_contract(
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
                verify_keychain_selection_contract(executable)

    @unittest.skipUnless(shutil.which("swiftc"), "optimized Swift diagnostic executes on macOS CI")
    def test_optimized_production_diagnostic_emits_scope_and_passes_link_gate(self):
        template = read_product_source("embedded_shared_keychain.swift")
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
            verify_keychain_selection_contract(executable.read_bytes())


    def test_handoff_uses_host_run_and_manifest_counts_expected_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = owner_source("SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift", "SideStore")
            self.assertIn('"expected_ids": expectedIDs, "requested_ids": requestedIDs, "skipped_ids": skippedIDs', result)
            self.assertIn('"version": 2', result)
            self.assertIn('"schema": "LiveContainerRefreshManifestV2"', result)
            self.assertNotIn('"error": error.localizedDescription', result)
            self.assertIn('defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier', result)
            self.assertIn('CombinedFailure.capture(V3HeadlessPairingFailure.tagIfInvalidPairing(error)', result)
            self.assertIn('"error": failure.message, "failure": failure.wire', result)
            self.assertIn('REFRESH_FAILED \\(failure.technicalDetails)', result)
            privacy_helpers = result[result.index("    private func automaticRefreshDefaults()"):result.index("    private func startListeningForRunningApps()") ]
            self.assertNotIn("error.localizedDescription", privacy_helpers,
                             "the composed output must not persist raw provider response text")
            self.assertNotIn(r"\\(refreshIdentifier)", result)


    def test_post_dispatch_connection_loss_retains_refresh_lease_without_matching_callback(self):
        handler = read_product_source("combined_refresh_handler.swift")
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
            root = Path(directory)
            # Execute the generated verification helper in isolation. The
            # fixture also carries a separate notification catch block so the
            # combined transform can verify both call sites; that fragment is
            # not part of the Operation class body in this harness.
            generated = owner_source("SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift", "SideStore")
            helper_start = generated.index("    private func automaticRefreshDefaults()")
            helper_end = generated.index("    private func startListeningForRunningApps()")
            helper = generated[helper_start:helper_end]
            self.assertNotIn('"group.com.SideStore.SideStore"', helper,
                             "the helper must resolve its suite from the published group, "
                             "not from a literal that a test can rewrite")
            wire = read_product_source("v3_wire_contract.swift")
            failure = read_product_source("combined_failure.swift")
            primitives = read_product_source("v3_behavioral_primitives.swift")
            bridge = read_product_source("v3_service_bridge.swift")
            context = bridge[bridge.index("enum V3CatalogRequestContext {"):]
            context = context[:context.index("\n@MainActor")]
            runtime = read_product_source("v3_headless_runtime.swift")
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
            shared_group = read_product_source("v3_shared_app_group.swift")
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
// The native group is external to this manifest serialization harness.
struct HarnessVerification { let wire: [String: Any] }
struct RefreshGroup {
    func verification(for bundleID: String) -> HarnessVerification? { nil }
    func transportObservationWire(for runID: String) -> [String: Any]? { nil }
}
@MainActor final class Operation {
    var installedApps = [InstalledApp()]
    var refreshIdentifier = UUID().uuidString
    func debugLog(_ message: String) { logs.append(message) }
    func record(_ error: Error) {
        persistAutomaticRefreshVerification(results: ["fixture.app": .failure(error)],
            attemptedAppIDs: ["fixture.app"], group: RefreshGroup(), runID: (try? automaticRefreshDefaults())?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier)
    }
    func record(_ results: [String: Result<InstalledApp, Error>], attemptedAppIDs: [String]) {
        persistAutomaticRefreshVerification(results: results, attemptedAppIDs: attemptedAppIDs, group: RefreshGroup(), runID: (try? automaticRefreshDefaults())?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier)
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
        targetProbe.record(["fixture.app": .success(InstalledApp())], attemptedAppIDs: ["fixture.app"], group: RefreshGroup(), runID: (try? automaticRefreshDefaults())?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier)
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
