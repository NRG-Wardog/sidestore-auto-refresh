import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import database_recovery_upstream_projection as p

class RecoverySuccessorTests(unittest.TestCase):
 def setUp(self):
  self.spec=p.specification();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.hashes=dict(self.spec['inputs'])
  for name in self.hashes:
   f=self.root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('synthetic')
  original=p.digest
  def digest(path):
   if path==p.HERE/p.MANIFEST:return original(path)
   if not path.is_file() or path.is_symlink():raise ValueError('missing/linked')
   return self.hashes.get(path.relative_to(self.root).as_posix(),'0'*64)
  m=patch.object(p,'digest',side_effect=digest);m.start();self.addCleanup(m.stop)
 def bind(self,names=None):return p.bind(self.root,self.spec['case_names'] if names is None else names,158)
 def report(self):return {'reviewed_preservation_successor':self.bind(),'status':'PASS','tests_run':164,'failures':0,'errors':0,'skipped':0,'target_native_executed':False,'authentication_executed':False,'scope':'source-owned fake-data/model and source tests only','test_sources_sha256':{k.removeprefix('SideStore/'):v for k,v in self.spec['inputs'].items() if '/tests/upstream_sync/' in k}}
 def test_exact_one_case_replacement_and_prior_unchanged(self):
  old=p.prior.specification();self.assertEqual(set(old['case_names'])-set(self.spec['case_names']),{p.OLD_CASE});self.assertEqual(set(self.spec['case_names'])-set(old['case_names']),{p.NEW_CASE});self.assertEqual(self.bind()['count'],164);p.verify_report(self.root,self.report())
 def test_every_source_missing_or_changed_rejected(self):
  for name in self.hashes:
   f=self.root/name;f.unlink()
   with self.assertRaises(ValueError):self.bind()
   f.write_text('synthetic');old=self.hashes[name];self.hashes[name]='0'*64
   with self.assertRaises(ValueError):self.bind()
   self.hashes[name]=old
 def test_cases_and_old_name_rejected(self):
  cases=self.spec['case_names']
  for bad in (cases[:-1],cases+[cases[0]],[p.OLD_CASE if n==p.NEW_CASE else n for n in cases],cases+['invented']):
   with self.assertRaises(ValueError):self.bind(bad)
 def test_extra_test_file_rejected(self):
  (self.root/'SideStore/tests/upstream_sync/test_invented.py').write_text('')
  with self.assertRaises(ValueError):self.bind()
 def test_report_strict_types_scope_and_closure(self):
  for key,value in [('tests_run',True),('tests_run',163),('failures',False),('errors',1),('skipped',1),('status','FAIL'),('extra',None),('target_native_executed',True),('authentication_executed',True),('scope','device'),('test_sources_sha256',{}),('reviewed_preservation_successor',{})]:
   row=self.report();row[key]=value
   with self.assertRaises(ValueError):p.verify_report(self.root,row)
 def test_descriptor_drift_and_duplicate_json_rejected(self):
  with patch.object(p,'MANIFEST_SHA','0'*64):
   with self.assertRaises(ValueError):p.specification()
  with self.assertRaises(ValueError):p.strict_pairs([('a',1),('a',2)])
 def test_projection_discloses_source_case_replacement(self):
  s=(p.HERE/'upstream_candidate_projection.py').read_text()
  self.assertIn("report['original_cases_and_markers_unchanged']=False",s)
  self.assertIn("report['original_runtime_cases_and_markers_unchanged']=True",s)
  self.assertIn("report['reviewed_source_case_map']=dict(test_successor['case_map'])",s)
 def test_routes_and_workflows_use_current_successor(self):
  for file in ('upstream_candidate_projection.py','verify_upstream_sync.py'):
   self.assertIn('from database_recovery_upstream_projection import',(p.HERE/file).read_text())
  root=p.HERE.parents[1]
  for name in ('refresh-truth-preflight','cold-vpn-native','issues-native','language-switch-native','issue56-native'):
   s=(root/'.github/workflows'/ (name+'.yml')).read_text()
   self.assertEqual(s.count('python3 -B harness/ci/issues-native/test_database_recovery_upstream_projection.py'),2)
   self.assertEqual(s.count('database_recovery_upstream_projection.py --sources sources --report evidence/SideStore-upstream-sync-tests.json'),1)

if __name__=='__main__':unittest.main()
