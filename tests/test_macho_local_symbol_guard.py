import struct
import unittest
from test_macho_stab_guard import fixture, alter_command
from macho_local_symbol_guard import classify, verify_local_removal
from macho_stab_guard import commands


def remove_fixture_local(data):
    """Synthetic nlist deletion for tests only. Never used to alter real artifacts."""
    rows = commands(data)[2]
    sym = next(raw for cmd,raw in rows if cmd==2)
    off,count,strings,size=struct.unpack_from('<4I',sym,8)
    dysym=struct.unpack('<20I',next(raw for cmd,raw in rows if cmd==0xb))
    result=data[:off]+data[off+16:]
    result=alter_command(result,2,12,count-1)
    result=alter_command(result,2,16,strings-16)
    for offset,value in ((12,0),(16,0),(24,1),(56,dysym[14]-16)):
        result=alter_command(result,0xb,offset,value)
    # second segment is LINKEDIT; first __TEXT must remain unchanged.
    result=bytearray(result);pos=32
    for cmd,raw in commands(bytes(result))[2]:
        if cmd==0x19 and raw[8:24].rstrip(b'\0')==b'__LINKEDIT':
            for field in (32,48):struct.pack_into('<Q',result,pos+field,struct.unpack_from('<Q',raw,field)[0]-16)
        pos+=len(raw)
    for i in range(dysym[15]):
        location=dysym[14]-16+4*i;value=struct.unpack_from('<I',result,location)[0]
        if not value&0xc0000000:struct.pack_into('<I',result,location,value-1)
    return bytes(result)


class LocalSymbolGuardTests(unittest.TestCase):
    def test_exact_local_deletion_with_semantic_indirect_rebase(self):
        before=fixture(False);after=remove_fixture_local(before)
        report=verify_local_removal(before,after)
        self.assertEqual(report['removed_local_records'],1)
        self.assertFalse(report['packaging_approved'])

    def test_stabs_and_no_reduction_rejected(self):
        with self.assertRaisesRegex(ValueError,'STAB'):classify(fixture(True))
        with self.assertRaisesRegex(ValueError,'no local-symbol'):verify_local_removal(fixture(False),fixture(False))

    def test_export_undefined_metadata_code_uuid_mutations_rejected(self):
        before=fixture(False);after=remove_fixture_local(before)
        for old,new in ((b'_public',b'_publix'),(b'_undefined',b'_undefineX'),
                        (b'export-trie',b'export-triX'),(b'CODE_CONTENT',b'CODE_CONTENX'),
                        (b'0123456789abcdef',b'X123456789abcdef')):
            with self.subTest(old=old),self.assertRaises(ValueError):
                verify_local_removal(before,after.replace(old,new))

    def test_indirect_referenced_local_is_protected(self):
        before=fixture(False,indirect_wrong=True)
        _,removable,protected=classify(before)
        self.assertFalse(removable)
        self.assertEqual(sum(protected.values()),3)
        with self.assertRaises((ValueError,struct.error)):
            verify_local_removal(before,remove_fixture_local(fixture(False)))

    def test_local_mutation_is_not_removal(self):
        with self.assertRaisesRegex(ValueError,'added or modified'):
            verify_local_removal(fixture(False),fixture(False,local=b'_other'),require_reduction=False)

    def test_nonlocal_absolute_private_and_undefined_stay_protected(self):
        original=fixture(False)
        raw=next(r for c,r in commands(original)[2] if c==2)
        offset=struct.unpack_from('<I',raw,8)[0]
        for kind in (1,3,0xa):
            changed=bytearray(original);changed[offset+4]=kind
            _,removable,_=classify(bytes(changed))
            self.assertFalse(removable)

if __name__=='__main__':unittest.main()
