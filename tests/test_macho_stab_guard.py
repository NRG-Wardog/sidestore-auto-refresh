from pathlib import Path
import struct
import hashlib
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from macho_stab_guard import snapshot, verify, commands, signature_metadata, verify_apple_adhoc_reseal, _checked_signature_entries


def fixture(debug=True, *, local=b'_local', indirect_wrong=False, payload=b'export-trie', signature=False):
    names = ([b'debug.c'] if debug else []) + [local, b'_public', b'_undefined']
    strings, records = b' \0', []
    for i, name in enumerate(names):
        idx = len(strings); strings += name + b'\0'
        typ = 0x64 if debug and i == 0 else (2 if name == local else 3 if name == b'_public' else 1)
        records.append(struct.pack('<IBBHQ', idx, typ, 0, 0, 0 if typ == 1 else 0x1234))
    symbol_bytes = b''.join(records)
    symoff = 512 + len(payload)
    stroff = symoff + len(symbol_bytes)
    indirectoff = stroff + len(strings)
    pub = len(records) - 2
    indirect = struct.pack('<3I', pub - 1 if indirect_wrong else pub, pub + 1, 0x80000000)
    tail = payload + symbol_bytes + strings + indirect
    sigoff = 512 + len(tail)
    if signature: tail += b'synthetic-code-signature'
    def segment(name, address, off, size, vm_size):
        return struct.pack('<II16s4Q4I', 0x19, 72, name, address, vm_size, off, size, 7, 5, 0, 0)
    commands = [segment(b'__TEXT', 0x1000, 0, 512, 512), segment(b'__LINKEDIT', 0x2000, 512, len(tail), len(tail)),
        struct.pack('<6I', 2, 24, symoff, len(records), stroff, len(strings)),
        struct.pack('<20I', 0xb, 80, 0, len(records)-2, len(records)-2, 1, len(records)-1, 1,
                    0, 0, 0, 0, 0, 0, indirectoff, 3, 0, 0, 0, 0),
        struct.pack('<II16s', 0x1b, 24, b'0123456789abcdef'),
        struct.pack('<4I', 0x80000033, 16, 512, len(payload)),
        struct.pack('<IIQQ', 0x80000028, 24, 500, 0),
        struct.pack('<III12s', 0xe, 24, 12, b'/usr/dyld\0')]
    if signature: commands.append(struct.pack('<4I', 0x1d, 16, sigoff, len(tail)-(sigoff-512)))
    joined = b''.join(commands)
    header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6, len(commands), len(joined), 0x80, 0)
    body = header + joined
    return body + b'\0' * (500-len(body)) + b'CODE_CONTENT' + tail


def alter_command(data, command, offset, value, fmt='<I'):
    result = bytearray(data); pos = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, size = struct.unpack_from('<II', data, pos)
        if cmd == command:
            struct.pack_into(fmt, result, pos+offset, value)
            return bytes(result)
        pos += size
    raise AssertionError(command)


def signed_fixture(debug=True, *, adhoc=False, correct_hashes=False):
    data = fixture(debug, signature=True)
    _, _, rows = commands(data)
    sig = next(raw for cmd, raw in rows if cmd == 0x1d)
    off = struct.unpack_from('<I', sig, 8)[0]
    children = []
    for slot, hashsize, hashtype in [(0, 20, 1), (0x1000, 32, 2)]:
        ident = b'SideStore\0'
        pages = (off + 4095) // 4096
        hoff = 88 + len(ident) + 2*hashsize
        length = hoff + pages*hashsize
        child = struct.pack('>9I4B4I4Q', 0xfade0c02, length, 0x20400, 2 if adhoc else 0, hoff, 88, 2, pages, off,
                            hashsize, hashtype, 0, 12, 0, 0, 0, 0, 0, 0, 512, 0)
        children.append((slot, child + ident + b'\0'*(hashsize*(2+pages))))
    children.insert(1, (2, struct.pack('>3I', 0xfade0c01, 12, 0)))
    if adhoc:
        children.append((0x10000, bytes.fromhex('fade0b0100000008')))
    pos = 12 + 8*len(children)
    index, body = b'', b''
    for slot, child in children:
        index += struct.pack('>2I', slot, pos); body += child; pos += len(child)
    blob = struct.pack('>3I', 0xfade0cc0, pos, len(children)) + index + body
    result = data[:off] + blob
    result = alter_command(result, 0x1d, 12, len(blob))
    # Select LINKEDIT (the second segment) rather than TEXT.
    result = bytearray(result)
    struct.pack_into('<Q', result, 32+72+32, len(result)-512)
    struct.pack_into('<Q', result, 32+72+48, len(result)-512)
    if correct_hashes:
        for i in range(len(children)):
            slot, start = struct.unpack_from('>2I', result, off+12+8*i)
            if slot not in (0,0x1000): continue
            child = off+start
            hoff, _, special, pages, limit = struct.unpack_from('>5I',result,child+16)
            hs, ht = struct.unpack_from('>2B',result,child+36)
            digest = hashlib.sha1 if ht == 1 else hashlib.sha256
            result[child+hoff-2*hs:child+hoff-hs] = digest(bytes.fromhex('fade0c010000000c00000000')).digest()
            for page in range(pages):
                result[child+hoff+page*hs:child+hoff+(page+1)*hs] = digest(result[page*4096:min((page+1)*4096,limit)]).digest()
    return bytes(result)


class StabGuardTests(unittest.TestCase):
    def test_only_debug_removal_and_symbol_index_rebase_accepted(self):
        result = verify(fixture(), fixture(False))
        self.assertEqual(result['removed_stabs'], 1)
        self.assertGreater(result['bytes_saved'], 0)
        self.assertFalse(result['runtime_proven'])

    def test_signature_delta_is_explicitly_separate(self):
        result = verify(fixture(signature=True), fixture(False, signature=True))
        self.assertEqual(result['before']['signature_sha256'], result['after']['signature_sha256'])
        verify(fixture(signature=True), fixture(False))

    def test_local_public_undefined_symbol_semantics_retained(self):
        with self.assertRaisesRegex(ValueError, 'symbols'):
            verify(fixture(), fixture(False, local=b'_changed'))
        after = bytearray(fixture(False))
        after[after.index(b'_undefined')] = ord('X')
        with self.assertRaisesRegex(ValueError, 'symbols'):
            verify(fixture(), bytes(after))

    def test_indirect_symbols_compare_meaning_not_raw_indices(self):
        with self.assertRaisesRegex(ValueError, 'indirect_symbols'):
            verify(fixture(), fixture(False, indirect_wrong=True))

    def test_code_exports_uuid_and_unusual_executable_commands_reject_changes(self):
        good = fixture(False)
        changed_code = good[:500] + b'EVIL_CONTENT' + good[512:]
        for changed in (changed_code, fixture(False, payload=b'changed-tri'),
                        alter_command(good, 0x1b, 8, 42), alter_command(good, 0x80000028, 8, 501, '<Q'),
                        alter_command(good, 0xe, 12, 42)):
            with self.subTest(changed=changed[500:512]), self.assertRaises(ValueError):
                verify(fixture(), changed)

    def test_debug_records_must_be_removed_and_result_smaller(self):
        with self.assertRaisesRegex(ValueError, 'STAB records remain'):
            verify(fixture(), fixture())
        with self.assertRaisesRegex(ValueError, 'no debug-only size reduction'):
            verify(fixture(False), fixture(False))

    def test_expanded_signature_command_cannot_overwrite_nonzero_header_slack(self):
        before = bytearray(fixture())
        end = commands(before)[1]
        before[end + 1] = 1
        with self.assertRaisesRegex(ValueError, 'nonzero header slack'):
            verify(bytes(before), fixture(False, signature=True))

    def test_linkedit_vm_growth_and_malformed_path_command_reject(self):
        after = bytearray(fixture(False))
        struct.pack_into('<Q', after, 32+72+32, 0x100000)
        with self.assertRaisesRegex(ValueError, 'VM allocation grew'):
            verify(fixture(), bytes(after))
        with self.assertRaisesRegex(ValueError, 'string offset'):
            snapshot(alter_command(fixture(), 0xe, 8, 0))

    def test_section_cannot_point_into_linkedit_outside_owning_segment(self):
        data = fixture()
        _, end, rows = commands(data)
        text = bytearray(rows[0][1])
        struct.pack_into('<I', text, 4, 152)
        struct.pack_into('<I', text, 64, 1)
        section = struct.pack('<16s16sQQ8I', b'__bad', b'__TEXT', 0x1000, 1, 512, 0, 0, 0, 0, 0, 0, 0)
        raw = bytes(text) + section + b''.join(r for _,r in rows[1:])
        header = bytearray(data[:32]); struct.pack_into('<I', header, 20, len(raw))
        malformed = bytes(header) + raw + b'\0'*(500-32-len(raw)) + data[500:]
        with self.assertRaisesRegex(ValueError, 'owning file segment'):
            snapshot(malformed)

    def test_signing_identity_policy_hash_algorithms_and_requirements_are_retained(self):
        old, new = signed_fixture(), signed_fixture(False)
        self.assertEqual(signature_metadata(old), signature_metadata(new))
        changed = bytearray(new)
        _,_,rows = commands(new)
        off = struct.unpack_from('<I', next(raw for cmd,raw in rows if cmd==0x1d), 8)[0]
        first = struct.unpack_from('>I', changed, off+16)[0]
        struct.pack_into('>I', changed, off+first+12, 2)
        self.assertNotEqual(signature_metadata(old), signature_metadata(bytes(changed)))
        changed = bytearray(new); struct.pack_into('>I', changed, off+12+8, 5)
        with self.assertRaisesRegex(ValueError, 'unreviewed entitlement'):
            signature_metadata(bytes(changed))

    def test_signature_only_probe_retains_full_debug_records_and_default_still_rejects(self):
        original = fixture()
        receipt = verify(original, original, require_reduction=False, preserve_stabs=True)
        self.assertEqual(receipt['removed_stabs'], 0)
        with self.assertRaisesRegex(ValueError, 'debug STAB records remain'):
            verify(original, original, require_reduction=False)
        changed = original.replace(b'debug.c', b'other.c')
        with self.assertRaisesRegex(ValueError, 'symbol/string tables changed'):
            verify(original, changed, require_reduction=False, preserve_stabs=True)
        with self.assertRaisesRegex(ValueError, 'altered debug records'):
            verify(original, fixture(False), require_reduction=False, preserve_stabs=True)

    def test_finite_apple_adhoc_policy_and_complete_hashes(self):
        old = signed_fixture(correct_hashes=True)
        new = signed_fixture(False, adhoc=True, correct_hashes=True)
        receipt = verify_apple_adhoc_reseal(old, new)
        self.assertTrue(receipt['all_code_pages_and_special_slots_verified'])
        self.assertFalse(receipt['original_designated_requirement_preserved'])
        self.assertTrue(receipt['installer_resigning_required'])
        offset = struct.unpack_from('<I', next(raw for cmd,raw in commands(new)[2] if cmd==0x1d),8)[0]
        starts = {struct.unpack_from('>I',new,offset+12+8*i)[0]: offset+struct.unpack_from('>I',new,offset+16+8*i)[0]
                  for i in range(4)}
        cd = starts[0]; hoff = struct.unpack_from('>I',new,cd+16)[0]
        mutations = {'page': cd+hoff, 'special': cd+hoff-20, 'flags': cd+15,
                     'team': cd+51, 'requirements': starts[2]+11, 'CMS': starts[65536]+7,
                     'coverage': cd+35, 'identifier': cd+88, 'runtime': cd+87}
        for name, index in mutations.items():
            changed = bytearray(new); changed[index] ^= 1
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_apple_adhoc_reseal(old, bytes(changed))
        padded = alter_command(new + bytes(16), 0x1d, 12, len(new)-offset+16)
        self.assertEqual(_checked_signature_entries(padded)[1], 16)
        corrupt_padding = padded[:-1] + b'\x01'
        with self.assertRaisesRegex(ValueError, 'nonzero signature padding'):
            _checked_signature_entries(corrupt_padding)
        sha256_cd = starts[0x1000]
        sha256_hash = struct.unpack_from('>I', new, sha256_cd+16)[0]
        bad_sha256 = bytearray(new); bad_sha256[sha256_cd+sha256_hash] ^= 1
        with self.assertRaisesRegex(ValueError, 'page hash mismatch'):
            verify_apple_adhoc_reseal(old, bytes(bad_sha256))
        old_offset = struct.unpack_from('<I', next(raw for cmd,raw in commands(old)[2] if cmd==0x1d),8)[0]
        old_cd = old_offset + struct.unpack_from('>I', old, old_offset+16)[0]
        old_hash = struct.unpack_from('>I', old, old_cd+16)[0]
        bad_original = bytearray(old); bad_original[old_cd+old_hash] ^= 1
        with self.assertRaisesRegex(ValueError, 'page hash mismatch'):
            verify_apple_adhoc_reseal(bytes(bad_original), new)
        changed = bytearray(new + b'\x01')
        changed = alter_command(bytes(changed), 0x1d,12,len(new)-offset+1)
        with self.assertRaises(ValueError): verify_apple_adhoc_reseal(old,changed)

    def test_malformed_and_unsupported_images_reject(self):
        original = fixture()
        for changed in (b'\xca\xfe\xba\xbe' + original[4:], original[:10],
                        alter_command(original, 2, 8, 0xfffffff0),
                        alter_command(original, 2, 20, 0xfffffff0),
                        alter_command(original, 0xb, 36, 1),
                        alter_command(original, 0x80000028, 0, 0xdeadbeef),
                        original[:-1], original+b'nonzero'):
            with self.subTest(length=len(changed)), self.assertRaises(ValueError):
                snapshot(changed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
