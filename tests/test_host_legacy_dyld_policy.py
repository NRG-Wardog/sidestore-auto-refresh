"""Host-only legacy dyld structural controls. Never executes synthetic images."""
import struct
import unittest
from test_macho_stab_guard import fixture, alter_command
from test_macho_local_symbol_guard import remove_fixture_local
from macho_stab_guard import commands, snapshot, verify, sha
from macho_local_symbol_guard import verify_local_removal
from openssl_local_symbol_policy import verify_openssl_local_removal


def host_fixture(debug=False, *, platform=1, empty=False, duplicate=False):
    payload=b'' if empty else b'ABCDEabcde!'
    data=fixture(debug,payload=payload)
    rows=[]
    pairs=[0]*10 if empty else [512,2,514,2,516,2,518,2,520,3]
    legacy=struct.pack('<12I',0x80000022,48,*pairs)
    for cmd,raw in commands(data)[2]:
        if cmd==0x80000033: rows.append(legacy)
        elif cmd!=0x80000028:rows.append(raw)
    rows.append(struct.pack('<6I',0x32,24,platform,0xb0000,0x1a0000,0))
    if duplicate:rows.append(legacy)
    joined=b''.join(rows);assert 32+len(joined)<=500
    header=bytearray(data[:32]);struct.pack_into('<2I',header,16,len(rows),len(joined))
    return bytes(header)+joined+bytes(500-32-len(joined))+data[500:]

class HostLegacyDyldTests(unittest.TestCase):
    def test_explicit_mac_only_and_five_payloads(self):
        data=host_fixture()
        with self.assertRaisesRegex(ValueError,'unreviewed load'):snapshot(data)
        result=snapshot(data,host_legacy_dyld=True)
        self.assertEqual(len(result['linkedit_blobs']),5)
        self.assertEqual([row[1] for row in result['linkedit_blobs']],list(range(5)))
    def test_wrong_platform_or_executable_rejected(self):
        for data in (host_fixture(platform=2),alter_command(host_fixture(),0x32,8,7)):
            with self.assertRaisesRegex(ValueError,'macOS'):snapshot(data,host_legacy_dyld=True)
        with self.assertRaises(ValueError):snapshot(host_fixture(),host_legacy_dyld=True,unsigned_executable=True)
    def test_duplicate_and_truncated_command(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):snapshot(host_fixture(duplicate=True),host_legacy_dyld=True)
        with self.assertRaises(ValueError):snapshot(alter_command(host_fixture(),0x80000022,4,40),host_legacy_dyld=True)
    def test_overlap_bounds_gap(self):
        for offset,value in ((16,512),(8,1),(8,0xffffffff),(12,0xffffffff),(12,1)):
            with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):snapshot(alter_command(host_fixture(),0x80000022,offset,value),host_legacy_dyld=True)
    def test_empty_payload_offset_must_be_zero(self):
        snapshot(host_fixture(empty=True),host_legacy_dyld=True)
        with self.assertRaisesRegex(ValueError,'bounds'):snapshot(alter_command(host_fixture(empty=True),0x80000022,8,512),host_legacy_dyld=True)
    def test_every_payload_byte_retained(self):
        before=host_fixture(True);after=host_fixture(False)
        verify(before,after,host_legacy_dyld=True)
        for index in (512,514,516,518,520):
            bad=bytearray(after);bad[index]^=1
            with self.subTest(index=index),self.assertRaises(ValueError):verify(before,bytes(bad),host_legacy_dyld=True)
    def test_host_local_removal_and_default_policy_reject(self):
        before=host_fixture();after=remove_fixture_local(before)
        result=verify_local_removal(before,after,host_legacy_dyld=True)
        self.assertEqual(result['removed_local_records'],1)
        with self.assertRaises(ValueError):verify_local_removal(before,after)
        kwargs={'expected_before_sha256':sha(before),'dwarf':before,'expected_dwarf_sha256':sha(before)}
        verify_openssl_local_removal(before,after,host_legacy_dyld=True,**kwargs)
        with self.assertRaises(ValueError):verify_openssl_local_removal(before,after,**kwargs)
    def test_mixed_modern_commands_rejected(self):
        data=bytearray(host_fixture());position=32
        for cmd,raw in commands(data)[2]:
            if cmd==0xe:
                struct.pack_into('<I',data,position,0x80000033);break
            position+=len(raw)
        with self.assertRaisesRegex(ValueError,'mixed'):snapshot(bytes(data),host_legacy_dyld=True)

if __name__=='__main__':unittest.main()
