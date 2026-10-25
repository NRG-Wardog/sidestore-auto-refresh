"""Fail-closed structural gate for Apple strip -S on two thin arm64 images.

This parser does not execute Mach-O or prove runtime compatibility. It preserves
all non-STAB symbols (including locals/undefined), indexed symbol semantics,
non-LINKEDIT bytes, dyld payloads, UUID, and non-signature load metadata.
"""
from collections import Counter
import hashlib
import struct

MAX_BYTES = 512 * 1024 * 1024
BLOB_COMMANDS = {0x80000034, 0x80000033, 0x26, 0x29}
EXACT_COMMANDS = {0xd, 0xe, 0x1b, 0x32, 0x2a, 0x80000028, 0x2c, 0xc, 0x80000018, 0x8000001c}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def commands(data):
    require(32 <= len(data) <= MAX_BYTES and data[:4] == b'\xcf\xfa\xed\xfe', 'only bounded thin little-endian Mach-O64 supported')
    cpu, subtype, kind, count, size, flags, reserved = struct.unpack_from('<7I', data, 4)
    require(cpu == 0x100000c and reserved == 0 and 0 < count <= 512 and size <= len(data) - 32, 'unsupported architecture/header')
    pos, end, rows = 32, 32 + size, []
    for _ in range(count):
        require(pos + 8 <= end, 'truncated command')
        command, length = struct.unpack_from('<II', data, pos)
        require(length >= 8 and length % 8 == 0 and length <= end - pos, 'invalid command length')
        rows.append((command, data[pos:pos + length]))
        pos += length
    require(pos == end, 'command closure differs')
    return (cpu, subtype, kind, flags, reserved), end, rows


def image_uuid(data):
    _, _, rows = commands(data)
    uuids = [raw[8:] for cmd, raw in rows if cmd == 0x1b]
    require(len(uuids) == 1 and len(uuids[0]) == 16, 'missing/duplicate UUID')
    return uuids[0].hex()


def symbol_digest(symbols):
    h = hashlib.sha256()
    for symbol, count in sorted(Counter(symbols).items()):
        name, typ, section, desc, value = symbol
        h.update(struct.pack('<II', len(name), count) + name + struct.pack('<BBHQ', typ, section, desc, value))
    return h.hexdigest()


def snapshot(data):
    header, command_end, rows = commands(data)
    require(header[2] == 6, 'only reviewed MH_DYLIB images supported')
    normalized, blobs, ranges, segments, section_rows = [], [], [], [], []
    linkedit = symtab = dysymtab = None
    signature = None
    section_count = 0
    first_file_data = len(data)
    linkedit_vmsize = None
    for command, raw in rows:
        norm = bytearray(raw)
        if command == 0x19:
            require(len(raw) >= 72, 'truncated segment')
            name = raw[8:24].rstrip(b'\0')
            vmaddr, vmsize, off, size, maxprot, initprot, nsects, flags = struct.unpack_from('<4Q4I', raw, 24)
            require(len(raw) == 72 + 80 * nsects and off <= len(data) and size <= len(data) - off and size <= vmsize, 'invalid segment bounds')
            if name == b'__LINKEDIT':
                require(linkedit is None and nsects == 0, 'duplicate/sectioned LINKEDIT')
                linkedit = (off, size)
                linkedit_vmsize = vmsize
                # Only allocation/file size may shrink/grow around the symbol tables.
                struct.pack_into('<Q', norm, 32, 0)
                struct.pack_into('<Q', norm, 48, 0)
            else:
                segments.append((name.hex(), vmaddr, vmsize, off, size))
            for i in range(nsects):
                section = raw[72 + i * 80:152 + i * 80]
                address, length, offset, alignment, reloff, nreloc, sflags = struct.unpack_from('<QQIIIII', section, 32)
                require(nreloc == 0 and reloff == 0, 'section relocation table unsupported')
                require(vmaddr <= address <= vmaddr + vmsize and length <= vmaddr + vmsize - address, 'section outside owning VM segment')
                if (sflags & 0xff) not in (1, 0xc, 0x12) and length:
                    require(off <= offset and offset + length <= off + size and offset >= command_end, 'section outside owning file segment/header')
                    first_file_data = min(first_file_data, offset)
                section_rows.append(section.hex())
            section_count += nsects
        elif command == 2:
            require(symtab is None and len(raw) == 24, 'invalid/duplicate symtab')
            symtab = struct.unpack_from('<4I', raw, 8)
            norm[8:] = b'\0' * 16
        elif command == 0xb:
            require(dysymtab is None and len(raw) == 80, 'invalid/duplicate dysymtab')
            dysymtab = struct.unpack('<20I', raw)
            require(not any(dysymtab[i] for i in (8, 9, 10, 11, 12, 13, 16, 17, 18, 19)), 'unsupported symbol relocation/module/reference tables')
            norm[8:32] = b'\0' * 24  # symbol category ranges remap with STAB removal
            struct.pack_into('<I', norm, 56, 0)  # indirect table location may move
        elif command in BLOB_COMMANDS or command == 0x1d:
            require(len(raw) == 16, 'invalid linkedit data command')
            offset, size = struct.unpack_from('<II', raw, 8)
            require(offset <= len(data) and size <= len(data) - offset, 'linkedit blob outside file')
            ranges.append((offset, offset + size, command))
            if command == 0x1d:
                require(signature is None, 'duplicate signature')
                signature = data[offset:offset + size]
                # Signature is separately resealed/verified, never claimed retained.
                continue
            blobs.append((command, size, sha(data[offset:offset + size])))
            struct.pack_into('<I', norm, 8, 0)
        elif command in EXACT_COMMANDS:
            exact_sizes = {0x1b: 24, 0x2a: 16, 0x80000028: 24, 0x2c: 24}
            if command in exact_sizes:
                require(len(raw) == exact_sizes[command], 'invalid fixed load command size')
            if command == 0x32:
                require(len(raw) >= 24 and len(raw) == 24 + 8 * struct.unpack_from('<I', raw, 20)[0], 'invalid build-version tool table')
            if command in (0xd, 0xc, 0x80000018, 0xe, 0x8000001c):
                minimum = 24 if command in (0xd, 0xc, 0x80000018) else 12
                require(len(raw) >= minimum, 'truncated path load command')
                string_offset = struct.unpack_from('<I', raw, 8)[0]
                require(minimum <= string_offset < len(raw) and b'\0' in raw[string_offset:], 'invalid load-command string offset/termination')
            if command == 0x2c:
                require(len(raw) == 24 and struct.unpack_from('<I', raw, 16)[0] == 0, 'encrypted image unsupported')
        else:
            raise ValueError('unreviewed load command: ' + hex(command))
        normalized.append((command, bytes(norm).hex()))
    require(linkedit is not None and symtab is not None and dysymtab is not None, 'missing linkedit symbol metadata')
    linkoff, linksize = linkedit
    require(command_end <= linkoff and linkoff + linksize == len(data), 'LINKEDIT must cover exact file tail')
    for name, vmaddr, vmsize, off, size in segments:
        require(off + size <= linkoff, 'non-LINKEDIT segment overlaps LINKEDIT')
    symoff, count, stroff, strsize = symtab
    require(count <= MAX_BYTES // 16 and symoff >= linkoff and count * 16 <= len(data) - symoff, 'invalid symbol table range')
    require(stroff >= linkoff and 0 < strsize <= len(data) - stroff, 'invalid string table range')
    strings = data[stroff:stroff + strsize]
    symbols, stabs = [], 0
    for i in range(count):
        strx, typ, section, desc, value = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
        require(strx < strsize, 'symbol string outside table')
        end = strings.find(b'\0', strx)
        require(end != -1, 'unterminated symbol string')
        if typ & 0xe0:
            symbols.append(None)
            stabs += 1
        else:
            require(section <= section_count, 'symbol section index outside image')
            symbols.append((strings[strx:end] if strx else b'', typ, section, desc, value))
    groups = []
    cursor = 0
    for first, length in zip(dysymtab[2:8:2], dysymtab[3:8:2]):
        require(first == cursor and length <= count - first, 'noncontiguous symbol categories')
        cursor += length
        groups.append(symbol_digest([s for s in symbols[first:first + length] if s is not None]))
    require(cursor == count, 'symbol categories incomplete')
    indirectoff, indirectcount = dysymtab[14:16]
    require(indirectoff >= linkoff and indirectcount * 4 <= len(data) - indirectoff, 'invalid indirect symbols range')
    indirect_hash = hashlib.sha256()
    for i in range(indirectcount):
        index = struct.unpack_from('<I', data, indirectoff + 4 * i)[0]
        if index & 0xc0000000:
            require(index & 0x3fffffff == 0, 'invalid indirect sentinel')
            indirect_hash.update(b'F' + struct.pack('<I', index))
        else:
            require(index < count and symbols[index] is not None, 'indirect symbol missing or debug-only')
            sym = symbols[index]
            indirect_hash.update(b'S' + bytes.fromhex(symbol_digest([sym])))
    ranges += [(symoff, symoff + count * 16, 2), (stroff, stroff + strsize, 'strings'),
               (indirectoff, indirectoff + indirectcount * 4, 'indirect')]
    cursor = linkoff
    for start, end, kind in sorted(ranges, key=lambda row: (row[0], row[1])):
        if start == end:
            continue
        require(start >= cursor and end <= len(data), 'overlapping/out-of-range LINKEDIT data')
        require(not any(data[cursor:start]), 'unaccounted nonzero LINKEDIT bytes')
        cursor = end
    require(not any(data[cursor:]), 'unaccounted LINKEDIT tail')
    non_stab = [s for s in symbols if s is not None]
    return {'header': header, 'uuid': image_uuid(data), 'commands': normalized,
            'segments': segments, 'sections': section_rows, 'linkedit_offset': linkoff, 'linkedit_vmsize': linkedit_vmsize, 'first_file_data': first_file_data,
            'linkedit_blobs': blobs, 'symbols': symbol_digest(non_stab), 'symbol_count': len(non_stab),
            'symbol_groups': groups, 'indirect_symbols': indirect_hash.hexdigest(),
            'stab_count': stabs, 'signature_sha256': sha(signature) if signature is not None else None,
            'command_end': command_end, 'bytes': len(data), 'sha256': sha(data)}


def verify(before, after, *, require_reduction=True, preserve_stabs=False):
    old, new = snapshot(before), snapshot(after)
    for key in ('header', 'uuid', 'commands', 'segments', 'sections', 'linkedit_offset',
                'linkedit_blobs', 'symbols', 'symbol_count', 'symbol_groups', 'indirect_symbols'):
        require(old[key] == new[key], 'non-debug image semantics changed: ' + key)
    # Signature commands can be added/removed by strip/reseal, but everything
    # beyond both load-command tables and before LINKEDIT remains exact.
    require(new['linkedit_vmsize'] <= old['linkedit_vmsize'], 'LINKEDIT VM allocation grew')
    start = max(old['command_end'], new['command_end'])
    require(start <= min(old['first_file_data'], new['first_file_data']), 'expanded load commands overwrite section data')
    if new['command_end'] > old['command_end']:
        require(not any(before[old['command_end']:new['command_end']]), 'signature command overwrote nonzero header slack')
    elif new['command_end'] < old['command_end']:
        require(not any(after[new['command_end']:old['command_end']]), 'removed signature command left nonzero header slack')
    require(before[start:old['linkedit_offset']] == after[start:new['linkedit_offset']], 'non-LINKEDIT body changed')
    if preserve_stabs:
        require(not require_reduction, 'signature-only comparison cannot request stripping')
        require(old['stab_count'] == new['stab_count'], 'signature-only change altered debug records')
        def symbol_bytes(data):
            raw = next(raw for cmd, raw in commands(data)[2] if cmd == 2)
            offset, count, strings, size = struct.unpack_from('<4I', raw, 8)
            return data[offset:offset+count*16], data[strings:strings+size]
        require(symbol_bytes(before) == symbol_bytes(after), 'signature-only symbol/string tables changed')
    else:
        require(new['stab_count'] == 0, 'debug STAB records remain')
    if require_reduction:
        require(old['stab_count'] > 0 and len(after) < len(before), 'no debug-only size reduction')
    return {'before': old, 'after': new, 'removed_stabs': old['stab_count'] - new['stab_count'],
            'bytes_saved': len(before) - len(after), 'runtime_proven': False}


def signature_metadata(data):
    """Retain original ldid CodeDirectory identity/policy and requirements.

Page hashes and coverage size necessarily change after stripping; Apple codesign
must separately verify those bytes. Only the exact observed dual-hash v20400
ldid format is accepted here, with no XML/DER entitlement slots.
"""
    _, _, rows = commands(data)
    signatures = [raw for command, raw in rows if command == 0x1d]
    require(len(signatures) == 1 and len(signatures[0]) == 16, 'missing/duplicate signature command')
    offset, size = struct.unpack_from('<II', signatures[0], 8)
    require(offset <= len(data) and size <= len(data) - offset, 'signature outside image')
    blob = data[offset:offset+size]
    require(len(blob) >= 12, 'truncated signature')
    magic, length, count = struct.unpack_from('>3I', blob)
    require(magic == 0xfade0cc0 and 12 + count * 8 <= length <= len(blob) and count <= 16,
            'unsupported signature superblob')
    require(not any(blob[length:]), 'nonzero signature allocation padding')
    entries, directories, others, used = set(), [], {}, []
    for i in range(count):
        slot, start = struct.unpack_from('>2I', blob, 12 + i*8)
        require(slot not in entries and start >= 12+count*8 and start <= length-8, 'invalid/duplicate signature slot')
        entries.add(slot)
        cmagic, clen = struct.unpack_from('>2I', blob, start)
        require(8 <= clen <= length-start and all(start+clen <= a or start >= b for a,b in used), 'signature child bounds/overlap')
        used.append((start,start+clen))
        child = blob[start:start+clen]
        if slot in (0, 0x1000):
            require(cmagic == 0xfade0c02 and clen >= 88, 'invalid CodeDirectory')
            version, flags, hash_offset, ident_offset, special, pages, limit = struct.unpack_from('>7I', child, 8)
            hashsize, hashtype, platform, pagepower = struct.unpack_from('>4B', child, 36)
            require(version == 0x20400 and special <= 64 and (hashsize,hashtype) in ((20,1),(32,2)) and pagepower <= 20,
                    'unreviewed CodeDirectory layout')
            require(limit == offset and pages == (limit + (1<<pagepower)-1)//(1<<pagepower), 'CodeDirectory coverage differs')
            require(hash_offset >= special*hashsize and hash_offset + pages*hashsize == clen, 'CodeDirectory hashes outside child')
            require(88 <= ident_offset < hash_offset-special*hashsize, 'invalid signing identifier offset')
            end = child.find(b'\0', ident_offset, hash_offset-special*hashsize)
            require(end >= ident_offset, 'unterminated signing identifier')
            spare2, scatter, team, spare3 = struct.unpack_from('>4I', child, 40)
            limit64, execbase, execlimit, execflags = struct.unpack_from('>4Q', child, 56)
            require(spare2 == scatter == team == spare3 == limit64 == 0, 'unsupported signing team/scatter/large coverage')
            directories.append((slot, version, flags, hash_offset, special, hashsize, hashtype, platform, pagepower,
                                child[ident_offset:end].hex(), team, execbase, execlimit, execflags))
        else:
            require(slot == 2 and cmagic == 0xfade0c01, 'unreviewed entitlement/CMS/signature slot')
            others[slot] = sha(child)
    require(entries == {0, 2, 0x1000}, 'original dual-hash/requirements signature closure differs')
    return {'directories': sorted(directories), 'other_slots': others}


def _checked_signature_entries(data):
    rows = [raw for cmd, raw in commands(data)[2] if cmd == 0x1d]
    require(len(rows) == 1 and len(rows[0]) == 16, 'one signature command required')
    offset, allocated = struct.unpack_from('<II', rows[0], 8)
    require(offset <= len(data) and allocated == len(data)-offset, 'signature must occupy exact image tail')
    blob = data[offset:]
    require(len(blob) >= 12, 'signature header truncated')
    magic, length, count = struct.unpack_from('>3I', blob)
    require(magic == 0xfade0cc0 and count <= 16 and 12+count*8 <= length <= len(blob), 'invalid signature index')
    require(not any(blob[length:]), 'nonzero signature padding')
    entries, ranges = {}, []
    for i in range(count):
        slot, start = struct.unpack_from('>2I', blob, 12+8*i)
        require(slot not in entries and 12+count*8 <= start <= length-8, 'duplicate/out-of-bounds signature slot')
        size = struct.unpack_from('>I', blob, start+4)[0]
        require(8 <= size <= length-start, 'signature child outside superblob')
        ranges.append((start,start+size)); entries[slot] = blob[start:start+size]
    cursor = 12+count*8
    for start, end in sorted(ranges):
        require(start >= cursor and not any(blob[cursor:start]), 'overlap/nonzero signature gaps')
        cursor = end
    require(not any(blob[cursor:length]), 'nonzero signature trailing gap')
    return offset, allocated-length, entries


def _verify_code_directory_hashes(data, offset, entries, expected_flags):
    policies = []
    for slot, hashsize, hashtype in ((0,20,1),(0x1000,32,2)):
        child = entries[slot]
        require(len(child) >= 88, 'CodeDirectory truncated')
        fields = struct.unpack_from('>9I4B4I4Q', child)
        magic, length, version, flags, hashes, ident, special, pages, limit = fields[:9]
        hs, ht, platform, pagepower = fields[9:13]
        spare2, scatter, team, spare3, limit64, execbase, execlimit, execflags = fields[13:]
        require((magic,length,version,flags,hs,ht,platform,pagepower,ident,special) ==
                (0xfade0c02,len(child),0x20400,expected_flags,hashsize,hashtype,0,12,88,2),
                'unreviewed ad-hoc CodeDirectory policy')
        require(spare2 == scatter == team == spare3 == limit64 == execflags == 0,
                'unexpected team/scatter/runtime signing policy')
        require(child[88:98] == b'SideStore\0' and hashes == 98+special*hashsize,
                'signing identifier/hash layout changed')
        require(limit == offset and pages == (limit+4095)//4096 and hashes+pages*hashsize == length,
                'CodeDirectory page coverage changed')
        digest = hashlib.sha1 if hashtype == 1 else hashlib.sha256
        for page in range(pages):
            expected = digest(data[page*4096:min((page+1)*4096,limit)]).digest()
            require(child[hashes+page*hashsize:hashes+(page+1)*hashsize] == expected,
                    'CodeDirectory page hash mismatch')
        require(child[hashes-hashsize:hashes] == bytes(hashsize), 'unexpected Info special-slot hash')
        require(child[hashes-2*hashsize:hashes-hashsize] == digest(entries[2]).digest(),
                'requirements special-slot hash mismatch')
        policies.append((slot,version,hashtype,execbase,execlimit,execflags))
    return policies


def verify_apple_adhoc_reseal(original, final):
    """Reusable final-product guard, independent of any artifact/run SHA.

The finite policy intentionally replaces the original designated requirement
with empty ad-hoc requirements. It is not provisioning or installer signing.
Apple strict verification remains a separate mandatory native-tool gate.
Zero allocation padding may vary by signer sizing; it cannot exceed the
original VM allocation or defeat the required final binary size reduction.
"""
    structural = verify(original, final)
    signature_metadata(original)  # Existing original ldid closure remains mandatory.
    old_offset, _, old_entries = _checked_signature_entries(original)
    offset, padding, entries = _checked_signature_entries(final)
    require(set(entries) == {0,2,0x1000,0x10000}, 'unreviewed ad-hoc signature slots')
    require(entries[2] == bytes.fromhex('fade0c010000000c00000000'), 'ad-hoc requirements differ')
    require(entries[0x10000] == bytes.fromhex('fade0b0100000008'), 'ad-hoc CMS must be empty wrapper')
    old_policy = _verify_code_directory_hashes(original, old_offset, old_entries, 0)
    policy = _verify_code_directory_hashes(final, offset, entries, 2)
    require(old_policy == policy, 'signing executable geometry/hash algorithms changed')
    return {'structural': structural, 'signature_policy': 'reviewed_apple_adhoc_v20400_dual_hash',
            'all_code_pages_and_special_slots_verified': True, 'signature_zero_padding_bytes': padding,
            'original_requirements_sha256': sha(old_entries[2]), 'final_requirements_sha256': sha(entries[2]),
            'original_designated_requirement_preserved': False,
            'installer_resigning_required': True, 'device_execution_verified': False}
