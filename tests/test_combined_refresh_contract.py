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


class CombinedRefreshContractTests(unittest.TestCase):
    def test_full_patch_composition_is_transactional_on_pinned_source(self):
        source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.getenv("SIDESTORE_TEST_SOURCE")
        if not source: self.skipTest("pinned embedded SideStore source required")
        revision = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, patch.PIN)
        spec = importlib.util.spec_from_file_location("background_automation", ROOT / "scripts/patch_background_automation.py")
        background = importlib.util.module_from_spec(spec); spec.loader.exec_module(background)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "side"
            paths = ["AltStore/Core/Components/Keychain.swift", "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"]
            for relative in paths:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(source) / relative, target)
            background.patch_background_operation(root)
            operation = root / paths[1]
            prepared = operation.read_bytes()
            def snapshot():
                return {name: (root / name).read_bytes() for name in paths + [".combined-refresh-contract.json"] if (root / name).exists()}
            # Shared Keychain transforms successfully in staging, then contract rejects
            # the changed anchor. Neither Keychain nor operation nor manifest may leak out.
            operation.write_bytes(prepared.replace(b"let nsError = error as NSError", b"let changed = error as NSError"))
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
            self.assertIn(b"Keychain.shared.embeddedAuthenticationFailure()", applied[paths[1]])
            self.assertIn(b'"failure": failure.wire', applied[paths[1]])
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
        path.write_text(helper + "\n    private func startListeningForRunningApps() {}\n")
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
            self.assertNotIn(r"\\(refreshIdentifier)", result)
            self.apply(root)
            self.assertEqual(result, file.read_text())

    def test_replay_and_anchor_drift_fail_closed(self):
        for change in ("anchor", "output", "manifest", "pin"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); file = self.fixture(root)
                if change == "anchor": file.write_text(file.read_text().replace("let nsError = error as NSError", "let changed = error as NSError"))
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

    def test_actual_record_to_bridge_keeps_error_stage_and_sanitizes_logs(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("requires Swift; executed by combined macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); file = self.fixture(root); self.apply(root)
            # Inject only the UserDefaults suite, preserving actual generated helper logic.
            helper = file.read_text().replace('"group.com.SideStore.SideStore"', "testSuite")
            swift = (ROOT / "scripts/templates/combined_failure.swift").read_text() + r'''
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
