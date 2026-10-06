"""Unsigned candidate identity and matching, non-runtime crash evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_candidate_ipa import (MACHO_MAGICS, is_java_class_file, macho_cpu_subtypes,
                                 macho_uuids, require_arm64_all_image,
                                 DEFAULT_ARCHIVE_LIMITS, preflight_archive,
                                 preflight_zip_directory, validate_ipa_size)


HOST_SOURCE_PATHS = [
    'SideStoreSupport/SideStore.swift', 'SideStoreSupport/SideStoreClient.swift',
    'SideStoreSupport/XPCServer.m', 'SideStoreSupport/XPCServer.h', 'LiveContainer/LCBootstrap.m',
    'LiveContainer/LCContainerStorage.h', 'LiveContainerSwiftUI/App/AppDelegate.swift',
    'LiveContainerSwiftUI/Models/AppLayoutStyle.swift',
    'LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift',
    'LiveContainerSwiftUI/Views/AppList/LCAppListView.swift',
    *['LiveContainerSwiftUI/Views/AppList/LCAppBanner/' + name for name in
      ('LCAppBanner.swift', 'LCAppBannerView.swift', 'LCAppBannerViewController.swift')],
    '.lc-app-layout.json', '.combined-service-startup.json',
]
from patch_native_error_presenters import TOUCHED_PATHS as NATIVE_ERROR_SOURCE_PATHS

V3_HOST_SOURCE_PATHS = [
    *NATIVE_ERROR_SOURCE_PATHS,
    'MultitaskSupport/AppSceneViewController.h',
    'MultitaskSupport/AppSceneViewController.m',
    'MultitaskSupport/DecoratedAppSceneViewController.m',
    'LiveContainer/Tweaks/Dead10ccFix.m',
    'LiveContainer/Tweaks/NSUserDefaults.m',
    'ZSign/zsigner.h',
    'ZSign/zsign.mm',
    'LiveContainerSwiftUI/Utilities/LCUtils.h',
    'LiveContainerSwiftUI/Utilities/LCUtils.m',
    'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
    'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift',
]
EMBEDDED_SOURCE_PATHS = [
    'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift',
    'SideStore/Core/Certificates/CertificateManager.swift',
    'SideStore/Core/Certificates/CodeSignValidator.swift',
    'AltStore/AppDelegate.swift', 'SideStore/Core/Operations/PipelineExecutor.swift',
    'SideStore/Core/Operations/PipelineRunner.swift',
    'SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift',
    '.combined-refresh-contract.json',
    'Dependencies/minimuxer/DeviceGateway/idevice/IdeviceGateway.swift',
    'Dependencies/SideSign/Sources/DeveloperPortal/DeveloperPortalAPI.swift',
    'Dependencies/SideSign/Sources/DeveloperPortal/AppIDs.swift',
    'SideStore/Core/Auth/DeveloperPortalProxy.swift',
    'SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift',
    'SideStore/Core/Operations/PipelineOperations/PerformBackupRestoreOperation.swift',
    'SideBackup/SideBackupApp.swift',
]


def macho_uuid(data):
    uuids = macho_uuids(data)
    return next(iter(uuids.values()), None)


def file_signature(stat_result):
    """Return stable file identity and timestamp fields for path-replacement checks."""
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_size,
            stat_result.st_mtime_ns, stat_result.st_ctime_ns)


def snapshot_ipa_file(path, limits=None):
    """Copy and hash one bounded open-file stream for later ZIP inspection."""
    policy = dict(DEFAULT_ARCHIVE_LIMITS)
    if limits is not None:
        policy.update(limits)
    max_size = policy["compressed_ipa_bytes"]
    digest = hashlib.sha256()
    bytes_read = 0
    chunk_size = 1024 * 1024
    snapshot_directory = tempfile.TemporaryDirectory(prefix='combined-ipa-snapshot-')
    snapshot_path = Path(snapshot_directory.name) / 'candidate.ipa'
    try:
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            validate_ipa_size(before.st_size, policy)
            with snapshot_path.open('wb') as snapshot:
                while True:
                    chunk = source.read(min(chunk_size, max_size - bytes_read + 1))
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    if bytes_read > max_size:
                        raise ValueError("compressed IPA exceeds the configured size limit")
                    snapshot.write(chunk)
                    digest.update(chunk)
            after = os.fstat(source.fileno())
        if bytes_read != before.st_size:
            raise ValueError("IPA size changed while hashing")
        if file_signature(before) != file_signature(after):
            raise ValueError("IPA changed while hashing")
        return snapshot_directory, snapshot_path, bytes_read, digest.hexdigest(), file_signature(after)
    except Exception:
        snapshot_directory.cleanup()
        raise


def hash_ipa_file(path, limits=None):
    """Rehash the candidate with bounded reads for the final provenance check."""
    policy = dict(DEFAULT_ARCHIVE_LIMITS)
    if limits is not None:
        policy.update(limits)
    max_size = policy['compressed_ipa_bytes']
    digest = hashlib.sha256()
    bytes_read = 0
    chunk_size = 1024 * 1024
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        validate_ipa_size(before.st_size, policy)
        while True:
            chunk = source.read(min(chunk_size, max_size - bytes_read + 1))
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > max_size:
                raise ValueError("compressed IPA exceeds the configured size limit")
            digest.update(chunk)
        after = os.fstat(source.fileno())
    if bytes_read != before.st_size:
        raise ValueError("IPA size changed while hashing")
    if file_signature(before) != file_signature(after):
        raise ValueError("IPA changed while hashing")
    return bytes_read, digest.hexdigest()


def require_unchanged_ipa_path(path, expected_signature):
    """Fail if collection's original path no longer names the snapshotted IPA."""
    try:
        actual_signature = file_signature(path.stat())
    except OSError as error:
        raise ValueError("IPA changed after evidence snapshot") from error
    if actual_signature != expected_signature:
        raise ValueError("IPA changed after evidence snapshot")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['identity', 'collect'])
    parser.add_argument('--product', required=True)
    parser.add_argument('--ipa', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--side-source', type=Path)
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
    if args.product not in ('v2', 'v3') and not re.fullmatch(r'v3\.\d+(\.\d+)*(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?', args.product):
        parser.error("argument --product: invalid choice (choose from 'v2', 'v3', or a 'v3.x[.y][-candidate]' release line)")
    commit = os.environ['GITHUB_SHA']
    if not re.fullmatch('[0-9a-f]{40}', commit): raise ValueError('immutable builder SHA required')
    run = 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + os.environ['GITHUB_RUN_ID']
    identity = {'LCProductLine': 'Combined LC+SS ' + args.product, 'LCBuilderCommit': commit, 'LCBuildRunURL': run}
    if args.mode == 'identity':
        for app in args.paths:
            path = app / 'Info.plist'
            info = plistlib.loads(path.read_bytes()); info.update(identity)
            path.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
        return
    # Reject an oversized package before archive inspection or evidence writes.
    snapshot_directory, snapshot_path, ipa_size, ipa_sha256, ipa_signature = snapshot_ipa_file(args.ipa)
    try:
        # Reuse the verifier's EOCD and central-directory byte/count scan
        # against the exact private snapshot that was hashed.
        preflight_zip_directory(snapshot_path, ipa_size)
        args.output.mkdir(parents=True, exist_ok=True)
        # Ensure repeated collection cannot retain stale files from an earlier run.
        for name in ('host', 'embedded', 'generated', 'embedded-generated'):
            target = args.output / name
            if target.exists(): shutil.rmtree(target)
        provenance_path = args.output / 'candidate-provenance.json'
        if provenance_path.exists(): provenance_path.unlink()
        binaries = {}
        binary_subtypes = {}
        with zipfile.ZipFile(snapshot_path) as archive:
            # Validate central metadata, expanded member limits and duplicates
            # before testzip decompresses or any later archive.read() occurs.
            archive_infos = preflight_archive(archive, ipa_size)
            for info_entry in archive_infos:
                name = info_entry.filename
                if name.endswith('/'): continue
                with archive.open(name) as member:
                    magic = member.read(4)
                if magic not in MACHO_MAGICS: continue
                data = archive.read(name)
                if magic == b'\xca\xfe\xba\xbe' and is_java_class_file(data): continue
                values = macho_uuids(data)
                if values:
                    require_arm64_all_image(data)
                    binaries[name] = values['arm64']
                    binary_subtypes[name] = macho_cpu_subtypes(data)['arm64']
            info = plistlib.loads(archive.read('Payload/LiveContainer.app/Info.plist'))
            assert all(info.get(key) == value for key, value in identity.items()), 'packaged identity mismatch'
            for executable in ('SideStoreSupport.framework/SideStoreSupport', 'SideStoreApp.framework/SideStore'):
                data = archive.read('Payload/LiveContainer.app/Frameworks/' + executable)
                marker = b'LCFAILURE1:' if executable.startswith('SideStoreSupport') else b'LCStructuredFailureStageV1'
                assert marker in data, 'structured error protocol absent: ' + executable
                if executable.startswith('SideStoreApp'):
                    assert b'UNIQUE_DEVICE_ID_QUERY_FAIL' in data, 'Issue 24 query diagnostics absent'
                    assert b'lc_stage=uniqueDeviceID' in data, 'Issue 24 structured category absent'
        require_unchanged_ipa_path(args.ipa, ipa_signature)
    finally:
        snapshot_directory.cleanup()
    symbols = {}
    symbol_hashes = {}
    for index, root in enumerate(args.paths):
        for dsym in root.glob('*.dSYM'):
            dwarf_root = dsym / 'Contents/Resources/DWARF'
            if not dwarf_root.is_dir(): continue
            dwarf_files = sorted(path for path in dwarf_root.iterdir() if path.is_file())
            matches = {}
            for dwarf in dwarf_files:
                for value in macho_uuids(dwarf.read_bytes()).values():
                    if value in binaries.values(): matches[dwarf.name] = value
            if matches:
                location = 'host' if index == 0 else 'embedded'
                destination = args.output / location / dsym.name
                shutil.copytree(dsym, destination, dirs_exist_ok=True)
                symbols.update(matches)
                for dwarf in dwarf_files:
                    data = dwarf.read_bytes()
                    evidence_path = location + '/' + dsym.name + '/Contents/Resources/DWARF/' + dwarf.name
                    symbol_hashes[evidence_path] = hashlib.sha256(data).hexdigest()
    support = binaries['Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport']
    assert support in symbols.values(), 'matching SideStoreSupport dSYM required'
    generated = {}
    if args.source:
        paths = HOST_SOURCE_PATHS + (V3_HOST_SOURCE_PATHS if args.product == 'v3' or args.product.startswith('v3.') else [])
        for name in paths:
            data = (args.source / name).read_bytes()
            target = args.output / 'generated' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            generated[name] = hashlib.sha256(data).hexdigest()
    if args.side_source:
        for name in EMBEDDED_SOURCE_PATHS:
            data = (args.side_source / name).read_bytes()
            target = args.output / 'embedded-generated' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            generated['embedded/' + name] = hashlib.sha256(data).hexdigest()
    expected_generated = set(HOST_SOURCE_PATHS + (V3_HOST_SOURCE_PATHS if args.product == 'v3' or args.product.startswith('v3.') else []))
    expected_generated.update('embedded/' + name for name in EMBEDDED_SOURCE_PATHS)
    if set(generated) != expected_generated:
        raise ValueError('collected generated-source inventory is incomplete')
    evidence = dict(identity, schema=1, candidate_product_version=args.product,
        physical_device_execution=False,
        verification_scope='Static package identity, error protocol, UUID and dSYM matching; not runtime validation',
        ipa=args.ipa.name, ipa_size_bytes=ipa_size, sha256=ipa_sha256, raw_ipa_sha256=ipa_sha256,
        framework_uuids=binaries, framework_cpu_subtypes=binary_subtypes,
        dsym_uuids=symbols, dsym_sha256=symbol_hashes, generated_source_sha256=generated,
        dependencies={key: os.environ[key] for key in ('LIVE_CONTAINER_REF', 'EMBEDDED_SIDESTORE_REF', 'MINIMUXER_REF', 'SIDESIGN_REF', 'SIDESIGN_GSA_FIX', 'IDEVICE_REF', 'JKTCP_REF')})
    final_size, final_sha256 = hash_ipa_file(args.ipa)
    if final_size != ipa_size or final_sha256 != ipa_sha256:
        raise ValueError("IPA changed during evidence collection")
    require_unchanged_ipa_path(args.ipa, ipa_signature)
    provenance_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__': main()
