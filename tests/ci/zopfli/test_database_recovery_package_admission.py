"""Synthetic recovery admission controls; all process/network access forbidden."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
import copy
import io
import json
import unittest
import zipfile
import database_recovery_package_admission as a
import test_provider_ownership_package_admission as ownership

class RecoveryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.h = ownership.OwnershipAdmissionTests()
        self.h.setUp(); self.addCleanup(self.h.doCleanups)
        self.contract, self.m = a.trusted()
        m = self.m
        closure = self.contract['expected_source_sha256'].copy()
        settings = {'target':'SideStore','configuration':'Release','declared':'5.0','swiftc_argument':'5','project_sha256':closure[m.PROJECT]}
        engine = {'schema':'sidestore-exact-recovery-sqlite-v2','physical_device_execution':False,'limitations':['synthetic','test','only'],'swift_build_settings':settings,'status':'PASS_NATIVE_EXACT_ENGINE_SYNTHETIC_SQLITE','cases':m.ENGINE_CASES.copy(),
            'production_coredata_validation':False,'exact_production_sources_compiled_without_rewriting':True,
            'source_sha256':{k:closure[k] for k in m.ENGINE_INPUTS},'swift_version':'synthetic','platform':'synthetic'}
        coredata = {'schema':'sidestore-exact-recovery-coredata-v2','status':'PASS_NATIVE_EXACT_ENGINE_REAL_COREDATA','swift_build_settings':settings,'swift_version':'synthetic','physical_device_execution':False,'synthetic_data_only':True,'app_startup_or_installed_app_runtime_executed':False,
            'cases':[{'mode':mode,'status':'PASS','original_persistent_bytes_unchanged':True} for mode in ('delete','wal')],
            'inputs':{k:v for k,v in closure.items() if k in m.COREDATA_INPUTS or k.startswith(m.MODEL+'/')}}
        identity = {k:self.h.native[k] for k in ('workflow_commit','run_id','run_attempt')}
        _,_,_,_,graph = a.projection.trusted()
        acquisition = {'identity':identity,'acquired_owners':{o:{'repository':graph['owners'][o][0],'commit':graph['owners'][o][1],'tree':graph['reviewed_deltas'][o]['tree']} for o in ('SideStore','LiveContainer')}}
        acquisition['source_inventory'] = {'SideStore':{name:{'sha256':digest} for name,digest in closure.items()}}
        self.data = {'schema-acquisition.json':json.dumps(acquisition).encode()}
        for name in m.FILES: self.data[m.flat(name)] = b'synthetic log\n'
        for name,value in [('source-before.json',closure),('source-after.json',closure),('source-immutability.json',{}),('engine/report.json',engine),('coredata/receipt.json',coredata)]:
            self.data[m.flat(name)] = json.dumps(value).encode()
        self.receipt = {'schema':m.SCHEMA,'status':m.STATUS,'identity':identity,'source_graph':graph['owners'],
            'source_sha256':closure,'engine':engine,'coredata':coredata,
            'evidence_sha256':{name:a.safe.sha(self.data[m.flat(name)]) for name in m.FILES},
            'acquisition_sha256':a.safe.sha(self.data['schema-acquisition.json']),
            'verifier_sha256':self.contract['trusted_helper_sha256'],'physical_device_execution':False}
        self.build()

    def build(self):
        h = self.h
        self.data['database-recovery-tests.json'] = json.dumps(self.receipt).encode()
        self.data['database-recovery-settings.json'] = json.dumps({'schema':'database-recovery-effective-swift-v1','identity':self.receipt['identity'],'recovery_receipt_sha256':a.safe.sha(self.data['database-recovery-tests.json']),'target':'SideStore','configuration':'Release','effective_swift_version':'5.0','swiftc_argument':'5'}).encode()
        h.native['evidence_sha256'] = {name:a.safe.sha(raw) for name,raw in self.data.items()}
        raw = json.dumps(h.native).encode(); (h.root/'native-receipt.json').write_bytes(raw)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('native-receipt.json',raw)
            for name,data in self.data.items(): z.writestr(name,data)
        archive = buffer.getvalue(); (h.root/'native-evidence-projection.zip').write_bytes(archive)
        h.report.update(native_receipt_sha256=a.safe.sha(raw),projection_sha256=a.safe.sha(archive),projection_bytes=len(archive),verified_reference_sha256=h.native['evidence_sha256'].copy(),verified_reference_count=len(self.data))
        h.save_report()
        for name in ('database-recovery-tests.json','database-recovery-settings.json'):(h.root/name).write_bytes(self.data[name])
        (h.root/'provenance.json').write_text(json.dumps({'database_recovery_regression_sha256':a.safe.sha(self.data['database-recovery-tests.json']),'database_recovery_settings_sha256':a.safe.sha(self.data['database-recovery-settings.json'])}))

    def verify(self): return a.verify(self.h.root,self.h.manifest,self.h.projector)

    def test_actual_projection_admission_without_execution(self):
        result = self.verify()
        self.assertEqual(result['input_ipa_sha256'],self.h.manifest['ipa_sha256'])
        self.assertFalse(result['adapter_native_execution'])
        self.assertEqual(len(result['evidence_sha256']),len(self.m.FILES)+3)

    def test_missing_raw_log_fails_even_rehashed_projection(self):
        self.data.pop(self.m.flat('engine/native.log')); self.build()
        with self.assertRaises(ValueError): self.verify()

    def test_changed_raw_log_fails_even_rehashed_projection(self):
        self.data[self.m.flat('coredata/wal-recover.log')] = b'wrong'; self.build()
        with self.assertRaises(ValueError): self.verify()

    def test_skip_missing_case_run_graph_and_receipt_tampering(self):
        original = copy.deepcopy(self.receipt)
        for change in (lambda v:v['engine']['cases'].pop(),lambda v:v['coredata']['cases'][0].update(status='SKIP'),
                       lambda v:v['identity'].update(run_attempt='2'),lambda v:v.update(physical_device_execution=True),
                       lambda v:v.update(verifier_sha256='f'*64)):
            self.receipt=copy.deepcopy(original); change(self.receipt); self.build()
            with self.assertRaises(ValueError): self.verify()

    def test_prior17_case_receipt_and_each_missing_root_case_rejected(self):
        required={'approved-root-canonicalization','approved-root-leaf-symlink','missing-approved-roots',
                  'invalid-approved-ancestor','inaccessible-approved-ancestor','aliased-roots-share-selection',
                  'reset-lock-alias-contention'}
        self.assertEqual(len(self.m.ENGINE_CASES),24)
        original=copy.deepcopy(self.receipt)
        for missing in [required,*({case} for case in required)]:
            self.receipt=copy.deepcopy(original)
            self.receipt['engine']['cases']=[case for case in self.receipt['engine']['cases'] if case not in missing]
            self.build()
            with self.assertRaises(ValueError):self.verify()

    def test_exact_source_closure_cannot_be_extended_or_rehashed(self):
        self.receipt['source_sha256'][self.m.MODEL+'/unreviewed.xcdatamodel/contents']='0'*64
        self.receipt['coredata']['inputs'][self.m.MODEL+'/unreviewed.xcdatamodel/contents']='0'*64
        self.build()
        with self.assertRaises(ValueError):self.verify()

    def test_raw_source_seal_mismatch_even_with_rehashed_receipt(self):
        name='source-before.json'; self.data[self.m.flat(name)]=b'{}'
        self.receipt['evidence_sha256'][name]=a.safe.sha(self.data[self.m.flat(name)])
        self.build()
        with self.assertRaises(ValueError):self.verify()

    def test_effective_settings_missing_from_projection(self):
        self.build()
        # Keep the native reference map but remove the mandatory raw file.
        archive=self.h.root/'native-evidence-projection.zip'
        with zipfile.ZipFile(archive) as z: rows={n:z.read(n) for n in z.namelist() if n!='database-recovery-settings.json'}
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in rows.items():z.writestr(name,data)
        archive.write_bytes(buffer.getvalue());self.h.report.update(projection_sha256=a.safe.sha(buffer.getvalue()),projection_bytes=len(buffer.getvalue()));self.h.save_report()
        with self.assertRaises(ValueError):self.verify()

    def test_workflow_requires_acquisition_precompression_and_final_admission(self):
        root=a.HERE.parents[2]
        workflow=(root/'.github/workflows/package-optimization.yml').read_text()
        self.assertIn('database_recovery_package_admission.py --root workspace',workflow)
        self.assertIn('database-recovery-source-contract.json} workspace/',workflow)
        acquire=(a.HERE/'acquire.py').read_text()
        self.assertIn('recovery.verify_portable(',acquire);self.assertIn('recovery.verify_effective(',acquire)
        audit=(a.HERE/'independent_verify.py').read_text()
        self.assertIn('database_recovery_admission=verify_database_recovery(',audit)
        self.assertIn("'database_recovery_final_binding'",audit)

    def test_wrong_ipa_or_provenance(self):
        (self.h.root/'original.ipa').write_bytes(b'wrong')
        with self.assertRaises(ValueError): self.verify()

    def test_acquisition_source_mismatch(self):
        value=json.loads(self.data['schema-acquisition.json']);value['acquired_owners']['SideStore']['tree']='a'*40
        self.data['schema-acquisition.json']=json.dumps(value).encode();self.receipt['acquisition_sha256']=a.safe.sha(self.data['schema-acquisition.json']);self.build()
        with self.assertRaises(ValueError):self.verify()

if __name__ == '__main__': unittest.main()
