from pathlib import Path
import struct
import io
import json
from unittest.mock import patch
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from macho_stab_guard import snapshot,verify
import experiment_sidebackup_stabs as driver
from experiment_sidebackup_stabs import replace_one
from macho_stab_guard import sha,image_uuid
from test_macho_stab_guard import fixture,alter_command

def executable(debug=True,**kwargs):
    data=bytearray(fixture(debug,**kwargs));struct.pack_into('<I',data,12,2);return bytes(data)

class UnsignedExecutableTests(unittest.TestCase):
    def test_explicit_gate_and_unchanged_default(self):
        old,new=executable(),executable(False)
        with self.assertRaises(ValueError):snapshot(old)
        self.assertEqual(verify(old,new,unsigned_executable=True)['removed_stabs'],1)
    def test_missing_entrypoint_rejected(self):
        data=bytearray(executable());pos=32
        for _ in range(struct.unpack_from('<I',data,16)[0]):
            cmd,size=struct.unpack_from('<II',data,pos)
            if cmd==0x80000028:struct.pack_into('<I',data,pos,0x999999)
            pos+=size
        with self.assertRaises(ValueError):snapshot(bytes(data),unsigned_executable=True)
    def test_signature_rejected(self):
        with self.assertRaises(ValueError):snapshot(executable(signature=True),unsigned_executable=True)
    def test_entrypoint_change_rejected(self):
        with self.assertRaises(ValueError):verify(executable(),alter_command(executable(False),0x80000028,8,499,'<Q'),unsigned_executable=True)
    def test_non_debug_symbol_change_rejected(self):
        with self.assertRaises(ValueError):verify(executable(),executable(False,local=b'_changed'),unsigned_executable=True)
    def test_body_change_rejected(self):
        new=bytearray(executable(False));new[500]^=1
        with self.assertRaises(ValueError):verify(executable(),bytes(new),unsigned_executable=True)
    def test_zip_exact_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'before.zip';dest=root/'after.zip'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('Payload/Test.app/Info.plist',b'plist');z.comment=b'comment';z.writestr('Payload/target',b'old');z.writestr('Payload/other',b'unchanged')
            replace_one(source,dest,'Payload/target',b'new')
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(z.read('Payload/other'),b'unchanged');self.assertEqual(z.read('Payload/target'),b'new');self.assertEqual(z.comment,b'comment')
            with self.assertRaises(ValueError):replace_one(source,root/'bad.zip','absent',b'new')

    def test_duplicate_entrypoint_rejected(self):
        data=bytearray(executable());pos=32
        for _ in range(struct.unpack_from('<I',data,16)[0]):
            cmd,size=struct.unpack_from('<II',data,pos)
            if cmd==0xe:struct.pack_into('<I',data,pos,0x80000028)
            pos+=size
        with self.assertRaises(ValueError):snapshot(bytes(data),unsigned_executable=True)

    def test_driver_native_failure_never_emits_product(self):
        def archive(entries):
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as z:
                for name,data in entries.items():z.writestr(name,data)
            return buffer.getvalue()
        binary=executable();dwarf=fixture()
        nested=archive({driver.BINARY:binary,'Payload/SideBackup.app/Info.plist':b'plist'})
        inner=archive({'native-transfer-SideStore/Products/SideStore.app/SideBackup.ipa':nested,
                       driver.PREFIX+'Contents/Info.plist':b'plist',
                       driver.PREFIX+'Contents/Resources/DWARF/SideBackup':dwarf})
        native_bytes=archive({'SideStore-unsigned.zip':inner})
        ipa_bytes=archive({driver.OUTER:nested})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);ipa=root/'input.ipa';native=root/'native.zip';output=root/'output'
            ipa.write_bytes(ipa_bytes);native.write_bytes(native_bytes)
            with patch.multiple(driver,NATIVE_SHA=sha(native_bytes),INNER_SHA=sha(inner),NESTED_SHA=sha(nested),
                                DWARF_SHA=sha(dwarf),UUID=image_uuid(binary)), \
                 patch.object(driver,'verify_combined',return_value={'pass':True}), \
                 patch.dict('os.environ',{'DEVELOPER_DIR':'/Applications/Xcode_26.4.1.app/Contents/Developer'}), \
                 patch.object(driver.subprocess,'run',side_effect=RuntimeError('native strip failed')):
                with self.assertRaisesRegex(RuntimeError,'native strip failed'):
                    driver.experiment(ipa,sha(ipa_bytes),native,output)
            self.assertFalse((output/'SideBackup-debug-qualified.ipa').exists())
            report=json.loads((output/'experiment.json').read_text())
            self.assertEqual(report['status'],'FAILED');self.assertFalse(report['ipa_emitted'])
            self.assertEqual(ipa.read_bytes(),ipa_bytes);self.assertEqual(native.read_bytes(),native_bytes)
