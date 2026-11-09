import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import preservation_upstream_tests_projection as p
class UpstreamSuccessorTests(unittest.TestCase):
 def setUp(self):
  self.spec=p.specification();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  for name in self.spec['inputs']:
   f=self.root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('controlled fixture')
  def digest(path):
   if path==p.HERE/p.MANIFEST:return p.MANIFEST_SHA
   if not path.is_file():raise ValueError('missing')
   return self.spec['inputs'].get(path.relative_to(self.root).as_posix(),'0'*64)
  self.patch=patch.object(p,'digest',side_effect=digest);self.patch.start();self.addCleanup(self.patch.stop)
 def bind(self,names=None):return p.bind(self.root,self.spec['case_names'] if names is None else names,158)
 def test_exact_closure_and_historical_count(self):
  result=self.bind();self.assertEqual(result['count'],164)
  frozen=json.loads((p.HERE/'upstream-sync-contract.json').read_text());self.assertEqual(frozen['sidestore_test_count'],158)
  self.assertEqual(len(self.spec['additional_cases']),6)
  with self.assertRaises(ValueError):p.bind(self.root,self.spec['case_names'],164)
 def test_unknown_missing_duplicate_replaced_cases_rejected(self):
  names=self.spec['case_names']
  for changed in [names[:-1],names+['invented'],names[:-1]+[names[0]],names[:-1]+['replacement']]:
   with self.assertRaises(ValueError):self.bind(changed)
 def test_missing_tampered_sources_and_extra_file_rejected(self):
  for name in self.spec['inputs']:
   path=self.root/name;path.unlink()
   with self.assertRaises(ValueError):self.bind()
   path.write_text('fixture')
   old=self.spec['inputs'][name];self.spec['inputs'][name]='0'*64
   with self.assertRaises(ValueError):self.bind()
   self.spec['inputs'][name]=old
  (self.root/'SideStore/tests/upstream_sync/test_extra.py').write_text('')
  with self.assertRaises(ValueError):self.bind()
 def report(self):
  return {'reviewed_preservation_successor':self.bind(),'status':'PASS','tests_run':164,'failures':0,'errors':0,'skipped':0,
    'target_native_executed':False,'authentication_executed':False,'scope':'source-owned fake-data/model and source tests only',
    'test_sources_sha256':{k.removeprefix('SideStore/'):v for k,v in self.spec['inputs'].items() if '/tests/upstream_sync/' in k}}
 def test_report_rebound_to_exact_sources_not_claimed_count(self):
  good=self.report();p.verify_report(self.root,good)
  for key,value in [('tests_run',158),('tests_run',True),('status','FAIL'),('scope','unreviewed'),('extra',True),('errors',False),('failures',False),('skipped',False),('skipped',1),('target_native_executed',True),('authentication_executed',True),('test_sources_sha256',{}),('reviewed_preservation_successor',{})]:
   changed=copy.deepcopy(good);changed[key]=value
   with self.assertRaises(ValueError):p.verify_report(self.root,changed)
 def test_report_missing_duplicate_replaced_case_ids_rejected(self):
  good=self.report()
  for mutation in ('missing','duplicate','replace','count','manifest','input'):
   changed=copy.deepcopy(good);proof=changed['reviewed_preservation_successor']
   if mutation=='missing':proof['case_names'].pop()
   elif mutation=='duplicate':proof['case_names'][-1]=proof['case_names'][0]
   elif mutation=='replace':proof['case_names'][-1]='unreviewed.test'
   elif mutation=='count':proof['count']=158
   elif mutation=='manifest':proof['descriptor_sha256']='0'*64
   else:proof['inputs'][p.TEST]='0'*64
   with self.assertRaises(ValueError):p.verify_report(self.root,changed)
 def test_all_workflow_callers_register_controls_and_report_revalidation(self):
  root=p.HERE.parents[1]
  for name in ('refresh-truth-preflight','cold-vpn-native','issues-native','issue56-native'):
   text=(root/'.github/workflows'/ (name+'.yml')).read_text()
   self.assertEqual(text.count('python3 -B harness/ci/issues-native/test_preservation_upstream_tests_projection.py'),2)
   command='python3 -B harness/ci/issues-native/database_recovery_upstream_projection.py --sources sources --report evidence/SideStore-upstream-sync-tests.json'
   self.assertEqual(text.count(command),1)
   self.assertLess(text.index('verify_upstream_sync.py sidestore-tests'),text.index(command))
 def test_descriptor_drift_rejected(self):
  with patch.object(p,'MANIFEST_SHA','0'*64):
   # The mock must keep the actual original descriptor hash, not follow mutation.
   with patch.object(p,'digest',return_value='c832e45fc86632a11714f08f76fc1beccb628dbb74cc4a7915e06ad9dbdc15e3'):
    with self.assertRaises(ValueError):p.specification()
if __name__=='__main__':unittest.main()
