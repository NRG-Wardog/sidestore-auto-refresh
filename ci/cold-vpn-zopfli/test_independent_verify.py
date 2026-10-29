"""Adversarial archives must fail before any optional provenance/source file access."""
import copy,json,pathlib,struct,subprocess,sys,tempfile,unittest
from contract import PIN,SHA,BUDGET,ITERATIONS,verify_round_schedule,verify_size_acceptance,CLOSEST_NATIVE_INPUT,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256
from test_sweep import sample
from zip32 import parse
ROOT=pathlib.Path(__file__).resolve().parent
class AuditControls(unittest.TestCase):
    def test_standard_size_target_remains_strict_for_other_candidates(self):
        unknown=dict(CLOSEST_NATIVE_INPUT,commit='a'*40)
        self.assertTrue(verify_size_acceptance(unknown,24000000,'a'*64,True)['target_met'])
        with self.assertRaises(ValueError):verify_size_acceptance(unknown,24000001,'a'*64,False)
        with self.assertRaises(ValueError):verify_size_acceptance(unknown,24000001,'a'*64,True)
    def test_exact_closest_candidate_reports_target_not_met(self):
        accepted=verify_size_acceptance(CLOSEST_NATIVE_INPUT,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
        self.assertFalse(accepted['target_met']);self.assertEqual(accepted['excess_bytes'],21064)
        self.assertEqual(accepted['policy'],'exact_reviewed_closest_candidate_only')
    def test_closest_candidate_rejects_any_input_or_output_drift(self):
        for key,value in CLOSEST_NATIVE_INPUT.items():
            changed=copy.deepcopy(CLOSEST_NATIVE_INPUT)
            changed[key]=value+1 if type(value) is int else value+'x'
            with self.subTest(key=key),self.assertRaises(ValueError):verify_size_acceptance(changed,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
        for size,digest,target in [(CLOSEST_OUTPUT_BYTES-1,CLOSEST_OUTPUT_SHA256,False),(CLOSEST_OUTPUT_BYTES+1,CLOSEST_OUTPUT_SHA256,False),(CLOSEST_OUTPUT_BYTES,'a'*64,False),(CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,True),(CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,0)]:
            with self.subTest(size=size,digest=digest,target=target),self.assertRaises(ValueError):verify_size_acceptance(CLOSEST_NATIVE_INPUT,size,digest,target)
        extra=dict(CLOSEST_NATIVE_INPUT,unexpected=True)
        with self.assertRaises(ValueError):verify_size_acceptance(extra,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256,False)
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
