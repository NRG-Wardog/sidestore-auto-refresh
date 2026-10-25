"""Diagnostic-only local nlist removal gate. Not runtime or signing approval.

Uses the unchanged strict Mach-O parser. Only unreferenced, non-external
N_SECT/N_ABS entries in the LC_DYSYMTAB local range may disappear. All other
symbol records, indirect semantics, exports, Swift/ObjC sections, VM layout and
non-LINKEDIT bytes must remain exact. This module never mutates input bytes.
"""
from collections import Counter
import struct
from macho_stab_guard import commands, require, snapshot


def symbol_rows(data):
    _, _, rows = commands(data)
    raw = next(raw for cmd, raw in rows if cmd == 2)
    offset, count, strings, size = struct.unpack_from('<4I', raw, 8)
    dysym = struct.unpack('<20I', next(raw for cmd, raw in rows if cmd == 0xb))
    result = []
    for index in range(count):
        name, kind, section, desc, value = struct.unpack_from('<IBBHQ', data, offset + 16 * index)
        end = data.find(b'\0', strings + name, strings + size)
        require(end >= strings + name, 'unterminated symbol name')
        result.append((data[strings+name:end] if name else b'', kind, section, desc, value))
    return result, dysym


def classify(data):
    shape = snapshot(data)  # All bounds/ranges, category closure and blob checks first.
    require(shape['stab_count'] == 0, 'local-symbol diagnostic requires prior STAB removal')
    rows, dysym = symbol_rows(data)
    local_start, local_count = dysym[2:4]
    protected_indices = set()
    for i in range(dysym[15]):
        index = struct.unpack_from('<I', data, dysym[14] + 4*i)[0]
        if not index & 0xc0000000: protected_indices.add(index)
    removable, protected = [], []
    for index, row in enumerate(rows):
        kind = row[1]
        is_local = local_start <= index < local_start + local_count
        eligible = is_local and not kind & 1 and (kind & 0x0e) in (2, 0x0e) and index not in protected_indices
        (removable if eligible else protected).append(row)
    return shape, Counter(removable), Counter(protected)


def verify_local_removal(before, after, *, require_reduction=True):
    old, old_local, old_protected = classify(before)
    new, new_local, new_protected = classify(after)
    for key in ('header', 'uuid', 'commands', 'segments', 'sections', 'linkedit_offset',
                'linkedit_blobs', 'indirect_symbols'):
        require(old[key] == new[key], 'local-strip invariant changed: ' + key)
    require(old['symbol_groups'][1:] == new['symbol_groups'][1:], 'external/undefined categories changed')
    require(old_protected == new_protected, 'protected or externally visible symbol changed')
    require(not (new_local - old_local), 'local symbol added or modified')
    removed = old_local - new_local
    require(new['linkedit_vmsize'] <= old['linkedit_vmsize'], 'LINKEDIT VM allocation grew')
    start = max(old['command_end'], new['command_end'])
    require(start <= min(old['first_file_data'], new['first_file_data']), 'expanded commands overwrite section data')
    if new['command_end'] > old['command_end']:
        require(not any(before[old['command_end']:new['command_end']]), 'signature command overwrote header slack')
    elif new['command_end'] < old['command_end']:
        require(not any(after[new['command_end']:old['command_end']]), 'removed signature command left header bytes')
    require(before[start:old['linkedit_offset']] == after[start:new['linkedit_offset']], 'non-LINKEDIT body changed')
    if require_reduction:
        require(sum(removed.values()) > 0 and len(after) < len(before), 'no local-symbol size reduction')
    return {'schema': 'diagnostic-local-symbol-preservation-v1', 'before': old, 'after': new,
            'removed_local_records': sum(removed.values()), 'remaining_local_records': sum(new_local.values()),
            'protected_records': sum(old_protected.values()), 'bytes_saved': len(before)-len(after),
            'signature_verified': False, 'runtime_proven': False, 'packaging_approved': False}
