import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import optimize_unsigned_widget as adapter
from test_sidebackup_stabs import executable
from test_macho_stab_guard import fixture

class UnsignedWidgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve(strict=True)
        self.transfer=self.root/'native';self.staged=self.root/'stage';self.receipt=self.root/'receipt.json'
        self.native_identity={'run_id':'native','run_attempt':'1','workflow_commit':'native-source'}
        self.packaging={'run_id':'current','run_attempt':'2','workflow_commit':'current-source'}
        self.env=patch.dict(os.environ,{'GITHUB_RUN_ID':'current','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':'current-source'})
        self.env.start();self.addCleanup(self.env.stop)
        self.native=self.transfer/'Products/SideStore.app'/adapter.RELATIVE;self.native.parent.mkdir(parents=True)
        self.native.write_bytes(executable());self.native.chmod(0o755)
        shutil.copytree(self.transfer/'Products/SideStore.app',self.staged)
        self.bundle=self.transfer/'Symbols'/adapter.DSYM;dwarf=self.bundle/'Contents/Resources/DWARF/AltWidgetExtension'
        dwarf.parent.mkdir(parents=True);dwarf.write_bytes(fixture());(self.bundle/'Contents/Info.plist').write_bytes(b'plist')
        evidence=self.transfer/'evidence';evidence.mkdir()
        products=evidence/'product-hashes.json';products.write_text(json.dumps({'SideStore':{adapter.RELATIVE:adapter.digest(self.native)}}))
        self.receipt.write_text(json.dumps({**self.native_identity,'status':'COMPILE_ONLY_PASS_REQUIRES_REVIEW',
            'evidence_sha256':{'product-hashes.json':adapter.digest(products)}}))
        shutil.copyfile(self.receipt,evidence/'receipt.json')
        manifest={'app':adapter.retained_inventory(self.transfer/'Products/SideStore.app'),
                  'symbols':{adapter.DSYM:adapter.retained_inventory(self.bundle)}}
        self.manifest=self.transfer/'retained-files.json';self.manifest.write_text(json.dumps(manifest));self.seal=adapter.digest(self.manifest)
    def reseal_fixture(self):
        products=self.transfer/'evidence/product-hashes.json'
        products.write_text(json.dumps({'SideStore':{adapter.RELATIVE:adapter.digest(self.native)}}))
        value=json.loads(self.receipt.read_text());value['evidence_sha256']['product-hashes.json']=adapter.digest(products)
        self.receipt.write_text(json.dumps(value));shutil.copyfile(self.receipt,self.transfer/'evidence/receipt.json')
        manifest={'app':adapter.retained_inventory(self.transfer/'Products/SideStore.app'),
                  'symbols':{adapter.DSYM:adapter.retained_inventory(self.bundle)}}
        self.manifest.write_text(json.dumps(manifest));self.seal=adapter.digest(self.manifest)
    def call(self,strip=None):
        with patch.object(adapter,'apple_strip',side_effect=strip or (lambda *args:executable(False))):
            return adapter.strip_unsigned_widget(self.staged,transfer=self.transfer,receipt_path=self.receipt,
                expected_manifest_sha=self.seal,native_identity=self.native_identity,packaging_identity=self.packaging,
                work=self.root/'work',retained=self.root/'retained')
    def test_only_unsigned_copy_changed_original_and_symbols_retained(self):
        original=self.native.read_bytes();before=adapter.retained_inventory(self.staged)
        result=self.call();after=adapter.retained_inventory(self.staged)
        self.assertEqual(self.native.read_bytes(),original);before[adapter.RELATIVE]['sha256']=adapter.sha(executable(False))
        self.assertEqual(before,after);self.assertEqual(result['native_run_identity'],self.native_identity)
        self.assertEqual(result['packaging_run_identity'],self.packaging)
        self.assertTrue((self.root/'retained/unsigned-widget-original-and-symbols.zip').is_file())
    def test_actual_symlink_parent_alias_is_supported(self):
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        self.staged=alias/'stage'
        self.call()
    def test_deliberately_linked_staged_root_rejected(self):
        real=self.staged;self.staged=self.root/'linked';self.staged.symlink_to(real,target_is_directory=True)
        with self.assertRaises(ValueError):self.call()
    def test_signed_widget_rejected_before_tool(self):
        self.native.write_bytes(executable(signature=True));(self.staged/adapter.RELATIVE).write_bytes(self.native.read_bytes())
        self.reseal_fixture()
        with patch.object(adapter,'apple_strip') as strip:
            with self.assertRaisesRegex(ValueError,'must have no signature'):
                adapter.strip_unsigned_widget(self.staged,transfer=self.transfer,receipt_path=self.receipt,
                    expected_manifest_sha=self.seal,native_identity=self.native_identity,packaging_identity=self.packaging,
                    work=self.root/'work',retained=self.root/'retained')
            strip.assert_not_called()
    def test_sealed_manifest_mutation_rejected(self):
        self.manifest.write_text(self.manifest.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'manifest'):self.call()
    def test_dsym_mutation_rejected(self):
        (self.bundle/'Contents/Info.plist').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'dSYM'):self.call()
    def test_non_debug_mutation_rejected_without_installing(self):
        original=(self.staged/adapter.RELATIVE).read_bytes()
        with self.assertRaises(ValueError):self.call(lambda *args:executable(False,local=b'_changed'))
        self.assertEqual((self.staged/adapter.RELATIVE).read_bytes(),original)
    def test_native_mutation_during_tool_rejected(self):
        def mutate(*args):self.native.write_bytes(b'changed');return executable(False)
        with self.assertRaisesRegex(ValueError,'sealed native'):self.call(mutate)
    def test_unrelated_staged_mutation_rejected(self):
        def mutate(*args):(self.staged/'extra').write_bytes(b'changed');return executable(False)
        with self.assertRaisesRegex(ValueError,'unapproved staged'):self.call(mutate)
    def test_tool_failure_has_failed_receipt_and_no_staged_change(self):
        original=(self.staged/adapter.RELATIVE).read_bytes()
        def failure(*args):raise ValueError('native tool failure')
        with self.assertRaisesRegex(ValueError,'native tool failure'):self.call(failure)
        self.assertEqual((self.staged/adapter.RELATIVE).read_bytes(),original)
        self.assertEqual(json.loads((self.root/'work/unsigned-widget-receipt.json').read_text())['status'],'FAILED')
    def test_mismatched_dsym_uuid_rejected_with_valid_inventory(self):
        dwarf=self.bundle/'Contents/Resources/DWARF/AltWidgetExtension'
        dwarf.write_bytes(dwarf.read_bytes().replace(b'0123456789abcdef',b'X'*16));self.reseal_fixture()
        with self.assertRaisesRegex(ValueError,'UUID differs'):self.call()
    def test_final_signed_widget_and_retained_receipt_must_remain_exact(self):
        report=self.call();initial=self.root/'initial.ipa';final=self.root/'final.ipa'
        entry='Payload/LiveContainer.app/PlugIns/LiveWidgetExtension.appex/LiveWidgetExtension'
        signed=executable(False,signature=True)
        def archive(path,data):
            with zipfile.ZipFile(path,'w') as z:z.writestr(entry,data)
        archive(initial,signed);archive(final,signed)
        receipt=self.root/'retained/unsigned-widget-receipt.json';sidecar=self.root/'retained/unsigned-widget-original-and-symbols.zip'
        # Synthetic signature shape exercises orchestration; finite hash policy has its own real-layout controls.
        with patch('widget_signature_policy.verify_widget_signature',return_value={'fixture_only':True}):
            result=adapter.verify_final_widget(initial,final,receipt,report,sidecar)
        self.assertFalse(result['compressed_savings_attributed'])
        archive(final,signed+b'changed')
        with self.assertRaisesRegex(ValueError,'post-packaging'):adapter.verify_final_widget(initial,final,receipt,report,sidecar)
        archive(final,signed);value=json.loads(receipt.read_text());value['raw_bytes_saved']+=1;adapter.write(receipt,value)
        with self.assertRaises(ValueError):adapter.verify_final_widget(initial,final,receipt,report,sidecar)
        adapter.write(receipt,report);sidecar.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'retained widget'):adapter.verify_final_widget(initial,final,receipt,report,sidecar)
