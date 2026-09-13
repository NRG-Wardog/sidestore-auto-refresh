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
    parser.add_argument('--product', required=True, choices=['v2', 'v3'])
    parser.add_argument('--ipa', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
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
            marker = b'LCFAILURE1:' if executable.startswith('SideStoreSupport') else b'LCFailureStage'
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
    evidence = dict(identity, schema=1, physical_device_execution=False,
        verification_scope='Static package identity, error protocol, UUID and dSYM matching; not runtime validation',
        ipa=args.ipa.name, sha256=hashlib.sha256(args.ipa.read_bytes()).hexdigest(),
        framework_uuids=binaries, dsym_uuids=symbols,
        dependencies={key: os.environ[key] for key in ('LIVE_CONTAINER_REF', 'EMBEDDED_SIDESTORE_REF', 'MINIMUXER_REF', 'SIDESIGN_REF', 'SIDESIGN_GSA_FIX', 'IDEVICE_REF', 'JKTCP_REF')})
    (args.output / 'candidate-provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')


if __name__ == '__main__': main()
