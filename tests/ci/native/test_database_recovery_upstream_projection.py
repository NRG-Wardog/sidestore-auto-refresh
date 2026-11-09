import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
    from ci.native_paths import native_file, REPO_ROOT
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ci.qualification.native import database_recovery_upstream_projection as p

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
   self.assertIn('from ci.qualification.native.database_recovery_upstream_projection import',(native_file(file)).read_text())
  root=(_CIPath(__file__).resolve().parents[3])
  for name, job in (('focused-diagnostics.yml', 'refresh_truth'), ('native-build.yml', None)):
   s=workflow_job(root/'.github/workflows'/ name, job)
   self.assertEqual(s.count('python3 -B harness/tests/ci/native/test_database_recovery_upstream_projection.py'),2)
   self.assertEqual(s.count('database_recovery_upstream_projection.py --sources sources --report evidence/SideStore-upstream-sync-tests.json'),1)

if __name__=='__main__':unittest.main()
