"""Behavioral regressions for the latest device-only v3.0.3 failures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates"


class DeviceAcceptanceBehaviorTests(unittest.TestCase):
    def test_picker_delete_refresh_and_route_behavior_executes(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        program = "\n".join([
            (TEMPLATES / "combined_failure.swift").read_text(encoding="utf-8"),
            (TEMPLATES / "v3_behavioral_primitives.swift").read_text(encoding="utf-8"),
            (ROOT / "tests/fixtures/v3_device_acceptance_regressions_harness.swift").read_text(encoding="utf-8"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "device-regressions.swift"
            executable = Path(directory) / "device-regressions"
            source.write_text(program, encoding="utf-8")
            built = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 DEVICE ACCEPTANCE REGRESSION BEHAVIOR PASS", result.stdout)

    def test_local_ipa_and_url_converge_only_after_resolution(self):
        runtime = (TEMPLATES / "v3_headless_runtime.swift").read_text(encoding="utf-8")
        start = runtime.index("private func makeDriver")
        end = runtime.index("private func resolveInstallTarget", start)
        driver = runtime[start:end]
        local = driver.index('kind == "installSharedIPA" ? .localIPA')
        remote = driver.index('kind == "installURL" ? .remoteURL')
        convergence = driver.index("makeInstallDriver(id: id, kind: kind, route: route, app: app")
        self.assertLess(local, convergence)
        self.assertLess(remote, convergence)
        shared = driver[driver.index("private func makeInstallDriver"):]
        self.assertIn("V3InstallPipelineParity.makeOperation(route: route, app)", shared)
        self.assertIn("AppOperation.install($0)", shared)
        self.assertEqual(shared.count("operation: built.operation"), 1)
        resolver = runtime[runtime.index("private func resolveInstallTarget"):runtime.index("static func readAppMetadata")]
        self.assertIn("V3IPAStaging.inspect(token: token", resolver)
        self.assertIn("return try await ipaTarget(url: url, scoped: false)", resolver)
        self.assertIn("return .app(AnyApp", resolver)

    def test_picker_handoff_waits_for_real_dismissal_and_reload_release(self):
        shell = (TEMPLATES / "v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertNotIn("selectedInstallToken", shell)
        self.assertIn("status.installPickerDidDismiss()", shell)
        self.assertIn("installHandoff.pickerDidDismiss()", shell)
        self.assertIn("installHandoff.takeIfReady(", shell)
        self.assertIn('drainInstallPresentation(trigger: "snapshot_finished")', shell)
        self.assertIn('drainInstallPresentation(trigger: "picker_dismissed")', shell)
        self.assertNotIn("Task.yield()", shell)

    def test_delete_has_backend_evidence_and_authoritative_reconciliation(self):
        runtime = (TEMPLATES / "v3_headless_runtime.swift").read_text(encoding="utf-8")
        center = runtime[runtime.index("private func deleteAndReconcile"):runtime.index("private func authoritativeLibraryContains")]
        self.assertIn("V3DeleteNativeSuccessRegistry.shared.contains(sessionID: id)", center)
        self.assertIn("authoritativeLibraryContains(bundleIdentifier: bundleIdentifier)", center)
        self.assertIn("deadlineExpired: reconciliationExpired", center)
        self.assertIn("progress: group.progress.fractionCompleted", center)
        self.assertIn("DELETE_RECONCILE_COMPLETED", center)
        self.assertIn("AppOperation.install($0)", runtime)

    def test_setup_and_home_use_same_manual_scheduler_request_path(self):
        shell = (TEMPLATES / "v3_unified_shell.swift").read_text(encoding="utf-8")
        scheduler = (TEMPLATES / "livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        self.assertIn('"origin": "home"', shell)
        self.assertIn('"origin": "setupAssistant"', shell)
        self.assertIn('await execute(source: "manual", manualRequestID: requestID, manualOrigin: origin)', scheduler)
        self.assertIn("NETWORK_PREFLIGHT_PASS", scheduler)
        self.assertIn("NETWORK_PREFLIGHT_START", scheduler)
        self.assertIn("MANIFEST run_id=", scheduler)
        self.assertIn("recordNetworkPreflight(\"passed\"", scheduler)
        self.assertIn(".sheet(isPresented: $status.connectionPresented)", shell)
        self.assertIn('case "connection": status.connectionPresented = true', shell)


if __name__ == "__main__":
    unittest.main()
