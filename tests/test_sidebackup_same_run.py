import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import optimize_sidebackup_candidate as adapter
from test_sidebackup_stabs import executable
from test_macho_stab_guard import fixture

class SameRunSideBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.transfer=self.root/'transfer';self.transfer.mkdir()
        self.identity={'run_id':'future-run','run_attempt':'3','workflow_commit':'future-commit'}
        self.receipt=self.root/'receipt.json';self.ipa=self.root/'prior.ipa'
        native=self.transfer/'Products/SideStore.app/SideBackup.ipa';native.parent.mkdir(parents=True)
        with zipfile.ZipFile(native,'w') as z:
            z.writestr(adapter.BINARY,executable());z.writestr('Payload/SideBackup.app/Info.plist',b'plist')
        with zipfile.ZipFile(self.ipa,'w') as z:
            z.writestr(adapter.OUTER,native.read_bytes());z.writestr('Payload/LiveContainer.app/Info.plist',b'plist')
        self.bundle=self.transfer/'Symbols/SideBackup.app.dSYM';dwarf=self.bundle/'Contents/Resources/DWARF/SideBackup'
        dwarf.parent.mkdir(parents=True);dwarf.write_bytes(fixture());(self.bundle/'Contents/Info.plist').write_bytes(b'plist')
        evidence=self.transfer/'evidence';evidence.mkdir()
        products=evidence/'product-hashes.json';products.write_text(json.dumps({'SideStore':{'SideBackup.ipa':adapter.digest(native)}}))
        self.receipt.write_text(json.dumps({**self.identity,'status':'COMPILE_ONLY_PASS_REQUIRES_REVIEW',
            'evidence_sha256':{'product-hashes.json':adapter.digest(products)}}))
        shutil.copyfile(self.receipt,evidence/'receipt.json')
        manifest={'app':{'SideBackup.ipa':{'kind':'file','mode':native.stat().st_mode&0o7777,'sha256':adapter.digest(native)}},
                  'symbols':{self.bundle.name:adapter.retained_inventory(self.bundle)}}
        (self.transfer/'retained-files.json').write_text(json.dumps(manifest))
        self.prior={'status':'TWO_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED',
                    'same_run_identity':self.identity,'final_ipa_sha256':adapter.digest(self.ipa),
                    'native_receipt_sha256':adapter.digest(self.receipt),'final_ipa_bytes':self.ipa.stat().st_size}
        self.manifest_sha=adapter.digest(self.transfer/'retained-files.json')
        self.prior.update(original_ipa_sha256='original',archive_bytes_saved=100,stages={
            'debug_stabs':{'input_ipa_sha256':'original','final_ipa_sha256':'stage1','archive_bytes_saved':40,'receipt_sha256':'first'},
            'local_symbols':{'input_ipa_sha256':'stage1','final_ipa_sha256':adapter.digest(self.ipa),'archive_bytes_saved':60,'receipt_sha256':'second'}})
    def bind(self):return adapter.bind_same_run(self.ipa,self.prior,self.transfer,self.receipt,self.identity,self.manifest_sha)
    def test_future_run_binding_no_fixed_diagnostic_hash(self):
        nested,bundle,inventory,diagnostics,seals=self.bind()
        self.assertEqual(bundle.resolve(),self.bundle.resolve());self.assertEqual(diagnostics['native_receipt_sha256'],adapter.digest(self.receipt))
        self.assertEqual(diagnostics['uuid'],adapter.image_uuid(executable()));self.assertGreater(len(seals),3)
    def test_wrong_run_rejected(self):
        with self.assertRaises(ValueError):adapter.bind_same_run(self.ipa,self.prior,self.transfer,self.receipt,{**self.identity,'run_id':'other'},self.manifest_sha)
    def test_tampered_native_product_rejected(self):
        (self.transfer/'Products/SideStore.app/SideBackup.ipa').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.bind()
    def test_tampered_symbol_inventory_rejected(self):
        (self.bundle/'Contents/Info.plist').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.bind()
    def test_tampered_sealed_product_manifest_rejected(self):
        (self.transfer/'evidence/product-hashes.json').write_text('{}')
        with self.assertRaises(ValueError):self.bind()
    def test_transfer_symbol_escape_rejected(self):
        shutil.move(self.bundle,self.root/'outside');self.bundle.symlink_to(self.root/'outside',target_is_directory=True)
        with self.assertRaises(ValueError):self.bind()
    def test_failed_transform_does_not_promote_product(self):
        output=self.root/'output'
        def failing(*args,**kwargs):
            (output/'SideBackup-debug-qualified.ipa').write_bytes(b'unapproved')
            raise ValueError('semantic gate failed')
        with patch.object(adapter,'transform_bound_nested',side_effect=failing):
            with self.assertRaisesRegex(ValueError,'semantic gate failed'):
                adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                    receipt_path=self.receipt,run_identity=self.identity,output=output,retained=self.root/'retained',expected_manifest_sha=self.manifest_sha)
        self.assertFalse((output/'SideBackup-debug-qualified.ipa').exists())
        self.assertEqual(json.loads((output/'experiment.json').read_text())['status'],'FAILED')

    def test_success_preserves_prior_receipt_and_cumulative_chain(self):
        output=self.root/'output';retained=self.root/'retained'
        self.prior.update(after_size={'uncompressed_bundle_bytes':20000},
                          before_size={'uncompressed_bundle_bytes':21000})
        def accepted(ipa,expected,nested,folder,report):
            final=folder/'SideBackup-debug-qualified.ipa'
            with zipfile.ZipFile(final,'w') as z:z.writestr('Payload/LiveContainer.app/Info.plist',b'plist')
            report.update(status='STRUCTURAL_PACKAGE_PASS_DEVICE_UNVERIFIED',ipa_emitted=True,
                          final_ipa_sha256=adapter.digest(final),final_ipa_bytes=final.stat().st_size,original_ipa_bytes=ipa.stat().st_size,
                          archive_bytes_saved=ipa.stat().st_size-final.stat().st_size)
        with patch.object(adapter,'transform_bound_nested',side_effect=accepted):
            final,result=adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                receipt_path=self.receipt,run_identity=self.identity,output=output,retained=retained,expected_manifest_sha=self.manifest_sha)
        self.assertEqual(result['stages']['debug_stabs'],self.prior['stages']['debug_stabs'])
        self.assertEqual(result['stages']['local_symbols'],self.prior['stages']['local_symbols'])
        self.assertEqual(result['stages']['sidebackup_debug_stabs']['input_ipa_sha256'],adapter.digest(self.ipa))
        self.assertEqual(result['archive_bytes_saved'],100+self.ipa.stat().st_size-final.stat().st_size)
        self.assertEqual(json.loads((retained/'prior-stage-receipt.json').read_text()),self.prior)
    def test_mutated_retention_manifest_rejected(self):
        path=self.transfer/'retained-files.json';value=json.loads(path.read_text());value['extra']='unsealed';path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'sealed retention'):self.bind()
    def test_wrong_prior_hash_chain_rejected(self):
        self.prior['stages']['local_symbols']['input_ipa_sha256']='not-stage1'
        with self.assertRaisesRegex(ValueError,'hash chain'):self.bind()
    def test_wrong_prior_savings_rejected(self):
        self.prior['archive_bytes_saved']=200
        with self.assertRaisesRegex(ValueError,'savings chain'):self.bind()
    def test_caller_mutation_during_transform_rejected(self):
        output=self.root/'output'
        def mutate(*args):self.prior['archive_bytes_saved']=999
        with patch.object(adapter,'transform_bound_nested',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'provenance mutated'):
                adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                    receipt_path=self.receipt,run_identity=self.identity,output=output,retained=self.root/'retained',
                    expected_manifest_sha=self.manifest_sha)
        self.assertFalse((output/'SideBackup-debug-qualified.ipa').exists())
    def test_transform_receipt_metadata_mutation_rejected(self):
        for key,value in [('input_ipa_sha256','wrong'),('runtime_proven',True),('native_receipt_sha256','wrong'),('prior_stage_receipt_sha256','wrong')]:
            with self.subTest(key=key):
                output=self.root/('output-'+key);retained=self.root/('retained-'+key)
                def mutate(*args):args[-1][key]=value
                with patch.object(adapter,'transform_bound_nested',side_effect=mutate):
                    with self.assertRaisesRegex(ValueError,'immutable third-stage'):
                        adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                            receipt_path=self.receipt,run_identity=self.identity,output=output,retained=retained,
                            expected_manifest_sha=self.manifest_sha)
    def test_persisted_prior_receipt_mutation_rejected(self):
        output=self.root/'output'
        def mutate(*args):(output/'prior-stage-receipt.json').write_text('{}')
        with patch.object(adapter,'transform_bound_nested',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'persisted admitted'):
                adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                    receipt_path=self.receipt,run_identity=self.identity,output=output,retained=self.root/'retained',
                    expected_manifest_sha=self.manifest_sha)
    def test_recovery_packaging_identity_is_separate_from_native_binding(self):
        packaging={'run_id':'recovery-run','run_attempt':'2','workflow_commit':'packager-source'}
        self.prior['packaging_run_identity']=packaging
        with patch.dict('os.environ',{'GITHUB_RUN_ID':'recovery-run','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':'packager-source'}), \
             patch.object(adapter,'transform_bound_nested',side_effect=ValueError('reached transform')):
            with self.assertRaisesRegex(ValueError,'reached transform'):
                adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                    receipt_path=self.receipt,run_identity=self.identity,output=self.root/'recovery-output',
                    retained=self.root/'recovery-retained',expected_manifest_sha=self.manifest_sha,packaging_identity=packaging)
        report=json.loads((self.root/'recovery-output/experiment.json').read_text())
        self.assertEqual(report['native_run_identity'],self.identity)
        self.assertEqual(report['packaging_run_identity'],packaging)
        self.assertEqual(report['same_run_identity'],self.identity)
    def test_third_stage_rejects_mismatched_packager(self):
        packaging={'run_id':'recovery-run','run_attempt':'2','workflow_commit':'packager-source'}
        self.prior['packaging_run_identity']={**packaging,'run_id':'wrong'}
        with patch.dict('os.environ',{'GITHUB_RUN_ID':'recovery-run','GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':'packager-source'}), \
             patch.object(adapter,'transform_bound_nested') as transform:
            with self.assertRaisesRegex(ValueError,'prior packaging identity'):
                adapter.optimize_sidebackup_same_run(self.ipa,prior_report=self.prior,transfer=self.transfer,
                    receipt_path=self.receipt,run_identity=self.identity,output=self.root/'output',
                    retained=self.root/'retained',expected_manifest_sha=self.manifest_sha,packaging_identity=packaging)
            transform.assert_not_called()
