"""Synthetic evidence tests. These are not iOS or cellular acceptance tests."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import verify_lifetime_receipts as verifier


def evidence_fixture():
    before = {
        "schema_version": 1,
        "captured_at_utc": "2026-10-01T00:00:00Z",
        "uptime_seconds": 86400,
        "boot_epoch_estimate_utc": "2026-09-30T00:00:00Z",
        "launch_id": "before",
        "bundle_id_sha256": "1" * 64,
        "version_sha256": "2" * 64,
        "executable_sha256": "3" * 64,
        "embedded_profile_sha256": "4" * 64,
        "canary_sha256": "5" * 64,
        "canary_was_created": True,
        "platform": {"system_version": "26.4"},
        "network_observation": {
            "observed_at_utc": "2026-10-01T00:00:00Z",
            "uses_interface_types": ["cellular"],
            "status": "satisfied",
        },
    }
    after = copy.deepcopy(before)
    after.update(captured_at_utc="2026-10-09T01:00:00Z", uptime_seconds=3600,
                 boot_epoch_estimate_utc="2026-10-09T00:00:00Z", launch_id="after",
                 canary_was_created=False)
    after["network_observation"]["observed_at_utc"] = after["captured_at_utc"]
    preflight = {
        "schema_version": 1,
        "old": {"cms_sha256": "4" * 64, "creation_utc": "2026-09-30T00:00:00Z",
                "expiration_utc": "2026-10-08T00:00:00Z"},
        "new": {"cms_sha256": "6" * 64, "creation_utc": "2026-10-06T00:00:00Z",
                "expiration_utc": "2026-10-12T00:00:00Z"},
        "comparison": {"status": "SUPPORTED_BY_SUPPLIED_METADATA"},
    }
    evidence = {
        "schema_version": 1, "operation": "profile_only", "target_kind": "probe",
        "baseline_authorization_method": "compatible_profile_inventory",
        "clock_margin_seconds": 300,
        "operator_controls": {name: True for name in verifier.CONTROLS},
        "environment": {"device_model": "synthetic", "ios_build": "synthetic",
                        "source_revision": "synthetic", "installation_backend": "synthetic"},
        "profile_store_readback": {"observed": True, "new_profile_cms_sha256": "6" * 64,
                                   "observed_at_utc": "2026-10-07T00:00:00Z"},
        "untreated_control_failed_after_expiry_and_reboot": True,
    }
    return before, after, preflight, evidence


class LifetimeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.before, self.after, self.preflight, self.evidence = evidence_fixture()

    def result(self):
        return verifier.evaluate(self.before, self.after, self.preflight, self.evidence)

    def test_supportive_observations_never_claim_attestation(self):
        report = self.result()
        self.assertEqual(report["verdict"], "RECORDED_OBSERVATIONS_SUPPORT_PROFILE_ONLY_LIFETIME_EXTENSION")
        self.assertIn("Inputs and operator controls are not cryptographically attested.", report["limitations"])

    def test_changed_executable_is_not_profile_only(self):
        self.after["executable_sha256"] = "7" * 64
        self.assertIn("changed_executable_sha256", self.result()["contradictions"])

    def test_data_canary_loss_is_detected(self):
        self.after["canary_sha256"] = "7" * 64
        self.assertIn("changed_canary_sha256", self.result()["contradictions"])

    def test_recreated_canary_is_contradictory_even_if_hashes_match(self):
        self.after["canary_was_created"] = True
        self.assertIn("after_receipt_reports_recreated_canary", self.result()["contradictions"])

    def test_changed_os_version_prevents_unqualified_support(self):
        self.after["platform"]["system_version"] = "27.0"
        self.assertIn("platform_version_changed_during_experiment", self.result()["incomplete"])

    def test_wifi_observation_conflicts_with_claimed_radio_controls(self):
        for receipt_name in ("before", "after"):
            with self.subTest(receipt=receipt_name):
                before, after, preflight, evidence = evidence_fixture()
                receipt = before if receipt_name == "before" else after
                receipt["network_observation"]["uses_interface_types"] = ["wifi", "other"]
                report = verifier.evaluate(before, after, preflight, evidence)
                self.assertIn(f"receipt_reports_wifi_path:{receipt_name}", report["contradictions"])

    def test_no_optional_network_observation_does_not_invent_a_path(self):
        self.before.pop("network_observation")
        self.after["network_observation"] = None
        self.assertEqual(self.result()["verdict"], "RECORDED_OBSERVATIONS_SUPPORT_PROFILE_ONLY_LIFETIME_EXTENSION")

    def test_changed_embedded_profile_is_not_unchanged_app(self):
        self.after["embedded_profile_sha256"] = "6" * 64
        self.assertIn("changed_embedded_profile_sha256", self.result()["contradictions"])

    def test_wrong_old_profile_rejected(self):
        self.preflight["old"]["cms_sha256"] = "7" * 64
        self.assertIn("baseline_does_not_match_old_cms", self.result()["contradictions"])

    def test_null_embedded_profile_is_readable_but_incomplete_for_profile_only(self):
        for receipt_name in ("before", "after"):
            with self.subTest(receipt=receipt_name):
                before, after, preflight, evidence = evidence_fixture()
                receipt = before if receipt_name == "before" else after
                receipt["embedded_profile_sha256"] = None
                report = verifier.evaluate(before, after, preflight, evidence)
                self.assertEqual(report["verdict"], "EVIDENCE_INCOMPLETE")
                self.assertIn(f"embedded_profile_not_recorded:{receipt_name}", report["incomplete"])

    def test_old_profile_cannot_postdate_baseline_receipt(self):
        self.preflight["old"]["creation_utc"] = "2026-10-02T00:00:00Z"
        self.assertIn("old_profile_created_after_baseline", self.result()["contradictions"])

    def test_new_profile_cannot_postdate_observed_launch(self):
        self.preflight["new"]["creation_utc"] = "2026-10-10T00:00:00Z"
        self.assertIn("new_profile_created_after_observed_launch", self.result()["contradictions"])

    def test_readback_cannot_precede_new_profile_creation(self):
        self.preflight["new"]["creation_utc"] = "2026-10-07T01:00:00Z"
        report = self.result()
        self.assertIn("profile_store_readback_precedes_new_profile_creation", report["contradictions"])
        self.assertFalse(report["profile_store_attribution_supported_by_supplied_report"])

    def test_missing_preflight_creation_time_is_rejected(self):
        self.preflight["new"].pop("creation_utc")
        with self.assertRaisesRegex(verifier.InvalidEvidence, "invalid_timestamp:new.creation_utc"):
            self.result()

    def test_same_expiry_does_not_prove_renewal(self):
        self.preflight["new"]["expiration_utc"] = self.preflight["old"]["expiration_utc"]
        self.assertIn("new_profile_does_not_advance_expiry", self.result()["contradictions"])

    def test_missing_controls_are_incomplete(self):
        self.evidence["operator_controls"]["wifi_stayed_off"] = None
        self.assertEqual(self.result()["verdict"], "EVIDENCE_INCOMPLETE")

    def test_embedded_expiry_without_effective_baseline_is_insufficient(self):
        self.evidence["operator_controls"].pop("baseline_effective_expiry_matches_old_profile")
        self.assertIn("control_not_recorded:baseline_effective_expiry_matches_old_profile", self.result()["incomplete"])

    def test_baseline_method_must_be_supported_and_explicit(self):
        for method in (None, "unique_app_id", "unknown"):
            with self.subTest(method=method):
                self.evidence["baseline_authorization_method"] = method
                self.assertIn("baseline_authorization_method_not_recorded", self.result()["incomplete"])

    def test_recorded_expiry_failure_is_an_alternative_baseline_method(self):
        self.evidence["baseline_authorization_method"] = "observed_expiry_failure"
        report = self.result()
        self.assertEqual(report["verdict"], "RECORDED_OBSERVATIONS_SUPPORT_PROFILE_ONLY_LIFETIME_EXTENSION")
        self.assertEqual(report["baseline_authorization_method"], "observed_expiry_failure")

    def test_surviving_untreated_control_leaves_causal_baseline_unresolved(self):
        self.evidence["untreated_control_failed_after_expiry_and_reboot"] = False
        report = self.result()
        self.assertEqual(report["verdict"], "EVIDENCE_INCOMPLETE")
        self.assertIn("untreated_control_survived_causal_baseline_unresolved", report["incomplete"])

    def test_untested_control_is_distinct_from_a_surviving_control(self):
        self.evidence["untreated_control_failed_after_expiry_and_reboot"] = None
        report = self.result()
        self.assertNotIn("untreated_control_survived_causal_baseline_unresolved", report["incomplete"])
        self.assertIn("no_untreated_control_causal_attribution_is_limited", report["observations"])

    def test_boolean_schema_version_is_not_integer_version_one(self):
        self.after["schema_version"] = True
        with self.assertRaisesRegex(verifier.InvalidEvidence, "invalid_schema:after"):
            self.result()

    def test_radio_toggle_is_a_failed_control(self):
        self.evidence["operator_controls"]["no_radio_toggles"] = False
        self.assertIn("control_failed:no_radio_toggles", self.result()["contradictions"])

    def test_string_true_is_not_an_attestation(self):
        self.evidence["operator_controls"]["free_personal_team"] = "true"
        self.assertIn("control_not_recorded:free_personal_team", self.result()["incomplete"])

    def test_fresh_process_without_reboot_is_insufficient(self):
        self.after["boot_epoch_estimate_utc"] = self.before["boot_epoch_estimate_utc"]
        self.after["uptime_seconds"] = 9 * 86400 + 3600
        report = self.result()
        self.assertIn("reboot_not_after_old_expiry_and_margin", report["incomplete"])
        self.assertIn("new_boot_epoch_not_established", report["incomplete"])

    def test_launch_before_old_expiry_is_incomplete(self):
        self.after.update(captured_at_utc="2026-10-07T01:00:00Z", uptime_seconds=3600,
                          boot_epoch_estimate_utc="2026-10-07T00:00:00Z")
        self.assertIn("launch_not_after_old_expiry_and_margin", self.result()["incomplete"])

    def test_reboot_before_old_expiry_does_not_flush_post_expiry_state(self):
        self.after["boot_epoch_estimate_utc"] = "2026-10-07T00:00:00Z"
        self.after["uptime_seconds"] = 2 * 86400 + 3600
        self.assertIn("reboot_not_after_old_expiry_and_margin", self.result()["incomplete"])

    def test_preflight_without_actual_signer_is_insufficient(self):
        self.preflight["comparison"]["status"] = "INDETERMINATE"
        self.assertIn("preflight_not_supportive", self.result()["incomplete"])

    def test_profile_store_hash_mismatch_is_detected(self):
        self.evidence["profile_store_readback"]["new_profile_cms_sha256"] = "7" * 64
        self.assertIn("profile_store_readback_hash_mismatch", self.result()["contradictions"])

    def test_no_store_readback_keeps_attribution_limitation(self):
        del self.evidence["profile_store_readback"]
        report = self.result()
        self.assertFalse(report["profile_store_attribution_supported_by_supplied_report"])
        self.assertIn("no_matching_profile_store_readback_specific_profile_attribution_is_limited", report["observations"])

    def test_inconsistent_boot_arithmetic_rejected(self):
        self.after["uptime_seconds"] = 9
        with self.assertRaisesRegex(verifier.InvalidEvidence, "inconsistent_boot_estimate"):
            self.result()

    def test_malformed_or_nonfinite_time_rejected(self):
        self.after["uptime_seconds"] = float("nan")
        with self.assertRaisesRegex(verifier.InvalidEvidence, "invalid_uptime"):
            self.result()

    def test_full_reinstall_requires_separate_test(self):
        self.evidence["operation"] = "full_reinstall"
        with self.assertRaisesRegex(verifier.InvalidEvidence, "unsupported_operation"):
            self.result()

    def test_cli_creates_redacted_file_with_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            arguments = []
            for name, data in zip(("before", "after", "preflight", "evidence"), evidence_fixture()):
                path = root / f"{name}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                arguments += [f"--{name}", str(path)]
            destination = root / "report.json"
            self.assertEqual(verifier.main(arguments + ["--output", str(destination)]), 0)
            report = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(set(report["input_file_sha256"]), {"before", "after", "preflight", "evidence"})
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_oversize_json_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "large.json"
            path.write_bytes(b" " * (verifier.MAX_INPUT_BYTES + 1))
            with self.assertRaisesRegex(verifier.InvalidEvidence, "input_size_limit"):
                verifier.read_json(path)


if __name__ == "__main__":
    unittest.main()
