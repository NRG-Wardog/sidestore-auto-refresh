#!/usr/bin/env python3
"""Inspect the exact raw candidate IPA and its checked provenance sidecar."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import zipfile

from audit_ipa_signing import inventory


BASE = "Payload/LiveContainer.app"
REQUIRED_FRAMEWORKS = (
    "LiveContainerShared.framework", "LiveContainerSwiftUI.framework",
    "SideStoreSupport.framework", "SideStoreApp.framework", "OpenSSL.framework",
)
REQUIRED_GROUP = "group.com.SideStore.SideStore"
REQUIRED_SCHEMES = {"livecontainer", "sidestore", "sidestore-com.kdt.livecontainer"}
REQUIRED_BACKGROUND_IDS = {
    "com.kdt.livecontainer.sidestore.automatic-refresh",
    "com.kdt.livecontainer.sidestore.automatic-refresh.watchdog",
}
PRIVATE_EXTENSIONS = {".p12", ".p8", ".pem", ".key", ".mobileprovision", ".log", ".crash", ".ips"}


def architectures(data: bytes) -> set[str]:
    magic = data[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        endian = ">" if magic == b"\xca\xfe\xba\xbe" else "<"
        count = struct.unpack_from(endian + "I", data, 4)[0]
        result = set()
        for index in range(count):
            cpu = struct.unpack_from(endian + "I", data, 8 + index * 20)[0]
            result.add("arm64" if cpu == 0x0100000C else f"cpu:{cpu}")
        return result
    if magic == b"\xcf\xfa\xed\xfe":
        cpu = struct.unpack_from("<I", data, 4)[0]
    elif magic == b"\xce\xfa\xed\xfe":
        cpu = struct.unpack_from("<I", data, 4)[0]
    elif magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        cpu = struct.unpack_from(">I", data, 4)[0]
    else:
        return set()
    return {"arm64" if cpu == 0x0100000C else f"cpu:{cpu}"}


def verify(ipa: Path, provenance_path: Path, product: str) -> dict:
    raw = ipa.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    size = len(raw)
    with zipfile.ZipFile(ipa) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"corrupt IPA member: {bad_member}")
        names = archive.namelist()
        lower_names = [name.lower() for name in names]
        if any(".audit" in name.split("/") or ".git" in name.split("/") for name in lower_names):
            raise ValueError("audit or repository implementation data is packaged")
        if any(name.rsplit("/", 1)[-1].lower().endswith(tuple(PRIVATE_EXTENSIONS)) for name in names):
            raise ValueError("private signing material or diagnostic logs are packaged")
        if any(name.endswith((".swift", ".m", ".mm", ".py")) for name in names):
            raise ValueError("implementation source is packaged")

        info = plistlib.loads(archive.read(BASE + "/Info.plist"))
        if info.get("LCProductLine") != "Combined LC+SS " + product:
            raise ValueError("candidate product identity does not match the requested version")
        if not re.fullmatch(r"[0-9a-f]{40}", str(info.get("LCBuilderCommit", ""))):
            raise ValueError("builder commit identity is missing")
        schemes = {
            scheme
            for entry in info.get("CFBundleURLTypes", [])
            for scheme in entry.get("CFBundleURLSchemes", [])
        }
        if not REQUIRED_SCHEMES.issubset(schemes):
            raise ValueError("required URL schemes are missing")
        if not REQUIRED_BACKGROUND_IDS.issubset(set(info.get("BGTaskSchedulerPermittedIdentifiers", []))):
            raise ValueError("required background task identifiers are missing")
        if "processing" not in set(info.get("UIBackgroundModes", [])):
            raise ValueError("background processing mode is missing")

        package_bundles = inventory(ipa)["bundles"]
        host = package_bundles[BASE]
        side_store_path = BASE + "/Frameworks/SideStoreApp.framework"
        side_store_info = package_bundles[side_store_path]["info"]
        for product_info in (info, side_store_info):
            if product_info.get("LCProductLine") != "Combined LC+SS " + product:
                raise ValueError("host and embedded SideStore product identities differ")
            if product_info.get("LCBuilderCommit") != info.get("LCBuilderCommit"):
                raise ValueError("host and embedded SideStore builder SHAs differ")
            if product_info.get("LCBuildRunURL") != info.get("LCBuildRunURL"):
                raise ValueError("host and embedded SideStore build run URLs differ")
        host_groups = (host.get("signing") or {}).get("xml_entitlements") or {}
        if REQUIRED_GROUP not in host_groups.get("com.apple.security.application-groups", []):
            raise ValueError("host App Group entitlement is missing")

        live_process_path = BASE + "/PlugIns/LiveProcess.appex"
        live_process = package_bundles.get(live_process_path)
        if not live_process or not live_process.get("executable_present"):
            raise ValueError("LiveProcess extension or executable is missing")
        for path, bundle in package_bundles.items():
            if path.startswith(BASE + "/PlugIns/") and path.endswith(".appex"):
                extension_groups = (bundle.get("signing") or {}).get("xml_entitlements") or {}
                if REQUIRED_GROUP not in extension_groups.get("com.apple.security.application-groups", []):
                    raise ValueError(f"extension App Group entitlement is missing: {path}")

        framework_names = {path.rsplit("/", 1)[-1] for path in package_bundles
                           if path.startswith(BASE + "/Frameworks/") and path.endswith(".framework")}
        if not set(REQUIRED_FRAMEWORKS).issubset(framework_names):
            raise ValueError("required frameworks are missing")
        for path, bundle in package_bundles.items():
            if path.startswith(BASE + "/Frameworks/") and path.endswith(".framework"):
                if not bundle.get("executable_present"):
                    raise ValueError(f"framework executable is missing: {path}")

        executable_paths = []
        for path, bundle in package_bundles.items():
            if path == BASE or path.startswith(BASE + "/PlugIns/") or \
                    (path.startswith(BASE + "/Frameworks/") and path.endswith(".framework")):
                if bundle.get("executable_present"):
                    executable_paths.append(path + "/" + bundle["info"]["CFBundleExecutable"])
        executable_paths.extend([live_process_path + "/LiveProcess", side_store_path + "/SideStore"])
        arch_report = {}
        for path in sorted(set(executable_paths)):
            archs = architectures(archive.read(path))
            if "arm64" not in archs:
                raise ValueError(f"arm64 architecture is missing: {path}")
            arch_report[path] = sorted(archs)

        for name in names:
            suffix = Path(name).suffix.lower()
            if suffix not in {".plist", ".json", ".txt", ".xml", ".strings", ".conf", ".yaml", ".yml"}:
                continue
            data = archive.read(name)
            if b"-----BEGIN PRIVATE KEY-----" in data or b"-----BEGIN RSA PRIVATE KEY-----" in data:
                raise ValueError("private key material is packaged")

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance.get("candidate_product_version") != product:
        raise ValueError("provenance product version mismatch")
    if provenance.get("schema") != 1 or provenance.get("physical_device_execution") is not False:
        raise ValueError("provenance schema or validation scope is invalid")
    if provenance.get("ipa") != ipa.name or provenance.get("ipa_size_bytes") != size:
        raise ValueError("provenance IPA filename or size mismatch")
    if provenance.get("raw_ipa_sha256") != digest or provenance.get("sha256") != digest:
        raise ValueError("provenance raw IPA SHA-256 mismatch")
    if provenance.get("LCBuilderCommit") != info.get("LCBuilderCommit"):
        raise ValueError("provenance builder SHA mismatch")
    if not str(provenance.get("LCBuildRunURL", "")).startswith("https://github.com/"):
        raise ValueError("provenance build run URL is missing")
    for key in ("LIVE_CONTAINER_REF", "EMBEDDED_SIDESTORE_REF", "MINIMUXER_REF",
                "SIDESIGN_REF", "SIDESIGN_GSA_FIX", "IDEVICE_REF", "JKTCP_REF"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("dependencies", {}).get(key, ""))):
            raise ValueError(f"provenance revision is missing or invalid: {key}")
        if os.environ.get(key) and provenance["dependencies"][key] != os.environ[key]:
            raise ValueError(f"provenance revision does not match the build environment: {key}")

    return {
        "verification": "PASS",
        "product": product,
        "ipa_filename": ipa.name,
        "ipa_size_bytes": size,
        "raw_ipa_sha256": digest,
        "builder_commit": info["LCBuilderCommit"],
        "architectures": arch_report,
        "liveprocess_extension": live_process_path,
        "required_frameworks": sorted(REQUIRED_FRAMEWORKS),
        "app_group": REQUIRED_GROUP,
        "url_schemes": sorted(REQUIRED_SCHEMES),
        "background_identifiers": sorted(REQUIRED_BACKGROUND_IDS),
        "audit_source_or_private_material": "absent",
        "provenance": "verified",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--product", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.ipa, args.provenance, args.product)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
