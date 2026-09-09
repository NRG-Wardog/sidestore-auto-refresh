"""Pinned-source/bootstrap checks; native checks skip explicitly without tools."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_combined_transport import SourceFixture, patch, snapshot

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/cellular"
spec = importlib.util.spec_from_file_location("cellular_bootstrap_patch", EXPERIMENT / "patch_bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class EmbeddedBootstrapTests(SourceFixture):
    def test_real_pinned_source_integration_and_idempotence(self):
        patch(self.mux)
        before = snapshot(self.side)
        bootstrap.patch_side(self.side)
        first = snapshot(self.side)
        bootstrap.patch_side(self.side)
        self.assertEqual(first, snapshot(self.side))
        changed = {name for name in first if before.get(name) != first[name]}
        self.assertTrue(changed)
        self.assertFalse(any("Pipeline" in name or name.endswith("Info.plist") for name in changed))
        gateway = self.gateway.read_text(encoding="utf-8")
        probe = gateway[gateway.index("    public func cellularReadOnlyProbe"):gateway.index("    private func ensureCoreDeviceConnection")]
        self.assertIn("withFFIDispatch(on: ffiQueue)", probe)
        self.assertIn("self.batchCount == 0", probe)
        self.assertIn("!tunnel_heartbeat_is_active()", probe)
        self.assertLess(probe.index("reachable.count == 1"), probe.index("cellular_lockdown_validate("))
        self.assertLess(probe.index("authentication == 0"), probe.index("cellular_tunnel_create("))
        self.assertIn("idevice_pairing_file_from_bytes", probe)
        self.assertIn("defer { idevice_provider_free(provider) }", probe)
        self.assertNotIn("lockdownd_connect(", probe)
        for forbidden in ("installation_proxy_install(", "afc_file_write(", "usbmuxd_provider_new(",
                          "getErrorMessage(", "idevice_set_transport_log_callback("):
            self.assertNotIn(forbidden, probe)
        discovery = self.read("Sources/Services/DeviceConnectionManager.swift")
        self.assertIn("resolveCandidatePeers(for: tunnel)", discovery)
        self.assertIn("NetworkIfaceScanner.scan(quiet: true)", discovery)
        view = (self.side / "SideStore/Views/Settings/Diagnostics/ExperimentalFeaturesView.swift").read_text(encoding="utf-8")
        self.assertIn("PairingFileManager.shared.fetchPairingFile()", view)
        self.assertIn("SwiftUI.Button", view)
        self.assertNotIn("HouseArrest", view)
        self.assertIn("cellularValidatePairing(content)", view)
        self.assertLess(view.index("cellularValidatePairing(content)"), view.index("saveDiagnosticPairing(contents:"))
        manager = (self.side / "SideStore/Core/Pairing/PairingFileManager.swift").read_text(encoding="utf-8")
        save = manager[manager.index("    func saveDiagnosticPairing"):manager.index("    func savePairingFile")]
        self.assertIn("Self.pairingFileName", save)
        self.assertIn(".completeFileProtectionUntilFirstUserAuthentication", save)
        self.assertIn("values.isExcludedFromBackup = true", save)
        self.assertLess(save.index("setResourceValues"), save.index("Darwin.rename"))
        self.assertNotIn("removeItem(at: destination)", save)


class BootstrapPolicyTests(unittest.TestCase):
    def test_pairing_error_feature_gates(self):
        validation = bootstrap.template("lockdown_validation.rs")
        classifier = validation[:validation.index("/// Read-only")]
        self.assertIn('#[cfg(feature = "pair")]\n        IdeviceError::UserDeniedPairing', classifier)
        compiler = shutil.which("rustc")
        if compiler is None:
            self.skipTest("rustc unavailable; feature-gated Rust execution not verified locally")
        source = '''enum IdeviceError {
    InvalidHostID,
    #[cfg(feature = "pair")]
    UserDeniedPairing,
    Other,
}
''' + classifier + '''
fn main() {
    assert_eq!(cellular_pairing_error_status(&IdeviceError::InvalidHostID), 2);
    assert_eq!(cellular_pairing_error_status(&IdeviceError::Other), 3);
    #[cfg(feature = "pair")]
    assert_eq!(cellular_pairing_error_status(&IdeviceError::UserDeniedPairing), 2);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.rs"
            main.write_text(source, encoding="utf-8")
            for features in ([], ["--cfg", 'feature="pair"']):
                executable = root / ("policy.exe" if os.name == "nt" else "policy")
                result = subprocess.run([compiler, str(main), *features, "-o", str(executable)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_executable_state_policy(self):
        compiler = shutil.which("swiftc")
        if compiler is None:
            self.skipTest("swiftc unavailable; policy execution is NOT verified")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.swift"
            main.write_text(bootstrap.template("BootstrapState.swift") + "\n" + bootstrap.template("state_tests.swift"), encoding="utf-8")
            executable = root / "policy"
            result = subprocess.run([compiler, str(main), "-o", str(executable)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("CELLULAR_BOOTSTRAP_POLICY_TESTS_PASS", result.stdout)

    def test_rust_borrowed_validation_and_shared_tunnel_patch(self):
        source = ROOT / ".audit/transport/idevice/ffi/src"
        if not (source / "tunnel_provider.rs").is_file():
            self.skipTest("Patched pinned idevice fixture unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ffi/src"
            target.mkdir(parents=True)
            for name in ("lockdown.rs", "tunnel_provider.rs"):
                shutil.copyfile(source / name, target / name)
            bootstrap.patch_rust(root)
            first = snapshot(root)
            bootstrap.patch_rust(root)
            self.assertEqual(first, snapshot(root))
            validation = bootstrap.template("lockdown_validation.rs")
            self.assertIn("client.start_session(&pairing).await", validation)
            self.assertIn('client.get_value(Some("UniqueDeviceID"), None)', validation)
            self.assertNotIn("Box::from_raw", validation)
            self.assertNotIn("tracing::", validation)
            self.assertNotIn("transport_log", validation)
            tunnel = (target / "tunnel_provider.rs").read_text(encoding="utf-8")
            creation = tunnel[:tunnel.index('pub unsafe extern "C" fn tunnel_pair_usb(')]
            self.assertEqual(creation.count("CoreDeviceProxy::connect(provider_ref)"), 1)
            self.assertIn("cellular_tunnel_create(lockdown_provider, out_adapter, out_handshake, std::ptr::null_mut())", tunnel)

    def test_no_polling_or_report_secrets(self):
        screen = bootstrap.template("screen.swift")
        for forbidden in ("Timer.scheduledTimer", "while true", "Task.sleep", "UIPasteboard", "ShareLink",
                          "SSID", "CLLocationManager", "pairing.plist"):
            self.assertNotIn(forbidden, screen)
        self.assertIn("timeout?.setEventHandler {}", screen)
        self.assertIn("monitor.pathUpdateHandler = nil", screen)
        self.assertIn("baselinePairing != pairingDigest", screen)
        self.assertIn("before == .cellular", screen)
        policy = bootstrap.template("BootstrapState.swift")
        report = policy[policy.index("public var report:"):]
        for forbidden in ("Identity", "pairingDigest", "HostPrivateKey", "EscrowBag"):
            self.assertNotIn(forbidden, report)


if __name__ == "__main__":
    unittest.main()
