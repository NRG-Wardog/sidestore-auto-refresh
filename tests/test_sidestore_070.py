"""Execute the standalone migration against the exact source used by CI."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import adapt_sidestore_070_signing as signing
from test_combined_transport import function, snapshot

RESIGN = "SideStore/Core/Operations/PipelineOperations/ResignAppOperation.swift"
SIGNING_BLOCK = r'''        let resignedAppURL = try await self.resignAppBundle(at: appBundleURL, team: team, certificate: certificate, profiles: Array(profiles.values))
        guard let resignedAppBundle = ALTApplication(fileURL: resignedAppURL) else { throw OperationError.invalidApp }

        self.debugLog("[ResignAppOperation] Resigned app \(self.context.bundleIdentifier) to \(resignedAppBundle.bundleIdentifier).")
'''


class SigningAdapterTests(unittest.TestCase):
    def test_whitespace_and_literal_interpolation_and_idempotence(self):
        for blank in ("\n", "        \n", "\t\n"):
            with self.subTest(blank=repr(blank)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / RESIGN
                path.parent.mkdir(parents=True)
                path.write_text(SIGNING_BLOCK.replace("\n\n", "\n" + blank), encoding="utf-8")
                signing.patch(root)
                result = path.read_text(encoding="utf-8")
                self.assertEqual(result.count("SIDESTORE_SIGN_PASS"), 1)
                self.assertIn(r"bundle_id=\(resignedAppBundle.bundleIdentifier)", result)
                self.assertIn("provisioningProfile != nil", result)
                signing.patch(root)
                self.assertEqual(path.read_text(encoding="utf-8"), result)

    def test_changed_or_duplicate_signing_statement_fails_without_writing(self):
        for source in (SIGNING_BLOCK * 2, SIGNING_BLOCK.replace("try await", "try? await")):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / RESIGN
                path.parent.mkdir(parents=True)
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(SystemExit):
                    signing.patch(root)
                self.assertEqual(path.read_text(encoding="utf-8"), source)


class StandaloneMigrationTests(unittest.TestCase):
    def test_full_patch_chain_on_pinned_source(self):
        source = os.environ.get("SIDESTORE_070_TEST_SOURCE")
        if not source:
            self.skipTest("Set SIDESTORE_070_TEST_SOURCE to the pinned standalone source")
        source = Path(source)
        self.assertTrue((source / RESIGN).is_file())
        with tempfile.TemporaryDirectory(prefix="standalone-070-") as directory:
            root = Path(directory) / "SideStore"
            shutil.copytree(source, root, ignore=shutil.ignore_patterns(
                ".git", ".build", "build", "*.xcframework", "*.ipa"))
            mux = root / "Dependencies/minimuxer"
            commands = [
                ("adapt_sidestore_070_pairing.py", mux),
                ("adapt_sidestore_070_signing.py", root),
                ("patch_sidestore_integration.py", mux, root),
                ("patch_background_automation.py", root),
                ("patch_local_idevice_package.py", mux),
            ]
            before = snapshot(root)
            for iteration in range(2):
                for script, *args in commands:
                    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script),
                                             *map(str, args)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, script + "\n" + result.stdout + result.stderr)
                current = snapshot(root)
                if iteration == 0:
                    first = current
                else:
                    self.assertEqual(first, current, "Full patch chain must be idempotent")
            gateway = (mux / "DeviceGateway/idevice/IdeviceGateway.swift").read_text(encoding="utf-8")
            self.assertEqual(gateway.count("private var usesCoreDevice:"), 1)
            connection = function(gateway, "ensureCoreDeviceConnection")
            self.assertIn("do {", connection)
            self.assertIn("catch {", connection)
            self.assertIn("tunnel_create_usb(provider, &adapter, &handshake)", connection)
            self.assertRegex(gateway, r"private var usesCoreDevice: Bool \{ pairingFileType == .lockdown \}")
            self.assertIn("if usesCoreDevice {", function(gateway, "ensureRPConnection"))
            self.assertNotIn("isRPPairing", gateway)
            # Preserve the actual 0.7 authentication implementation, not just its imports.
            for path, data in before.items():
                if path.startswith(("SideStore/Core/Auth/", "Dependencies/SideSign/")):
                    self.assertEqual(current[path], data, path)
            background = (root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift").read_text(encoding="utf-8")
            self.assertIn("hasPasswordCredentials || hasTokenCredentials || hasReusableSession", background)
            self.assertIn("persistAutomaticRefreshVerification(results: results)", background)
            self.assertIn("group?.cancel()", background)
            for line in background.splitlines():
                if 'debugLog("[AUTO_REFRESH]' in line:
                    self.assertNotIn(r"\\(", line, "Diagnostic values must use Swift interpolation")
            # Swift parsing runs on macOS CI before any IPA archive is started.
            swiftc = shutil.which("swiftc")
            if os.environ.get("REQUIRE_SWIFT_070_CHECKS") == "1":
                self.assertIsNotNone(swiftc, "CI must execute Swift validation")
            if swiftc:
                binary = root / "pairing-policy-test"
                result = subprocess.run([swiftc, str(mux / "Common/MinimuxerConstants.swift"),
                                         str(mux / "Common/PairingProtocol.swift"),
                                         str(mux / "Common/PairingFile.swift"),
                                         str(ROOT / "tests/standalone_pairing_policy.swift"),
                                         "-o", str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                subprocess.run([str(binary)], check=True, timeout=30)
                for path, data in current.items():
                    if path.endswith(".swift") and before.get(path) != data:
                        result = subprocess.run([swiftc, "-frontend", "-parse", str(root / path)],
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, path + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()
