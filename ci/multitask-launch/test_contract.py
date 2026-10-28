import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import contract,run_attachment,run_completion
class ContractTests(unittest.TestCase):
 def test_duplicate_json_rejected(self):
  with self.assertRaises(ValueError):json.loads('{"status":1,"status":2}',object_pairs_hook=contract.strict)
 def test_manifest_owner_and_immutable_pins(self):
  self.assertEqual(contract.manifest()['repository'],'NRG-Wardog/LiveContainer')
  data=contract.manifest();data['commit']='main'
  with patch.object(contract,'read',return_value=data),self.assertRaises(ValueError):contract.manifest()
 def test_run_identity_not_missing_or_non_numeric(self):
  env={'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
  with patch.dict(os.environ,env):self.assertEqual(contract.identity()['run_id'],'123')
  with patch.dict(os.environ,{**env,'GITHUB_RUN_ID':'../1'}),self.assertRaises(ValueError):contract.identity()
 def observation(self):
  return {'schema':'multitask-attachment-baseline-v1','status':'OBSERVED_NOT_PRODUCT_QUALIFICATION','physical_guest_execution':False,'records':[{'case':name,'same_parent_as_production':True,'marker_hit':False,'overlay_attached_to_window':True,'first_child_interactive':True,'first_child_hidden':False,'host_child_count':2} for name in run_attachment.LABELS]}
 def test_observation_does_not_require_synthetic_success(self):
  data=self.observation();self.assertIs(run_attachment.validate(data),data)
  data['records'][0]['marker_hit']=True;run_attachment.validate(data)
 def test_observation_rejects_false_scope_and_missing_case(self):
  for key,value in [('status','PASS'),('physical_guest_execution',True)]:
   data=self.observation();data[key]=value
   with self.assertRaises(ValueError):run_attachment.validate(data)
  data=self.observation();data['records'].pop()
  with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_projection_and_negative_control_required(self):
  data=self.observation();data['records'][0]['same_parent_as_production']=False
  with self.assertRaises(ValueError):run_attachment.validate(data)
  data=self.observation();data['records'][-1]['marker_hit']=True
  with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_baseline_hashes_bind_only_unchanged_production(self):
  self.assertEqual(set(run_attachment.BASELINE_HASHES),{'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift','MultitaskSupport/VirtualWindowsHostView.m','MultitaskSupport/MultitaskDockView.swift'})
  for digest in run_attachment.BASELINE_HASHES.values():self.assertRegex(digest,'^[0-9a-f]{64}$')
 def test_source_snapshots_cover_both_runners_before_after(self):
  for filename in ('run_completion.py','run_attachment.py'):
   text=(HERE/filename).read_text()
   self.assertIn('before=inventory(root)',text)
   self.assertIn('inventory(root)==before',text)
  text=(HERE/'verify.py').read_text()
  self.assertIn("set(prepared['source_files'])==set(run_attachment.BASELINE_HASHES)|fixture_names",text)
  self.assertIn("sha(run_attachment.HERE/'attachment'/name)==prepared['source_files'][name]",text)
 def test_native_execution_not_inferred_from_status(self):
  data={'schema':'guest-launch-completion-v1','status':'PASS','native_compiled':True,'native_executed':False,'cases':[],'source_sha256':{}}
  with self.assertRaises(ValueError):run_completion.validate(data,Path('/unused'))
 def native_report(self):
  return {'schema':'guest-launch-completion-v1','status':'PASS','native_compiled':True,'native_executed':True,'cases':list(run_completion.EXPECTED_CASES),'source_sha256':{name:'a'*64 for name in run_completion.EXPECTED_SOURCES},'counterfactuals':copy.deepcopy(run_completion.EXPECTED_COUNTERFACTUALS)}
 def test_missing_source_or_counterfactual_rejected(self):
  for mutation in ('source','counterfactual','expected_failure','rejected_type','cases'):
   data=self.native_report()
   if mutation=='source':data['source_sha256'].pop(next(iter(data['source_sha256'])))
   elif mutation=='counterfactual':data['counterfactuals'].pop('nil-guard')
   elif mutation=='expected_failure':data['counterfactuals']['nil-guard']['expected_failure']='anything'
   elif mutation=='rejected_type':data['counterfactuals']['nil-guard']['rejected']=1
   else:data['cases'].reverse()
   with self.assertRaises(ValueError):run_completion.validate(data,Path('/unused'))
class WorkflowTests(unittest.TestCase):
 def test_no_product_packaging_or_skipped_failure(self):
  text=(HERE.parents[1]/'.github/workflows/multitask-launch-preflight.yml').read_text()
  for forbidden in ('continue-on-error','package_candidate.py','brew ','cargo ','xcodebuild -project','cancel-in-progress: true'):self.assertNotIn(forbidden,text)
  self.assertIn("test '${{ steps.completion.outcome }}' = success",text)
  self.assertIn("test '${{ steps.attachment.outcome }}' = success",text)
  self.assertIn('contents: read',text)
  self.assertIn('/Applications/Xcode_26.4.1.app/Contents/Developer',text)
  self.assertIn('submodules: false',text)
 def test_same_sdk_policy_no_new_runtime_download(self):
  text=(HERE/'run_attachment.py').read_text()
  self.assertIn('available_device(catalog,sdk_version)',text)
  self.assertIn('wait_owned_simulator_boot(udid,work)',text)
  self.assertNotIn('download',text)
  self.assertEqual(text.count("'simctl','launch'"),1)
  self.assertEqual(text.count("'simctl','boot',udid"),1)
if __name__=='__main__':unittest.main(verbosity=2)
