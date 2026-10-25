#!/usr/bin/env python3
"""Synthetic CMS tests; fixtures and private keys exist only in temporary dirs.

These tests exercise integrity, conservative metadata decisions and redaction.
They do NOT create Apple-issued profiles or validate any iOS installation path.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name("profile_preflight.py")
SPEC = importlib.util.spec_from_file_location("profile_preflight", MODULE)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for synthetic CMS integrity tests")
class ProfilePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="test-profile-preflight-")
        cls.root = Path(cls.workspace.name)
        cls.certificates = []
        cls.private_keys = []
        for index in range(2):
            cert = cls.root / f"fixture-{index}.pem"
            key = cls.root / f"fixture-{index}.key"
            cls.command("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                        "-days", "365", "-subj", f"/CN=SyntheticFixture{index}",
                        "-keyout", str(key), "-out", str(cert))
            cls.certificates.append(cert)
            cls.private_keys.append(key)
        cls.der = [cls.command("x509", "-in", str(cert), "-outform", "DER") for cert in cls.certificates]
        cls.now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        cls.udid = "00008110-001234567890001E"
        cls.entitlements = {
            "application-identifier": "FIXTURE123.com.example.probe",
            "com.apple.developer.team-identifier": "FIXTURE123",
            "get-task-allow": True,
            "keychain-access-groups": ["FIXTURE123.com.example.probe"],
            "com.apple.security.application-groups": ["group.example.probe"],
        }

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    @staticmethod
    def command(*arguments):
        result = subprocess.run([shutil.which("openssl"), *arguments], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError("Synthetic OpenSSL fixture generation failed")
        return result.stdout

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix="case-", dir=self.root)
        self.directory = Path(self.case.name)
        self.addCleanup(self.case.cleanup)
        self.old = {
            "UUID": "SYNTHETIC-OLD-PROFILE",
            "Name": "Private fixture name not for reports",
            "CreationDate": (self.now - dt.timedelta(hours=1)).replace(tzinfo=None),
            "ExpirationDate": (self.now + dt.timedelta(days=1)).replace(tzinfo=None),
            "TeamIdentifier": ["FIXTURE123"],
            "ProvisionedDevices": [self.udid],
            "DeveloperCertificates": [self.der[0]],
            "Entitlements": copy.deepcopy(self.entitlements),
            # Deliberately synthetic: presence is recorded, DER is NOT evaluated.
            "DER-Encoded-Profile": b"\x30\x00",
        }
        self.new = copy.deepcopy(self.old)
        self.new["UUID"] = "SYNTHETIC-NEW-PROFILE"
        self.new["ExpirationDate"] = (self.now + dt.timedelta(days=6)).replace(tzinfo=None)

    def encode(self, profile, label):
        content = self.directory / f"{label}.plist"
        envelope = self.directory / f"{label}.mobileprovision"
        content.write_bytes(plistlib.dumps(profile))
        self.command("cms", "-sign", "-binary", "-nodetach", "-nosmimecap", "-md", "sha256",
                     "-in", str(content), "-signer", str(self.certificates[0]),
                     "-inkey", str(self.private_keys[0]), "-outform", "DER", "-out", str(envelope))
        return envelope

    def report(self, *, with_signer=True, with_entitlements=True, device=True):
        old_path, new_path = self.encode(self.old, "old"), self.encode(self.new, "new")
        old, old_meta = preflight.decode_profile(old_path, "old")
        new, new_meta = preflight.decode_profile(new_path, "new")
        return preflight.evaluate(
            old, new, old_meta, new_meta,
            signer=preflight.read_signer_certificate(self.certificates[0]) if with_signer else None,
            signed_entitlements=copy.deepcopy(self.entitlements) if with_entitlements else None,
            device_udid=self.udid if device else None, at=self.now,
        )

    def test_matching_metadata_does_not_claim_apple_trust_or_device_validation(self):
        report = self.report()
        self.assertEqual(report["comparison"]["status"], "SUPPORTED_BY_SUPPLIED_METADATA")
        self.assertEqual(report["apple_trust"], "UNVERIFIED")
        self.assertEqual(report["device_validation"], "NOT_DEVICE_VALIDATED")
        self.assertEqual(report["new"]["der_profile"], "PRESENT_NOT_EVALUATED")
        raw = json.dumps(report)
        for private_value in (self.udid, "FIXTURE123", "com.example.probe", "group.example.probe", self.old["Name"], self.old["UUID"]):
            self.assertNotIn(private_value, raw)
        self.assertEqual(report["old"]["cms_sha256"], preflight.sha256((self.directory / "old.mobileprovision").read_bytes()))

    def test_tampering_content_fails_integrity_before_metadata_comparison(self):
        path = self.encode(self.old, "tampered")
        raw = path.read_bytes()
        self.assertIn(b"com.example.probe", raw)
        path.write_bytes(raw.replace(b"com.example.probe", b"org.example.probe", 1))
        with self.assertRaisesRegex(preflight.PreflightError, "old_cms_signature_integrity_failed"):
            preflight.decode_profile(path, "old")

    def test_changed_app_identity_is_incompatible(self):
        self.new["Entitlements"]["application-identifier"] = "FIXTURE123.com.example.different"
        report = self.report()
        self.assertEqual(report["comparison"]["status"], "INCOMPATIBLE")
        self.assertIn("signed_entitlement_values_not_authorized_by_new_profile_metadata", report["comparison"]["failures"])

    def test_changed_team_is_incompatible(self):
        self.new["TeamIdentifier"] = ["OTHERTEAM1"]
        self.assertIn("profile_team_changed", self.report()["comparison"]["failures"])

    def test_certificate_overlap_does_not_prove_actual_signer_coverage(self):
        self.old["DeveloperCertificates"] = self.der
        self.new["DeveloperCertificates"] = [self.der[1]]
        report = self.report()
        self.assertTrue(report["comparison"]["allowed_certificate_sets_overlap"])
        self.assertEqual(report["comparison"]["status"], "INCOMPATIBLE")
        self.assertIn("actual_signer_not_authorized_by_new_profile_metadata", report["comparison"]["failures"])

    def test_missing_actual_signer_or_entitlements_stays_indeterminate(self):
        for arguments in ({"with_signer": False}, {"with_entitlements": False}, {"device": False}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.report(**arguments)["comparison"]["status"], "INDETERMINATE")

    def test_no_expiry_advance_is_not_refresh(self):
        self.new["ExpirationDate"] = self.old["ExpirationDate"]
        self.assertIn("profile_expiration_does_not_advance", self.report()["comparison"]["failures"])

    def test_expired_new_profile_is_incompatible_at_explicit_evaluation_time(self):
        self.old["CreationDate"] = (self.now - dt.timedelta(days=8)).replace(tzinfo=None)
        self.old["ExpirationDate"] = (self.now - dt.timedelta(days=2)).replace(tzinfo=None)
        self.new["CreationDate"] = self.old["CreationDate"]
        self.new["ExpirationDate"] = (self.now - dt.timedelta(days=1)).replace(tzinfo=None)
        self.assertIn("new_profile_expired_at_evaluation", self.report()["comparison"]["failures"])

    def test_same_entitlement_keys_with_different_group_values_is_incompatible(self):
        self.new["Entitlements"]["com.apple.security.application-groups"] = ["group.example.other"]
        self.assertEqual(self.report()["comparison"]["status"], "INCOMPATIBLE")

    def test_known_trailing_wildcards_cover_exact_signed_identifiers(self):
        self.new["Entitlements"]["application-identifier"] = "FIXTURE123.*"
        self.new["Entitlements"]["keychain-access-groups"] = ["FIXTURE123.*"]
        self.assertEqual(self.report()["comparison"]["status"], "SUPPORTED_BY_SUPPLIED_METADATA")

    def test_unknown_wildcard_semantics_do_not_guess_compatibility(self):
        self.new["Entitlements"]["com.apple.security.application-groups"] = ["group.example.*"]
        self.assertEqual(self.report()["comparison"]["status"], "INDETERMINATE")

    def test_claimed_wildcards_and_nested_boolean_integer_coercion_are_not_matches(self):
        self.assertEqual(preflight.entitlement_match(["FIXTURE123.*"], ["FIXTURE123.*"], "keychain-access-groups"), "UNKNOWN")
        self.assertEqual(preflight.entitlement_match([True], [1], "example"), "UNKNOWN")
        self.assertEqual(preflight.entitlement_match({"flag": True}, {"flag": 1}, "example"), "UNKNOWN")

    def test_malformed_xml_returns_sanitized_error(self):
        with self.assertRaisesRegex(preflight.PreflightError, "input_plist_invalid"):
            preflight.load_plist(b'<?xml version="1.0"?><plist><dict><key>private_value', "input")

    def test_device_mismatch_is_incompatible(self):
        self.new["ProvisionedDevices"] = ["00008110-0000000000000000"]
        self.assertIn("device_not_authorized_by_new_profile_metadata", self.report()["comparison"]["failures"])

    def test_certificate_expiration_is_policy_unknown_not_universal_runtime_bound(self):
        old, old_meta = preflight.decode_profile(self.encode(self.old, "old"), "old")
        new, new_meta = preflight.decode_profile(self.encode(self.new, "new"), "new")
        # Directly provide dates to isolate conservative expiry-policy handling.
        report = preflight.evaluate(old, new, old_meta, new_meta,
            signer=(self.der[0], self.now - dt.timedelta(days=365), self.now - dt.timedelta(seconds=1)),
            signed_entitlements=self.entitlements, device_udid=self.udid, at=self.now)
        self.assertEqual(report["comparison"]["status"], "INDETERMINATE")
        self.assertEqual(report["comparison"]["failures"], [])

    def test_cli_private_output_and_schema_contract(self):
        old, new = self.encode(self.old, "old"), self.encode(self.new, "new")
        entitlements = self.directory / "signed.plist"
        device = self.directory / "device.txt"
        output = self.directory / "report.json"
        entitlements.write_bytes(plistlib.dumps(self.entitlements))
        device.write_text(self.udid + "\n", encoding="ascii")
        run = subprocess.run([sys.executable, str(MODULE), "--old", str(old), "--new", str(new),
            "--signer-cert", str(self.certificates[0]), "--signed-entitlements", str(entitlements),
            "--device-udid-file", str(device), "--at", preflight.utc_text(self.now), "--output", str(output)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, b"")
        self.assertEqual(run.stderr, b"")
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        report = json.loads(output.read_text())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["old"]["cms_sha256"], preflight.sha256(old.read_bytes()))
        self.assertEqual(report["new"]["expiration_utc"], preflight.utc_text(self.now + dt.timedelta(days=6)))


if __name__ == "__main__":
    unittest.main()
