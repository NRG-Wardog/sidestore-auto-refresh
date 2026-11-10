"""Maintained-owner privacy guards for password-equivalent certificate serials."""
from tests.owner_source import owner_source
from tests.support.certificate_log_privacy import (swift_log_call_ranges, contains_certificate_serial_log_identifier)
import unittest

CERTIFICATE_LOG_FILES = (
    ("SideStore/Core/Certificates/CertificateManager.swift", "CertificateManager"),
    ("SideStore/Core/Certificates/OCSPValidator.swift", "OCSPValidator"),
    ("SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift", "SignInOperation"),
    ("SideStore/Core/Operations/PipelineOperations/VerifyCertificateOperation.swift", "VerifyCertificateOperation"),
    ("SideStore/Core/Operations/PipelineOperations/UpdateAppCertificateOperation.swift", "UpdateAppCertificateOperation"),
)


class CertificateSerialLogPrivacyTests(unittest.TestCase):
    def test_owner_backend_log_calls_omit_serials(self):
        scanned = 0
        for relative, _ in CERTIFICATE_LOG_FILES:
            source = owner_source(relative, "SideStore")
            calls = swift_log_call_ranges(source)
            self.assertTrue(calls, f"expected backend diagnostics in {relative}")
            self.assertEqual([call for _, _, call in calls
                              if contains_certificate_serial_log_identifier(call)], [],
                             f"owner logs expose certificate serial/password values in {relative}")
            scanned += len(calls)
        self.assertGreater(scanned, 20)

    def test_scanner_rejects_serial_hex_multiline_logs_and_interpolation(self):
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
        calls = swift_log_call_ranges(source)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(contains_certificate_serial_log_identifier(call) for _, _, call in calls))
        self.assertIn('details.serialHex', calls[0][2])
        self.assertIn('certificate.serialNumber', calls[1][2])
        # Safe finite summaries remain permitted; the guard is not a ban on logs.
        self.assertFalse(contains_certificate_serial_log_identifier(
            'debugLog("[Fixture] Certificate identity details omitted.")'))

    def test_scanner_matches_serial_identifiers_case_insensitively(self):
        source = r'''
func example(installedApp: InstalledApp, info: CertificateInfo) {
    debugLog("installed serial: \(installedApp.certificateSerialNumber)")
    verboseLog("serial bytes: \(info.SerialHex)")
}
'''
        calls = swift_log_call_ranges(source)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(contains_certificate_serial_log_identifier(call) for _, _, call in calls))

    def test_backend_certificate_files_remain_in_side_store_target_not_ui_exclusions(self):
        from tests.owner_source import owner_source
        from scripts import headless_artifact_contract as contract
        project = owner_source("AltStore.xcodeproj/project.pbxproj", "SideStore")
        group_start = project.index("A8EECF2A2F4B195000F2436D /* SideStore */")
        group_end = project.index("\n\t\t};", group_start) + len("\n\t\t};")
        self.assertIn("path = SideStore;", project[group_start:group_end])
        exception_start = project.index("A8EECF492F4B195000F2436D /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {")
        exception_end = project.index("\n\t\t};", exception_start) + len("\n\t\t};")
        membership = project[exception_start:exception_end]
        for relative, _ in CERTIFICATE_LOG_FILES:
            side_relative = relative[len("SideStore/"):]
            self.assertNotIn(f'"{side_relative}"', membership)
            self.assertNotIn(side_relative, contract.HEADLESS_SIDESTORE_VIEW_FILES)
            self.assertNotIn(side_relative, contract.HEADLESS_SIDESTORE_AUX_UI_FILES)
            self.assertTrue(owner_source(relative, "SideStore"))


if __name__ == "__main__":
    unittest.main()
