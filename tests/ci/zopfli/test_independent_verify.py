"""Adversarial archives must fail before any optional provenance/source file access."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
from pathlib import Path as _CIPath
import copy,json,pathlib,struct,subprocess,sys,tempfile,unittest
from contract import PIN,SHA,BUDGET,ITERATIONS,verify_round_schedule,verify_size_acceptance,CLOSEST_NATIVE_INPUT,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256
from test_sweep import sample
from zip32 import parse
ROOT=(_CIPath(__file__).resolve().parents[3]/'ci/packaging/zopfli')
class AuditControls(unittest.TestCase):
    def test_measured_exception_binds_complete_current_manifest(self):
        from contract import manifest
        self.assertEqual(CLOSEST_NATIVE_INPUT,manifest(ROOT/'input-manifest.json'))
    def test_previous_delivered_candidate_cannot_use_current_bound(self):
        previous={'schema': 'cold-vpn-green-native-input-v1', 'run_id': 41441341176, 'run_attempt': 1, 'commit': '0ce0b994e8be93e004a8777dad12846a6b6c97a9', 'artifact_id': 13143497473, 'artifact_bytes': 26753225, 'artifact_sha256': '5f018b617ea8fde6407b31f7307fefed9a08401f41ea327d595603193dbce65d', 'ipa_bytes': 25028009, 'ipa_sha256': 'a8eab0e4f8610b50e26cc9ecc0b6b28be40e931e38b7164a7dba718775ad48a4', 'entries': 274}
        recent={'schema': 'cold-vpn-green-native-input-v1', 'run_id': 41656076254, 'run_attempt': 2, 'commit': 'bfe6432d9eeb23dffa60ff9e550e0f15fe14171e', 'artifact_id': 13250861043, 'artifact_bytes': 26766239, 'artifact_sha256': '2d86992b02659633398688478c9cdf90d39797c96db3d6541cd5a965e27bded2', 'ipa_bytes': 25028780, 'ipa_sha256': '130a72735272fc484b7bd872edd62228a264b2e529dceb3e15cca2183ff8cf28', 'entries': 274}
        latest={'schema': 'cold-vpn-green-native-input-v1', 'run_id': 41699105117, 'run_attempt': 1, 'commit': '5a6ea2020a23386661b9ab5ba9a025c693ac0a66', 'artifact_id': 13267085125, 'artifact_bytes': 26768242, 'artifact_sha256': 'a62b24a84f7872877f9e097963aebd6975bdf9e424508a433d42ebac824687ba', 'ipa_bytes': 25031534, 'ipa_sha256': '6a544be5c9cdf0360cc4b85304835a91293c362da6082067eb69b3cc77d8be27', 'entries': 274}
        fallback={'schema': 'cold-vpn-green-native-input-v1', 'run_id': 41816078385, 'run_attempt': 2, 'commit': '849742a0718bdf5409926a6885e92cc013c7a6ef', 'artifact_id': 13322905493, 'artifact_bytes': 26768567, 'artifact_sha256': '2f6980d6d85c1e9486fcef1dc1a8a11527cefe01d36143a66f51c1c91f9e8819', 'ipa_bytes': 25034407, 'ipa_sha256': 'fd8491ad3e09c6e9ac3fdc0560ab8c939cdcdd00d329cf27a20e9a4e3f191727', 'entries': 274}
        combined={'schema': 'cold-vpn-green-native-input-v1', 'run_id': 41867573278, 'run_attempt': 1, 'commit': 'a78ea0efece1afde269a893522b79dca797de578', 'artifact_id': 13345328061, 'artifact_bytes': 26821400, 'artifact_sha256': '7d5a328d2f56e810eb08ee32b3d356fca8a04f89f4fbbef65b44d20c6e2ce5a2', 'ipa_bytes': 25068355, 'ipa_sha256': '39cd12e531fe4e6477da98d77cda2e6b52ed5880ca64c80cf150bf33539e79de', 'entries': 274}
        for candidate in (previous,recent,latest,fallback,combined):
            with self.subTest(run=candidate['run_id']),self.assertRaisesRegex(ValueError,'closest-result native input differs'):
                verify_size_acceptance(candidate,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
    def test_standard_size_target_remains_strict_for_other_candidates(self):
        unknown=dict(CLOSEST_NATIVE_INPUT,commit='a'*40)
        self.assertTrue(verify_size_acceptance(unknown,24000000,'a'*64,True)['target_met'])
        with self.assertRaises(ValueError):verify_size_acceptance(unknown,24000001,'a'*64,False)
        with self.assertRaises(ValueError):verify_size_acceptance(unknown,24000001,'a'*64,True)
    def test_exact_closest_candidate_reports_target_not_met(self):
        accepted=verify_size_acceptance(CLOSEST_NATIVE_INPUT,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
        self.assertFalse(accepted['target_met']);self.assertEqual(accepted['excess_bytes'],CLOSEST_OUTPUT_BYTES-24000000)
        self.assertEqual(accepted['policy'],'exact_reviewed_candidate_measured_upper_bound')
    def test_closest_candidate_rejects_any_input_or_output_drift(self):
        for key,value in CLOSEST_NATIVE_INPUT.items():
            changed=copy.deepcopy(CLOSEST_NATIVE_INPUT)
            changed[key]=value+1 if type(value) is int else value+'x'
            with self.subTest(key=key),self.assertRaises(ValueError):verify_size_acceptance(changed,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
        for size,digest,target in [(CLOSEST_OUTPUT_BYTES+1,CLOSEST_OUTPUT_SHA256,False),(CLOSEST_OUTPUT_BYTES,'invalid',False),(CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,True),(CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,0)]:
            with self.subTest(size=size,digest=digest,target=target),self.assertRaises(ValueError):verify_size_acceptance(CLOSEST_NATIVE_INPUT,size,digest,target)
        extra=dict(CLOSEST_NATIVE_INPUT,unexpected=True)
        with self.assertRaises(ValueError):verify_size_acceptance(extra,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
    def test_smaller_same_candidate_binds_actual_not_reference_digest(self):
        actual='a'*64
        accepted=verify_size_acceptance(CLOSEST_NATIVE_INPUT,CLOSEST_OUTPUT_BYTES-1,actual,False)
        self.assertEqual(accepted['accepted_output_sha256'],actual)
        self.assertEqual(accepted['maximum_output_bytes'],CLOSEST_OUTPUT_BYTES)
        self.assertEqual(accepted['reference_measurement_sha256'],CLOSEST_OUTPUT_SHA256)
    def test_below_bound_payload_and_metadata_tamper_rejected_by_real_auditor(self):
        import io,runpy,zipfile,traceback
        from unittest.mock import patch
        import contract
        def archive(payload):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
                z.writestr(zipfile.ZipInfo('one',(2026,1,1,0,0,0)),payload);z.writestr(zipfile.ZipInfo('two',(2026,1,1,0,0,0)),b'fixed')
            return out.getvalue()
        data=archive(b'original')
        metadata=bytearray(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive_reader:central=archive_reader.start_dir
        oldtime=struct.unpack_from('<H',metadata,10)[0]
        struct.pack_into('<H',metadata,10,oldtime^1);struct.pack_into('<H',metadata,central+12,oldtime^1)
        for changed,payload_changed in ((archive(b'tampered'),True),(bytes(metadata),False)):
            with self.subTest(payload_changed=payload_changed),tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory).resolve();self.fixture(root,data,changed)
                m=json.loads((root/'input-manifest.json').read_text())
                result=json.loads((root/'result.json').read_text());result['target_met']=False
                (root/'result.json').write_text(json.dumps(result))
                # Test-only miniature bound: exercise the actual candidate branch,
                # then the unchanged full auditor, without a 24 MB synthetic file.
                with patch.object(contract,'TARGET_BYTES',1),patch.object(contract,'CLOSEST_NATIVE_INPUT',m),patch.object(contract,'CLOSEST_OUTPUT_BYTES',len(data)+1),patch.object(sys,'argv',['independent_verify.py','--root',str(root)]):
                    self.assertFalse(contract.verify_size_acceptance(m,len(changed),SHA(changed),False)['target_met'])
                    try:
                        runpy.run_path(str(ROOT/'independent_verify.py'),run_name='__main__')
                    except AssertionError as error:
                        frames=traceback.extract_tb(error.__traceback__)
                        frame=frames[-1]
                        self.assertEqual(pathlib.Path(frame.filename).resolve(),(ROOT/'independent_verify.py').resolve())
                        expected='raw==b.read(y)' if payload_changed else 'getattr(x,attr)==getattr(y,attr)'
                        self.assertIn(expected,frame.line)
                    else:self.fail('actual payload/metadata tamper was admitted')
    def test_current_exception_rejects_equal_numeric_type_drift(self):
        for key,value in CLOSEST_NATIVE_INPUT.items():
            if type(value) is int:
                changed=dict(CLOSEST_NATIVE_INPUT);changed[key]=float(value)
                with self.subTest(key=key),self.assertRaises(ValueError):
                    verify_size_acceptance(changed,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
        with self.assertRaises(ValueError):
            verify_size_acceptance(CLOSEST_NATIVE_INPUT,float(CLOSEST_OUTPUT_BYTES),CLOSEST_OUTPUT_SHA256,False)
    def test_round_stop_audit_rejects_false_target_extra_round_or_incomplete_schedule(self):
        def report(savings,stop):
            return {'rounds':[{'iterations':iteration,'attempts':1,'elapsed_seconds':1} for iteration in ITERATIONS[:len(savings)]],
                    'results':[{'iterations':iteration,'saved_vs_previous':saved} for iteration,saved in zip(ITERATIONS,savings)],'stop_reason':stop,'elapsed_seconds':2}
        verify_round_schedule(report([2],'size_target_met'),24000001,23999999)
        for count in range(1,len(ITERATIONS)+1):
            verify_round_schedule(report([0]*(count-1)+[2],'size_target_met'),24000001,23999999)
        verify_round_schedule(report([0]*len(ITERATIONS),'iteration_schedule_exhausted'),24000001,24000001)
        for savings,stop in [([0],'size_target_met'),([2,0],'size_target_met'),([0],'iteration_schedule_exhausted'),([0,2],'iteration_schedule_exhausted')]:
            with self.subTest(savings=savings,stop=stop),self.assertRaises(ValueError):
                verify_round_schedule(report(savings,stop),24000001,24000001-sum(savings))
    def fixture(self,root,data,changed):
        (root/'original.ipa').write_bytes(data);(root/'recompressed.ipa').write_bytes(changed)
        m={'schema':'cold-vpn-green-native-input-v1','run_id':1,'run_attempt':1,'artifact_id':2,'artifact_bytes':100,'ipa_bytes':len(data),'entries':2,'commit':'a'*40,'artifact_sha256':'b'*64,'ipa_sha256':SHA(data)}
        (root/'input-manifest.json').write_text(json.dumps(m))
        result={'schema':'cold-vpn-zopfli-quick-v1','source_commit':PIN,'workers':2,'process_memory_limit':2147483648,'aggregate_timeout_seconds':BUDGET,'entry_timeout_seconds':600,'approved_total_budget_seconds':1800,'iterations_schedule':list(ITERATIONS),'elapsed_seconds':1,'device_executed':False,'original_bytes':len(data),'original_sha256':SHA(data),'final_bytes':len(changed),'final_sha256':SHA(changed),'saved_bytes':len(data)-len(changed),'target_met':True}
        (root/'result.json').write_text(json.dumps(result))
    def test_same_payload_altered_metadata_rejected(self):
        data=sample();changed=bytearray(data)
        # Change DOS time in both matching local/central headers, leaving payload/CRC intact.
        rows,_=parse(data);central=len(data)-22-sum(len(r['central']) for r in rows)
        struct.pack_into('<H',changed,10,0);struct.pack_into('<H',changed,central+12,0)
        if bytes(changed)==data:struct.pack_into('<H',changed,10,1);struct.pack_into('<H',changed,central+12,1)
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();self.fixture(root,data,bytes(changed))
            p=subprocess.run([sys.executable,'-B',str(ROOT/'independent_verify.py'),'--root',str(root)],text=True,capture_output=True,timeout=10)
            self.assertNotEqual(p.returncode,0);self.assertIn('AssertionError',p.stderr);self.assertNotIn('FileNotFoundError',p.stderr)
    def test_receipt_hash_tamper_rejected(self):
        data=sample()
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();self.fixture(root,data,data)
            result=json.loads((root/'result.json').read_text());result['final_sha256']='0'*64;(root/'result.json').write_text(json.dumps(result))
            p=subprocess.run([sys.executable,'-B',str(ROOT/'independent_verify.py'),'--root',str(root)],text=True,capture_output=True,timeout=10)
            self.assertNotEqual(p.returncode,0);self.assertIn('AssertionError',p.stderr);self.assertNotIn('FileNotFoundError',p.stderr)
if __name__=='__main__':unittest.main()
