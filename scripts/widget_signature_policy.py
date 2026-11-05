"""Finite current ldid widget signing policy, not Apple trust/runtime acceptance.

Policy measured from verified recovery 40250137449. XML/DER/requirements are
immutable current recipe outputs, not generalized entitlement authorization.
Info.plist and CodeResources slots are zero in this recipe and are NOT claimed
cryptographically covered. Existing package semantic gates remain mandatory.
"""
import hashlib
import struct
from macho_stab_guard import commands, require, sha, _checked_signature_entries

CHILD_POLICY = {
    2: (136, '7f0ac3f1bca90e16ed9e3c59d7450c031035aa220d4b711b9a47745dd017a9b3'),
    5: (524, '2fdf4a48802d400837a70895672716361e6cd38a23a84b281d46b08d02869e82'),
    7: (228, '66fd7d89f2128c4455a07d298422cf6fba0cd2e5bcc606ce8f1e601037997e0a'),
}
IDENTIFIER = b'LiveWidgetExtension\0'


def verify_widget_signature(data):
    header, command_end, rows = commands(data)
    require(header[2] == 2 and sum(cmd == 0x80000028 for cmd, _ in rows) == 1,
            'widget must remain executable with one entrypoint')
    texts = [raw for cmd, raw in rows if cmd == 0x19 and raw[8:24].split(b'\0')[0] == b'__TEXT']
    require(len(texts) == 1 and len(texts[0]) >= 72 and
            struct.unpack_from('<2Q', texts[0], 40) == (0,2588672),
            'widget executable segment geometry differs')
    offset, padding, entries = _checked_signature_entries(data)
    require(offset >= 2588672, 'widget executable segment exceeds signed coverage')
    require(offset >= command_end, 'signature overlaps widget commands')
    require(set(entries) == {0, 2, 5, 7, 0x1000}, 'widget signature slot closure differs')
    for slot, (length, expected) in CHILD_POLICY.items():
        require(len(entries[slot]) == length and sha(entries[slot]) == expected,
                'widget requirements/XML/DER recipe changed')
    pages_checked = {}
    for slot, hs, ht, algorithm in ((0, 20, 1, hashlib.sha1), (0x1000, 32, 2, hashlib.sha256)):
        child = entries[slot]
        require(len(child) >= 88, 'truncated widget CodeDirectory')
        fields = struct.unpack_from('>9I4B4I4Q', child)
        magic, length, version, flags, hashes, ident, special, pages, limit = fields[:9]
        hashsize, hashtype, platform, pagepower = fields[9:13]
        spare2, scatter, team, spare3, limit64, execbase, execlimit, execflags = fields[13:]
        require((magic,length,version,flags,ident,special,hashsize,hashtype,platform,pagepower) ==
                (0xfade0c02,len(child),0x20400,0,88,7,hs,ht,0,12),
                'widget CodeDirectory policy differs')
        require(spare2 == scatter == team == spare3 == limit64 == 0 and
                (execbase,execlimit,execflags) == (0,2588672,17),
                'widget team/scatter/executable policy differs')
        require(child[88:88+len(IDENTIFIER)] == IDENTIFIER and hashes == 88+len(IDENTIFIER)+7*hs,
                'widget identifier/hash layout differs')
        require(limit == offset and pages == (offset+4095)//4096 and hashes+pages*hs == length,
                'widget page coverage differs')
        for page in range(pages):
            expected = algorithm(data[page*4096:min((page+1)*4096,offset)]).digest()
            require(child[hashes+page*hs:hashes+(page+1)*hs] == expected,
                    'widget code page hash mismatch')
        for special_slot in range(1,8):
            expected = algorithm(entries[special_slot]).digest() if special_slot in CHILD_POLICY else bytes(hs)
            require(child[hashes-special_slot*hs:hashes-(special_slot-1)*hs] == expected,
                    'widget special slot hash mismatch')
        pages_checked[str(ht)] = pages
    return {'policy':'current_ldid_widget_v20400_flags0_dual_hash',
            'signed_binary_sha256':sha(data), 'code_pages_verified':pages_checked,
            'special_slots_verified':[2,5,7], 'zero_special_slots':[1,3,4,6],
            'info_plist_cryptographically_covered':False, 'code_resources_cryptographically_covered':False,
            'signature_padding_bytes':padding, 'recipe_child_sha256':{str(k):sha(entries[k]) for k in CHILD_POLICY},
            'apple_trust_or_device_execution_verified':False}
