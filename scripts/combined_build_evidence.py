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
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_candidate_ipa import (MACHO_MAGICS, is_java_class_file, macho_cpu_subtypes,
                                 macho_uuids, require_arm64_all_image)


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
V3_HOST_SOURCE_PATHS = [
    'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
    'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift',
]
EMBEDDED_SOURCE_PATHS = [
    'AltStore/AppDelegate.swift', 'SideStore/Core/Operations/PipelineExecutor.swift',
    'SideStore/Core/Operations/PipelineRunner.swift',
    'SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift',
    '.combined-refresh-contract.json',
    'Dependencies/minimuxer/DeviceGateway/idevice/IdeviceGateway.swift',
]


def macho_uuid(data):
    uuids = macho_uuids(data)
    return next(iter(uuids.values()), None)


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
    args.output.mkdir(parents=True, exist_ok=True)
    # Ensure repeated collection cannot retain stale files from an earlier run.
    for name in ('host', 'embedded', 'generated', 'embedded-generated'):
        target = args.output / name
        if target.exists(): shutil.rmtree(target)
    provenance_path = args.output / 'candidate-provenance.json'
    if provenance_path.exists(): provenance_path.unlink()
    binaries = {}
    binary_subtypes = {}
    with zipfile.ZipFile(args.ipa) as archive:
        for name in archive.namelist():
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
    ipa_size = args.ipa.stat().st_size
    ipa_sha256 = hashlib.sha256(args.ipa.read_bytes()).hexdigest()
    evidence = dict(identity, schema=1, candidate_product_version=args.product,
        physical_device_execution=False,
        verification_scope='Static package identity, error protocol, UUID and dSYM matching; not runtime validation',
        ipa=args.ipa.name, ipa_size_bytes=ipa_size, sha256=ipa_sha256, raw_ipa_sha256=ipa_sha256,
        framework_uuids=binaries, framework_cpu_subtypes=binary_subtypes,
        dsym_uuids=symbols, dsym_sha256=symbol_hashes, generated_source_sha256=generated,
        dependencies={key: os.environ[key] for key in ('LIVE_CONTAINER_REF', 'EMBEDDED_SIDESTORE_REF', 'MINIMUXER_REF', 'SIDESIGN_REF', 'SIDESIGN_GSA_FIX', 'IDEVICE_REF', 'JKTCP_REF')})
    provenance_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__': main()
