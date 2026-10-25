"""Read-only identity contract for the exact unchanged P2 simulator build.

The caller authenticates the built inventory to the source/build provenance.
This module binds BOTH supplied inventories to fresh filesystem bytes and never
changes permissions, signatures, files, or product source. It makes no general
claim about Simulator mode normalization. Signature validation is a separate
mandatory caller gate. This is a structural Mach-O parser, not a code loader.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct

ALLOWED_DYLIB_PATHS = frozenset({
    'Frameworks/LiveContainerShared.framework/LiveContainerShared',
    'Frameworks/LiveContainerSwiftUI.framework/LiveContainerSwiftUI',
    'Frameworks/OpenSSL.framework/OpenSSL',
    'Frameworks/SideStoreSupport.framework/SideStoreSupport',
    'Frameworks/TestJITLess.dylib',
    'Frameworks/TweakLoader.dylib',
    'Frameworks/ZSign.dylib',
})
MAIN_EXECUTABLE = 'LiveContainer'
MH_EXECUTE, MH_DYLIB, PLATFORM_IOSSIMULATOR = 2, 6, 7
CONTRACT = 'p2-exact-installation-identity-v1'


class ContractError(ValueError):
    """The observed installation does not meet this bounded contract."""


def _require(value, message):
    if not value:
        raise ContractError(message)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(name):
    _require(isinstance(name, str) and name and '\\' not in name and '\0' not in name,
             'Invalid inventory path')
    _require(all(p not in ('', '.', '..') for p in name.split('/')),
             'Unsafe inventory path: ' + name)


def _fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _contained_links(rows, directories):
    # Resolve only authenticated inventory entries; never follow OS symlinks.
    for name, row in rows.items():
        if row['type'] != 'symlink':
            continue
        pending, resolved, followed = name.split('/'), [], set()
        while pending:
            component = pending.pop(0)
            if component in ('', '.'):
                continue
            if component == '..':
                _require(resolved, 'Escaping bundle link: ' + name)
                resolved.pop()
                continue
            candidate = '/'.join([*resolved, component])
            target = rows.get(candidate)
            if target and target['type'] == 'symlink':
                _require(candidate not in followed and len(followed) < 128,
                         'Cyclic or excessive bundle link: ' + name)
                followed.add(candidate)
                link = target['link']
                _require(link and not link.startswith('/') and '\\' not in link,
                         'Absolute, empty or ambiguous bundle link: ' + name)
                pending = link.split('/') + pending
            else:
                _require(candidate in directories or (target and target['type'] == 'regular'),
                         'Missing bundle link destination: ' + name)
                _require(not pending or candidate in directories,
                         'Non-directory link ancestor: ' + name)
                resolved.append(component)
        _require(resolved, 'Bundle link resolves to bundle root: ' + name)


def _snapshot(root, *, capture=False):
    """Descriptor-anchored walk, with no symlink traversal and stability checks."""
    root = Path(root).absolute()
    before = root.lstat()
    _require(stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode),
             'Missing or linked bundle root: ' + str(root))
    _require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
             'No safe descriptor-based filesystem support')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    rows, payloads, directories, observations = {}, {}, set(), {}

    def visit(fd, prefix):
        first = os.fstat(fd)
        names = sorted(os.listdir(fd))
        for name in names:
            relative = prefix + name
            _relative(relative)
            seen = os.stat(name, dir_fd=fd, follow_symlinks=False)
            mode = stat.S_IMODE(seen.st_mode)
            if stat.S_ISDIR(seen.st_mode):
                directories.add(relative)
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=fd)
                try:
                    _require(_fingerprint(seen) == _fingerprint(os.fstat(child)),
                             'Directory changed before observation: ' + relative)
                    visit(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(seen.st_mode):
                file_fd = os.open(name, flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=fd)
                try:
                    _require(_fingerprint(seen) == _fingerprint(os.fstat(file_fd)),
                             'File changed before observation: ' + relative)
                    chunks, digest, length = [], hashlib.sha256(), 0
                    while True:
                        chunk = os.read(file_fd, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        length += len(chunk)
                        if capture:
                            chunks.append(chunk)
                    _require(_fingerprint(seen) == _fingerprint(os.fstat(file_fd)) and length == seen.st_size,
                             'File changed during observation: ' + relative)
                finally:
                    os.close(file_fd)
                rows[relative] = {'type': 'regular', 'mode': mode, 'bytes': length,
                                  'sha256': digest.hexdigest(), 'executable': bool(mode & 0o111)}
                if capture:
                    payloads[relative] = b''.join(chunks)
            elif stat.S_ISLNK(seen.st_mode):
                raw = os.readlink(os.fsencode(name), dir_fd=fd)
                rows[relative] = {'type': 'symlink', 'mode': mode, 'bytes': len(raw),
                                  'sha256': _digest(raw), 'link': os.fsdecode(raw)}
                if capture:
                    payloads[relative] = raw
            else:
                raise ContractError('Unsupported filesystem type: ' + relative)
            _require(_fingerprint(seen) == _fingerprint(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                     'Entry changed during observation: ' + relative)
            observations[relative] = _fingerprint(seen)
        _require(names == sorted(os.listdir(fd)) and _fingerprint(first) == _fingerprint(os.fstat(fd)),
                 'Directory changed during observation: ' + (prefix or '.'))

    fd = os.open(root, flags | os.O_DIRECTORY)
    try:
        _require(_fingerprint(before) == _fingerprint(os.fstat(fd)), 'Bundle root changed before observation')
        visit(fd, '')
        _require(_fingerprint(before) == _fingerprint(root.lstat()), 'Bundle root changed during observation')
    finally:
        os.close(fd)
    observations[''] = _fingerprint(before)
    _require(rows, 'Empty bundle inventory')
    _contained_links(rows, directories)
    return rows, payloads, directories, observations


def bundle_inventory(root):
    """Inventory regular files and raw links, including their full S_IMODE."""
    return _snapshot(root)[0]


def _inventory(value, label):
    _require(type(value) is dict and value, label + ' inventory must be a nonempty object')
    for name, row in value.items():
        _relative(name)
        _require(type(row) is dict and row.get('type') in ('regular', 'symlink'),
                 label + ' unsupported inventory type: ' + name)
        fields = {'type', 'mode', 'bytes', 'sha256', 'executable' if row['type'] == 'regular' else 'link'}
        _require(set(row) == fields, label + ' unexpected inventory fields: ' + name)
        _require(type(row['mode']) is int and 0 <= row['mode'] <= 0o7777,
                 label + ' invalid full mode: ' + name)
        _require(type(row['bytes']) is int and row['bytes'] >= 0,
                 label + ' invalid byte count: ' + name)
        _require(type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}', row['sha256']),
                 label + ' invalid SHA-256: ' + name)
        if row['type'] == 'regular':
            _require(type(row['executable']) is bool and row['executable'] == bool(row['mode'] & 0o111),
                     label + ' inconsistent executable field: ' + name)
        else:
            _require(type(row['link']) is str, label + ' invalid link target: ' + name)
            raw = os.fsencode(row['link'])
            _require(row['bytes'] == len(raw) and row['sha256'] == _digest(raw),
                     label + ' inconsistent raw link binding: ' + name)


def _range(offset, size, length, message):
    _require(0 <= offset <= length and 0 <= size <= length - offset, message)


def _thin(raw, offset, size):
    magic = raw[offset:offset + 4]
    formats = {b'\xce\xfa\xed\xfe': ('<', 32), b'\xcf\xfa\xed\xfe': ('<', 64),
               b'\xfe\xed\xfa\xce': ('>', 32), b'\xfe\xed\xfa\xcf': ('>', 64)}
    _require(magic in formats, 'Unknown or nested non-thin Mach-O slice')
    endian, bits = formats[magic]
    header_size = 32 if bits == 64 else 28
    _require(size >= header_size, 'Truncated Mach-O header')
    header = struct.unpack_from(endian + ('8I' if bits == 64 else '7I'), raw, offset)
    _, cpu, subtype, filetype, count, command_bytes, flags = header[:7]
    if bits == 64:
        _require(header[7] == 0, 'Nonzero reserved Mach-O header')
    _range(header_size, command_bytes, size, 'Truncated load-command region')
    _require(0 < count <= command_bytes // 8, 'Invalid load-command count')
    position, end = offset + header_size, offset + header_size + command_bytes
    versions, commands = [], []
    fixed = {0x2: 24, 0xb: 80, 0x1b: 24, 0x2a: 16, 0x80000028: 24,
             0x80000022: 48, 0x22: 48, 0x2c: 24,
             0x24: 16, 0x25: 16, 0x2f: 16, 0x30: 16}
    linkedit = {0x1d, 0x1e, 0x26, 0x29, 0x2b, 0x2e, 0x80000033, 0x80000034}
    strings = {0xc: 24, 0xd: 24, 0x80000018: 24, 0x8000001f: 24, 0x20: 24,
               0x80000023: 24, 0xe: 12, 0xf: 12, 0x8000001c: 12, 0x27: 12}
    for _ in range(count):
        _require(position + 8 <= end, 'Truncated load-command header')
        command, length = struct.unpack_from(endian + 'II', raw, position)
        _require(length >= 8 and length % (8 if bits == 64 else 4) == 0,
                 'Invalid load-command size/alignment')
        _require(length <= end - position, 'Load command exceeds declared region')
        if command == 0x32:  # LC_BUILD_VERSION, including every build_tool_version.
            _require(length >= 24, 'Truncated build-version command')
            platform, minimum, sdk, tools_count = struct.unpack_from(endian + '4I', raw, position + 8)
            _require(length == 24 + 8 * tools_count, 'Malformed build-version tools')
            tools = [dict(zip(('tool', 'version'), struct.unpack_from(endian + 'II', raw, position + 24 + i * 8)))
                     for i in range(tools_count)]
            _require(len({tool['tool'] for tool in tools}) == tools_count, 'Duplicate build-version tool')
            versions.append({'command': command, 'platform': platform, 'minimumOS': minimum, 'sdk': sdk, 'tools': tools})
        elif command in (0x1, 0x19):
            is64 = command == 0x19
            _require(is64 == (bits == 64), 'Segment/header width mismatch')
            base, section_size = (72, 80) if is64 else (56, 68)
            _require(length >= base, 'Truncated segment command')
            section_count = struct.unpack_from(endian + 'I', raw, position + (64 if is64 else 48))[0]
            _require(length == base + section_size * section_count, 'Malformed segment sections')
            file_offset, file_size = struct.unpack_from(endian + ('QQ' if is64 else 'II'), raw, position + (40 if is64 else 32))
            _range(file_offset, file_size, size, 'Segment exceeds Mach-O slice')
        elif command in fixed:
            _require(length == fixed[command], 'Malformed fixed-size load command')
            if command in (0x24, 0x25, 0x2f, 0x30):
                # Legacy commands do not unambiguously encode platform 7.
                versions.append({'command': command, 'legacyVersion': list(struct.unpack_from(endian + 'II', raw, position + 8))})
            if command == 0x2:
                symbol_offset, symbol_count, string_offset, string_size = struct.unpack_from(endian + '4I', raw, position + 8)
                _range(symbol_offset, symbol_count * (16 if bits == 64 else 12), size, 'Symbol table exceeds slice')
                _range(string_offset, string_size, size, 'String table exceeds slice')
            if command in (0x22, 0x80000022):
                fields = struct.unpack_from(endian + '10I', raw, position + 8)
                for start, amount in zip(fields[::2], fields[1::2]):
                    _range(start, amount, size, 'Dyld-info payload exceeds slice')
        elif command in linkedit:
            _require(length == 16, 'Malformed link-edit command')
            start, amount = struct.unpack_from(endian + 'II', raw, position + 8)
            _range(start, amount, size, 'Link-edit payload exceeds slice')
        elif command in strings:
            minimum = strings[command]
            _require(length > minimum, 'Truncated string-bearing load command')
            string_offset = struct.unpack_from(endian + 'I', raw, position + 8)[0]
            _require(minimum <= string_offset < length, 'Load-command string offset out of bounds')
            value = raw[position + string_offset:position + length]
            _require(b'\0' in value and not any(value[value.index(0):]), 'Unterminated string or nonzero load-command padding')
        else:
            raise ContractError('Unreviewed load-command type: ' + hex(command))
        commands.append({'command': command, 'bytes': length})
        position += length
    _require(position == end, 'Load-command count/size disagreement')
    _require(len(versions) == 1 and versions[0].get('command') == 0x32,
             'Exactly one explicit build-version platform required')
    _require(versions[0]['platform'] == PLATFORM_IOSSIMULATOR, 'Slice is not explicitly iOS Simulator')
    return {'offset': offset, 'bytes': size, 'sha256': _digest(raw[offset:offset + size]),
            'bits': bits, 'endianness': 'little' if endian == '<' else 'big',
            'cpuType': cpu, 'cpuSubtype': subtype, 'fileType': filetype, 'flags': flags,
            'loadCommandCount': count, 'loadCommandBytes': command_bytes,
            'buildVersion': versions[0], 'loadCommands': commands}


def parse_macho(raw):
    """Parse all thin/fat32/fat64 slices; reject unsupported/ambiguous structures."""
    _require(type(raw) is bytes and len(raw) >= 4, 'Missing Mach-O bytes')
    formats = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
               b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
    if raw[:4] not in formats:
        return {'container': 'thin', 'slices': [_thin(raw, 0, len(raw))]}
    endian, is64 = formats[raw[:4]]
    _require(len(raw) >= 8, 'Truncated fat header')
    count = struct.unpack_from(endian + 'I', raw, 4)[0]
    record_size = 32 if is64 else 20
    _require(0 < count <= (len(raw) - 8) // record_size, 'Truncated or empty fat record table')
    table_end = 8 + count * record_size
    records, architectures = [], set()
    for i in range(count):
        fields = struct.unpack_from(endian + ('IIQQII' if is64 else 'IIIII'), raw, 8 + i * record_size)
        cpu, subtype, offset, size, alignment = fields[:5]
        if is64:
            _require(fields[5] == 0, 'Nonzero fat64 reserved field')
        _require(alignment <= (63 if is64 else 31) and offset % (1 << alignment) == 0,
                 'Invalid fat slice alignment')
        _require(offset >= table_end and size > 0, 'Fat slice overlaps header/table or is empty')
        _range(offset, size, len(raw), 'Truncated fat slice')
        _require((cpu, subtype) not in architectures, 'Duplicate fat architecture')
        architectures.add((cpu, subtype))
        records.append((offset, size, cpu, subtype, alignment))
    ordered = sorted(records)
    _require(all(a[0] + a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), 'Overlapping fat slices')
    slices = []
    for offset, size, cpu, subtype, alignment in records:
        row = _thin(raw, offset, size)
        _require((row['cpuType'], row['cpuSubtype']) == (cpu, subtype), 'Fat/thin architecture mismatch')
        row['fatAlignment'] = alignment
        slices.append(row)
    return {'container': 'fat64' if is64 else 'fat32', 'endianness': 'little' if endian == '<' else 'big', 'slices': slices}


def _inventory_digest(rows):
    return _digest(json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii'))


def verify_installation_identity(built_root, installed_root, built_inventory, installed_inventory):
    """Return evidence only if exact bytes and the seven bounded mode rules hold.

    Inputs are strict inventories from the caller's source-bound build and
    retained installed observation. Both are re-observed; caller still owns
    provenance, codesign, lifecycle ordering and persistence of this receipt.
    No snapshot promises that a mutable filesystem cannot change after return.
    """
    _inventory(built_inventory, 'Built')
    _inventory(installed_inventory, 'Installed')
    _require(set(built_inventory) == set(installed_inventory), 'Installed inventory path set differs')
    needed = ALLOWED_DYLIB_PATHS | {MAIN_EXECUTABLE, 'Info.plist'}
    _require(needed <= set(built_inventory), 'Required product inventory paths missing')
    built, built_bytes, built_dirs, built_observations = _snapshot(built_root, capture=True)
    installed, installed_bytes, installed_dirs, installed_observations = _snapshot(installed_root, capture=True)
    _require(built == built_inventory, 'Built inventory does not bind to actual bytes/modes/types')
    _require(installed == installed_inventory, 'Installed inventory does not bind to actual bytes/modes/types')
    _require(built_dirs == installed_dirs, 'Installed directory path set differs')
    changes, binaries = [], {}
    for name in sorted(built):
        before, after = built[name], installed[name]
        _require(before['type'] == after['type'], 'Installed filesystem type differs: ' + name)
        _require(built_bytes[name] == installed_bytes[name], 'Installed literal bytes differ: ' + name)
        for field in ('bytes', 'sha256', 'link'):
            _require(before.get(field) == after.get(field), 'Installed ' + field + ' differs: ' + name)
        if name in ALLOWED_DYLIB_PATHS or name == MAIN_EXECUTABLE:
            _require(before['type'] == 'regular', 'Mach-O path must be a regular file: ' + name)
            expected = MH_EXECUTE if name == MAIN_EXECUTABLE else MH_DYLIB
            left, right = parse_macho(built_bytes[name]), parse_macho(installed_bytes[name])
            _require(left == right, 'Installed architecture/platform metadata differs: ' + name)
            _require(all(row['fileType'] == expected for row in left['slices']),
                     'Unexpected Mach-O file type in one or more slices: ' + name)
            binaries[name] = {'sha256': before['sha256'], 'bytes': before['bytes'],
                              'builtMode': before['mode'], 'installedMode': after['mode'], **left}
        if name == MAIN_EXECUTABLE:
            _require(before['mode'] == after['mode'] == 0o755, 'Main executable must retain full mode 0755')
        if before['mode'] != after['mode']:
            _require(name in ALLOWED_DYLIB_PATHS and before['type'] == 'regular'
                     and (before['mode'], after['mode']) == (0o755, 0o644),
                     'Unapproved full-mode transition: ' + name)
            changes.append({'path': name, 'builtMode': before['mode'], 'installedMode': after['mode'],
                            'sha256': before['sha256'], 'bytes': before['bytes'], 'fileType': MH_DYLIB,
                            'sliceCount': len(binaries[name]['slices'])})
    # Rehash every byte and recheck all modes, types, directory topology and entry
    # identities after parsing. This detects changed or swapped read operands.
    for root, rows, directories, observations, label in (
            (built_root, built, built_dirs, built_observations, 'Built'),
            (installed_root, installed, installed_dirs, installed_observations, 'Installed')):
        final, _, final_dirs, final_observations = _snapshot(root)
        _require(final == rows and final_dirs == directories and final_observations == observations,
                 label + ' bundle changed during installation verification')
    return {'schemaVersion': 1, 'contract': CONTRACT, 'accepted': True,
            'builtInventorySHA256': _inventory_digest(built),
            'installedInventorySHA256': _inventory_digest(installed),
            'pathCount': len(built), 'regularFileCount': sum(row['type'] == 'regular' for row in built.values()),
            'symlinkCount': sum(row['type'] == 'symlink' for row in built.values()),
            'exactPathSet': True, 'exactTypes': True, 'literalBytesEqual': True,
            'fullModesChecked': True, 'observationsStable': True,
            'modeTransitions': changes, 'machO': binaries,
            'scope': 'Only this source-bound build and installed observation; no universal Simulator normalization claim.',
            'signatureVerification': 'Required separate caller gate; not performed by this read-only contract.'}
