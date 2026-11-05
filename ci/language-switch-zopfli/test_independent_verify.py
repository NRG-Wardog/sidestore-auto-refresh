"""Adversarial archives must fail before any optional provenance/source file access."""
import json,pathlib,struct,subprocess,sys,tempfile,unittest
from contract import PIN,SHA,BUDGET,ITERATIONS
from test_sweep import sample
from zip32 import parse
ROOT=pathlib.Path(__file__).resolve().parent
class AuditControls(unittest.TestCase):
    def fixture(self,root,data,changed):
        (root/'original.ipa').write_bytes(data);(root/'recompressed.ipa').write_bytes(changed)
        m={'schema':'language-switch-green-native-input-v1','run_id':1,'run_attempt':1,'artifact_id':2,'artifact_bytes':100,'ipa_bytes':len(data),'entries':2,'commit':'a'*40,'artifact_sha256':'b'*64,'ipa_sha256':SHA(data)}
        (root/'input-manifest.json').write_text(json.dumps(m))
        result={'schema':'language-switch-zopfli-quick-v1','source_commit':PIN,'workers':2,'process_memory_limit':2147483648,'aggregate_timeout_seconds':BUDGET,'entry_timeout_seconds':600,'approved_total_budget_seconds':1800,'iterations_schedule':list(ITERATIONS),'elapsed_seconds':1,'device_executed':False,'original_bytes':len(data),'original_sha256':SHA(data),'final_bytes':len(changed),'final_sha256':SHA(changed),'saved_bytes':len(data)-len(changed),'target_met':True}
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
