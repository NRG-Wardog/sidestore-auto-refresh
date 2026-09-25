"""Unsigned candidate identity and matching, non-runtime crash evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import uuid
import zipfile


def macho_uuid(data):
    if data[:4] != b'\xcf\xfa\xed\xfe': return None
    offset = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, offset)
        if size < 8 or offset + size > len(data): raise ValueError('invalid Mach-O command')
        if command == 0x1b: return str(uuid.UUID(bytes=data[offset+8:offset+24])).upper()
        offset += size
    return None


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
    binaries = {}
    with zipfile.ZipFile(args.ipa) as archive:
        for name in archive.namelist():
            if name.endswith('/'): continue
            with archive.open(name) as member:
                header = member.read(65536)
            value = macho_uuid(header)
            if value: binaries[name] = value
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
    for index, root in enumerate(args.paths):
        for dsym in root.glob('*.dSYM'):
            for dwarf in (dsym / 'Contents/Resources/DWARF').iterdir():
                value = macho_uuid(dwarf.read_bytes())
                if value in binaries.values():
                    destination = args.output / ('host' if index == 0 else 'embedded') / dsym.name
                    shutil.copytree(dsym, destination, dirs_exist_ok=True)
                    symbols[dwarf.name] = value
    support = binaries['Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport']
    assert support in symbols.values(), 'matching SideStoreSupport dSYM required'
    generated = {}
    if args.source:
        paths = ['SideStoreSupport/SideStore.swift', 'SideStoreSupport/SideStoreClient.swift',
            'SideStoreSupport/XPCServer.m', 'SideStoreSupport/XPCServer.h', 'LiveContainer/LCBootstrap.m',
            'LiveContainer/LCContainerStorage.h', 'LiveContainerSwiftUI/App/AppDelegate.swift',
            'LiveContainerSwiftUI/Models/AppLayoutStyle.swift', 'LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift',
            'LiveContainerSwiftUI/Views/AppList/LCAppListView.swift']
        paths += ['LiveContainerSwiftUI/Views/AppList/LCAppBanner/' + name for name in
                  ('LCAppBanner.swift', 'LCAppBannerView.swift', 'LCAppBannerViewController.swift')]
        paths += ['.lc-app-layout.json', '.combined-service-startup.json']
        if args.product == 'v3' or args.product.startswith('v3.'):
            paths += ['LiveContainerSwiftUI/Views/V3UnifiedShell.swift']
            # The generated Settings list is the host UI most likely to carry
            # layout residue from the injection patches, so it ships as evidence.
            paths += ['LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift']
        for name in paths:
            data = (args.source / name).read_bytes()
            target = args.output / 'generated' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            generated[name] = hashlib.sha256(data).hexdigest()
    if args.side_source:
        for name in ['AltStore/AppDelegate.swift', 'SideStore/Core/Operations/PipelineExecutor.swift',
                     'SideStore/Core/Operations/PipelineRunner.swift',
                     'SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift',
                     '.combined-refresh-contract.json',
                     'Dependencies/minimuxer/DeviceGateway/idevice/IdeviceGateway.swift']:
            data = (args.side_source / name).read_bytes()
            target = args.output / 'embedded-generated' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            generated['embedded/' + name] = hashlib.sha256(data).hexdigest()
    ipa_size = args.ipa.stat().st_size
    ipa_sha256 = hashlib.sha256(args.ipa.read_bytes()).hexdigest()
    evidence = dict(identity, schema=1, candidate_product_version=args.product,
        physical_device_execution=False,
        verification_scope='Static package identity, error protocol, UUID and dSYM matching; not runtime validation',
        ipa=args.ipa.name, ipa_size_bytes=ipa_size, sha256=ipa_sha256, raw_ipa_sha256=ipa_sha256,
        framework_uuids=binaries, dsym_uuids=symbols, generated_source_sha256=generated,
        dependencies={key: os.environ[key] for key in ('LIVE_CONTAINER_REF', 'EMBEDDED_SIDESTORE_REF', 'MINIMUXER_REF', 'SIDESIGN_REF', 'SIDESIGN_GSA_FIX', 'IDEVICE_REF', 'JKTCP_REF')})
    (args.output / 'candidate-provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')


if __name__ == '__main__': main()
