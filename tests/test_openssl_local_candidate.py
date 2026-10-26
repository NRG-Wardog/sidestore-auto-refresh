"""Adapter controls: only Apple tool and host crypto/package boundaries mocked.
Real seals, native inventories, -S/-x policies and ZIP preservation execute.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import optimize_openssl_local_candidate as adapter
from native_transfer_seal import create
from test_macho_stab_guard import fixture
from test_macho_local_symbol_guard import remove_fixture_local

class OpenSSLCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve(strict=True)
        self.identity={'workflow_commit':'1'*40,'run_id':'1','run_attempt':'1'}
        self.packaging={'workflow_commit':'2'*40,'run_id':'2','run_attempt':'1'}
        env=patch.dict(os.environ,dict(GITHUB_SHA='2'*40,GITHUB_RUN_ID='2',GITHUB_RUN_ATTEMPT='1'));env.start();self.addCleanup(env.stop)
        self.native=fixture(True);self.before=fixture(False);self.after=remove_fixture_local(self.before)
        self.ipa=self.root/'input.ipa'
        with zipfile.ZipFile(self.ipa,'w',compression=zipfile.ZIP_STORED) as archive:
            for _,member in adapter.TARGETS.values():archive.writestr(member,self.before)
            archive.writestr('Payload/LiveContainer.app/unchanged',b'unchanged');archive.comment=b'preserve me'
            archive.writestr('Payload/LiveContainer.app/Info.plist',b'synthetic plist')
        self.products={owner:{adapter.NATIVE:adapter.sha(self.native)} for owner in adapter.TARGETS}
        products=json.dumps(self.products).encode();self.receipt=self.root/'receipt.json'
        self.receipt.write_text(json.dumps({'schema_version':1,**self.identity,'status':'COMPILE_ONLY_PASS_REQUIRES_REVIEW',
            'evidence_sha256':{'product-hashes.json':adapter.sha(products)}}))
        self.transfers={}
        for owner in adapter.TARGETS:
            root=self.root/owner;binary=root/'Products'/(owner+'.app')/adapter.NATIVE;binary.parent.mkdir(parents=True);binary.write_bytes(self.native)
            symbols=root/'Symbols/OpenSSL.framework.dSYM';dwarf=symbols/'Contents/Resources/DWARF/OpenSSL';dwarf.parent.mkdir(parents=True);dwarf.write_bytes(self.native)
            (symbols/'Contents/Info.plist').write_bytes(b'synthetic plist')
            (root/'evidence').mkdir();(root/'evidence/product-hashes.json').write_bytes(products)
            (root/'retained-files.json').write_text(json.dumps({'app':adapter.retained_inventory(root/'Products'/(owner+'.app')),
                'symbols':{symbols.name:adapter.retained_inventory(symbols)}}))
            self.transfers[owner]=root
        self.seal=self.root/'seal.json';self.seal_sha=create(self.receipt,self.transfers,self.seal,self.identity)
        self.sidecar=self.root/'original-symbols.zip';self.sidecar.write_bytes(b'authenticated original full-symbol fixture boundary')
        self.host=self.root/'host.json';self.host.write_text('{}');self.host_sha=adapter.digest(self.host)
        self.prior={'status':'THREE_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED',
            'same_run_identity':self.identity,'packaging_run_identity':self.packaging,
            'native_receipt_sha256':adapter.digest(self.receipt),'original_ipa_sha256':'a'*64,
            'final_ipa_sha256':adapter.digest(self.ipa),'final_ipa_bytes':self.ipa.stat().st_size,'archive_bytes_saved':6,
            'retained_debug_sidecar_sha256':adapter.digest(self.sidecar),
            'stages':{'debug_stabs':{'input_ipa_sha256':'a'*64,'final_ipa_sha256':'b'*64,'archive_bytes_saved':1},
                      'local_symbols':{'input_ipa_sha256':'b'*64,'final_ipa_sha256':'c'*64,'archive_bytes_saved':2},
                      'sidebackup_debug_stabs':{'input_ipa_sha256':'c'*64,'final_ipa_sha256':adapter.digest(self.ipa),'archive_bytes_saved':3}},
            'debug_stabs_transformations':{key:adapter.verify(self.native,self.before) for key,_ in adapter.TARGETS.values()},
            'before_size':{'uncompressed_bundle_bytes':10000},'after_size':{'uncompressed_bundle_bytes':9000}}
        self.prior_path=self.root/'prior.json';adapter.write(self.prior_path,self.prior);self.prior_sha=adapter.digest(self.prior_path)
    def call(self, host=None, strip=None):
        with patch.object(adapter,'verify_host_qualification',side_effect=host or (lambda *a:{'synthetic_mock_only':True})), \
             patch.object(adapter,'strip_copy',side_effect=strip or (lambda *a:(self.after,{'synthetic_mock_only':True}))), \
             patch.object(adapter,'verify_package',return_value={'synthetic_mock_only':True}):
            return adapter.optimize_openssl_local_candidate(self.ipa,prior_report=self.prior,prior_receipt_path=self.prior_path,
                expected_prior_receipt_sha=self.prior_sha,transfer_roots=self.transfers,receipt_path=self.receipt,
                seal_path=self.seal,expected_seal_sha=self.seal_sha,native_identity=self.identity,packaging_identity=self.packaging,
                original_sidecar=self.sidecar,host_qualification_path=self.host,expected_host_qualification_sha=self.host_sha,
                output=self.root/'output',retained=self.root/'retained')
    def test_positive_real_policy_seal_zip_and_four_stage_measurement(self):
        final,report=self.call();self.assertEqual(report['final_ipa_sha256'],adapter.digest(final))
        self.assertEqual(report['archive_bytes_saved'],6+self.ipa.stat().st_size-final.stat().st_size)
        with zipfile.ZipFile(final) as z:
            self.assertEqual(z.comment,b'preserve me');self.assertEqual(z.read('Payload/LiveContainer.app/unchanged'),b'unchanged')
            for _,member in adapter.TARGETS.values():self.assertEqual(z.read(member),self.after)
        self.assertEqual(adapter.digest(self.prior_path),self.prior_sha)
    def test_host_failure_before_any_output(self):
        def fail(*a):raise ValueError('host qualification rejected')
        with self.assertRaisesRegex(ValueError,'host qualification'):self.call(host=fail)
        self.assertFalse((self.root/'output').exists())
    def test_wrong_prior_hash_or_chain_rejected(self):
        self.prior['stages']['local_symbols']['input_ipa_sha256']='0'*64
        adapter.write(self.prior_path,self.prior);self.prior_sha=adapter.digest(self.prior_path)
        with self.assertRaisesRegex(ValueError,'hash chain'):self.call()
    def test_post_s_receipt_mutation_rejected(self):
        self.prior['debug_stabs_transformations']['OpenSSLHost']['after']['symbols']='0'*64
        adapter.write(self.prior_path,self.prior);self.prior_sha=adapter.digest(self.prior_path)
        with self.assertRaisesRegex(ValueError,'structural receipt'):self.call()
    def test_native_symbol_inventory_mutation_rejected(self):
        (self.transfers['SideStore']/'Symbols/OpenSSL.framework.dSYM/extra').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError,'dSYM'):self.call()
    def test_sidecar_mutation_rejected(self):
        self.sidecar.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'sidecar'):self.call()
    def test_tool_failure_and_external_mutation_never_emit(self):
        def fail(*a):raise ValueError('tool failure')
        with self.assertRaisesRegex(ValueError,'tool failure'):self.call(strip=fail)
        self.assertFalse((self.root/'output/OpenSSL-local-qualified.ipa').exists())
    def test_source_mutation_during_tool_rejected(self):
        def mutate(*a):self.sidecar.write_bytes(b'changed');return self.after,{}
        with self.assertRaisesRegex(ValueError,'immutable'):self.call(strip=mutate)
        self.assertFalse((self.root/'output/OpenSSL-local-qualified.ipa').exists())
    def test_protected_symbol_mutation_rejected(self):
        with self.assertRaises(ValueError):self.call(strip=lambda *a:(self.after.replace(b'_public',b'_publix'),{}))
        self.assertFalse((self.root/'output/OpenSSL-local-qualified.ipa').exists())
    def test_caller_prior_mutation_rejected(self):
        def mutate(*a):self.prior['archive_bytes_saved']=99;return self.after,{}
        with self.assertRaisesRegex(ValueError,'caller provenance'):self.call(strip=mutate)
    def test_missing_host_module_is_not_success(self):
        with patch.object(adapter,'__file__',str(self.root/'scripts/adapter.py')):
            with self.assertRaises(FileNotFoundError):adapter.verify_host_qualification(self.host,self.host_sha,self.packaging,self.identity)

if __name__=='__main__':unittest.main()
