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
import shutil
import struct
import subprocess
import tempfile
import zipfile

from audit_ipa_signing import inventory
from patch_v3_service import HEADLESS_SIDESTORE_VIEW_FILES


BASE = "Payload/LiveContainer.app"
REQUIRED_FRAMEWORKS = (
    "LiveContainerShared.framework", "LiveContainerSwiftUI.framework",
    "SideStoreSupport.framework", "SideStoreApp.framework", "OpenSSL.framework",
)
REQUIRED_GROUP = "group.com.SideStore.SideStore"
REQUIRED_LIVECONTAINER_GROUPS = {
    REQUIRED_GROUP,
    "group.com.rileytestut.AltStore",
}
REQUIRED_SCHEMES = {"livecontainer", "sidestore", "sidestore-com.kdt.livecontainer"}
REQUIRED_BACKGROUND_IDS = {
    "com.kdt.livecontainer.sidestore.automatic-refresh",
    "com.kdt.livecontainer.sidestore.automatic-refresh.watchdog",
}
REQUIRED_BACKGROUND_MODES = {"processing", "fetch"}
REQUIRED_DEAD10CC_MARKER = b"DEAD10CC_FIX_E98699A registered both observers in guest process"
REMOVED_SIDESTORE_ICON_NAMES = {
    "blueicon", "darkicon", "honeydewicon", "prideicon",
    "sandyicon", "skyicon", "snowicon", "starbursticon", "stormicon", "vistaicon", "wintericon",
}
PRIVATE_EXTENSIONS = {".p12", ".p8", ".pem", ".key", ".mobileprovision", ".log", ".crash", ".ips"}
REMOVED_SIDESTORE_INTENT_SYMBOLS = (
    "InstallIPAIntent", "IntentHandler", "ViewAppIntentHandler",
)
REMOVED_SIDESTORE_INTENT_INFO_KEYS = ("INIntentsSupported", "NSUserActivityTypes")
SWIFT_TYPE_DECLARATION = re.compile(
    r"(?m)^\s*(?:(?:public|private|internal|fileprivate|open)\s+)?"
    r"(?:(?:final|indirect)\s+)*(?:class|struct|enum|protocol)\s+([A-Za-z_]\w*)")
REMOVED_SIDESTORE_UI_SYMBOLS = (
    "ResignAltStoreViewController", "FeaturedViewController", "BrowseViewController",
    "FeaturedComponents", "BackgroundTaskManager",
    "NewsViewController", "NewsCollectionViewCell", "TabBarController", "SourcesViewController",
    "SourceDetailViewController", "SourceDetailContentViewController",
    "HeaderContentViewController", "AppIDsViewController",
    "AppViewController", "AppContentViewController", "AppDetailCollectionViewController",
    "AppScreenshotsViewController", "AppPermissionsCard", "PreviewAppScreenshotsViewController",
    "AppScreenshotCollectionViewCell", "AppCardCollectionViewCell",
    "ScreenshotCollectionViewCell", "ForwardingNavigationController",
    "NavigationBarAppearance", "LargeIconCollectionViewCell", "IconButtonCollectionReusableView",
    "SourceComponents", "SourceHeaderView", "AppInfoView", "CodeResourcesViewer",
    "InfoPlistContainerView", "MachOResourceViewer", "CollapsingMarkdownView",
    "CertificatesView", "CertificatesViewModel", "DeveloperServicesView",
    "DeveloperServicesViewModel", "HealthCheckView", "HealthCheckViewModel",
    "StorageExplorerView", "StorageExplorerViewModel", "SideJITServerConfigView",
    "SideSignConfigurationView", "UserCustomizationsView", "WirelessPairView",
    "BonjourDiscoveryView", "BackupAndRestoreView", "AppGroupsListView", "AppIDsListView",
    "AddSourceTextFieldCell", "AddSourceViewController", "AuthenticationViewController",
    "InstructionsViewController", "SelectTeamViewController", "MyAppsViewController",
    "MyAppsComponents", "InstalledAppsCollectionHeaderView", "UpdateCollectionViewCell",
    "SettingsViewController", "LaunchViewController", "AltAppIconsViewController",
    "PatreonViewController", "LicensesViewController", "RefreshAttemptsViewController",
    "ErrorDetailsViewController", "ErrorLogTableViewCell", "ErrorLogViewController",
)


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


def has_required_livecontainer_groups(groups) -> bool:
    return REQUIRED_LIVECONTAINER_GROUPS.issubset(set(groups or []))


def archive_size_report(infos, executable_paths: set[str]) -> dict:
    files = [info for info in infos if not info.is_dir()]
    executable_paths = set(executable_paths)
    categories = {
        "executables": 0,
        "swift_runtime_dylibs": 0,
        "nested_archives": 0,
        "framework_payload_excluding_executables": 0,
        "extension_payload_excluding_executables": 0,
        "Assets.car": 0,
        "localizations": 0,
        "storyboards_and_nibs": 0,
        "fonts": 0,
        "images": 0,
        "audio_and_video": 0,
        "metadata_and_signing": 0,
        "other_files": 0,
    }
    bundle_totals: dict[str, int] = {}
    bundle_file_counts: dict[str, int] = {}
    for info in files:
        name = info.filename
        components = name.split("/")
        bundle_paths = []
        for index, component in enumerate(components):
            if component.endswith((".app", ".appex", ".framework")):
                bundle_paths.append("/".join(components[:index + 1]))
        for bundle_path in bundle_paths:
            bundle_totals[bundle_path] = bundle_totals.get(bundle_path, 0) + info.file_size
            bundle_file_counts[bundle_path] = bundle_file_counts.get(bundle_path, 0) + 1
        suffix = Path(name).suffix.lower()
        basename = name.rsplit("/", 1)[-1]
        if name in executable_paths:
            category = "executables"
        elif suffix in {".ipa", ".zip"}:
            category = "nested_archives"
        elif "/usr/lib/swift/" in name or (basename.startswith("libswift") and suffix == ".dylib"):
            category = "swift_runtime_dylibs"
        elif name.endswith("/Assets.car") or name == "Assets.car":
            category = "Assets.car"
        elif ".storyboardc/" in name or ".nib/" in name or name.endswith(".nib"):
            category = "storyboards_and_nibs"
        elif any(component.endswith(".lproj") for component in name.split("/")):
            category = "localizations"
        elif suffix in {".ttf", ".otf", ".woff", ".woff2"}:
            category = "fonts"
        elif suffix in {".png", ".jpg", ".jpeg", ".heic", ".gif", ".pdf"}:
            category = "images"
        elif suffix in {".m4a", ".mp3", ".aac", ".wav", ".mov", ".mp4"}:
            category = "audio_and_video"
        elif suffix in {".plist", ".json", ".xml", ".strings", ".stringsdict", ".mobileprovision"} or "/_CodeSignature/" in name:
            category = "metadata_and_signing"
        elif "/Frameworks/" in name:
            category = "framework_payload_excluding_executables"
        elif "/PlugIns/" in name:
            category = "extension_payload_excluding_executables"
        else:
            category = "other_files"
        categories[category] += info.file_size
    largest = sorted(files, key=lambda info: (-info.file_size, info.filename))[:20]
    return {
        "file_count": len(files),
        "uncompressed_bytes": sum(info.file_size for info in files),
        "zip_member_bytes": sum(info.compress_size for info in files),
        "payload_breakdown_bytes": categories,
        "bundle_totals_bytes": dict(sorted(bundle_totals.items())),
        "bundle_totals_semantics": "inclusive_parent_bundles; nested files count in each ancestor",
        "bundle_file_counts": dict(sorted(bundle_file_counts.items())),
        "largest_files": [
            {"path": info.filename, "uncompressed_bytes": info.file_size,
             "zip_member_bytes": info.compress_size}
            for info in largest
        ],
    }


def find_legacy_side_store_resources(side_store_path: str, names: list[str]) -> list[str]:
    prefix = side_store_path.rstrip("/") + "/"
    excluded = []
    for name in names:
        if not name.startswith(prefix):
            continue
        components = name[len(prefix):].split("/")
        lower_components = [component.lower() for component in components]
        suffix = Path(name).suffix.lower()
        basename = name.rsplit("/", 1)[-1].lower()
        if (any(component.endswith((".storyboardc", ".nib")) for component in lower_components)
                or "metadata.appintents" in lower_components
                or suffix in {".storyboard", ".xib", ".nib", ".intentdefinition"}
                or basename in {"silence.m4a", "alticons.plist"}):
            excluded.append(name)
    return sorted(excluded)


def find_legacy_side_store_intent_symbols(executable: bytes) -> list[str]:
    return [name for name in REMOVED_SIDESTORE_INTENT_SYMBOLS if name.encode("utf-8") in executable]


def find_legacy_side_store_intent_info_keys(info: dict) -> list[str]:
    return [key for key in REMOVED_SIDESTORE_INTENT_INFO_KEYS if key in info]


def find_legacy_side_store_ui_symbols(executable: bytes) -> list[str]:
    return [name for name in REMOVED_SIDESTORE_UI_SYMBOLS if name.encode("utf-8") in executable]


def excluded_side_store_view_type_names(side_source: Path,
                                        view_files=HEADLESS_SIDESTORE_VIEW_FILES) -> list[str]:
    synchronized_root = side_source / "SideStore"
    excluded_paths = set(view_files)
    removed_types: set[str] = set()
    for relative in view_files:
        path = synchronized_root / relative
        if not path.is_file():
            raise ValueError(f"headless SideStore UI source is missing: {relative}")
        removed_types.update(SWIFT_TYPE_DECLARATION.findall(path.read_text(encoding="utf-8")))

    retained_types: set[str] = set()
    for path in synchronized_root.rglob("*.swift"):
        if path.relative_to(synchronized_root).as_posix() in excluded_paths:
            continue
        retained_types.update(SWIFT_TYPE_DECLARATION.findall(path.read_text(encoding="utf-8")))
    return sorted(removed_types - retained_types - {"Color"})


def missing_excluded_ui_symbols(executable: bytes, expected_symbols: list[str]) -> list[str]:
    return sorted(name for name in expected_symbols if name.encode("utf-8") in executable)


def missing_required_background_modes(info: dict) -> list[str]:
    configured = set(info.get("UIBackgroundModes", []))
    return sorted(REQUIRED_BACKGROUND_MODES - configured)


def has_required_dead10cc_marker(executable: bytes) -> bool:
    return REQUIRED_DEAD10CC_MARKER in executable


def verify_side_store_assetutil_records(records: list[dict]) -> dict:
    if not isinstance(records, list) or not records:
        raise ValueError("SideStore Assets.car has no readable asset records")
    names = sorted({record.get("Name") for record in records
                    if isinstance(record, dict) and isinstance(record.get("Name"), str)})
    excluded = sorted({name.casefold() for name in names} & REMOVED_SIDESTORE_ICON_NAMES)
    if excluded:
        raise ValueError("excluded SideStore alternate-icon assets remain: " + ", ".join(excluded))
    primary_icons = [name for name in names if "appicon" in name.casefold()]
    if "AppIcon" not in primary_icons:
        raise ValueError("the primary SideStore AppIcon is missing from Assets.car")
    return {
        "asset_catalog_record_count": len(records),
        "appicon_named_asset_name_count": len(primary_icons),
        "primary_app_icon_present": True,
        "alternate_icon_sets": "11 alternate app icons absent; Classic/Modern previews retained",
    }


def side_store_primary_icon_report(asset_report: dict) -> dict:
    if not asset_report.get("primary_app_icon_present"):
        raise ValueError("the primary SideStore AppIcon is missing from Assets.car")
    return {
        "assets_car_record_present": True,
        "named_appicon_asset_count": asset_report["appicon_named_asset_name_count"],
    }


def inspect_side_store_asset_catalog(asset_data: bytes) -> dict:
    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise ValueError("Xcode assetutil is required to inspect the embedded SideStore Assets.car")
    with tempfile.TemporaryDirectory(prefix="v3-assets-") as directory:
        catalog = Path(directory) / "Assets.car"
        catalog.write_bytes(asset_data)
        result = subprocess.run([xcrun, "assetutil", "--info", str(catalog)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ValueError("Xcode assetutil could not inspect the embedded SideStore Assets.car")
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("Xcode assetutil returned malformed asset metadata") from error
        return verify_side_store_assetutil_records(records)


def verify(ipa: Path, provenance_path: Path, product: str,
           side_source: Path | None = None) -> dict:
    raw = ipa.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    size = len(raw)
    side_store_asset_report = {}
    with zipfile.ZipFile(ipa) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"corrupt IPA member: {bad_member}")
        names = archive.namelist()
        archive_infos = archive.infolist()
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
        missing_background_modes = missing_required_background_modes(info)
        if missing_background_modes:
            raise ValueError("required host background modes are missing: "
                             + ", ".join(missing_background_modes))

        package_bundles = inventory(ipa)["bundles"]
        host = package_bundles[BASE]
        side_store_path = BASE + "/Frameworks/SideStoreApp.framework"
        side_store_info = package_bundles[side_store_path]["info"]
        legacy_intent_keys = find_legacy_side_store_intent_info_keys(side_store_info)
        if legacy_intent_keys:
            raise ValueError("embedded SideStore still declares its legacy intents or activities: "
                             + ", ".join(legacy_intent_keys))
        for icon_key in ("CFBundleIcons", "CFBundleIcons~ipad"):
            icons = side_store_info.get(icon_key, {})
            if isinstance(icons, dict) and icons.get("CFBundleAlternateIcons"):
                raise ValueError("embedded SideStore still declares alternate app icons")
        legacy_resources = find_legacy_side_store_resources(side_store_path, names)
        if legacy_resources:
            raise ValueError("embedded SideStore contains excluded UI/audio resources: "
                             + ", ".join(legacy_resources[:8]))
        side_store_executable = side_store_path + "/" + side_store_info["CFBundleExecutable"]
        side_store_executable_data = archive.read(side_store_executable)
        legacy_intents = find_legacy_side_store_intent_symbols(side_store_executable_data)
        if legacy_intents:
            raise ValueError("embedded SideStore still contains legacy app intent code: "
                             + ", ".join(legacy_intents))
        legacy_ui = find_legacy_side_store_ui_symbols(side_store_executable_data)
        headless_view_symbols = excluded_side_store_view_type_names(side_source) if side_source else []
        legacy_view_types = missing_excluded_ui_symbols(side_store_executable_data, headless_view_symbols)
        legacy_ui = sorted(set(legacy_ui + legacy_view_types))
        if legacy_ui:
            raise ValueError("embedded SideStore still contains excluded presenter UI: "
                             + ", ".join(legacy_ui))
        shared_framework_path = BASE + "/Frameworks/LiveContainerShared.framework"
        shared_framework = package_bundles.get(shared_framework_path)
        if not shared_framework or not shared_framework.get("executable_present"):
            raise ValueError("LiveContainerShared framework executable is missing")
        shared_executable = shared_framework_path + "/" + shared_framework["info"]["CFBundleExecutable"]
        if not has_required_dead10cc_marker(archive.read(shared_executable)):
            raise ValueError("packaged LiveContainerShared executable is missing the Dead10CC lifecycle fix")
        if "UIBackgroundModes" in side_store_info:
            raise ValueError("embedded SideStore still declares app background modes")
        if any(key in side_store_info for key in ("UIMainStoryboardFile", "UILaunchStoryboardName")):
            raise ValueError("embedded SideStore still declares a legacy UI storyboard")
        asset_catalog_path = side_store_path + "/Assets.car"
        if asset_catalog_path not in names:
            raise ValueError("embedded SideStore Assets.car is missing")
        side_store_asset_report = inspect_side_store_asset_catalog(archive.read(asset_catalog_path))
        scene_configurations = side_store_info.get("UIApplicationSceneManifest", {}).get(
            "UISceneConfigurations", {})
        for configurations in scene_configurations.values():
            for configuration in configurations:
                if any(key in configuration for key in ("UISceneStoryboardFile", "UILaunchStoryboardName")):
                    raise ValueError("embedded SideStore scene still declares a storyboard root")
        for product_info in (info, side_store_info):
            if product_info.get("LCProductLine") != "Combined LC+SS " + product:
                raise ValueError("host and embedded SideStore product identities differ")
            if product_info.get("LCBuilderCommit") != info.get("LCBuilderCommit"):
                raise ValueError("host and embedded SideStore builder SHAs differ")
            if product_info.get("LCBuildRunURL") != info.get("LCBuildRunURL"):
                raise ValueError("host and embedded SideStore build run URLs differ")
        host_groups = (host.get("signing") or {}).get("xml_entitlements") or {}
        if not has_required_livecontainer_groups(
                host_groups.get("com.apple.security.application-groups", [])):
            raise ValueError("host SideStore/AltStore App Group entitlements are incomplete")

        live_process_path = BASE + "/PlugIns/LiveProcess.appex"
        live_process = package_bundles.get(live_process_path)
        if not live_process or not live_process.get("executable_present"):
            raise ValueError("LiveProcess extension or executable is missing")
        live_process_groups = (live_process.get("signing") or {}).get("xml_entitlements") or {}
        if not has_required_livecontainer_groups(
                live_process_groups.get("com.apple.security.application-groups", [])):
            raise ValueError("LiveProcess SideStore/AltStore App Group entitlements are incomplete")
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
        size_report = archive_size_report(archive_infos, set(executable_paths))

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
        **size_report,
        "builder_commit": info["LCBuilderCommit"],
        "architectures": arch_report,
        "liveprocess_extension": live_process_path,
        "required_frameworks": sorted(REQUIRED_FRAMEWORKS),
        "dead10cc_lifecycle_fix": "verified in LiveContainerShared",
        "sidestore_storyboard_root": "absent",
        "sidestore_legacy_storyboard_nib_audio": "absent",
        "sidestore_legacy_app_intents": "absent",
        "sidestore_excluded_view_type_count": len(headless_view_symbols),
        "sidestore_legacy_resign_ui": "absent",
        "sidestore_alternate_icon_sets": side_store_asset_report,
        "sidestore_primary_icon": side_store_primary_icon_report(side_store_asset_report),
        "sidestore_legacy_background_modes": "absent",
        "app_group": REQUIRED_GROUP,
        "livecontainer_app_groups": sorted(REQUIRED_LIVECONTAINER_GROUPS),
        "url_schemes": sorted(REQUIRED_SCHEMES),
        "background_identifiers": sorted(REQUIRED_BACKGROUND_IDS),
        "host_background_modes": sorted(REQUIRED_BACKGROUND_MODES),
        "audit_source_or_private_material": "absent",
        "provenance": "verified",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--product", required=True)
    parser.add_argument("--side-source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.ipa, args.provenance, args.product, side_source=args.side_source)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
