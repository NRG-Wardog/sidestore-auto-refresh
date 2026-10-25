import hashlib
import json
from pathlib import Path
import struct
import tempfile
import zipfile
import unittest
from unittest.mock import patch
from test_macho_stab_guard import signed_fixture,alter_command,commands,fixture
from test_macho_local_symbol_guard import remove_fixture_local
import local_symbol_packaging as package


def signed_pair():
    before=signed_fixture(False,adhoc=True,correct_hashes=True,padding=128)
    raw=next(r for c,r in commands(before)[2] if c==0x1d)
    oldoff,size=struct.unpack_from('<II',raw,8)
    after=bytearray(alter_command(remove_fixture_local(before),0x1d,8,oldoff-16))
    off=oldoff-16;count=struct.unpack_from('>I',after,off+8)[0]
    for i in range(count):
        slot,start=struct.unpack_from('>II',after,off+12+8*i)
        if slot not in (0,0x1000):continue
        child=off+start;struct.pack_into('>I',after,child+32,off)
        hashes=struct.unpack_from('>I',after,child+16)[0]
        hs,ht=struct.unpack_from('>BB',after,child+36)
        digest=hashlib.sha1 if ht==1 else hashlib.sha256
        after[child+hashes:child+hashes+hs]=digest(after[:off]).digest()
    return before,bytes(after)


def slot_location(data,slot):
    off=struct.unpack_from('<I',next(r for c,r in commands(data)[2] if c==0x1d),8)[0]
    count=struct.unpack_from('>I',data,off+8)[0]
    for i in range(count):
        key,start=struct.unpack_from('>II',data,off+12+8*i)
        if key==slot:return off+start
    raise AssertionError(slot)


class LocalPackagingTests(unittest.TestCase):
    def test_valid_apple_to_apple_page_hashes_and_local_reduction(self):
        before,after=signed_pair();report=package.verify_local_reseal(before,after)
        self.assertTrue(report['all_code_pages_and_special_slots_verified'])
        self.assertEqual(report['structural']['removed_local_records'],1)
        self.assertTrue(report['installer_resigning_required'])

    def test_only_validated_unclaimed_padding_can_be_zeroed(self):
        before,after=signed_pair();dirty=bytearray(after);dirty[-1]=123
        with self.assertRaisesRegex(ValueError,'padding'):package.verify_local_reseal(before,bytes(dirty))
        fixed,receipt=package.canonicalize_local_signature_padding(before,bytes(dirty))
        self.assertEqual(fixed,after);self.assertEqual(receipt['before_nonzero_padding_bytes'],1)
        self.assertEqual(fixed[:-128],dirty[:-128])

    def test_each_hash_algorithm_and_baseline_page_is_verified(self):
        before,after=signed_pair()
        for slot in (0,0x1000):
            child=slot_location(after,slot);hashes=struct.unpack_from('>I',after,child+16)[0]
            bad=bytearray(after);bad[child+hashes]^=1
            with self.assertRaisesRegex(ValueError,'page hash'):package.apple_policy(before,bytes(bad))
        bad=bytearray(before);bad[499]^=1
        with self.assertRaisesRegex(ValueError,'page hash'):package.apple_policy(bytes(bad),after)

    def test_flags_identifier_team_requirements_CMS_and_coverage_rejected(self):
        before,after=signed_pair();child=slot_location(after,0)
        mutations=[(child+12,'>I',0),(child+48,'>I',88),(child+88,'>B',ord('X')),
                   (child+32,'>I',1),(slot_location(after,2)+8,'>I',1),
                   (slot_location(after,0x10000),'>I',0)]
        for pos,fmt,value in mutations:
            bad=bytearray(after);struct.pack_into(fmt,bad,pos,value)
            with self.subTest(pos=pos),self.assertRaises(ValueError):package.apple_policy(before,bytes(bad))
        bad=bytearray(after);hs=struct.unpack_from('>I',after,child+16)[0]
        bad[child+hs-40]^=1
        with self.assertRaisesRegex(ValueError,'special-slot'):package.apple_policy(before,bytes(bad))

    def test_rejected_semantics_never_reaches_repackage_or_emits_derivative(self):
        before,after=signed_pair()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original=root/'input.ipa';original.write_bytes(b'immutable placeholder')
            targets={'LiveContainerSwiftUI':before,'SideStore':before}
            for key in targets:
                (root/key).mkdir();(root/key/'stripped').write_bytes(after.replace(b'_undefined',b'_undefineX'))
            with patch.object(package,'repackage') as repack, self.assertRaises(ValueError):
                package.qualify_package(original,targets,{},root,{})
            repack.assert_not_called();self.assertFalse((root/'local-symbol-qualified.ipa').exists())

    def test_native_strict_failure_cannot_emit_package(self):
        before,after=signed_pair()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original=root/'input.ipa';original.write_bytes(b'immutable placeholder')
            targets={'SideStore':before,'LiveContainerSwiftUI':before}
            (root/'SideStore').mkdir();(root/'SideStore/stripped').write_bytes(after)
            with patch.object(package,'native',side_effect=ValueError('strict failure')),patch.object(package,'repackage') as repack:
                with self.assertRaisesRegex(ValueError,'strict failure'):
                    package.qualify_package(original,targets,{'SideStore':{'uuid':package.image_uuid(before)}},root,{})
            repack.assert_not_called();self.assertFalse((root/'local-symbol-qualified.ipa').exists())

    def test_final_strict_verification_precedes_any_archive_emission(self):
        before,after=signed_pair();ui=fixture(False);ui_after=remove_fixture_local(ui)
        for reject_final in (False,True):
            with self.subTest(reject_final=reject_final),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);original=root/'input.ipa';original.write_bytes(b'fixture original'+bytes(4096))
                targets={'LiveContainerSwiftUI':ui,'SideStore':before};events=[];report={}
                for key,data in [('LiveContainerSwiftUI',ui_after),('SideStore',after)]:
                    (root/key).mkdir();(root/key/'stripped').write_bytes(data)
                def fake_native(command,log):
                    events.append(command)
                    if reject_final and len(events)==3:raise ValueError('final strict failure')
                def fake_archive(source,destination,replacements):
                    self.assertEqual(len(events),3)
                    self.assertEqual(events[-1][1:3],['--verify','--strict'])
                    with zipfile.ZipFile(destination,'w') as archive:archive.writestr('test',b'qualified fixture')
                    return {'synthetic_test_only':True}
                with patch.object(package,'native',side_effect=fake_native),patch.object(package,'repackage',side_effect=fake_archive) as repack:
                    if reject_final:
                        with self.assertRaisesRegex(ValueError,'final strict'):
                            package.qualify_package(original,targets,{k:{'uuid':package.image_uuid(v)} for k,v in targets.items()},root,report)
                        repack.assert_not_called();self.assertFalse((root/'local-symbol-qualified.ipa').exists())
                    else:
                        package.qualify_package(original,targets,{k:{'uuid':package.image_uuid(v)} for k,v in targets.items()},root,report)
                        self.assertTrue((root/'local-symbol-qualified.ipa').is_file())
                        self.assertTrue(report['signature_verified'])
                        self.assertEqual(report['final_ipa_sha256'],package.digest(root/'local-symbol-qualified.ipa'))

if __name__=='__main__':unittest.main()
