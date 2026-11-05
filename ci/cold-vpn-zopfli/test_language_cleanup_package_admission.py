import ast,copy,hashlib,json,pathlib,tempfile,unittest
from unittest.mock import patch
import language_cleanup_package_admission as admission
ROOT=pathlib.Path(__file__).resolve().parent
UDID='11111111-1111-1111-1111-111111111111'
OTHER='22222222-2222-2222-2222-222222222222'
def fixture():
    rows=[{'action':action,'timeout_seconds':15 if action=='list' else 45,'state':'completed','exit_code':0,'output':json.dumps({'devices':{'runtime':[]}}) if action=='list' else '', 'error':None} for action in ('shutdown','delete','list')]
    return {'schema':'owned-simulator-cleanup-v1','udid':UDID,'budget_seconds':180,'attempts':rows,'status':'VERIFIED_ABSENT','elapsed_seconds':1}
def envelope(report=None):
    text=json.dumps(fixture() if report is None else report)
    return {'simulator':{'udid':UDID},'cleanup_evidence_json':text,'cleanup_evidence_sha256':hashlib.sha256(text.encode()).hexdigest()}
class CleanupPackageControls(unittest.TestCase):
    def test_valid_receipt_no_external_execution(self):
        with patch('subprocess.check_output',side_effect=AssertionError('execution forbidden')):
            self.assertEqual(admission.verify_language_cleanup(envelope())['status'],'VERIFIED_ABSENT')
    def test_missing_or_wrong_hash_rejected(self):
        for field,value in [('cleanup_evidence_json',None),('cleanup_evidence_json','{}'),('cleanup_evidence_sha256','0'*64),('simulator',None)]:
            receipt=envelope();receipt[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):admission.verify_language_cleanup(receipt)
    def test_wrong_owned_uuid_rejected_even_with_valid_hash(self):
        receipt=envelope();receipt['simulator']['udid']=OTHER
        with self.assertRaises(ValueError):admission.verify_language_cleanup(receipt)
    def test_tamper_rehashed_still_rejected(self):
        changes=[lambda r:r.update(status='FAILED'),lambda r:r.update(elapsed_seconds=181),lambda r:r.update(udid=OTHER),lambda r:r['attempts'][-1].update(output=json.dumps({'devices':{'runtime':[{'udid':UDID}]}})),lambda r:r['attempts'][-1].update(output='{}'),lambda r:r['attempts'][-1].update(state='failed'),lambda r:r['attempts'][1].update(action='erase')]
        for change in changes:
            report=fixture();change(report)
            with self.subTest(report=report),self.assertRaises(ValueError):admission.verify_language_cleanup(envelope(report))
    def test_duplicate_json_keys_rejected(self):
        receipt=envelope();receipt['cleanup_evidence_json']=receipt['cleanup_evidence_json'][:-1]+',"status":"VERIFIED_ABSENT"}'
        receipt['cleanup_evidence_sha256']=hashlib.sha256(receipt['cleanup_evidence_json'].encode()).hexdigest()
        with self.assertRaises(ValueError):admission.verify_language_cleanup(receipt)
    def test_snapshot_tampering_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp,'cleanup-evidence.snapshot').write_text('raise AssertionError("not executed")')
            with patch.object(admission,'HERE',pathlib.Path(tmp)),self.assertRaisesRegex(ValueError,'Reviewed cleanup source differs'):
                admission.verify_language_cleanup(envelope())
    def test_snapshot_link_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp,'cleanup-evidence.snapshot').symlink_to(ROOT/'cleanup-evidence.snapshot')
            with patch.object(admission,'HERE',pathlib.Path(tmp)),self.assertRaisesRegex(ValueError,'missing or linked'):
                admission.verify_language_cleanup(envelope())
    def test_both_real_package_boundaries_enforce_cleanup(self):
        for name in ('acquire.py','independent_verify.py'):
            tree=ast.parse((ROOT/name).read_text())
            calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='verify_language_cleanup']
            self.assertEqual(len(calls),1,name)
    def test_workflow_stages_reviewed_snapshot_and_verifier(self):
        workflow=(ROOT.parents[1]/'.github/workflows/cold-vpn-zopfli.yml').read_text()
        self.assertIn('cp ci/cold-vpn-zopfli/{language_cleanup_package_admission.py,cleanup-evidence.snapshot} workspace/',workflow)
if __name__=='__main__':unittest.main()
