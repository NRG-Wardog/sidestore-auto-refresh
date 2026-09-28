"""Read-only IPA inventory, including Mach-O XML signing entitlements."""
import argparse
import hashlib
import json
import plistlib
import struct
import zipfile
from pathlib import Path


FAT_MAGICS = {
    b'\xca\xfe\xba\xbe': ('>', False),
    b'\xbe\xba\xfe\xca': ('<', False),
    b'\xca\xfe\xba\xbf': ('>', True),
    b'\xbf\xba\xfe\xca': ('<', True),
}
THIN_MAGICS = {
    b'\xce\xfa\xed\xfe': ('<', 28), b'\xfe\xed\xfa\xce': ('>', 28),
    b'\xcf\xfa\xed\xfe': ('<', 32), b'\xfe\xed\xfa\xcf': ('>', 32),
}


def signing(data):
    """Read embedded XML entitlements after validating Mach-O command bounds."""
    magic = bytes(data[:4])
    fat_layout = FAT_MAGICS.get(magic)
    if fat_layout is not None:
        endian, is_64 = fat_layout
        if len(data) < 8:
            raise ValueError('truncated fat Mach-O header')
        count = struct.unpack_from(endian + 'I', data, 4)[0]
        entry_size = 32 if is_64 else 20
        if count == 0 or count > 64 or 8 + count * entry_size > len(data):
            raise ValueError('invalid fat Mach-O architecture table')
        table_end = 8 + count * entry_size
        slices = []
        result = []
        for index in range(count):
            entry = 8 + index * entry_size
            if is_64:
                _cpu, _subtype, offset, size, align, reserved = struct.unpack_from(
                    endian + 'IIQQII', data, entry)
                max_align = 63
                if reserved != 0:
                    raise ValueError('fat Mach-O reserved field must be zero')
            else:
                _cpu, _subtype, offset, size, align = struct.unpack_from(
                    endian + 'IIIII', data, entry)
                max_align = 31
            if align > max_align:
                raise ValueError('fat Mach-O alignment exponent is invalid')
            if size == 0 or offset < table_end or offset > len(data) or size > len(data) - offset:
                raise ValueError('fat Mach-O slice is outside the file')
            if offset % (1 << align):
                raise ValueError('fat Mach-O slice offset violates its alignment')
            end = offset + size
            if any(offset < other_end and other_start < end for other_start, other_end in slices):
                raise ValueError('fat Mach-O slices overlap')
            slices.append((offset, end))
            image = data[offset:end]
            if bytes(image[:4]) not in THIN_MAGICS:
                raise ValueError('fat Mach-O slice is not a thin Mach-O image')
            result.append(_thin_signing(image))
        return result
    return _thin_signing(data)


def _thin_signing(data):
    layout = THIN_MAGICS.get(bytes(data[:4]))
    if layout is None:
        return {'format': 'not_supported'}
    endian, header_size = layout
    if len(data) < header_size:
        raise ValueError('truncated Mach-O header')
    count, command_bytes = struct.unpack_from(endian + 'II', data, 16)
    if command_bytes > len(data) - header_size:
        raise ValueError('Mach-O load-command bounds exceed the member')
    if count > command_bytes // 8:
        raise ValueError('Mach-O load-command count exceeds its declared bounds')
    command_end = header_size + command_bytes
    result = {'signature_present': False, 'xml_entitlements': None}
    pos = header_size
    for _ in range(count):
        if pos + 8 > command_end:
            raise ValueError('truncated Mach-O load command')
        cmd, size = struct.unpack_from(endian + 'II', data, pos)
        if size < 8 or size > command_end - pos:
            raise ValueError('invalid Mach-O load-command size')
        if cmd == 0x1d:
            if size < 16:
                raise ValueError('truncated LC_CODE_SIGNATURE command')
            offset, length = struct.unpack_from(endian + 'II', data, pos + 8)
            if offset > len(data) or length > len(data) - offset:
                raise ValueError('LC_CODE_SIGNATURE data is outside the member')
            blob = data[offset:offset + length]
            result['signature_present'] = True
            if len(blob) >= 12 and struct.unpack_from('>I', blob)[0] == 0xfade0cc0:
                blob_length, blob_count = struct.unpack_from('>II', blob, 4)
                if blob_length > len(blob) or blob_length < 12 or blob_count > (blob_length - 12) // 8:
                    raise ValueError('invalid code-signature superblob bounds')
                for i in range(blob_count):
                    slot, start = struct.unpack_from('>II', blob, 12 + i * 8)
                    if start > blob_length - 8:
                        raise ValueError('code-signature blob index is outside the superblob')
                    child_length = struct.unpack_from('>I', blob, start + 4)[0]
                    if child_length < 8 or child_length > blob_length - start:
                        raise ValueError('invalid code-signature child blob bounds')
                    if slot == 5:
                        end = start + child_length
                        result['xml_entitlements'] = plistlib.loads(blob[start + 8:end])
                    if slot == 7:
                        result['der_entitlements_present'] = True
        pos += size
    if pos != command_end:
        raise ValueError('Mach-O load-command size does not match its header')
    return result


def inventory(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    result = {'file': str(path), 'sha256': digest.hexdigest(), 'bundles': {}}
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
