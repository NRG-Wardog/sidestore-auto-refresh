"""Read-only IPA inventory, including Mach-O XML signing entitlements."""
import argparse
import hashlib
import json
import plistlib
import struct
import zipfile
from pathlib import Path


def signing(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        count = struct.unpack_from('>I', data, 4)[0]
        return [signing(data[offset:offset + size]) for _, _, offset, size, _ in
                (struct.unpack_from('>5I', data, 8 + i * 20) for i in range(count))]
    if data[:4] not in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        return {'format': 'not_supported'}
    pos = 32 if data[0] == 0xcf else 28
    count = struct.unpack_from('<I', data, 16)[0]
    result = {'signature_present': False, 'xml_entitlements': None}
    for _ in range(count):
        cmd, size = struct.unpack_from('<II', data, pos)
        if cmd == 0x1d:
            offset, length = struct.unpack_from('<II', data, pos + 8)
            blob = data[offset:offset + length]
            result['signature_present'] = True
            if len(blob) >= 12 and struct.unpack_from('>I', blob)[0] == 0xfade0cc0:
                for i in range(struct.unpack_from('>I', blob, 8)[0]):
                    slot, start = struct.unpack_from('>II', blob, 12 + i * 8)
                    if slot == 5:
                        end = start + struct.unpack_from('>I', blob, start + 4)[0]
                        result['xml_entitlements'] = plistlib.loads(blob[start + 8:end])
                    if slot == 7:
                        result['der_entitlements_present'] = True
        pos += size
    return result


def inventory(path):
    result = {'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bundles': {}}
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for name in sorted(names):
            if not name.endswith('/Info.plist'):
                continue
            folder = name[:-11]
            if not folder.endswith(('.app', '.appex', '.framework')):
                continue
            info = plistlib.loads(archive.read(name))
            executable = folder + '/' + info.get('CFBundleExecutable', '')
            result['bundles'][folder] = {
                'info': info,
                'mobileprovision_present': folder + '/embedded.mobileprovision' in names,
                'executable_present': executable in names,
                'signing': signing(archive.read(executable)) if executable in names else None,
            }
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ipa', type=Path, nargs='+')
    args = parser.parse_args()
    print(json.dumps([inventory(p) for p in args.ipa], indent=2, default=str, sort_keys=True))
