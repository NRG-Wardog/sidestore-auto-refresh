import json,subprocess,tempfile,unittest
from pathlib import Path
import cleanup_evidence as C
U='12345678-1234-1234-1234-123456789ABC'
V='AAAAAAAA-1234-1234-1234-123456789ABC'
def inventory(*ids):return json.dumps({'devices':{'runtime':[{'udid':x} for x in ids]}})
class CleanupTests(unittest.TestCase):
 def execute(self,results,clock=lambda:0):
  calls=[]
  def run(args,**kwargs):
   calls.append((args,kwargs));value=results.pop(0)
   if isinstance(value,Exception):raise value
   return value
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);report=C.cleanup(U,root,run=run,clock=clock);text=(root/'cleanup-evidence.json').read_text()
  return report,text,calls
 def test_normal_deletion_requires_actual_absence(self):
  report,text,calls=self.execute(['','',inventory(V)]);C.verify(text,U)
  self.assertEqual(report['status'],'VERIFIED_ABSENT');self.assertEqual(len(calls),3)
  self.assertEqual(calls[0][0],['xcrun','simctl','shutdown',U]);self.assertEqual(calls[1][0],['xcrun','simctl','delete',U])
 def test_shutdown_timeout_recovers_only_by_absence(self):
  error=subprocess.TimeoutExpired('shutdown',45,output=b'partial')
  report,text,calls=self.execute([error,'',inventory(V)]);C.verify(text,U)
  self.assertEqual(report['attempts'][0]['error']['type'],'TimeoutExpired');self.assertEqual(report['attempts'][0]['output'],'partial')
 def test_delete_timeout_uncertain_outcome_observed_absent_no_retry(self):
  report,text,calls=self.execute(['',subprocess.TimeoutExpired('delete',45),inventory()]);C.verify(text,U);self.assertEqual(len(calls),3)
 def test_present_retries_only_owned_delete_once(self):
  report,text,calls=self.execute(['','',inventory(U,V),'',inventory(V)]);C.verify(text,U)
  self.assertEqual([c[0][2] for c in calls],['shutdown','delete','list','delete','list'])
  self.assertEqual(calls[3][0][-1],U)
 def test_present_after_retry_fails(self):
  report,text,calls=self.execute(['','',inventory(U),'',inventory(U)]);self.assertEqual(report['status'],'FAILED')
  with self.assertRaises(ValueError):C.verify(text,U)
 def test_bad_inventory_or_unknown_outcome_never_proves_absence(self):
  for invalid in ['{}','{"devices":{}}','{"devices":[]}', '{"devices":{"r":[{}]}}',inventory(U,U),'not JSON','{"devices":{},"devices":{"r":[]}}']:
   report,text,calls=self.execute(['','',invalid,'',invalid]);self.assertEqual(report['status'],'FAILED')
   with self.assertRaises(ValueError):C.verify(text,U)
  report,text,calls=self.execute(['','',subprocess.TimeoutExpired('list',15),'',subprocess.TimeoutExpired('list',15)])
  self.assertEqual(report['status'],'FAILED')
 def test_budget_exhaustion_fails_without_unbounded_retries(self):
  ticks=iter([0,0,45,45,90,90,105,105,150,150,181,181])
  report,text,calls=self.execute(['','',inventory(U),'',inventory()],clock=lambda:next(ticks))
  self.assertEqual(report['status'],'FAILED');self.assertLessEqual(len(calls),5)
 def test_tampered_owner_proof_or_budget_rejected(self):
  report,text,_=self.execute(['','',inventory()])
  for key,value in [('udid',V),('status','FAILED'),('elapsed_seconds',181),('budget_seconds',999)]:
   bad={**report,key:value}
   with self.assertRaises(ValueError):C.verify(json.dumps(bad),U)
  report['attempts'][-1]['output']=inventory(U)
  with self.assertRaises(ValueError):C.verify(json.dumps(report),U)
 def test_invalid_identity_never_dispatches(self):
  for uid in ['all','booted','../','',V+'x']:
   with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):C.cleanup(uid,Path(tmp),run=lambda *a,**k:self.fail('dispatched'))
 def test_fixture_failure_not_promoted_and_cleanup_is_bound(self):
  source=(Path(__file__).parent/'run_language_switch_tests.py').read_text()
  final=source.split(' finally:\n  if udid:',1)[1].split(' verify_receipt(',1)[0]
  self.assertNotIn("status='PASS",final);self.assertNotIn("report['status']='PASS",final)
  self.assertIn("cleanup['status']!='VERIFIED_ABSENT'",final)
  self.assertIn("verify_cleanup(cleanup.read_text(),d['simulator']['udid'])",source)
if __name__=='__main__':unittest.main(verbosity=2)
