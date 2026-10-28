import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import contract,run_attachment,run_completion,diagnostics_contract
class NestedCompletionReplayTests(unittest.TestCase):
 def test_native_inventory_reaches_completion_and_actual_hashes_remain_required(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);tracked={};hashes={}
   for name in run_completion.EXPECTED_SOURCES:
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('controlled source fixture\n');hashes[name]=contract.sha(path);tracked[name]={'mode':'100644','sha256':hashes[name]}
   data={'schema':'guest-launch-completion-v1','status':'PASS','native_compiled':True,'native_executed':True,'cases':list(run_completion.EXPECTED_CASES),'counterfactuals':run_completion.EXPECTED_COUNTERFACTUALS,'source_sha256':hashes}
   with patch.object(run_completion,'inventory',side_effect=ValueError('Source worktree is not clean')) as pristine:
    self.assertIs(run_completion.validate(data,root,validated_native_inventory=tracked),data);pristine.assert_not_called()
    with self.assertRaisesRegex(ValueError,'not clean'):run_completion.validate(data,root)
    (root/run_completion.EXPECTED_SOURCES[0]).write_text('tampered')
    with self.assertRaisesRegex(ValueError,'source bytes differ'):run_completion.validate(data,root,validated_native_inventory=tracked)
 def test_complete_replay_forwards_native_context_to_nested_validator(self):
  import ast
  tree=ast.parse((HERE/'verify.py').read_text())
  calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='run_completion' and node.func.attr=='validate']
  self.assertEqual(len(calls),1)
  forwarding=[kw for kw in calls[0].keywords if kw.arg=='validated_native_inventory']
  self.assertEqual(len(forwarding),1);self.assertIsInstance(forwarding[0].value,ast.Name);self.assertEqual(forwarding[0].value.id,'validated_native_inventory')

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
  historical=[{'case':name,'same_parent_as_production':True,'marker_hit':hit,'overlay_attached_to_window':True,'first_child_interactive':True,'first_child_hidden':False,'host_child_count':2} for name,hit in zip(run_attachment.LABELS,[True,True,False,False,False])]
  fields=('marker_hit','repeated_marker_hit','direct_host_parent','same_guest_identity','single_attachment','bounds_match','guest_explicit_ltr_preserved','old_parent_released','hidden_state_preserved_by_helper','caller_unhid_marker_only','detached_rejected','empty_rejected','original_parent_preserved')
  corrected=[dict(case=name,bridge_refresh_count=1,**{f:True for f in fields}) for name in run_attachment.CORRECTED_LABELS]
  corrected[8]['scope']='helper_marker_only_not_live_controller_migration'
  return {'schema':'multitask-attachment-qualified-v2','status':'PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH','physical_guest_execution':False,'records':historical,'corrected_records':corrected,'resolver_records':[{'case':name,'selected_expected':True,'expected':expected} for name,expected in run_attachment.RESOLVER_EXPECTED],'scene_provider_scope':'projected_inputs_real_uiwindows','screenshots':run_attachment.SCREENSHOTS.copy()}
 def test_complete_qualified_fixture_passes(self):
  data=self.observation();self.assertIs(run_attachment.validate(data),data)
 def test_historical_bridge_failure_must_reproduce(self):
  data=self.observation();data['records'][2]['marker_hit']=True
  with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_missing_corrected_or_resolver_case_rejected(self):
  for key in ('records','corrected_records','resolver_records','screenshots'):
   data=self.observation();data[key].pop()
   with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_corrected_case_cannot_hide_failed_gate(self):
  for index,key in [(0,'marker_hit'),(1,'same_guest_identity'),(2,'guest_explicit_ltr_preserved'),(3,'repeated_marker_hit'),(4,'single_attachment'),(5,'direct_host_parent'),(6,'bounds_match'),(7,'hidden_state_preserved_by_helper'),(8,'old_parent_released'),(9,'detached_rejected')]:
   data=self.observation();data['corrected_records'][index][key]=False
   with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_resolver_boolean_strict_and_scope(self):
  for key,value in [('selected_expected',1),('expected','wrong')]:
   data=self.observation();data['resolver_records'][0][key]=value
   with self.assertRaises(ValueError):run_attachment.validate(data)
  data=self.observation();data['scene_provider_scope']='physical_device'
  with self.assertRaises(ValueError):run_attachment.validate(data)
 def test_unchanged_bridge_and_historical_implementation_pins(self):
  self.assertEqual(set(run_attachment.UNCHANGED_BRIDGE_HASHES),{'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift'})
  self.assertEqual(contract.sha(HERE/'attachment/baseline/VirtualWindowsHostView.m'),run_attachment.BASELINE_VIRTUAL_SHA)
 def test_source_snapshots_cover_both_runners_before_after(self):
  for filename in ('run_completion.py','run_attachment.py'):
   text=(HERE/filename).read_text()
   self.assertIn('before=inventory(root)',text)
   self.assertIn('inventory(root)==before',text)
  text=(HERE/'verify.py').read_text()
  self.assertIn("set(prepared['source_files'])==set(run_attachment.PRODUCTION_FILES)|fixture_names",text)
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
class DiagnosticContractTests(unittest.TestCase):
 def report(self):
  return {'schema':'guest-launch-diagnostics-v1','status':'PASS','native_compiled':True,'native_executed':True,'ios_swift_typechecked':True,'copy_policy_executed':True,'cases':list(diagnostics_contract.EXPECTED_CASES),'source_sha256':{name:'a'*64 for name in diagnostics_contract.EXPECTED_SOURCES}}
 def test_complete_diagnostics_contract_passes(self):
  data=self.report();self.assertIs(diagnostics_contract.validate(data),data)
 def test_each_native_stage_mandatory(self):
  for flag in ('native_compiled','native_executed','ios_swift_typechecked','copy_policy_executed'):
   for value in (False,1):
    data=self.report();data[flag]=value
    with self.assertRaises(ValueError):diagnostics_contract.validate(data)
 def test_cases_and_source_closure_mandatory(self):
  data=self.report();data['cases'].reverse()
  with self.assertRaises(ValueError):diagnostics_contract.validate(data)
  data=self.report();data['source_sha256']={}
  with self.assertRaises(ValueError):diagnostics_contract.validate(data)
 def test_diagnostic_workflow_failure_cannot_be_hidden(self):
  text=(HERE.parents[1]/'.github/workflows/multitask-launch-preflight.yml').read_text()
  self.assertIn("test '${{ steps.diagnostics.outcome }}' = success",text)
  self.assertIn('run_diagnostics.py --source sources/LiveContainer --output evidence',text)
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
