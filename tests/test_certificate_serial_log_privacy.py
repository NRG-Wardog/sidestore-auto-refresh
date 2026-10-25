"""Generated-source privacy regression tests for password-equivalent certificate serials."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]
PIN = "ff25922e5c13ccfafd83bda5092910d848ebd409"
CERTIFICATE_LOG_FILES = (
    ("SideStore/Core/Certificates/CertificateManager.swift", "CertificateManager"),
    ("SideStore/Core/Certificates/OCSPValidator.swift", "OCSPValidator"),
    ("SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift", "SignInOperation"),
    ("SideStore/Core/Operations/PipelineOperations/VerifyCertificateOperation.swift", "VerifyCertificateOperation"),
    ("SideStore/Core/Operations/PipelineOperations/UpdateAppCertificateOperation.swift", "UpdateAppCertificateOperation"),
)


def module(name):
    directory = "tests" if name.startswith("test_") else "scripts"
    spec = importlib.util.spec_from_file_location(name, ROOT / directory / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def pinned_source(test, relative):
    source_root = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.getenv("SIDESTORE_TEST_SOURCE")
    if not source_root:
        test.skipTest("Pinned SideStore source is supplied by the macOS CI fixture")
    return subprocess.check_output(
        ["git", "-C", source_root, "show", f"{PIN}:{relative}"],
        text=True, encoding="utf-8")


class CertificateSerialLogPrivacyTests(unittest.TestCase):
    def test_generated_backend_log_calls_omit_serials_and_are_idempotent(self):
        service = module("patch_v3_service")
        scanned = 0
        redacted = 0
        for relative, owner in CERTIFICATE_LOG_FILES:
            source = pinned_source(self, relative)
            calls_before = service._swift_log_call_ranges(source)
            generated = service.headless_certificate_serial_log_redaction(source, owner)
            self.assertEqual(service.headless_certificate_serial_log_redaction(generated, owner), generated)
            calls_after = service._swift_log_call_ranges(generated)
            self.assertGreaterEqual(len(calls_after), len(calls_before))
            unsafe_before = [
                call for _, _, call in calls_before
                if service.contains_certificate_serial_log_identifier(call)
            ]
            safe_before = [
                call for _, _, call in calls_before
                if not service.contains_certificate_serial_log_identifier(call)
            ]
            unsafe_after = [
                call for _, _, call in calls_after
                if service.contains_certificate_serial_log_identifier(call)
            ]
            self.assertTrue(unsafe_before, f"expected pinned serial-bearing logs in {relative}")
            self.assertEqual(unsafe_after, [], f"generated logs expose a serial/password value in {relative}")
            for call in safe_before:
                self.assertIn(call, [value for _, _, value in calls_after],
                              f"non-sensitive diagnostic/correlation log changed in {relative}")
            redacted += len(unsafe_before)
            scanned += len(calls_after)
        self.assertGreaterEqual(redacted, 20)
        self.assertGreater(scanned, redacted)

    def test_redaction_handles_serial_hex_multiline_logs_and_literal_interpolation(self):
        service = module("patch_v3_service")
        source = r'''
func example() {
    verboseLog("""
      Certificate record:
      - Serial Hex: '\(details.serialHex)'
      - Parsed certificate: \(certificate.serialNumber)
    """)
    self.debugLog("target serial: \(targetSerial) mismatch with \(certificate.serialNumber)")
}
'''
        generated = service.headless_certificate_serial_log_redaction(source, "Fixture")
        self.assertEqual(service.headless_certificate_serial_log_redaction(generated, "Fixture"), generated)
        self.assertIn('self.debugLog("[Fixture] Certificate identity details omitted.")', generated)
        calls = service._swift_log_call_ranges(generated)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("serial" not in call.lower() for _, _, call in calls))
        self.assertTrue(all("details.serialHex" not in call and "certificate.serialNumber" not in call
                            for _, _, call in calls))

    def test_redaction_matches_serial_identifiers_case_insensitively(self):
        service = module("patch_v3_service")
        source = r'''
func example(installedApp: InstalledApp, info: CertificateInfo) {
    debugLog("installed serial: \(installedApp.certificateSerialNumber)")
    verboseLog("serial bytes: \(info.SerialHex)")
}
'''
        generated = service.headless_certificate_serial_log_redaction(source, "Fixture")
        self.assertEqual(service.headless_certificate_serial_log_redaction(generated, "Fixture"), generated)
        calls = [call for _, _, call in service._swift_log_call_ranges(generated)]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not service.contains_certificate_serial_log_identifier(call) for call in calls))
        self.assertNotIn("certificateSerialNumber", generated)
        self.assertNotIn("SerialHex", generated)

    def test_backend_certificate_files_remain_in_side_store_target_not_ui_exclusions(self):
        service = module("patch_v3_service")
        test_service = module("test_v3_service")
        fixture = test_service.ServicePatchTests("test_headless_operation_inventory")
        with tempfile.TemporaryDirectory() as name:
            roots = fixture.fixture(Path(name))
            side = roots[1]
            for relative, _ in CERTIFICATE_LOG_FILES:
                path = side / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(pinned_source(self, relative), encoding="utf-8")
            fixture.apply(roots)
            background = module("patch_background_automation")
            background.patch_background_operation(side)
            test_service.embedded_keychain.patch(side)
            project = (roots[1] / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
            group_start = project.index("A8EECF2A2F4B195000F2436D /* SideStore */")
            group_end = project.index("\n\t\t};", group_start) + len("\n\t\t};")
            self.assertIn("path = SideStore;", project[group_start:group_end])
            exception_start = project.index(
                "A8EECF492F4B195000F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {")
            exception_end = project.index("\n\t\t};", exception_start) + len("\n\t\t};")
            membership = project[exception_start:exception_end]
            for relative, _ in CERTIFICATE_LOG_FILES:
                side_relative = relative[len("SideStore/"):]
                self.assertNotIn(f'"{side_relative}"', membership)
                self.assertNotIn(relative, service.HEADLESS_SIDESTORE_VIEW_FILES)
                self.assertNotIn(relative, service.HEADLESS_SIDESTORE_AUX_UI_FILES)
            for relative, owner in CERTIFICATE_LOG_FILES:
                generated = (side / relative).read_text(encoding="utf-8")
                self.assertIn(service.V3_CERTIFICATE_SERIAL_LOG_MARKER, generated)
                self.assertEqual(
                    service.headless_certificate_serial_log_redaction(generated, owner), generated)

            pinned_sign_in = pinned_source(
                self, "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift")
            with mock.object(service.subprocess, "check_output", return_value=pinned_sign_in):
                # This is the exact verifier used by --verify-sign-in-operation.
                service.verify_sign_in_operation(side, PIN)


if __name__ == "__main__":
    unittest.main()
