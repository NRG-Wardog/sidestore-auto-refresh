"""Transport settings must update the retained upstream runtime caches."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_v3_settings_effective_defaults import declaration

ROOT = Path(__file__).resolve().parents[1]


class TransportSettingParityTests(unittest.TestCase):
    def test_integer_writes_validate_then_refresh_upstream_caches(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        setter = declaration(runtime, "    static func settingsSet(")
        integer = setter[setter.index("} else if intSettings.contains(key)"):]
        self.assertLess(integer.index("guard value >= 0"), integer.index("UserDefaults.standard.set"))
        self.assertIn('key != "remotePairingPortOverride" || value <= Int(UInt16.max)', integer)
        self.assertLess(integer.index("UserDefaults.standard.set"), integer.index("remotePairingPortCache ="))
        self.assertIn("AppConstants.Minimuxer.remotePairingPort", integer)
        self.assertIn("AppConstants.Minimuxer.defaultTCPProbeTimeoutMs", integer)
        self.assertNotIn("selectedGatewayBackendCache =", setter)
        self.assertNotIn("syncMinimuxerBackendFromUserDefaults()", setter)

    def test_real_settings_adapter_updates_real_upstream_caches(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; transport-settings harness runs in macOS CI")
        source_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
        if not source_root:
            self.skipTest("Pinned SideStore source unavailable")
        def upstream(path):
            return subprocess.check_output(["git", "-C", source_root, "show",
                "ff25922e5c13ccfafd83bda5092910d848ebd409:" + path], text=True)
        defaults = upstream("AltStore/Core/Extensions/UserDefaults+AltStore.swift")
        properties = "\n".join(declaration(defaults, "    @objc var " + signature) for signature in (
            "remotePairingPortOverride: Int", "deviceProbeTimeoutOverride: Int", "minimuxerGatewayBackend: String"))
        wrapper = upstream("SideStore/Core/DeviceApi/MinimuxerWrapper.swift")
        cache_start = wrapper.index("public var selectedGatewayBackendCache")
        cache_end = wrapper.index("var minimuxer: any MinimuxerFacade")
        caches = wrapper[cache_start:cache_end]
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        arrays = runtime[runtime.index("    static let boolSettings:"):runtime.index("    static func settingsGet()")]
        setter = declaration(runtime, "    static func settingsSet(")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text()
        strict = "\n".join(declaration(wire, "    static func " + name) for name in ("strictBool(", "strictInt("))
        text = '''import Foundation
import CoreFoundation
public enum GatewayBackend: String { case idevice, libimobiledevice }
public enum AppConstants {
    public enum Minimuxer { public static let remotePairingPort: UInt16 = 62078; public static let defaultTCPProbeTimeoutMs = 1000 }
}
enum V3SideStoreServiceError: Error { case invalidRequest }
final class WidgetDataManager { static let shared = WidgetDataManager(); var isVerboseLoggingEnabled = false }
extension UserDefaults {
''' + properties + '''
}
''' + caches + '''
enum V3WireContract {
''' + strict + '''
}
enum V3BackendCommands {
''' + arrays + setter + '''
}
@main struct Test {
    static func main() throws {
        let defaults = UserDefaults.standard
        let keys = ["remotePairingPortOverride", "deviceProbeTimeoutOverride", "minimuxerGatewayBackend"]
        for key in keys { defaults.removeObject(forKey: key) }
        defer { for key in keys { defaults.removeObject(forKey: key) } }
        syncMinimuxerBackendFromUserDefaults()
        precondition(remotePairingPortCache == AppConstants.Minimuxer.remotePairingPort)
        defaults.minimuxerGatewayBackend = GatewayBackend.libimobiledevice.rawValue
        precondition(selectedGatewayBackendCache == .idevice)
        try V3BackendCommands.settingsSet(payload: ["key": "remotePairingPortOverride", "int": 65535])
        precondition(selectedGatewayBackendCache == .idevice,
                     "An integer edit must not activate a backend change that requires restart")
        precondition(defaults.remotePairingPortOverride == 65535 && remotePairingPortCache == 65535)
        try V3BackendCommands.settingsSet(payload: ["key": "deviceProbeTimeoutOverride", "int": 2500])
        precondition(defaults.deviceProbeTimeoutOverride == 2500 && deviceProbeTimeoutCache == 2500)
        for bad in [-1, 65536, Int.max] {
            do {
                try V3BackendCommands.settingsSet(payload: ["key": "remotePairingPortOverride", "int": bad])
                fatalError("Invalid port must fail without changing storage or cache")
            } catch V3SideStoreServiceError.invalidRequest {}
            precondition(defaults.remotePairingPortOverride == 65535 && remotePairingPortCache == 65535)
        }
        do {
            try V3BackendCommands.settingsSet(payload: ["key": "deviceProbeTimeoutOverride", "int": -1])
            fatalError("Negative timeout must fail")
        } catch V3SideStoreServiceError.invalidRequest {}
        precondition(deviceProbeTimeoutCache == 2500)
        for key in ["remotePairingPortOverride", "deviceProbeTimeoutOverride"] {
            try V3BackendCommands.settingsSet(payload: ["key": key, "int": 0])
        }
        precondition(remotePairingPortCache == AppConstants.Minimuxer.remotePairingPort)
        precondition(deviceProbeTimeoutCache == AppConstants.Minimuxer.defaultTCPProbeTimeoutMs)
        print("V3_TRANSPORT_SETTINGS_PARITY_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "main.swift").write_text(text)
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_TRANSPORT_SETTINGS_PARITY_PASS", executed.stdout)


if __name__ == "__main__":
    unittest.main()
