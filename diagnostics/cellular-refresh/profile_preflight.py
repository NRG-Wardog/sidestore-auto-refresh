#!/usr/bin/env python3
"""Read-only provisioning-profile metadata preflight (Python stdlib + OpenSSL).

This deliberately is NOT an iOS code-signing verifier or an installer. OpenSSL
``cms -verify -noverify`` checks the CMS signature/content integrity but does NOT
authenticate the signer's chain as Apple, check revocation, or apply iOS policy.
The plist is diagnostic metadata: modern iOS uses DER-Encoded-Profile, which this
tool does not interpret. Supplied executable signer/entitlements must come from
the actual signed artifact; this tool does not extract or attest them.

Conservative entitlement comparison supports exact scalar/container values,
array subsets, and a single trailing '*' for application-identifier and
keychain-access-groups. Other wildcard/range/Boolean-relaxation semantics are
INDETERMINATE rather than guessed. This is sufficient for an unchanged small
probe; it is not a replacement for the platform's entitlement evaluator.

Exit codes: 0 = SUPPORTED_BY_SUPPLIED_METADATA; 1 = INCOMPATIBLE supplied metadata;
2 = INDETERMINATE or input/tool failure. Every result is NOT_DEVICE_VALIDATED.
Reports omit raw identifiers/entitlement values. Digests remain linkable and
are not anonymization. --output writes atomically with mode 0600.

References:
https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles
https://docs.openssl.org/3.0/man1/openssl-cms/
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from xml.parsers.expat import ExpatError

UTC = dt.timezone.utc
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_CERT_BYTES = 128 * 1024
MAX_NODES = 20000
MAX_COLLECTION = 512
MAX_DEPTH = 16
TOOL_TIMEOUT_SECONDS = 15
WILDCARD_KEYS = {"application-identifier", "keychain-access-groups"}
STATUSES = {"SUPPORTED_BY_SUPPLIED_METADATA": 0, "INCOMPATIBLE": 1, "INDETERMINATE": 2}


class PreflightError(ValueError):
    """Messages are fixed diagnostic codes, never raw profile/tool contents."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def text_hash(value):
    return sha256(value.encode("utf-8")) if isinstance(value, str) else None


def utc_text(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_at(value):
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise PreflightError("evaluation_time_invalid") from exc
    if result.tzinfo is None or result.utcoffset() != dt.timedelta(0):
        raise PreflightError("evaluation_time_requires_utc")
    return result


def read_bounded(path, label, limit=MAX_INPUT_BYTES):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise PreflightError(f"{label}_unreadable") from exc
    if not data or len(data) > limit:
        raise PreflightError(f"{label}_empty_or_too_large")
    return data


def run_openssl(arguments, code, *, input_data=None):
    executable = shutil.which("openssl")
    if not executable:
        raise PreflightError("openssl_unavailable")
    try:
        result = subprocess.run(
            [executable, *arguments], input=input_data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise PreflightError(f"{code}_timeout") from exc
    except OSError as exc:
        raise PreflightError("openssl_execution_failed") from exc
    if result.returncode:
        # OpenSSL diagnostics can contain identifiers and supplied path names.
        raise PreflightError(code)
    if len(result.stdout) > MAX_INPUT_BYTES:
        raise PreflightError("openssl_output_too_large")
    return result.stdout


def bounded_tree(value):
    """Bound comparison cost, including maliciously large nested arrays."""
    count = 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise PreflightError("plist_structure_limit")
        if isinstance(item, dict):
            if len(item) > MAX_COLLECTION or any(not isinstance(k, str) for k in item):
                raise PreflightError("plist_dictionary_limit_or_key_type")
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            if len(item) > MAX_COLLECTION:
                raise PreflightError("plist_array_limit")
            pending.extend((v, depth + 1) for v in item)


def load_plist(data, label):
    try:
        value = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, RecursionError, ExpatError, plistlib.InvalidFileException) as exc:
        raise PreflightError(f"{label}_plist_invalid") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label}_plist_not_dictionary")
    bounded_tree(value)
    return value


def profile_date(value, label):
    if not isinstance(value, dt.datetime):
        raise PreflightError(f"{label}_date_missing_or_invalid")
    # plist dates have UTC semantics; plistlib returns naive UTC on older Python.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def decode_profile(path, label):
    raw = read_bounded(path, label)
    with tempfile.TemporaryDirectory(prefix="profile-preflight-") as directory:
        source = Path(directory) / "profile.cms"
        source.write_bytes(raw)
        decoded = run_openssl(
            ["cms", "-verify", "-verify_retcode", "-noverify", "-binary", "-inform", "DER", "-in", str(source)],
            f"{label}_cms_signature_integrity_failed",
        )
    profile = load_plist(decoded, label)
    expires = profile_date(profile.get("ExpirationDate"), f"{label}_expiration")
    created = profile_date(profile.get("CreationDate"), f"{label}_creation")
    if expires <= created:
        raise PreflightError(f"{label}_date_order_invalid")
    entitlements = profile.get("Entitlements")
    if not isinstance(entitlements, dict):
        raise PreflightError(f"{label}_entitlements_missing_or_invalid")
    certificates = profile.get("DeveloperCertificates")
    if not isinstance(certificates, list) or not certificates or any(
        not isinstance(cert, bytes) or not cert or len(cert) > MAX_CERT_BYTES for cert in certificates
    ):
        raise PreflightError(f"{label}_certificates_missing_or_invalid")
    teams = profile.get("TeamIdentifier")
    teams_valid = isinstance(teams, list) and bool(teams) and all(isinstance(t, str) and t for t in teams)
    devices = profile.get("ProvisionedDevices")
    device_bound = isinstance(devices, list) and bool(devices) and all(isinstance(d, str) and d for d in devices)
    all_devices = profile.get("ProvisionsAllDevices") is True
    der = profile.get("DER-Encoded-Profile")
    metadata = {
        "cms_sha256": sha256(raw),
        "cms_bytes": len(raw),
        "cms_signature_integrity": "VERIFIED_WITH_UNTRUSTED_EMBEDDED_SIGNER",
        "apple_trust": "UNVERIFIED",
        "der_profile": "PRESENT_NOT_EVALUATED" if isinstance(der, bytes) and der else "ABSENT_OR_INVALID",
        "creation_utc": utc_text(created),
        "expiration_utc": utc_text(expires),
        "profile_uuid_sha256": text_hash(profile.get("UUID")),
        "application_identifier_sha256": text_hash(entitlements.get("application-identifier")),
        "team_identifier_sha256": sorted(text_hash(t) for t in teams) if teams_valid else [],
        "allowed_certificate_sha256": sorted(sha256(cert) for cert in certificates),
        "entitlement_count": len(entitlements),
        "device_scope": "DEVICE_BOUND" if device_bound and not all_devices else (
            "ALL_DEVICES" if all_devices and devices is None else "UNRESOLVED"
        ),
        "device_count": len(devices) if device_bound else None,
    }
    return profile, metadata


def certificate_date(value):
    # Fixed English month mapping avoids locale-dependent parsing of OpenSSL dates.
    match = re.fullmatch(r"([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\d{4}) GMT", value)
    if not match:
        raise PreflightError("signer_certificate_date_invalid")
    months = {name: index for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
    try:
        month, day, hour, minute, second, year = match.groups()
        return dt.datetime(int(year), months[month], int(day), int(hour), int(minute), int(second), tzinfo=UTC)
    except (ValueError, KeyError) as exc:
        raise PreflightError("signer_certificate_date_invalid") from exc


def read_signer_certificate(path):
    raw = read_bounded(path, "signer_certificate", MAX_CERT_BYTES)
    form = "PEM" if raw.lstrip().startswith(b"-----BEGIN CERTIFICATE-----") else "DER"
    # A single certificate is required; do not silently select one in a PEM chain.
    if form == "PEM" and raw.count(b"-----BEGIN CERTIFICATE-----") != 1:
        raise PreflightError("signer_certificate_requires_single_certificate")
    der = run_openssl(["x509", "-inform", form, "-outform", "DER"], "signer_certificate_invalid", input_data=raw)
    dates = run_openssl(["x509", "-inform", "DER", "-noout", "-dates"], "signer_certificate_invalid", input_data=der)
    try:
        fields = dict(line.split("=", 1) for line in dates.decode("ascii").splitlines())
        before = certificate_date(fields["notBefore"])
        after = certificate_date(fields["notAfter"])
    except (ValueError, KeyError, UnicodeDecodeError) as exc:
        raise PreflightError("signer_certificate_date_invalid") from exc
    return der, before, after


def typed_equal(left, right):
    """Python considers True == 1, which is not equal entitlement metadata."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(typed_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def entitlement_match(claim, allowed, key):
    """Return MATCH, MISMATCH or UNKNOWN; do not emulate undocumented policy."""
    if type(claim) is not type(allowed):
        return "UNKNOWN"
    if isinstance(claim, str) and key in WILDCARD_KEYS and "*" in claim:
        return "UNKNOWN"
    if not isinstance(claim, list) and typed_equal(claim, allowed):
        # Arrays still require walking their values, including wildcard checks.
        return "MATCH"
    if isinstance(claim, str):
        if "*" not in allowed:
            return "MISMATCH"
        if key not in WILDCARD_KEYS or allowed.count("*") != 1 or not allowed.endswith("*") or "*" in claim:
            return "UNKNOWN"
        return "MATCH" if claim.startswith(allowed[:-1]) else "MISMATCH"
    if isinstance(claim, list):
        unresolved = False
        for item in claim:
            results = [entitlement_match(item, grant, key) for grant in allowed]
            if "MATCH" in results:
                continue
            if "UNKNOWN" in results:
                unresolved = True
            else:
                return "MISMATCH"
        return "UNKNOWN" if unresolved else "MATCH"
    # Unequal nested dictionaries, numeric ranges and Boolean relaxations can
    # have entitlement-specific semantics. Do not declare blanket incompatibility.
    return "UNKNOWN"


def check_entitlements(claimed, granted):
    results = []
    for key, value in sorted(claimed.items()):
        if key in ("keychain-access-groups", "com.apple.security.application-groups") and (
            not isinstance(value, list) or any(not isinstance(v, str) or "*" in v for v in value)
        ):
            result = "UNKNOWN"
        else:
            result = "MISMATCH" if key not in granted else entitlement_match(value, granted[key], key)
        results.append({"key_sha256": text_hash(key), "status": result})
    return results


def evaluate(old, new, old_metadata, new_metadata, *, signer=None, signed_entitlements=None, device_udid=None, at=None):
    at = at or dt.datetime.now(UTC)
    failures, unknowns, observations = [], [], []
    old_expiry = profile_date(old["ExpirationDate"], "old_expiration")
    new_expiry = profile_date(new["ExpirationDate"], "new_expiration")
    new_created = profile_date(new["CreationDate"], "new_creation")
    if new_expiry <= old_expiry:
        failures.append("profile_expiration_does_not_advance")
    if new_expiry <= at:
        failures.append("new_profile_expired_at_evaluation")
    if new_created > at:
        failures.append("new_profile_created_after_evaluation_time")
    if old_expiry <= at:
        observations.append("embedded_baseline_profile_already_expired")
    if old_metadata["cms_sha256"] == new_metadata["cms_sha256"]:
        failures.append("profile_bytes_identical")

    old_teams, new_teams = old_metadata["team_identifier_sha256"], new_metadata["team_identifier_sha256"]
    if not old_teams or not new_teams:
        unknowns.append("profile_team_unresolved")
    elif old_teams != new_teams:
        failures.append("profile_team_changed")
    for label, metadata in (("old", old_metadata), ("new", new_metadata)):
        if metadata["der_profile"] != "PRESENT_NOT_EVALUATED":
            unknowns.append(f"{label}_modern_der_profile_absent_or_invalid")

    overlap = bool(set(old_metadata["allowed_certificate_sha256"]) & set(new_metadata["allowed_certificate_sha256"]))
    signer_report = {"supplied": signer is not None, "source": "OPERATOR_SUPPLIED_NOT_EXTRACTED_FROM_EXECUTABLE",
                     "certificate_chain_trust": "UNVERIFIED", "revocation": "NOT_CHECKED"}
    if signer is None:
        unknowns.append("actual_executable_signer_not_supplied")
    else:
        der, before, after = signer
        fingerprint = sha256(der)
        signer_report.update({"certificate_sha256": fingerprint, "not_before_utc": utc_text(before), "not_after_utc": utc_text(after)})
        for label, metadata in (("old", old_metadata), ("new", new_metadata)):
            covered = fingerprint in metadata["allowed_certificate_sha256"]
            signer_report[f"authorized_by_{label}_profile_metadata"] = covered
            if not covered:
                failures.append(f"actual_signer_not_authorized_by_{label}_profile_metadata")
        if not before <= at < after:
            unknowns.append("signer_certificate_outside_current_validity_window_policy_not_evaluated")
        if new_expiry > after:
            unknowns.append("new_profile_outlasts_signer_certificate_policy_not_evaluated")

    entitlement_report = {"supplied": signed_entitlements is not None, "source": "OPERATOR_SUPPLIED_NOT_EXTRACTED_FROM_EXECUTABLE"}
    if signed_entitlements is None:
        unknowns.append("actual_signed_entitlements_not_supplied")
    else:
        application_id = signed_entitlements.get("application-identifier")
        if not isinstance(application_id, str) or not application_id or "*" in application_id:
            unknowns.append("actual_signed_application_identifier_missing_or_unsupported")
        entitlement_report["application_identifier_sha256"] = text_hash(application_id)
        for label, profile in (("old", old), ("new", new)):
            checks = check_entitlements(signed_entitlements, profile["Entitlements"])
            entitlement_report[label] = checks
            if any(c["status"] == "MISMATCH" for c in checks):
                failures.append(f"signed_entitlement_values_not_authorized_by_{label}_profile_metadata")
            if any(c["status"] == "UNKNOWN" for c in checks):
                unknowns.append(f"{label}_entitlement_policy_outside_comparison_scope")

    device_report = {"supplied": device_udid is not None}
    if device_udid is not None:
        device_report["udid_sha256"] = text_hash(device_udid.lower())
    for label, profile, metadata in (("old", old, old_metadata), ("new", new, new_metadata)):
        if metadata["device_scope"] == "DEVICE_BOUND":
            if device_udid is None:
                unknowns.append(f"{label}_device_bound_profile_requires_device_udid")
            else:
                covered = device_udid.lower() in [d.lower() for d in profile["ProvisionedDevices"]]
                device_report[f"authorized_by_{label}_profile_metadata"] = covered
                if not covered:
                    failures.append(f"device_not_authorized_by_{label}_profile_metadata")
        elif metadata["device_scope"] == "ALL_DEVICES":
            device_report[f"authorized_by_{label}_profile_metadata"] = True
        else:
            unknowns.append(f"{label}_device_scope_unresolved")

    status = "INCOMPATIBLE" if failures else ("INDETERMINATE" if unknowns else "SUPPORTED_BY_SUPPLIED_METADATA")
    return {
        "schema_version": 1,
        "evaluated_at_utc": utc_text(at),
        "operation": "profile_only_metadata_preflight",
        "device_validation": "NOT_DEVICE_VALIDATED",
        "apple_trust": "UNVERIFIED",
        "old": old_metadata,
        "new": new_metadata,
        "signer": signer_report,
        "signed_entitlements": entitlement_report,
        "device": device_report,
        "comparison": {
            "status": status,
            "failures": sorted(set(failures)),
            "unresolved": sorted(set(unknowns)),
            "observations": sorted(set(observations)),
            "allowed_certificate_sets_overlap": overlap,
            "overlap_is_not_signer_proof": True,
        },
        "limitations": [
            "CMS_SIGNATURE_INTEGRITY_IS_NOT_APPLE_CHAIN_TRUST_OR_REVOCATION_VALIDATION",
            "MODERN_DER_PROFILE_CONTENT_AND_IOS_POLICY_NOT_EVALUATED",
            "SUPPLIED_SIGNER_AND_ENTITLEMENTS_NOT_ATTESTED_AGAINST_INSTALLED_EXECUTABLE",
            "METADATA_COMPATIBILITY_IS_NOT_PROFILE_INSTALLATION_OR_USABLE_LIFETIME_PROOF",
            "CERTIFICATE_DATES_ARE_NOT_A_UNIVERSAL_IOS_RUNTIME_LIFETIME_FORMULA",
            "ONE_PROFILE_PAIR_ONLY_EACH_EXTENSION_REQUIRES_ITS_OWN_CHECK",
            "DIGESTS_ARE_LINKABLE_NOT_ANONYMIZED",
        ],
    }


def write_report(report, output=None):
    content = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(content)
        return
    path = Path(output)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".profile-preflight-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise PreflightError("report_write_failed") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="Baseline embedded .mobileprovision CMS file")
    parser.add_argument("--new", required=True, help="Fresh Apple-issued .mobileprovision CMS file")
    parser.add_argument("--signer-cert", help="Actual existing executable's signing certificate, single DER or PEM certificate")
    parser.add_argument("--signed-entitlements", help="Actual existing executable's signed entitlement plist")
    parser.add_argument("--device-udid-file", help="Text file containing the target UDID (avoids exposing it in argv)")
    parser.add_argument("--at", help="Explicit evaluation instant in UTC ISO8601; default is current time")
    parser.add_argument("--output", help="Write JSON atomically with mode0600; default stdout")
    args = parser.parse_args(argv)
    try:
        at = parse_at(args.at) if args.at else dt.datetime.now(UTC)
        old, old_metadata = decode_profile(args.old, "old")
        new, new_metadata = decode_profile(args.new, "new")
        signer = read_signer_certificate(args.signer_cert) if args.signer_cert else None
        signed = load_plist(read_bounded(args.signed_entitlements, "signed_entitlements"), "signed_entitlements") if args.signed_entitlements else None
        device = None
        if args.device_udid_file:
            try:
                device = read_bounded(args.device_udid_file, "device_udid", 256).decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise PreflightError("device_udid_invalid") from exc
            if not re.fullmatch(r"[A-Fa-f0-9-]{16,128}", device):
                raise PreflightError("device_udid_invalid")
        report = evaluate(old, new, old_metadata, new_metadata, signer=signer, signed_entitlements=signed, device_udid=device, at=at)
    except PreflightError as exc:
        report = {"schema_version": 1, "device_validation": "NOT_DEVICE_VALIDATED", "apple_trust": "UNVERIFIED",
                  "comparison": {"status": "INDETERMINATE"}, "error": {"code": str(exc)}}
    try:
        write_report(report, args.output)
    except PreflightError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    return STATUSES[report["comparison"]["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
