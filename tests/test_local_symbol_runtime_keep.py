import struct
import unittest
from test_macho_stab_guard import fixture,alter_command,commands
from macho_local_symbol_guard import runtime_save_list,verify_local_removal,symbol_rows
from package_livecontainer_combined import REQUIRED_SIDESTORE_INTENT_SYMBOLS


def runtime_fixture(*,discard=True,missing=None,unsafe=False,changed_value=False):
    roots=[b'_$s'+stem+suffix for stem in REQUIRED_SIDESTORE_INTENT_SYMBOLS for suffix in (b'Ma',b'Mn',b'N')]
    locals=[(n,0x12) for n in roots if n!=missing]+([(b'_discard',2)] if discard else [])
    names=locals+[(b'_public',3),(b'_unsafe\nname' if unsafe else b'_undefined',1)]
    strings=b' \0';records=[]
    for name,kind in names:
        idx=len(strings);strings+=name+b'\0'
        value=0 if kind==1 else (0x9999 if changed_value and name==roots[0] else 0x1234)
        records.append(struct.pack('<IBBHQ',idx,kind,0,0,value))
    base=fixture(False);raw=next(r for c,r in commands(base)[2] if c==2);offset=struct.unpack_from('<I',raw,8)[0]
    stroff=offset+len(records)*16;indirectoff=stroff+len(strings)
    body=b''.join(records)+strings+struct.pack('<3I',len(locals),len(locals)+1,0x80000000)
    result=base[:offset]+body
    for field,value in ((12,len(records)),(16,stroff),(20,len(strings))):result=alter_command(result,2,field,value)
    for field,value in ((12,len(locals)),(16,len(locals)),(24,len(locals)+1),(56,indirectoff)):
        result=alter_command(result,0xb,field,value)
    result=bytearray(result);pos=32
    for cmd,raw in commands(bytes(result))[2]:
        if cmd==0x19 and raw[8:24].rstrip(b'\0')==b'__LINKEDIT':
            for field in (32,48):struct.pack_into('<Q',result,pos+field,len(result)-512)
        pos+=len(raw)
    return bytes(result)


class RuntimeKeepTests(unittest.TestCase):
    def test_full_private_runtime_and_protected_external_names_are_listed(self):
        before=runtime_fixture();content,receipt=runtime_save_list(before)
        self.assertEqual(receipt['runtime_adapter_tuple_count'],6)
        self.assertEqual(receipt['keep_name_count'],8)
        self.assertIn(b'_undefined\n',content);self.assertIn(b'_public\n',content)
        self.assertNotIn(b'_discard\n',content)
        result=verify_local_removal(before,runtime_fixture(discard=False))
        self.assertEqual(result['runtime_adapter_private_tuples_preserved'],6)
        self.assertEqual(result['removed_local_records'],1)

    def test_absent_root_and_unsafe_keep_list_names_fail_closed(self):
        root=b'_$s'+REQUIRED_SIDESTORE_INTENT_SYMBOLS[0]+b'Ma'
        with self.assertRaisesRegex(ValueError,'metadata root'):runtime_save_list(runtime_fixture(missing=root))
        with self.assertRaisesRegex(ValueError,'unsafe keep-list'):runtime_save_list(runtime_fixture(unsafe=True))

    def test_guard_rejects_any_dropped_or_modified_required_tuple(self):
        before=runtime_fixture();root=b'_$s'+REQUIRED_SIDESTORE_INTENT_SYMBOLS[0]+b'Mn'
        for after in (runtime_fixture(discard=False,missing=root),runtime_fixture(discard=False,changed_value=True)):
            with self.assertRaisesRegex(ValueError,'protected'):verify_local_removal(before,after)

if __name__=='__main__':unittest.main()
