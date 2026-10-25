#!/usr/bin/env python3
"""Compare operator-supplied evidence for a profile-only lifetime experiment.

This does not attest a phone, verify an Apple trust chain, read the profile store,
or declare production acceptance. Receipts and operator controls are untrusted
observations. Full IPA installation and host replacement are separate tests.

The old embedded profile's expiry is not necessarily the installed app's
effective deadline: another compatible profile may already authorize it. The
operator must establish the baseline with either a complete inventory of
compatible installed profiles or an actual post-expiry, post-reboot launch
failure observed before intervention. A unique app ID alone does not exclude
wildcard profiles. This tool records that supplied method; it does not attest it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

MAX_INPUT_BYTES = 1024 * 1024
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
UTC = dt.timezone.utc
CONTROLS = (
    "wifi_off_in_settings_before_start",
    "wifi_stayed_off",
    "cellular_data_in_use",
    "no_radio_toggles",
    "no_pc_usb_during_run",
    "no_install_transport_relay",
    "no_jailbreak",
    "free_personal_team",
    "automatic_clock_unchanged",
    "reboot_after_old_expiry",
    "first_unlock_then_fresh_launch",
    "no_other_signing_changes",
    "same_signed_bundle_as_preflight",
    "baseline_effective_expiry_matches_old_profile",
)
BASELINE_AUTHORIZATION_METHODS = (
    "compatible_profile_inventory",
    "observed_expiry_failure",
)
HASH_FIELDS = (
    "bundle_id_sha256", "version_sha256", "executable_sha256",
    "embedded_profile_sha256", "canary_sha256",
)


class InvalidEvidence(ValueError):
    """Malformed input. Messages contain field names, never input contents."""


def timestamp(value, field):
    if not isinstance(value, str):
        raise InvalidEvidence(f"invalid_timestamp:{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEvidence(f"invalid_timestamp:{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise InvalidEvidence(f"timestamp_requires_utc:{field}")
    return parsed


def digest(value, field):
    if not isinstance(value, str) or not HEX256.fullmatch(value):
        raise InvalidEvidence(f"invalid_sha256:{field}")
    return value


def validate_receipt(receipt, name):
    if not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1:
        raise InvalidEvidence(f"invalid_schema:{name}")
    for field in HASH_FIELDS:
        # The generic iOS receipt records null if its bundle has no embedded
        # profile. That receipt is readable, but cannot support this experiment.
        if field == "embedded_profile_sha256" and receipt.get(field) is None:
            continue
        digest(receipt.get(field), f"{name}.{field}")
    captured = timestamp(receipt.get("captured_at_utc"), f"{name}.captured_at_utc")
    boot = timestamp(receipt.get("boot_epoch_estimate_utc"), f"{name}.boot_epoch_estimate_utc")
    uptime = receipt.get("uptime_seconds")
    if isinstance(uptime, bool) or not isinstance(uptime, (float, int)) or not math.isfinite(uptime) or uptime < 0:
        raise InvalidEvidence(f"invalid_uptime:{name}")
    if abs((captured - boot).total_seconds() - uptime) > 5:
        raise InvalidEvidence(f"inconsistent_boot_estimate:{name}")
    launch = receipt.get("launch_id")
    if not isinstance(launch, str) or not 1 <= len(launch) <= 128:
        raise InvalidEvidence(f"invalid_launch_id:{name}")
    platform = receipt.get("platform")
    if platform is not None and (
        not isinstance(platform, dict)
        or not isinstance(platform.get("system_version"), str)
        or not platform["system_version"].strip()
    ):
        raise InvalidEvidence(f"invalid_platform_observation:{name}")
    if "canary_was_created" in receipt and type(receipt["canary_was_created"]) is not bool:
        raise InvalidEvidence(f"invalid_canary_creation_observation:{name}")
    network = receipt.get("network_observation")
    if network is not None:
        if (
            not isinstance(network, dict)
            or not isinstance(network.get("uses_interface_types"), list)
            or any(not isinstance(value, str) for value in network["uses_interface_types"])
        ):
            raise InvalidEvidence(f"invalid_network_observation:{name}")
        timestamp(network.get("observed_at_utc"), f"{name}.network_observation.observed_at_utc")
    return captured, boot


def evaluate(before, after, preflight, evidence):
    before_at, before_boot = validate_receipt(before, "before")
    after_at, after_boot = validate_receipt(after, "after")
    if not isinstance(preflight, dict) or type(preflight.get("schema_version")) is not int or preflight["schema_version"] != 1:
        raise InvalidEvidence("invalid_schema:preflight")
    if not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1:
        raise InvalidEvidence("invalid_schema:evidence")
    if evidence.get("operation") != "profile_only":
        raise InvalidEvidence("unsupported_operation:separate_full_install_test_required")
    old = preflight.get("old")
    new = preflight.get("new")
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise InvalidEvidence("missing_profile_metadata")
    old_hash = digest(old.get("cms_sha256"), "old.cms_sha256")
    new_hash = digest(new.get("cms_sha256"), "new.cms_sha256")
    old_expiry = timestamp(old.get("expiration_utc"), "old.expiration_utc")
    new_expiry = timestamp(new.get("expiration_utc"), "new.expiration_utc")
    old_created = timestamp(old.get("creation_utc"), "old.creation_utc")
    new_created = timestamp(new.get("creation_utc"), "new.creation_utc")
    comparison = preflight.get("comparison")
    if not isinstance(comparison, dict):
        raise InvalidEvidence("missing_preflight_comparison")
    controls = evidence.get("operator_controls")
    if not isinstance(controls, dict):
        raise InvalidEvidence("missing_operator_controls")
    margin = evidence.get("clock_margin_seconds", 300)
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(margin) or not 60 <= margin <= 86400:
        raise InvalidEvidence("invalid_clock_margin")
    margin_delta = dt.timedelta(seconds=margin)

    contradictions = []
    incomplete = []
    observations = []
    for field in HASH_FIELDS:
        if field == "embedded_profile_sha256" and (before.get(field) is None or after.get(field) is None):
            for name, receipt in (("before", before), ("after", after)):
                if receipt.get(field) is None:
                    incomplete.append(f"embedded_profile_not_recorded:{name}")
            continue
        if before[field] != after[field]:
            contradictions.append(f"changed_{field}")
    if before.get("embedded_profile_sha256") is not None and before["embedded_profile_sha256"] != old_hash:
        contradictions.append("baseline_does_not_match_old_cms")
    if old_created > before_at:
        contradictions.append("old_profile_created_after_baseline")
    if new_created > after_at:
        contradictions.append("new_profile_created_after_observed_launch")
    if old_created >= old_expiry or new_created >= new_expiry:
        contradictions.append("profile_creation_expiration_order")
    if after_at <= before_at:
        contradictions.append("receipt_time_order")
    if not before_at < old_expiry:
        incomplete.append("baseline_not_recorded_before_old_expiry")
    if not new_expiry > old_expiry:
        contradictions.append("new_profile_does_not_advance_expiry")
    if old_hash == new_hash:
        contradictions.append("profiles_are_identical")
    if not after_at > old_expiry + margin_delta:
        incomplete.append("launch_not_after_old_expiry_and_margin")
    if not after_at < new_expiry - margin_delta:
        incomplete.append("launch_not_safely_inside_new_profile_window")
    if not after_boot > old_expiry + margin_delta:
        incomplete.append("reboot_not_after_old_expiry_and_margin")
    if not after_boot > before_boot + margin_delta:
        incomplete.append("new_boot_epoch_not_established")
    if before["launch_id"] == after["launch_id"]:
        incomplete.append("fresh_process_launch_not_recorded")
    if after.get("canary_was_created") is True:
        contradictions.append("after_receipt_reports_recreated_canary")
    before_platform, after_platform = before.get("platform"), after.get("platform")
    if before_platform is not None and after_platform is not None:
        if before_platform["system_version"] != after_platform["system_version"]:
            incomplete.append("platform_version_changed_during_experiment")
    elif before_platform is not None or after_platform is not None:
        incomplete.append("platform_observation_missing_for_one_receipt")
    for name, receipt in (("before", before), ("after", after)):
        network = receipt.get("network_observation")
        if network is not None and "wifi" in network["uses_interface_types"]:
            contradictions.append(f"receipt_reports_wifi_path:{name}")
    if comparison.get("status") == "INCOMPATIBLE":
        contradictions.append("preflight_incompatible")
    elif comparison.get("status") != "SUPPORTED_BY_SUPPLIED_METADATA":
        incomplete.append("preflight_not_supportive")
    for control in CONTROLS:
        if controls.get(control) is False:
            contradictions.append(f"control_failed:{control}")
        elif controls.get(control) is not True:
            incomplete.append(f"control_not_recorded:{control}")
    baseline_method = evidence.get("baseline_authorization_method")
    if baseline_method not in BASELINE_AUTHORIZATION_METHODS:
        incomplete.append("baseline_authorization_method_not_recorded")
    target = evidence.get("target_kind")
    if target not in ("probe", "ordinary_app", "standalone_host", "combined_outer_host"):
        incomplete.append("target_kind_not_recorded")
    environment = evidence.get("environment")
    if not isinstance(environment, dict) or any(
        not isinstance(environment.get(key), str) or not environment[key].strip()
        for key in ("device_model", "ios_build", "source_revision", "installation_backend")
    ):
        incomplete.append("environment_provenance_incomplete")

    readback = evidence.get("profile_store_readback")
    matched_readback = False
    if isinstance(readback, dict) and readback.get("observed") is True:
        readback_hash = digest(readback.get("new_profile_cms_sha256"), "readback.cms_sha256")
        readback_at = timestamp(readback.get("observed_at_utc"), "readback.observed_at_utc")
        if readback_hash != new_hash:
            contradictions.append("profile_store_readback_hash_mismatch")
        elif not before_at <= readback_at <= after_at:
            contradictions.append("profile_store_readback_time_outside_experiment")
        elif new_created > readback_at:
            contradictions.append("profile_store_readback_precedes_new_profile_creation")
        else:
            matched_readback = True
            observations.append("operator_reports_matching_profile_store_readback")
    if not matched_readback:
        observations.append("no_matching_profile_store_readback_specific_profile_attribution_is_limited")
    if evidence.get("untreated_control_failed_after_expiry_and_reboot") is True:
        observations.append("operator_reports_untreated_control_failure")
    elif evidence.get("untreated_control_failed_after_expiry_and_reboot") is False:
        observations.append("operator_reports_untreated_control_survived_after_expiry_and_reboot")
        incomplete.append("untreated_control_survived_causal_baseline_unresolved")
    else:
        observations.append("no_untreated_control_causal_attribution_is_limited")

    if contradictions:
        verdict = "RECORDED_OBSERVATIONS_CONTRADICT_REQUESTED_EXPERIMENT"
    elif incomplete:
        verdict = "EVIDENCE_INCOMPLETE"
    else:
        verdict = "RECORDED_OBSERVATIONS_SUPPORT_PROFILE_ONLY_LIFETIME_EXTENSION"
    return {
        "schema_version": 1,
        "verdict": verdict,
        "operation": "profile_only",
        "target_kind": target,
        "contradictions": contradictions,
        "incomplete": incomplete,
        "observations": observations,
        "profile_store_attribution_supported_by_supplied_report": matched_readback,
        "baseline_authorization_method": baseline_method if baseline_method in BASELINE_AUTHORIZATION_METHODS else None,
        "limitations": [
            "Inputs and operator controls are not cryptographically attested.",
            "Hashes are identifiers, not anonymization or proof of Apple trust.",
            "Uptime plus wall clock is a consistency check, not proof of reboot or clock integrity.",
            "No network state, Apple revocation status, profile store or phone was inspected by this tool.",
            "The baseline authorization method is operator supplied, not independently verified.",
            "Inventory must cover every compatible installed profile; a unique bundle ID does not exclude wildcard profiles.",
            "Observed expiry failure must precede intervention, follow expiry and reboot, and concern this same installed target.",
            "One launch does not establish the full new expiration window or background reliability.",
            "Main app results do not validate every extension or another host variant.",
            "Full IPA installation and full host replacement require separate acceptance tests.",
        ],
    }


def read_json(path):
    with Path(path).open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise InvalidEvidence("input_size_limit")
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise InvalidEvidence("invalid_json") from exc
    return parsed, hashlib.sha256(data).hexdigest()


def write_report(report, destination):
    payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if destination is None:
        sys.stdout.write(payload)
        return
    path = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-report-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "preflight", "evidence"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = {name: read_json(getattr(args, name)) for name in ("before", "after", "preflight", "evidence")}
        report = evaluate(*(inputs[name][0] for name in ("before", "after", "preflight", "evidence")))
        report["input_file_sha256"] = {name: value[1] for name, value in inputs.items()}
        write_report(report, args.output)
    except InvalidEvidence as exc:
        sys.stderr.write(f"Invalid evidence: {exc}\n")
        return 2
    except OSError:
        sys.stderr.write("Input/output failure; check local file access.\n")
        return 2
    return 0 if report["verdict"].startswith("RECORDED_OBSERVATIONS_SUPPORT_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
