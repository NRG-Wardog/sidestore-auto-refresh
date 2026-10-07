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
from urllib.parse import urlsplit
import uuid
import zipfile

from audit_ipa_signing import inventory
from package_livecontainer_combined import (verify_shared_secret_handoff_group,
                                            verify_auth_answer_transport)
from patch_v3_service import (HEADLESS_BACKEND_CONNECTION_CONFIG,
                              HEADLESS_SIDESTORE_AUX_UI_FILES,
                              HEADLESS_SIDESTORE_HANDLER_UI_FILES,
                              HEADLESS_SIDESTORE_PIPELINE_UI_FILES,
                              HEADLESS_SIDESTORE_VIEW_FILES, PINS as SOURCE_PINS)


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
ANISETTE_EVIDENCE_DIRECTORY = "anisette-generated"
ANISETTE_EVIDENCE_MANIFEST = "isolated-otp-patch.json"
ANISETTE_COMPILED_MARKER = b"V3_CHECKED_ANISETTE_STAGING_V1"
ANISETTE_COMPILED_LITERAL = b"Checked OTP staging failed"
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
    "SettingsHeaderFooterView", "InsetGroupTableViewCell",
    "PatreonViewController", "LicensesViewController", "RefreshAttemptsViewController",
    "ErrorDetailsViewController", "ErrorLogTableViewCell", "ErrorLogViewController",
)

# The generated transport-only backend retains the ConnectionConfig type name.
# These class-qualified members belong only to the retired SwiftUI model and
# remain visible in the pre-headless release binary even under Release linking.
RETIRED_CONNECTION_CONFIG_MEMBERS = (
    "formattedTunnelIface", "formattedTunnelPeer", "overrideIPStorage",
    "remoteServerIPStorage", "tunnelPeerActive",
)


CPU_TYPE_ARM64 = 0x0100000C
MIB = 1024 * 1024
# The observed candidate is about 39 MB compressed and 95 MB expanded. These
# ceilings allow over 5x growth; the entry cap stays below classic ZIP's ZIP64
# sentinel, so ZIP64 is unnecessary and is rejected by the pre-constructor scan.
DEFAULT_ARCHIVE_LIMITS = {
    "compressed_ipa_bytes": 250 * MIB,
    "member_count": 60_000,
    "central_directory_bytes": 64 * MIB,
    "total_uncompressed_bytes": 500 * MIB,
    "member_uncompressed_bytes": 256 * MIB,
    "compression_ratio": 1000,
}
THIN_MAGICS = {
    b"\xce\xfa\xed\xfe": ("<", 28), b"\xfe\xed\xfa\xce": (">", 28),
    b"\xcf\xfa\xed\xfe": ("<", 32), b"\xfe\xed\xfa\xcf": (">", 32),
}
FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
    b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True),
}


def _architecture_name(cpu: int) -> str:
    return "arm64" if cpu == CPU_TYPE_ARM64 else f"cpu:{cpu}"


def _thin_macho_info(data) -> tuple[int, int, str | None]:
    magic = bytes(data[:4])
    layout = THIN_MAGICS.get(magic)
    if layout is None:
        raise ValueError("fat Mach-O slice is not a thin Mach-O image")
    endian, header_size = layout
    if len(data) < header_size:
        raise ValueError("truncated Mach-O header")
    cpu, subtype, _filetype, command_count, command_bytes = struct.unpack_from(endian + "IIIII", data, 4)
    if command_count > 65535 or command_bytes > len(data) - header_size:
        raise ValueError("invalid Mach-O load-command bounds")
    command_end = header_size + command_bytes
    offset = header_size
    image_uuid = None
    for _ in range(command_count):
        if offset + 8 > command_end:
            raise ValueError("truncated Mach-O load command")
        command, size = struct.unpack_from(endian + "II", data, offset)
        if size < 8 or size % 4 != 0 or offset + size > command_end:
            raise ValueError("invalid Mach-O load-command size")
        if command == 0x1B:
            if size != 24 or image_uuid is not None:
                raise ValueError("invalid Mach-O UUID command")
            image_uuid = str(uuid.UUID(bytes=bytes(data[offset + 8:offset + 24]))).upper()
        offset += size
    if offset != command_end:
        raise ValueError("Mach-O load-command size does not match its header")
    return cpu, subtype, image_uuid


def macho_uuids(data: bytes) -> dict[str, str]:
    view = memoryview(data)
    magic = bytes(view[:4])
    if magic in THIN_MAGICS:
        cpu, _subtype, image_uuid = _thin_macho_info(view)
        return {_architecture_name(cpu): image_uuid} if image_uuid else {}
    fat_layout = FAT_MAGICS.get(magic)
    if fat_layout is None:
        return {}
    endian, is_64 = fat_layout
    if len(view) < 8:
        raise ValueError("truncated fat Mach-O header")
    count = struct.unpack_from(endian + "I", view, 4)[0]
    entry_size = 32 if is_64 else 20
    if count == 0 or count > 64 or 8 + count * entry_size > len(view):
        raise ValueError("invalid fat Mach-O architecture table")
    table_end = 8 + count * entry_size
    slices = []
    architectures_seen = set()
    result: dict[str, str] = {}
    for index in range(count):
        entry = 8 + index * entry_size
        if is_64:
            cpu, subtype, offset, size, align, reserved = struct.unpack_from(endian + "IIQQII", view, entry)
            if reserved != 0:
                raise ValueError("fat Mach-O reserved field must be zero")
            max_alignment = 63
        else:
            cpu, subtype, offset, size, align = struct.unpack_from(endian + "IIIII", view, entry)
            max_alignment = 31
        if align > max_alignment:
            raise ValueError("fat Mach-O alignment exponent is invalid")
        architecture = (cpu, subtype)
        if architecture in architectures_seen:
            raise ValueError("fat Mach-O contains a duplicate CPU subtype slice")
        architectures_seen.add(architecture)
        if size == 0 or offset < table_end or offset > len(view) or size > len(view) - offset:
            raise ValueError("fat Mach-O slice is outside the file")
        if offset % (1 << align):
            raise ValueError("fat Mach-O slice offset violates its alignment")
        end = offset + size
        if any(offset < other_end and other_start < end for other_start, other_end in slices):
            raise ValueError("fat Mach-O slices overlap")
        slices.append((offset, end))
        slice_cpu, slice_subtype, image_uuid = _thin_macho_info(view[offset:end])
        if slice_cpu != cpu or slice_subtype != subtype:
            raise ValueError("fat Mach-O CPU type or subtype does not match its slice header")
        if not image_uuid:
            raise ValueError("fat Mach-O slice is missing an LC_UUID command")
        name = _architecture_name(cpu)
        if name in result:
            raise ValueError("fat Mach-O contains multiple slices for one CPU architecture")
        result[name] = image_uuid
    return result


def macho_cpu_subtypes(data: bytes) -> dict[str, int]:
    """Return the CPU subtype for each validated thin or universal slice."""
    view = memoryview(data)
    magic = bytes(view[:4])
    if magic in THIN_MAGICS:
        cpu, subtype, _image_uuid = _thin_macho_info(view)
        return {_architecture_name(cpu): subtype}
    fat_layout = FAT_MAGICS.get(magic)
    if fat_layout is None:
        return {}
    # Reuse the UUID parser so all table/slice consistency checks also apply.
    macho_uuids(data)
    endian, is_64 = fat_layout
    count = struct.unpack_from(endian + "I", view, 4)[0]
    entry_size = 32 if is_64 else 20
    result = {}
    for index in range(count):
        entry = 8 + index * entry_size
        cpu, subtype = struct.unpack_from(endian + "II", view, entry)
        name = _architecture_name(cpu)
        if name in result:
            raise ValueError("fat Mach-O contains multiple slices for one CPU architecture")
        result[name] = subtype
    return result


def require_arm64_all_image(data: bytes) -> dict[str, str]:
    """Enforce the package's supported architecture: generic ARM64 (subtype 0)."""
    archs = architectures(data)
    if archs != {"arm64"}:
        raise ValueError(f"unexpected architecture set {sorted(archs)}")
    subtypes = macho_cpu_subtypes(data)
    if subtypes.get("arm64") != 0:
        raise ValueError(f"unsupported arm64 CPU subtype {subtypes.get('arm64')}; expected ARM64_ALL (0)")
    return macho_uuids(data)


def architectures(data: bytes) -> set[str]:
    view = memoryview(data)
    magic = bytes(view[:4])
    if magic in THIN_MAGICS:
        cpu, _subtype, _image_uuid = _thin_macho_info(view)
        return {_architecture_name(cpu)}
    if magic in FAT_MAGICS:
        # Parse and validate every slice, including slices without an LC_UUID.
        macho_uuids(data)
        endian, is_64 = FAT_MAGICS[magic]
        count = struct.unpack_from(endian + "I", view, 4)[0]
        entry_size = 32 if is_64 else 20
        return {
            _architecture_name(struct.unpack_from(endian + "I", view, 8 + index * entry_size)[0])
            for index in range(count)
        }
    return set()


MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
}


def mach_o_paths(archive: zipfile.ZipFile, infos, root: str | None = None) -> set[str]:
    prefix = root.rstrip("/") + "/" if root else None
    paths = set()
    for info in infos:
        if info.is_dir() or (prefix is not None and not info.filename.startswith(prefix)):
            continue
        with archive.open(info) as member:
            magic = member.read(4)
            # CAFEBABE is also the Java class-file magic. Ignore it only when
            # the entire member has a structurally valid class-file layout.
            if magic == b"\xca\xfe\xba\xbe" and is_java_class_file(archive.read(info)):
                continue
            if magic in MACHO_MAGICS:
                paths.add(info.filename)
    return paths


def is_java_class_file(data: bytes) -> bool:
    """Recognize a class file by its class-file structure, not its suffix."""
    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        return False
    try:
        _minor, major, count = struct.unpack_from(">HHH", data, 4)
        if major < 45 or major > 100 or count == 0:
            return False
        offset = 10
        pool = [None] * count
        index = 1
        while index < count:
            tag = data[offset]
            offset += 1
            if tag == 1:
                length = struct.unpack_from(">H", data, offset)[0]
                offset += 2 + length
                pool[index] = (tag,)
            elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
                first, second = struct.unpack_from(">HH", data, offset)
                offset += 4
                pool[index] = (tag, first, second)
            elif tag in (5, 6):
                offset += 8
                pool[index] = (tag,)
                index += 1
            elif tag in (7, 8, 16, 19, 20):
                value = struct.unpack_from(">H", data, offset)[0]
                offset += 2
                pool[index] = (tag, value)
            elif tag == 15:
                kind, reference = struct.unpack_from(">BH", data, offset)
                offset += 3
                pool[index] = (tag, kind, reference)
            else:
                return False
            if offset > len(data):
                return False
            index += 1

        def has_tag(pool_index, *tags):
            return (0 < pool_index < len(pool) and pool[pool_index] is not None and
                    pool[pool_index][0] in tags)

        for entry in pool[1:]:
            if entry is None:
                continue
            tag = entry[0]
            if tag == 7 and not has_tag(entry[1], 1):
                return False
            if tag == 8 and not has_tag(entry[1], 1):
                return False
            if tag in (9, 10, 11) and not (has_tag(entry[1], 7) and has_tag(entry[2], 12)):
                return False
            if tag == 12 and not (has_tag(entry[1], 1) and has_tag(entry[2], 1)):
                return False
            if tag == 15 and (entry[1] < 1 or entry[1] > 9 or
                              not has_tag(entry[2], 9, 10, 11)):
                return False
            if tag == 16 and not has_tag(entry[1], 1):
                return False
            if tag in (17, 18) and not has_tag(entry[2], 12):
                return False
            if tag in (19, 20) and not has_tag(entry[1], 1):
                return False
        # access_flags, this_class, super_class, interfaces_count
        _access, this_class, _super_class, interfaces = struct.unpack_from(">HHHH", data, offset)
        if not has_tag(this_class, 7) or (_super_class and not has_tag(_super_class, 7)):
            return False
        offset += 8 + interfaces * 2
        interface_values = struct.unpack_from(">" + "H" * interfaces, data, offset - interfaces * 2) if interfaces else ()
        if any(not has_tag(value, 7) for value in interface_values):
            return False
        for _ in range(2):  # fields_count and methods_count with members
            count_members = struct.unpack_from(">H", data, offset)[0]
            offset += 2
            for _member in range(count_members):
                _flags, name_index, descriptor_index, attributes = struct.unpack_from(">HHHH", data, offset)
                if not has_tag(name_index, 1) or not has_tag(descriptor_index, 1):
                    return False
                offset += 8
                for _attribute in range(attributes):
                    _name = struct.unpack_from(">H", data, offset)[0]
                    length = struct.unpack_from(">I", data, offset + 2)[0]
                    if not has_tag(_name, 1):
                        return False
                    offset += 6 + length
                    if offset > len(data):
                        return False
        attributes = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        for _attribute in range(attributes):
            name_index = struct.unpack_from(">H", data, offset)[0]
            length = struct.unpack_from(">I", data, offset + 2)[0]
            if not has_tag(name_index, 1):
                return False
            offset += 6 + length
            if offset > len(data):
                return False
        return offset == len(data)
    except (IndexError, struct.error):
        return False


def require_unique_archive_member_names(infos) -> None:
    seen = set()
    duplicates = set()
    for info in infos:
        if info.filename in seen:
            duplicates.add(info.filename)
        seen.add(info.filename)
    if duplicates:
        raise ValueError("IPA contains duplicate ZIP member names: " + ", ".join(sorted(duplicates)[:8]))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_ipa_size(ipa_size_bytes: int, limits: dict | None = None) -> None:
    policy = dict(DEFAULT_ARCHIVE_LIMITS)
    if limits is not None:
        policy.update(limits)
    if ipa_size_bytes < 0 or ipa_size_bytes > policy["compressed_ipa_bytes"]:
        raise ValueError("compressed IPA exceeds the configured size limit")


def validate_archive_metadata(ipa_size_bytes: int, infos, limits: dict | None = None) -> None:
    """Reject oversized ZIP metadata before any member is decompressed."""
    policy = dict(DEFAULT_ARCHIVE_LIMITS)
    if limits is not None:
        policy.update(limits)
    validate_ipa_size(ipa_size_bytes, policy)
    if len(infos) > policy["member_count"]:
        raise ValueError("IPA exceeds the configured ZIP member-count limit")
    total_uncompressed = 0
    for info in infos:
        if info.file_size < 0 or info.compress_size < 0:
            raise ValueError(f"ZIP member has invalid size metadata: {info.filename}")
        if info.file_size > policy["member_uncompressed_bytes"]:
            raise ValueError(f"ZIP member exceeds the configured expanded-size limit: {info.filename}")
        total_uncompressed += info.file_size
        if total_uncompressed > policy["total_uncompressed_bytes"]:
            raise ValueError("IPA exceeds the configured total expanded-size limit")
        if info.file_size:
            if info.compress_size == 0 or info.file_size > info.compress_size * policy["compression_ratio"]:
                raise ValueError(f"ZIP member exceeds the configured compression-ratio limit: {info.filename}")


def preflight_zip_directory(path: Path, ipa_size_bytes: int, limits: dict | None = None) -> int:
    """Validate EOCD and count central-directory entries without building ZipInfo objects."""
    policy = dict(DEFAULT_ARCHIVE_LIMITS)
    if limits is not None:
        policy.update(limits)
    validate_ipa_size(ipa_size_bytes, policy)
    member_limit = policy["member_count"]
    if member_limit <= 0 or member_limit >= 0xFFFF:
        raise ValueError("ZIP member-count limit must be between 1 and the classic ZIP maximum")
    if ipa_size_bytes < 22:
        raise ValueError("IPA is too short to contain a ZIP end record")

    tail_length = min(ipa_size_bytes, 22 + 0xFFFF)
    with path.open("rb") as source:
        source.seek(ipa_size_bytes - tail_length)
        tail = source.read(tail_length)
        eocd_offset_in_tail = tail.rfind(b"PK\x05\x06")
        while eocd_offset_in_tail >= 0:
            if eocd_offset_in_tail + 22 <= len(tail):
                comment_size = struct.unpack_from("<H", tail, eocd_offset_in_tail + 20)[0]
                if eocd_offset_in_tail + 22 + comment_size == len(tail):
                    break
            eocd_offset_in_tail = tail.rfind(b"PK\x05\x06", 0, eocd_offset_in_tail)
        if eocd_offset_in_tail < 0:
            raise ValueError("IPA ZIP end record is missing or malformed")

        eocd_offset = ipa_size_bytes - tail_length + eocd_offset_in_tail
        (_signature, disk_number, directory_disk, entries_on_disk, entry_count,
         directory_size, directory_offset, _comment_size) = struct.unpack_from(
             "<4s4H2LH", tail, eocd_offset_in_tail)
        if disk_number or directory_disk or entries_on_disk != entry_count:
            raise ValueError("multi-disk ZIP archives are unsupported")
        if (entry_count == 0xFFFF or directory_size == 0xFFFFFFFF or
                directory_offset == 0xFFFFFFFF):
            raise ValueError("ZIP64 archives are unsupported by the configured size limits")
        if entry_count > member_limit:
            raise ValueError("IPA exceeds the configured ZIP member-count limit")

        directory_end = eocd_offset
        if directory_size > policy["central_directory_bytes"]:
            raise ValueError("IPA central directory exceeds the configured size limit")
        if directory_size > directory_end:
            raise ValueError("IPA central directory is outside the archive")
        directory_start = directory_end - directory_size
        # ZIP permits a prepended stub; infer its size from the recorded offset.
        prefix_size = directory_start - directory_offset
        if prefix_size < 0:
            raise ValueError("IPA central directory offset is outside the archive")

        if eocd_offset >= 20:
            source.seek(eocd_offset - 20)
            locator = source.read(20)
            if locator[:4] == b"PK\x06\x07":
                raise ValueError("ZIP64 archives are unsupported by the configured size limits")

        position = directory_start
        counted = 0
        while position < directory_end:
            if counted >= member_limit:
                raise ValueError("IPA exceeds the configured ZIP member-count limit")
            source.seek(position)
            fixed = source.read(46)
            if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
                raise ValueError("IPA central directory contains a malformed entry")
            fields = struct.unpack_from("<4s6H3I5H2I", fixed)
            (_signature, _made_by, _needed, _flags, _method, _mtime, _mdate,
             _crc, compressed_size, expanded_size, name_size, extra_size,
             comment_size, disk_start, _internal_attributes, _external_attributes,
             _local_header_offset) = fields
            entry_size = 46 + name_size + extra_size + comment_size
            if entry_size > directory_end - position:
                raise ValueError("IPA central-directory entry exceeds its bounds")
            if disk_start:
                raise ValueError("multi-disk ZIP archives are unsupported")
            if (compressed_size == 0xFFFFFFFF or expanded_size == 0xFFFFFFFF or
                    disk_start == 0xFFFF or _local_header_offset == 0xFFFFFFFF):
                raise ValueError("ZIP64 archives are unsupported by the configured size limits")
            source.seek(position + 46 + name_size)
            extra = source.read(extra_size)
            if len(extra) != extra_size:
                raise ValueError("IPA central-directory extra data is truncated")
            extra_position = 0
            while extra_position < len(extra):
                if extra_position + 4 > len(extra):
                    raise ValueError("IPA central-directory extra field is malformed")
                extra_id, field_size = struct.unpack_from("<HH", extra, extra_position)
                extra_position += 4
                if field_size > len(extra) - extra_position:
                    raise ValueError("IPA central-directory extra field exceeds its bounds")
                if extra_id == 0x0001:
                    raise ValueError("ZIP64 archives are unsupported by the configured size limits")
                extra_position += field_size
            counted += 1
            position += entry_size
        if position != directory_end or counted != entry_count:
            raise ValueError("IPA central-directory count or size does not match its end record")
    return counted


def preflight_archive(archive, ipa_size_bytes: int, limits: dict | None = None):
    """Apply central-directory limits and duplicate checks before testzip reads payloads."""
    validate_ipa_size(ipa_size_bytes, limits)
    infos = archive.infolist()
    validate_archive_metadata(ipa_size_bytes, infos, limits)
    require_unique_archive_member_names(infos)
    bad_member = archive.testzip()
    if bad_member:
        raise ValueError(f"corrupt IPA member: {bad_member}")
    return infos


from patch_native_error_presenters import TOUCHED_PATHS as NATIVE_ERROR_SOURCE_PATHS

REQUIRED_GENERATED_HOST_SOURCES = {
    *NATIVE_ERROR_SOURCE_PATHS,
    'MultitaskSupport/AppSceneViewController.h',
    'MultitaskSupport/AppSceneViewController.m',
    'MultitaskSupport/DecoratedAppSceneViewController.m',
    "LiveContainer/Tweaks/Dead10ccFix.m",
    "LiveContainer/Tweaks/NSUserDefaults.m",
    'ZSign/zsigner.h',
    'ZSign/zsign.mm',
    'LiveContainerSwiftUI/Utilities/LCUtils.h',
    'LiveContainerSwiftUI/Utilities/LCUtils.m',
    "SideStoreSupport/SideStore.swift", "SideStoreSupport/SideStoreClient.swift",
    "SideStoreSupport/XPCServer.m", "SideStoreSupport/XPCServer.h",
    "LiveContainer/LCBootstrap.m", "LiveContainer/LCContainerStorage.h",
    "LiveContainerSwiftUI/App/AppDelegate.swift",
    "LiveContainerSwiftUI/Models/AppLayoutStyle.swift",
    "LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift",
    "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift",
    "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBanner.swift",
    "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerView.swift",
    "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift",
    ".lc-app-layout.json", ".combined-service-startup.json",
    "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
    "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift",
}
REQUIRED_GENERATED_EMBEDDED_SOURCES = {
    "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
    "SideStore/Core/Certificates/CertificateManager.swift",
    "AltStore/Core/Components/Keychain.swift",
    "SideStore/Core/Anisette/AnisetteConfigManager.swift",
    "SideStore/Core/Anisette/OnDeviceAnisetteManager.swift",
    "SideStore/Core/Anisette/AnisetteProvider.swift",
    "SideStore/Core/Certificates/CodeSignValidator.swift",
    "AltStore/AppDelegate.swift", "SideStore/Core/Operations/PipelineExecutor.swift",
    "SideStore/Core/Operations/PipelineRunner.swift",
    "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift",
    ".combined-refresh-contract.json",
    "Dependencies/minimuxer/DeviceGateway/idevice/IdeviceGateway.swift",
    "Dependencies/SideSign/Sources/DeveloperPortal/DeveloperPortalAPI.swift",
    "Dependencies/SideSign/Sources/DeveloperPortal/AppIDs.swift",
    "SideStore/Core/Auth/DeveloperPortalProxy.swift",
    "SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift",
    "SideStore/Core/Operations/PipelineOperations/PerformBackupRestoreOperation.swift",
    "SideBackup/SideBackupApp.swift",
}


def verify_isolated_anisette_binary(executable: bytes) -> None:
    # Require the active normal-provider staging correction. Automatic isolated
    # recovery is disabled, so its unused API may legitimately be dead-stripped;
    # retaining dormant code is neither necessary nor proof of this active fix.
    if (ANISETTE_COMPILED_MARKER not in executable or
            ANISETTE_COMPILED_LITERAL not in executable):
        raise ValueError("embedded SideStore is missing the compiled checked Anisette staging path")


def verify_isolated_anisette_evidence(evidence_root: Path, binding: dict,
                                     executable: bytes) -> None:
    from patch_anisette_isolated_otp import expected_evidence
    expected_manifest = expected_evidence()
    if (expected_manifest.get("marker") != ANISETTE_COMPILED_MARKER.decode("ascii") or
            expected_manifest.get("compiled_literal") != ANISETTE_COMPILED_LITERAL.decode("ascii")):
        raise ValueError("Anisette source manifest and active compiled staging gate disagree")
    expected_paths = {entry["path"] for entry in expected_manifest["files"]}
    if not isinstance(binding, dict) or set(binding) != {
            "manifest_sha256", "source_sha256", "executable_sha256"}:
        raise ValueError("isolated Anisette OTP evidence binding is missing or invalid")
    verify_isolated_anisette_binary(executable)
    if binding["executable_sha256"] != hashlib.sha256(executable).hexdigest():
        raise ValueError("isolated Anisette OTP evidence is bound to another executable")
    root = evidence_root / ANISETTE_EVIDENCE_DIRECTORY
    if root.is_symlink() or not root.is_dir():
        raise ValueError("isolated Anisette OTP source evidence is missing or linked")
    actual_paths = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("isolated Anisette OTP evidence contains a link")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths | {ANISETTE_EVIDENCE_MANIFEST}:
        raise ValueError("isolated Anisette OTP evidence inventory mismatch")
    manifest_data = (root / ANISETTE_EVIDENCE_MANIFEST).read_bytes()
    if binding["manifest_sha256"] != hashlib.sha256(manifest_data).hexdigest():
        raise ValueError("isolated Anisette OTP manifest hash mismatch")
    # Derive these exact hashes from the pinned originals and the reviewed
    # transformation, not from the copied manifest's own claims.
    if json.loads(manifest_data) != expected_manifest:
        raise ValueError("isolated Anisette OTP manifest differs from its pinned transformation")
    expected_hashes = {entry["path"]: entry["prepared_sha256"]
                       for entry in expected_manifest["files"]}
    if binding["source_sha256"] != expected_hashes:
        raise ValueError("isolated Anisette OTP source binding differs from its pinned transformation")
    for name, expected_hash in expected_hashes.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("isolated Anisette OTP prepared source hash mismatch: " + name)


def verify_generated_source_evidence(evidence_root: Path, hashes: dict,
                                    product: str = "v3") -> None:
    required = set(REQUIRED_GENERATED_HOST_SOURCES)
    if product in ("v2",):
        required.difference_update(set(NATIVE_ERROR_SOURCE_PATHS) - {
            "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift",
        })
        required.difference_update({
            'MultitaskSupport/AppSceneViewController.h',
            'MultitaskSupport/AppSceneViewController.m',
            'MultitaskSupport/DecoratedAppSceneViewController.m',
            'LiveContainer/Tweaks/Dead10ccFix.m',
            'LiveContainer/Tweaks/NSUserDefaults.m',
            "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
            "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift",
            'ZSign/zsigner.h',
            'ZSign/zsign.mm',
            'LiveContainerSwiftUI/Utilities/LCUtils.h',
            'LiveContainerSwiftUI/Utilities/LCUtils.m',
        })
    required.update("embedded/" + name for name in REQUIRED_GENERATED_EMBEDDED_SOURCES)
    if set(hashes) != required:
        missing = sorted(required - set(hashes))
        extra = sorted(set(hashes) - required)
        raise ValueError(f"generated source evidence inventory mismatch (missing={missing}, extra={extra})")
    for name, expected in hashes.items():
        if not isinstance(name, str) or not isinstance(expected, str) or \
                not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("generated source evidence hashes are malformed")
        if name.startswith("embedded/"):
            root = evidence_root / "embedded-generated"
            relative = Path(name[len("embedded/"):])
        else:
            root = evidence_root / "generated"
            relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("generated source evidence path is unsafe")
        root_resolved = root.resolve()
        path = (root / relative).resolve()
        try:
            remains_under_root = Path(os.path.commonpath((str(root_resolved), str(path)))) == root_resolved
        except ValueError:
            remains_under_root = False
        if not remains_under_root or not path.is_file():
            raise ValueError(f"generated source evidence file is missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"generated source evidence hash mismatch: {name}")
    for directory, prefix in ((evidence_root / "generated", ""),
                              (evidence_root / "embedded-generated", "embedded/")):
        actual = {prefix + path.relative_to(directory).as_posix()
                  for path in directory.rglob("*") if path.is_file()} if directory.exists() else set()
        expected = ({name for name in hashes if not name.startswith("embedded/")}
                    if not prefix else {name for name in hashes if name.startswith(prefix)})
        if actual != expected:
            raise ValueError("generated source evidence files do not match the provenance inventory")


def preserved_dsym_evidence(evidence_root: Path, packaged_uuids: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    found = {}
    hashes = {}
    for root_name in ("host", "embedded"):
        root = evidence_root / root_name
        if not root.is_dir():
            continue
        for dsym in sorted(root.rglob("*.dSYM")):
            dwarf_root = dsym / "Contents" / "Resources" / "DWARF"
            if not dwarf_root.is_dir():
                continue
            for dwarf in sorted(dwarf_root.iterdir()):
                if not dwarf.is_file():
                    continue
                relative = dwarf.relative_to(evidence_root).as_posix()
                data = dwarf.read_bytes()
                hashes[relative] = hashlib.sha256(data).hexdigest()
                try:
                    uuids = macho_uuids(data)
                except ValueError:
                    continue
                for value in uuids.values():
                    if value not in packaged_uuids:
                        continue
                    previous = found.get(dwarf.name)
                    if previous is not None and previous != value:
                        raise ValueError(f"ambiguous dSYM UUID evidence for {dwarf.name}")
                    found[dwarf.name] = value
    return found, hashes


def preserved_dsym_uuids(evidence_root: Path, packaged_uuids: set[str]) -> dict[str, str]:
    return preserved_dsym_evidence(evidence_root, packaged_uuids)[0]


def is_github_actions_run_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and parsed.netloc == "github.com" and
            re.fullmatch(r"/NRG-Wardog/sidestore-auto-refresh/actions/runs/[0-9]+", parsed.path) is not None and
            not parsed.query and not parsed.fragment)


def has_required_livecontainer_groups(groups) -> bool:
    return REQUIRED_LIVECONTAINER_GROUPS.issubset(set(groups or []))


def verify_service_app_group_ownership(host_groups, live_process_groups,
                                       configured_groups, live_process_configured_groups) -> list[str]:
    """The SideStore service runs in LiveProcess, not in its framework signature.

    LC forwards any host-selected, container-resolving group to LiveProcess.
    Every group the host can select must therefore be entitled in that process
    as well. Info.plist declarations alone do not grant container access.

    Both processes also keep a packaged ALTAppGroups list as the fallback for a
    launch that published no group. If the two lists ranked different groups, a
    launch without a published group would put the host and the service in two
    different shared stores, so each list must itself be entitled in both
    processes.
    """
    host = set(host_groups or [])
    service = set(live_process_groups or [])
    configured = set(configured_groups or [])
    if not has_required_livecontainer_groups(host) or not has_required_livecontainer_groups(service):
        raise ValueError("host and LiveProcess must both be entitled for the shared App Groups")
    if not configured or not configured.issubset(host & service):
        raise ValueError("runtime configured App Group is not entitled in both host and LiveProcess")
    if host != service:
        raise ValueError("host-selectable App Groups differ from LiveProcess service entitlements")
    service_configured = set(live_process_configured_groups or ())
    if not service_configured or not service_configured.issubset(host & service):
        raise ValueError("LiveProcess packaged App Group fallback is not entitled in both processes")
    return sorted(host)


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
        if "/usr/lib/swift/" in name or (basename.startswith("libswift") and suffix == ".dylib"):
            category = "swift_runtime_dylibs"
        elif name in executable_paths:
            category = "executables"
        elif suffix in {".ipa", ".zip"}:
            category = "nested_archives"
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
    found = [name for name in REMOVED_SIDESTORE_UI_SYMBOLS
             if contains_side_store_swift_type(executable, name)]
    for member in RETIRED_CONNECTION_CONFIG_MEMBERS:
        if any(f"{len(module)}{module}16ConnectionConfigC{len(member)}{member}".encode("utf-8")
               in executable for module in ("SideStore", "AltStore")):
            found.append("ConnectionConfig." + member)
    return found


def contains_side_store_swift_type(executable: bytes, name: str) -> bool:
    # Do not substring-match UIKit/Nuke types such as UIDocumentPickerViewController,
    # UITabBarController, UINavigationBarAppearance, UIActivityViewController, or
    # Nuke.RoundedCorners. Require the SideStore/AltStore Swift module qualifier.
    for module in ("SideStore", "AltStore"):
        mangled = f"{len(module)}{module}{len(name)}{name}".encode("utf-8")
        qualified = f"{module}.{name}".encode("utf-8")
        if mangled in executable or qualified in executable:
            return True
    return False


def excluded_side_store_view_type_names(side_source: Path,
                                        view_files=HEADLESS_SIDESTORE_VIEW_FILES,
                                        source_ref: str | None = None,
                                        additional_source_roots: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    if not source_ref:
        raise ValueError("pinned SideStore revision is required for headless source analysis")
    files_by_root = {"SideStore": tuple(view_files)}
    for root, files in (additional_source_roots or {}).items():
        files_by_root[root] = tuple(files)
    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(side_source), "ls-tree", "-r", "--name-only", source_ref,
             "--", *files_by_root],
            text=True, stderr=subprocess.PIPE).splitlines()
    except subprocess.CalledProcessError as error:
        raise ValueError("pinned SideStore source inventory could not be read") from error
    swift_paths = [path for path in tracked if path.endswith(".swift")]
    by_git_path: dict[str, str] = {}
    for path in swift_paths:
        try:
            by_git_path[path] = subprocess.check_output(
                ["git", "-C", str(side_source), "show", f"{source_ref}:{path}"],
                text=True, encoding="utf-8", stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as error:
            raise ValueError(f"pinned SideStore source could not be read: {path}") from error
    removed_types: set[str] = set()
    excluded_paths = set()
    for root, files in files_by_root.items():
        for relative in files:
            git_path = f"{root}/{relative}"
            source = by_git_path.get(git_path)
            if source is None:
                raise ValueError(f"headless SideStore UI source is missing from the pinned tree: {git_path}")
            removed_types.update(SWIFT_TYPE_DECLARATION.findall(source))
            excluded_paths.add(git_path)
    retained_types = set()
    for git_path, source in by_git_path.items():
        if git_path not in excluded_paths:
            retained_types.update(SWIFT_TYPE_DECLARATION.findall(source))
    # ConnectionConfig keeps its upstream type name because Minimuxer uses its
    # backend API. The former SwiftUI model was removed, then the exact
    # transport-only definition was generated under Core/DeviceApi. A raw
    # symbol-name check cannot distinguish those two definitions.
    retired_connection = side_source / "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift"
    backend_connection = side_source / "SideStore/Core/DeviceApi/ConnectionConfig.swift"
    if backend_connection.is_file():
        expected_retired = ("// V3_HEADLESS_CONNECTION_CONFIG_MOVED_V1: transport settings now live "
                            "in Core/DeviceApi/ConnectionConfig.swift.\n")
        if (not retired_connection.is_file() or
                retired_connection.read_text(encoding="utf-8") != expected_retired or
                backend_connection.read_text(encoding="utf-8") != HEADLESS_BACKEND_CONNECTION_CONFIG + "\n"):
            raise ValueError("generated backend ConnectionConfig differs from the headless source contract")
        retained_types.add("ConnectionConfig")
    return sorted(removed_types - retained_types - {"Color"})


def missing_excluded_ui_symbols(executable: bytes, expected_symbols: list[str]) -> list[str]:
    return sorted(name for name in expected_symbols if contains_side_store_swift_type(executable, name))


def verify_no_excluded_side_store_ui(executable: bytes, expected_symbols: list[str]) -> None:
    legacy_ui = find_legacy_side_store_ui_symbols(executable)
    legacy_view_types = missing_excluded_ui_symbols(executable, expected_symbols)
    legacy_ui = sorted(set(legacy_ui + legacy_view_types))
    if legacy_ui:
        raise ValueError("embedded SideStore still contains excluded presenter UI: "
                         + ", ".join(legacy_ui))


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
           side_source: Path | None = None, expected_builder_commit: str | None = None,
           expected_run_url: str | None = None) -> dict:
    if expected_builder_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_builder_commit):
        raise ValueError("expected builder commit must be a full lowercase Git SHA")
    if expected_run_url is not None and not is_github_actions_run_url(expected_run_url):
        raise ValueError("expected workflow run URL is invalid")
    if side_source is None or not side_source.is_dir():
        raise ValueError("the pinned SideStore source checkout is required for headless verification")
    try:
        side_source_sha = subprocess.check_output(
            ["git", "-C", str(side_source), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.PIPE).strip()
    except subprocess.CalledProcessError as error:
        raise ValueError("the SideStore source checkout has no readable Git revision") from error
    if side_source_sha != SOURCE_PINS[1]:
        raise ValueError("SideStore source checkout does not match the pinned revision")
    size = ipa.stat().st_size
    validate_ipa_size(size)
    preflight_zip_directory(ipa, size)
    digest = sha256_file(ipa)
    side_store_asset_report = {}
    with zipfile.ZipFile(ipa) as archive:
        archive_infos = preflight_archive(archive, size)
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
        host_code = archive.read(BASE + "/Frameworks/LiveContainerSwiftUI.framework/LiveContainerSwiftUI")
        support_path = BASE + "/Frameworks/SideStoreSupport.framework"
        support_bundle = package_bundles[support_path]
        support_executable = support_path + "/" + support_bundle["info"]["CFBundleExecutable"]
        support_code = archive.read(support_executable)
        verify_auth_answer_transport(host_code, side_store_executable_data, support_code)
        legacy_intents = find_legacy_side_store_intent_symbols(side_store_executable_data)
        if legacy_intents:
            raise ValueError("embedded SideStore still contains legacy app intent code: "
                             + ", ".join(legacy_intents))
        headless_view_symbols = excluded_side_store_view_type_names(
            side_source,
            HEADLESS_SIDESTORE_VIEW_FILES + HEADLESS_SIDESTORE_AUX_UI_FILES +
            HEADLESS_SIDESTORE_HANDLER_UI_FILES,
            source_ref=SOURCE_PINS[1],
            additional_source_roots={"AltStore": HEADLESS_SIDESTORE_PIPELINE_UI_FILES})
        verify_no_excluded_side_store_ui(side_store_executable_data, headless_view_symbols)
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
        live_process_path = BASE + "/PlugIns/LiveProcess.appex"
        live_process = package_bundles.get(live_process_path)
        if not live_process or not live_process.get("executable_present"):
            raise ValueError("LiveProcess extension or executable is missing")
        live_process_groups = (live_process.get("signing") or {}).get("xml_entitlements") or {}
        service_app_groups = verify_service_app_group_ownership(
            host_groups.get("com.apple.security.application-groups", []),
            live_process_groups.get("com.apple.security.application-groups", []),
            info.get("ALTAppGroups", []),
            (live_process.get("info") or {}).get("ALTAppGroups", []))
        shared_keychain_group = verify_shared_secret_handoff_group(
            host_groups.get("keychain-access-groups"),
            live_process_groups.get("keychain-access-groups"))
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

        bundle_executable_paths = []
        for path, bundle in package_bundles.items():
            if path == BASE or path.startswith(BASE + "/PlugIns/") or \
                    (path.startswith(BASE + "/Frameworks/") and path.endswith(".framework")):
                if bundle.get("executable_present"):
                    bundle_executable_paths.append(path + "/" + bundle["info"]["CFBundleExecutable"])
        executable_paths = mach_o_paths(archive, archive_infos)
        missing_bundle_executables = sorted(set(bundle_executable_paths) - executable_paths)
        if missing_bundle_executables:
            raise ValueError("bundle executable is not a recognized Mach-O image: "
                             + ", ".join(missing_bundle_executables))
        arch_report = {}
        binary_uuid_report = {}
        binary_subtype_report = {}
        for path in sorted(set(executable_paths)):
            image = archive.read(path)
            image_uuids = require_arm64_all_image(image)
            archs = architectures(image)
            arch_report[path] = sorted(archs)
            if "arm64" not in image_uuids:
                raise ValueError(f"arm64 Mach-O UUID is missing: {path}")
            binary_uuid_report[path] = image_uuids["arm64"]
            binary_subtype_report[path] = macho_cpu_subtypes(image)["arm64"]
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
    if expected_builder_commit is not None and info.get("LCBuilderCommit") != expected_builder_commit:
        raise ValueError("candidate builder SHA does not match the exact workflow commit")
    build_run_url = provenance.get("LCBuildRunURL")
    if (not is_github_actions_run_url(build_run_url) or
            build_run_url != info.get("LCBuildRunURL")):
        raise ValueError("provenance build run URL does not match the embedded GitHub Actions run")
    if expected_run_url is not None and build_run_url != expected_run_url:
        raise ValueError("candidate workflow run URL does not match this Actions run")
    framework_uuids = provenance.get("framework_uuids")
    if not isinstance(framework_uuids, dict) or framework_uuids != binary_uuid_report:
        raise ValueError("provenance Mach-O UUID inventory does not match every packaged executable")
    if provenance.get("framework_cpu_subtypes") != binary_subtype_report:
        raise ValueError("provenance Mach-O CPU subtype inventory does not match every packaged executable")
    dsym_uuids = provenance.get("dsym_uuids")
    if not isinstance(dsym_uuids, dict) or not dsym_uuids:
        raise ValueError("matching dSYM UUID evidence is missing")
    packaged_uuids = set(binary_uuid_report.values())
    if any(not isinstance(value, str) or value not in packaged_uuids
           for value in dsym_uuids.values()):
        raise ValueError("dSYM UUID evidence does not match a packaged executable")
    support_uuid = binary_uuid_report.get(
        BASE + "/Frameworks/SideStoreSupport.framework/SideStoreSupport")
    if not support_uuid or dsym_uuids.get("SideStoreSupport") != support_uuid:
        raise ValueError("matching SideStoreSupport dSYM UUID is missing")
    generated_hashes = provenance.get("generated_source_sha256")
    if (not isinstance(generated_hashes, dict) or not generated_hashes or
            any(not isinstance(name, str) or not isinstance(value, str) or
                not re.fullmatch(r"[0-9a-f]{64}", value)
                for name, value in generated_hashes.items())):
        raise ValueError("generated source evidence hashes are missing or invalid")
    verify_generated_source_evidence(provenance_path.parent, generated_hashes, product)
    if product != "v2":
        verify_isolated_anisette_evidence(provenance_path.parent,
            provenance.get("isolated_anisette_otp"), side_store_executable_data)
    actual_dsym_uuids, actual_dsym_hashes = preserved_dsym_evidence(provenance_path.parent, packaged_uuids)
    if actual_dsym_uuids != dsym_uuids:
        raise ValueError("preserved dSYM contents do not match provenance UUID evidence")
    if provenance.get("dsym_sha256") != actual_dsym_hashes:
        raise ValueError("preserved dSYM DWARF files do not match provenance SHA-256 evidence")
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
        "cpu_subtypes": binary_subtype_report,
        "macho_uuid_count": len(binary_uuid_report),
        "matching_dsym_count": len(dsym_uuids),
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
        "secret_handoff_keychain_group": shared_keychain_group,
        "livecontainer_app_groups": sorted(REQUIRED_LIVECONTAINER_GROUPS),
        "sidestore_service_process_app_groups": service_app_groups,
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
    parser.add_argument("--side-source", required=True, type=Path)
    parser.add_argument("--builder-commit", required=True)
    parser.add_argument("--build-run-url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.ipa, args.provenance, args.product, side_source=args.side_source,
                    expected_builder_commit=args.builder_commit, expected_run_url=args.build_run_url)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
