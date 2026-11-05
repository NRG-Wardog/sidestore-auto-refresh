"""Diagnostic-only local nlist removal gate. Not runtime or signing approval.

Uses the unchanged strict Mach-O parser. Only unreferenced, non-external
N_SECT/N_ABS entries in the LC_DYSYMTAB local range may disappear. All other
symbol records, indirect semantics, exports, Swift/ObjC sections, VM layout and
non-LINKEDIT bytes must remain exact. This module never mutates input bytes.
"""
from collections import Counter
import struct
from macho_stab_guard import commands, require, snapshot, symbol_digest, sha
from package_livecontainer_combined import REQUIRED_SIDESTORE_INTENT_SYMBOLS


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


APPLE_SOURCE = {'commit':'e0d56624eca2a76c2ace4c21850df9e666de4ca5',
    'strip_c_sha256':'53d2ce75886237bfd99e953790e48920bb9dbfd58873fb2a5c46d25dfe195ddb',
    'url':'https://github.com/apple-oss-distributions/cctools/blob/e0d56624eca2a76c2ace4c21850df9e666de4ca5/misc/strip.c#L4148-L4264',
    'compiled_Xcode_source_identity_claimed':False}

APPLE_LOCAL_SENTINEL = (b'radr://5614542', 0x3c, 0, 0, 0x05614542)


def is_runtime_adapter_name(name):
    return any(stem in name for stem in REQUIRED_SIDESTORE_INTENT_SYMBOLS)


def runtime_save_list(data):
    """Apple -s can retain private-extern/global records, not ordinary locals."""
    shape,removable,protected=classify(data)
    rows,_=symbol_rows(data)
    runtime=[row for row in rows if is_runtime_adapter_name(row[0]) and row[1] & 0x11]
    names={row[0] for row in runtime}
    for stem in REQUIRED_SIDESTORE_INTENT_SYMBOLS:
        for suffix in (b'Ma',b'Mn',b'N'):
            require(b'_$s'+stem+suffix in names,'App Intent runtime metadata root missing')
    keep=runtime+[row for row in rows if row[1] & 1]
    names=sorted({row[0] for row in keep})
    require(all(name and not any(c in name for c in (b'\n',b'\r',b'\0')) for name in names),'unsafe keep-list name')
    content=b''.join(name+b'\n' for name in names)
    ordinary=[row for row in rows if is_runtime_adapter_name(row[0]) and not row[1]&0x11]
    return content,{'runtime_adapter_tuple_count':len(runtime),'runtime_adapter_tuple_digest':symbol_digest(runtime),
                   'ordinary_family_label_count_before':len(ordinary),'keep_name_count':len(names),
                   'keep_list_sha256':sha(content),'source_semantics':'Apple strip -x -s, no -S or -i',
                   'six_metadata_access_descriptor_and_type_roots_required':True}


def classify(data, *, allow_apple_local_sentinel=False, unsigned_executable=False, preserve_private_externals=False, host_legacy_dyld=False):
    require(not unsigned_executable or preserve_private_externals, 'unsigned executable local policy must preserve all private externals')
    shape = snapshot(data, unsigned_executable=unsigned_executable, host_legacy_dyld=host_legacy_dyld)  # All bounds/ranges, category closure and blob checks first.
    rows, dysym = symbol_rows(data)
    sentinel = shape['stab_count'] != 0
    if sentinel:
        require(allow_apple_local_sentinel and shape['stab_count'] == 1 and rows[0] == APPLE_LOCAL_SENTINEL,
                'local-symbol diagnostic requires prior STAB removal or exact Apple sentinel')
        require(tuple(dysym[2:4]) == (0, 1) and dysym[15] > 0, 'Apple sentinel local range/indirect condition differs')
    local_start, local_count = dysym[2:4]
    protected_indices = set()
    for i in range(dysym[15]):
        index = struct.unpack_from('<I', data, dysym[14] + 4*i)[0]
        if not index & 0xc0000000:
            require(not sentinel or index != 0, 'Apple sentinel referenced indirectly')
            protected_indices.add(index)
    removable, protected = [], []
    for index, row in enumerate(rows):
        if sentinel and index == 0: continue
        kind = row[1]
        is_local = local_start <= index < local_start + local_count
        runtime_adapter = is_runtime_adapter_name(row[0]) and bool(kind & 0x11)
        eligible = not runtime_adapter and is_local and not kind & 1 and (kind & 0x0e) in (2, 0x0e) and index not in protected_indices
        if preserve_private_externals:
            eligible = eligible and not kind & 0x10 and (kind & 0x0e) == 0x0e and row[2] > 0
        (removable if eligible else protected).append(row)
    return shape, Counter(removable), Counter(protected)


def verify_local_removal(before, after, *, require_reduction=True, unsigned_executable=False, preserve_private_externals=False, host_legacy_dyld=False):
    policy = {'unsigned_executable': unsigned_executable, 'preserve_private_externals': preserve_private_externals, 'host_legacy_dyld': host_legacy_dyld}
    old, old_local, old_protected = classify(before, **policy)
    new, new_local, new_protected = classify(after, allow_apple_local_sentinel=True, **policy)
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
            'removed_local_records': sum(removed.values()), 'remaining_removable_local_records': sum(new_local.values()),
            'actual_local_symbol_category_count': symbol_rows(after)[1][3],
            'compatibility_marker_count': new['stab_count'],
            'protected_records': sum(old_protected.values()),
            'runtime_adapter_private_tuples_preserved':sum(count for row,count in old_protected.items() if is_runtime_adapter_name(row[0]) and row[1]&0x10),
            'ordinary_runtime_family_labels_removed':sum(count for row,count in removed.items() if is_runtime_adapter_name(row[0])), 'bytes_saved': len(before)-len(after),
            'apple_compatibility_sentinel_retained': new['stab_count'] == 1,
            'apple_sentinel_source_evidence': APPLE_SOURCE,
            'signature_verified': False, 'runtime_proven': False, 'packaging_approved': False}
