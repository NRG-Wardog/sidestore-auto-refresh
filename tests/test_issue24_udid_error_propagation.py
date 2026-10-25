"""Unit and regression tests for Issue #24: CoreDevice and transport error propagation.

Verifies that transport, heartbeat, RSD, lockdownd, and device-query failures
are cleanly distinguished from invalid pairing file errors and never collapse
into '.lockdown UDID not found'.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    from test_combined_transport import SourceFixture, function, snapshot
except ImportError:
    from tests.test_combined_transport import SourceFixture, function, snapshot

from patch_combined_transport import patch
from patch_sidestore_integration import patch_gateway


class Issue24UdidErrorPropagationTests(SourceFixture):
    def setUp(self):
        super().setUp()
        patch(self.mux)
        self.gateway_text = self.gateway.read_text(encoding="utf-8")
        self.impl_text = self.read("Sources/MinimuxerImpl.swift")
        wrapper_path = self.side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift"
        self.wrapper_text = wrapper_path.read_text(encoding="utf-8") if wrapper_path.is_file() else ""

    def test_requirement1_real_pairing_parse_validation_error_remains_pairing_error(self):
        """1. Real pairing parse/validation errors remain pairing errors (.invalidPairing -> .invalidPairingFile)."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn("try verifyInitialized()", fetch)

        # Verify invalidPairingFile is preserved in runIdeviceCheckingVPN
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn("if err.code == .invalidPairingFile {", run_vpn)
        self.assertIn("throw MinimuxerError.invalidPairing(protocol: self.gateway.pairingFileType, reason: err.reason)", run_vpn)

        # Verify invalidPairing maps to invalidPairingFile in MinimuxerWrapper
        if self.wrapper_text:
            self.assertIn("case .invalidPairing(_, let reason):    return .invalidPairingFile(reason: reason)", self.wrapper_text)

    def test_requirement2_coredevice_connection_failure_remains_transport_error(self):
        """2. CoreDevice connection failures remain transport errors (.createCoreDevice / .noDevice)."""
        ensure_core = function(self.gateway_text, "ensureCoreDeviceConnection")
        self.assertIn("tunnel_create_usb(provider, &adapter, &handshake)", ensure_core)
        self.assertIn("selected_transport=FAILED_NO_VALID_TRANSPORT", ensure_core)

        # Verify runIdeviceCheckingVPN maps connectionFailed to createCoreDevice / noDevice
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn("if self.gateway.coreDeviceTransportEnabled || err.reason.contains(\"CoreDevice\") {", run_vpn)
        self.assertIn("throw MinimuxerError.createCoreDevice(err.reason)", run_vpn)
        self.assertIn("throw MinimuxerError.noDevice(err.reason)", run_vpn)

        # Verify MinimuxerWrapper maps createCoreDevice to .noDevice, not .invalidPairingFile
        if self.wrapper_text:
            self.assertIn("case .createCoreDevice(let reason)", self.wrapper_text)
            self.assertRegex(self.wrapper_text, r"case \.createCoreDevice\(let reason\)[^:]*:\s*return \.noDevice\(reason: reason\)")

    def test_requirement3_heartbeat_failure_remains_transport_heartbeat_error(self):
        """3. Heartbeat failures remain transport/heartbeat errors (.connect)."""
        ensure_core = function(self.gateway_text, "ensureCoreDeviceConnection")
        self.assertIn("guard tunnel_heartbeat_is_active() else {", ensure_core)
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] HEARTBEAT_FAIL")', ensure_core)
        self.assertIn('throw IdeviceGatewayError(.connectionFailed, reason: "CoreDevice heartbeat is inactive")', ensure_core)

        # Verify runIdeviceCheckingVPN maps heartbeat failure to MinimuxerError.connect
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn('if err.reason.lowercased().contains("heartbeat") {', run_vpn)
        self.assertIn("throw MinimuxerError.connect(err.reason)", run_vpn)

        # Verify MinimuxerWrapper maps .connect to .noDevice, never .invalidPairingFile
        if self.wrapper_text:
            self.assertIn(".connect(let reason)", self.wrapper_text)

    def test_requirement4_rsd_service_failure_remains_rsd_service_error(self):
        """4. RSD service failures remain RSD/service errors (.noService / .createLockdown)."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] FETCH_UDID_FAIL stage=rsd_service', fetch)
        self.assertIn('throw IdeviceGatewayError(.serviceError, reason: "Lockdownd RSD connection failed', fetch)

        # Verify runIdeviceCheckingVPN maps rsd_service failures
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn('if err.reason.contains("stage=rsd_service") || (err.reason.contains("RSD") && err.reason.contains("service")) {', run_vpn)
        self.assertIn("throw MinimuxerError.noService(err.reason)", run_vpn)

    def test_requirement5_lockdownd_connection_failure_remains_lockdownd_error(self):
        """5. Lockdownd connection failures remain lockdownd/service errors (.createLockdown)."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] LOCKDOWN_CONNECT_FAIL', fetch)
        self.assertIn("lockdownd_connect_rsd(", fetch)

        # Verify runIdeviceCheckingVPN maps Lockdown connection errors
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn('if err.reason.contains("Lockdown") || err.reason.contains("lockdown") {', run_vpn)
        self.assertIn("throw MinimuxerError.createLockdown(err.reason)", run_vpn)

        if self.wrapper_text:
            self.assertIn(".createLockdown(let reason)", self.wrapper_text)

    def test_requirement6_unique_device_id_query_failure_preserves_underlying_cause(self):
        """6. UniqueDeviceID query failures preserve the underlying cause (.getLockdownValue)."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn('lockdownd_get_value(client, "UniqueDeviceID", nil, &plistVal)', fetch)
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] UNIQUE_DEVICE_ID_QUERY_FAIL', fetch)
        self.assertIn('throw IdeviceGatewayError(.serviceError, reason: "Querying UniqueDeviceID failed', fetch)

        # Verify runIdeviceCheckingVPN maps UniqueDeviceID query errors
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn('if err.reason.contains("UniqueDeviceID") {', run_vpn)
        self.assertIn("throw MinimuxerError.getLockdownValue(err.reason)", run_vpn)

        if self.wrapper_text:
            self.assertIn(".getLockdownValue(let reason)", self.wrapper_text)

    def test_requirement7_transport_and_service_failures_never_become_invalid_pairing(self):
        """7. None of cases 2-6 become .invalidPairing or .invalidPairingFile."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        # In syncFetchUDID, ensureRPConnection errors and lockdownd errors must be rethrown, NOT swallowed to return nil
        self.assertNotIn("catch {\n                return nil\n            }", fetch)
        self.assertNotIn("catch {\n                    return nil\n                }", fetch)

        # In MinimuxerImpl runIdeviceCheckingVPN, fetching device UDID must rethrow typed errors
        run_vpn = function(self.impl_text, "runIdeviceCheckingVPN")
        self.assertIn('if context.contains("fetching device UDID") {', run_vpn)
        self.assertNotIn('if context.contains("fetching device UDID") {\n            return fallback\n        }', run_vpn)

        # In MinimuxerWrapper, verify that .createCoreDevice, .createLockdown, .getLockdownValue, .connect, .noService do NOT map to .invalidPairingFile
        if self.wrapper_text:
            self.assertNotRegex(self.wrapper_text, r"case \.(?:createCoreDevice|createLockdown|getLockdownValue|connect|noService)[^:]*:\s*return \.invalidPairingFile")

    def test_requirement8_valid_lockdown_pairing_path_continues_to_work(self):
        """8. Existing valid Lockdown pairing path continues to work."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] FETCH_UDID_START mode=\\(pairingFileType)")', fetch)
        self.assertIn('debugLog("[SIDESTORE_COREDEVICE] FETCH_UDID_PASS")', fetch)
        self.assertIn("return udid", fetch)

        readiness = function(self.impl_text, "isReady")
        self.assertIn("try await fetchUDID()", readiness)
        self.assertIn("guard deviceUDID != nil else {", readiness)
        self.assertIn("return .success(true)", readiness)

    def test_requirement9_remote_pairing_behavior_not_regressed(self):
        """9. RemotePairing behavior is not regressed."""
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertIn("if pairingFileType == .rppairing || usesCoreDevice {", fetch)
        pairing_file = self.read("Common/PairingFile.swift")
        self.assertIn("RPPairingFile.missingKeys(in: plist)", pairing_file)
        self.assertIn("return .rppairing", pairing_file)

    def test_requirement10_strict_privacy_no_udid_or_credentials_in_logs(self):
        """10. Strict privacy: NEVER leak UDID, certs, keys, or credentials into logs."""
        # Check MinimuxerImpl.swift
        self.assertNotIn('deviceUDID=\\(deviceUDID ?? "nil")', self.impl_text)
        self.assertIn("deviceUDID_present=\\(deviceUDID != nil)", self.impl_text)

        # Check IdeviceGateway.swift
        fetch = function(self.gateway_text, "syncFetchUDID")
        self.assertNotIn("udid=\\(udid)", fetch)
        self.assertNotIn("deviceUDID=\\(", fetch)
        self.assertNotIn("identity=\\(", fetch)
        self.assertNotIn("key=\\(", fetch)

    def test_requirement11_patches_remain_idempotent(self):
        """11. Patches remain idempotent and pass all repository & combined transport tests."""
        first_snap = snapshot(self.side)
        patch(self.mux)
        second_snap = snapshot(self.side)
        self.assertEqual(first_snap, second_snap, "Second application of patch() must be a no-op")

    def test_requirement12_verification_markers_complete(self):
        """12. Gateway and combined verification checks include all required markers."""
        from patch_sidestore_integration import verify_gateway
        # Should not raise SystemExit
        verify_gateway(self.gateway_text)


if __name__ == "__main__":
    unittest.main()
