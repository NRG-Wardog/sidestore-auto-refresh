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

    def test_picker_is_presented_directly_from_the_root_uikit_anchor(self):
        shell = (TEMPLATES / "v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertNotIn("selectedInstallToken", shell)
        self.assertIn("V3InstallPickerPresenter(status: status)", shell)
        self.assertIn("anchor.present(documentPicker, animated: true)", shell)
        self.assertIn("UIDocumentPickerViewController(forOpeningContentTypes: [.data], asCopy: true)", shell)
        self.assertIn(".fullScreenCover(item: $status.presentation", shell)
        self.assertNotIn(".sheet(isPresented: pickerBinding", shell)
        self.assertNotIn("V3FullScreenCoverHost", shell)
        self.assertIn("V3InstallPickerPresentationState", shell)
        self.assertIn("presentation.didPresent(attemptID: attemptID)", shell)
        self.assertIn("status?.installPickerDidDisappear(attemptID: attemptID)", shell)
        self.assertIn("func resetInstallUI(attemptID: UUID", shell)
        for marker in ("[V3_INSTALL_UI] tap", "begin_attempt result=started",
                       "tap_rejected reason=presentation_active", "tap_rejected reason=attempt_not_idle",
                       "picker_present_requested", "picker_did_present", "picker_selected",
                       "picker_dismissed", "staged", "operation_present_requested",
                       "operation_did_present", "terminal", "reset_to_idle"):
            self.assertIn(marker, shell)
        self.assertIn("operationCoverDidDismiss", shell)
        self.assertIn("retryInstallCancellation", shell)
        self.assertIn("status.installTerminal(attemptID: request.installAttemptID", shell)
        self.assertIn('drainInstallPresentation(trigger: "snapshot_finished")', shell)
        self.assertIn('drainInstallPresentation(trigger: "picker_did_dismiss")', shell)
        self.assertNotIn("installHandoff", shell)
        self.assertNotIn("installPickerDidDismiss", shell)
        self.assertNotIn("presentImmediately", shell)
        self.assertNotIn("asyncAfter", shell)

    def test_install_menu_action_has_icon_and_side_store_context(self):
        shell = (TEMPLATES / "v3_unified_shell.swift").read_text(encoding="utf-8")
        button = shell[shell.index("struct V3InstallButton:"):shell.index("struct V3OperationRequest:")]
        self.assertIn('Button("Install with SideStore", systemImage: "arrow.down.app")', button)
        self.assertIn("Install / Sideload App with SideStore", button)
        self.assertIn("Choose an IPA", button)

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
