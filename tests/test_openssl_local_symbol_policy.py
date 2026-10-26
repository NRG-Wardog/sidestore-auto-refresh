"""Synthetic structural policy controls, no Apple strip/crypto execution."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import openssl_local_symbol_policy as gate
from test_macho_stab_guard import fixture
from test_macho_local_symbol_guard import remove_fixture_local
from macho_stab_guard import commands
import struct

class OpenSSLLocalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(strict=True)
        self.before=fixture(False);self.after=remove_fixture_local(self.before)
        self.inputs={}
        for owner in gate.OWNERS:
            bundle=self.root/owner/'OpenSSL.framework.dSYM';dwarf=bundle/'Contents/Resources/DWARF/OpenSSL'
            dwarf.parent.mkdir(parents=True);dwarf.write_bytes(self.before)
            (bundle/'Contents/Info.plist').write_bytes(b'synthetic plist')
            self.inputs[owner]={'before':self.before,'after':self.after,'before_sha256':gate.sha(self.before),
                'dsym':bundle,'dsym_inventory':gate.retained_inventory(bundle)}
    def verify(self):return gate.verify_openssl_owner_pair(self.inputs)
    def test_both_owners_full_symbols_and_honest_evidence(self):
        result=self.verify();self.assertEqual(result['raw_bytes_saved_total'],32)
        self.assertFalse(result['packaging_approved']);self.assertFalse(result['transformed_host_crypto_qualified'])
        self.assertFalse(result['compressed_savings_attributed'])
    def test_missing_or_additional_owner_rejected(self):
        for owners in ({'LiveContainer':self.inputs['LiveContainer']},{**self.inputs,'other':self.inputs['SideStore']}):
            with self.assertRaises(ValueError):gate.verify_openssl_owner_pair(owners)
    def test_unsealed_source_hash_rejected(self):
        self.inputs['SideStore']['before_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()
    def test_full_dsym_mutation_and_addition_rejected(self):
        bundle=self.inputs['SideStore']['dsym'];(bundle/'Contents/Info.plist').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'inventory'):self.verify()
    def test_uuid_mismatch_with_matching_inventory_rejected(self):
        item=self.inputs['SideStore'];dwarf=item['dsym']/'Contents/Resources/DWARF/OpenSSL'
        dwarf.write_bytes(self.before.replace(b'0123456789abcdef',b'X123456789abcdef'))
        item['dsym_inventory']=gate.retained_inventory(item['dsym'])
        with self.assertRaisesRegex(ValueError,'UUID'):self.verify()
    def test_exports_undefined_code_and_metadata_mutations_rejected(self):
        for old,new in ((b'_public',b'_publix'),(b'_undefined',b'_undefineX'),(b'export-trie',b'export-triX'),(b'CODE_CONTENT',b'CODE_CONTENX')):
            self.inputs['SideStore']['after']=self.after.replace(old,new)
            with self.subTest(old=old),self.assertRaises(ValueError):self.verify()
    def test_signed_input_rejected_after_hash_admission(self):
        item=self.inputs['SideStore'];item['before']=fixture(False,signature=True);item['before_sha256']=gate.sha(item['before'])
        with self.assertRaisesRegex(ValueError,'unsigned'):self.verify()
    def test_private_extern_input_rejected(self):
        item=self.inputs['SideStore'];data=bytearray(self.before)
        sym=next(raw for cmd,raw in commands(data)[2] if cmd==2);offset=struct.unpack_from('<I',sym,8)[0]
        data[offset+4]|=0x10;item['before']=bytes(data);item['before_sha256']=gate.sha(data)
        with self.assertRaisesRegex(ValueError,'private-extern'):self.verify()
    def test_alias_parent_allowed_but_linked_bundle_rejected(self):
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        self.inputs['SideStore']['dsym']=alias/'SideStore/OpenSSL.framework.dSYM';self.verify()
        linked=self.root/'linked';linked.symlink_to(self.inputs['SideStore']['dsym'],target_is_directory=True)
        self.inputs['SideStore']['dsym']=linked
        with self.assertRaisesRegex(ValueError,'real full'):self.verify()
    def test_no_reduction_rejected(self):
        self.inputs['SideStore']['after']=self.before
        with self.assertRaisesRegex(ValueError,'no local-symbol'):self.verify()

if __name__=='__main__':unittest.main()
