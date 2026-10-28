"""Synthetic model controls for native/IPA guest evidence binding, not guest execution."""
import copy,hashlib,json,re,sys,tempfile,types,unittest
from pathlib import Path
from unittest import mock
import multitask_receipt as M
ROOT=Path(__file__).resolve().parents[2]
class BindingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.fakepath=Path(self.temp.name)/'attachment_contract.py';self.fakepath.write_text('# controlled model double\n')
  self.validator=mock.Mock();self.module=types.SimpleNamespace(__file__=str(self.fakepath),validate=self.validator,PRODUCTION_FILES=('MultitaskSupport/AppSceneViewController.m',),UNCHANGED_BRIDGE_HASHES={},BASELINE_VIRTUAL_SHA='a'*64,SCREENSHOTS=['synthetic.png'])
  self.diagpath=Path(self.temp.name)/'diagnostics_contract.py';self.diagpath.write_text('# diagnostic model double\n');self.diagvalidator=mock.Mock();self.diagmodule=types.SimpleNamespace(__file__=str(self.diagpath),validate=self.diagvalidator,EXPECTED_SOURCES=('LiveContainerSwiftUI/Models/LCAppModel.swift',))
  self.patch=mock.patch.dict(sys.modules,{'attachment_contract':self.module,'diagnostics_contract':self.diagmodule});self.patch.start()
  self.identity={'workflow_commit':'a'*40,'run_id':'10','run_attempt':'1'};self.graph={'LiveContainer':['NRG-Wardog/LiveContainer','b'*40]}
  source={'schema':'multitask-launch-source-v1','repository':'NRG-Wardog/LiveContainer','commit':'b'*40,'baseline_app_builder':'c'*40}
  inventory={p:{'mode':'100644','sha256':'d'*64} for p in M.COMPLETION_INPUTS|{'tests/test_guest_host_attachment.py'}}
  common={'identity':self.identity,'source':source,'source_inventory':inventory,'physical_device_execution':False,'real_extension_launch':False}
  reports={'completion':dict(common,schema='multitask-completion-ci-v1',status='PASS_ACTUAL_OBJC_BOUNDARY_NOT_DEVICE',fixture_sha256='e'*64),
   'completion_fixture':{'schema':'guest-launch-completion-v1','status':'PASS','native_compiled':True,'native_executed':True,'cases':M.COMPLETION_CASES,'counterfactuals':M.COUNTERFACTUALS,'source_sha256':{p:'d'*64 for p in M.COMPLETION_INPUTS}},
   'attachment':dict(common,schema='multitask-attachment-ci-v2',status='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH',simulator_execution=True,result_sha256='e'*64,production_source_sha256={'MultitaskSupport/AppSceneViewController.m':'d'*64},wiring_test_sha256='d'*64,unchanged_bridge_sha256={},historical_virtual_source_sha256='a'*64,screenshot_sha256={'synthetic.png':'f'*64}),
   'attachment_fixture':{'synthetic':'delegated separately to the actual attachment contract'},
   'diagnostics':dict(common,schema='multitask-diagnostics-ci-v1',status='PASS_ACTUAL_DIAGNOSTIC_BOUNDARIES_NOT_DEVICE',fixture_sha256='e'*64),
   'diagnostics_fixture':{'source_sha256':{'LiveContainerSwiftUI/Models/LCAppModel.swift':'d'*64}}}
  self.value={'schema':M.SCHEMA,'status':M.STATUS,'identity':self.identity,'source_graph':self.graph,'source':source,'source_inventory_sha256':M.canonical(inventory),'physical_device_execution':False,'real_extension_launch':False,'reports':reports,'artifact_sha256':{p:'e'*64 for p in M.FILES.values()},'embedded_sha256':{k:M.canonical(v) for k,v in reports.items()},'verifier_sha256':{'multitask_receipt.py':M.sha(Path(M.__file__)),'attachment_contract.py':M.sha(self.fakepath),'diagnostics_contract.py':M.sha(self.diagpath)}}
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def reject(self,change,rehash=False):
  v=copy.deepcopy(self.value);change(v)
  if rehash:v['embedded_sha256']={k:M.canonical(r) for k,r in v['reports'].items()}
  with self.assertRaises((ValueError,KeyError,TypeError)):M.verify_portable(v,self.graph,self.identity)
 def test_valid_model_delegates_actual_attachment_contract(self):
  M.verify_portable(self.value,self.graph,self.identity);self.validator.assert_called_once_with(self.value['reports']['attachment_fixture']);self.diagvalidator.assert_called_once_with(self.value['reports']['diagnostics_fixture'])
 def test_attachment_assertion_failure_is_mandatory(self):
  self.validator.side_effect=ValueError('actual attachment failed')
  with self.assertRaisesRegex(ValueError,'actual attachment failed'):M.verify_portable(self.value,self.graph,self.identity)
 def test_diagnostic_failure_is_mandatory(self):
  self.diagvalidator.side_effect=ValueError('diagnostic runtime failed')
  with self.assertRaisesRegex(ValueError,'diagnostic runtime failed'):M.verify_portable(self.value,self.graph,self.identity)
 def test_wrong_run_pin_source_or_execution_claim_rejected(self):
  for key,val in [('status','FAILED'),('physical_device_execution',True),('real_extension_launch',True),('identity',dict(self.identity,run_id='11')),('source_graph',{'LiveContainer':['NRG-Wardog/LiveContainer','f'*40]})]:
   with self.subTest(key=key):self.reject(lambda v:v.__setitem__(key,val))
  self.reject(lambda v:v['source'].__setitem__('commit','f'*40),True)
 def test_missing_extra_or_tampered_report_rejected(self):
  self.reject(lambda v:v['reports'].pop('completion_fixture'))
  self.reject(lambda v:v['reports'].__setitem__('unreviewed',{}))
  self.reject(lambda v:v['reports']['completion_fixture'].__setitem__('status','FAIL'))
  self.reject(lambda v:v['artifact_sha256'].__setitem__('unknown.json','a'*64))
 def test_failed_skipped_or_observation_only_not_qualification(self):
  for field,val in [('status','STATIC_PASS_NATIVE_NOT_RUN'),('native_compiled',False),('native_executed',False),('native_executed',1),('cases',M.COMPLETION_CASES[:-1])]:
   with self.subTest(field=field):self.reject(lambda v:v['reports']['completion_fixture'].__setitem__(field,val),True)
  self.reject(lambda v:v['reports']['attachment'].__setitem__('status','OBSERVED_NOT_PRODUCT_QUALIFICATION'),True)
  self.reject(lambda v:v['reports']['attachment'].__setitem__('simulator_execution',1),True)
 def test_counterfactual_exactness_required(self):
  self.reject(lambda v:v['reports']['completion_fixture']['counterfactuals'].pop('nil-guard'),True)
  self.reject(lambda v:v['reports']['completion_fixture']['counterfactuals']['nil-guard'].__setitem__('rejected',1),True)
 def test_source_and_artifact_hashes_closed(self):
  self.reject(lambda v:v['reports']['completion_fixture']['source_sha256'].pop(next(iter(M.COMPLETION_INPUTS))),True)
  self.reject(lambda v:v['reports']['completion_fixture']['source_sha256'].__setitem__(next(iter(M.COMPLETION_INPUTS)),'f'*64),True)
  self.reject(lambda v:v['reports']['completion'].__setitem__('fixture_sha256','f'*64),True)
  self.reject(lambda v:v['reports']['attachment'].__setitem__('result_sha256','f'*64),True)
  self.reject(lambda v:v['verifier_sha256'].__setitem__('attachment_contract.py','f'*64))
  self.reject(lambda v:v['reports']['attachment']['production_source_sha256'].__setitem__('unknown','d'*64),True)
  self.reject(lambda v:v['reports']['attachment'].__setitem__('wiring_test_sha256','f'*64),True)
  self.reject(lambda v:v['reports']['attachment']['screenshot_sha256'].pop('synthetic.png'),True)
 def test_duplicate_json_key_rejected(self):
  p=Path(self.temp.name)/'bad.json';p.write_text('{"status":"PASS","status":"FAIL"}')
  with self.assertRaises(ValueError):M.read(p)
class NativeInventoryTests(unittest.TestCase):
 def setUp(self):
  import record,subprocess,os
  self.record=record;self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.sources=self.base/'sources';self.root=self.sources/'LiveContainer';self.root.mkdir(parents=True);self.evidence=self.base/'evidence';self.evidence.mkdir()
  self.git=lambda *args:subprocess.check_output(['git','-C',str(self.root),'-c','core.hooksPath=/dev/null',*args],text=True).strip()
  self.git('init','-q');self.git('config','user.email','fixture@example.invalid');self.git('config','user.name','fixture');(self.root/'source.swift').write_text('let value = 1\n');self.git('add','.');self.git('commit','-qm','controlled fixture')
  self.commit=self.git('rev-parse','HEAD');self.tree=self.git('rev-parse','HEAD^{tree}');self.identity={'workflow_commit':'a'*40,'run_id':'10','run_attempt':'1'}
  self.env=mock.patch.dict(os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'10','GITHUB_RUN_ATTEMPT':'1'});self.env.start()
  self.tracked=record.tracked(self.root);self.overlay=self.root/'GeneratedDependencies/OpenSSL35';self.overlay.mkdir(parents=True);(self.overlay/'fixture.bin').write_bytes(b'controlled generated bytes')
  self.generated={'relative_path':'LiveContainer/GeneratedDependencies/OpenSSL35','files':record.generated_inventory(self.overlay),'present':True}
  self.snapshot={'LiveContainer':{'commit':self.commit,'tree':self.tree,'files':self.tracked},'generated-openssl':self.generated}
  for name in ('acquired.json','compiler-inputs.json','after-build.json'):(self.evidence/name).write_text(json.dumps(self.snapshot,sort_keys=True))
  def sealed(**kwargs):
   self.assertEqual(kwargs,{'required':True,'sealed':True})
   if record.generated_inventory(self.overlay)!=self.generated['files']:raise ValueError('Generated bytes/modes changed')
   return self.generated
  self.patches=[mock.patch.object(record,'S',self.sources),mock.patch.object(record,'E',self.evidence),mock.patch.object(record,'OWNERS',{'LiveContainer':('NRG-Wardog/LiveContainer',self.commit)}),mock.patch.object(record,'generated_openssl_snapshot',side_effect=sealed)]
  for patch in self.patches:patch.start()
 def tearDown(self):
  for patch in reversed(self.patches):patch.stop()
  self.env.stop();self.temp.cleanup()
 def check(self):return M.native_inventory(self.sources,self.evidence,self.identity)
 def test_exact_sealed_generated_overlay_accepted(self):self.assertEqual(self.check(),self.tracked)
 def test_actual_tracked_change_rejected(self):
  (self.root/'source.swift').write_text('let value = 2\n')
  with self.assertRaisesRegex(ValueError,'tracked LC'):self.check()
 def test_unknown_untracked_rejected(self):
  (self.root/'unknown.swift').write_text('unreviewed')
  with self.assertRaisesRegex(ValueError,'untracked LC'):self.check()
 def test_generated_drift_rejected(self):
  (self.overlay/'fixture.bin').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'Generated'):self.check()
 def test_generated_symlink_rejected(self):
  p=self.overlay/'fixture.bin';p.unlink();p.symlink_to(self.root/'source.swift')
  with self.assertRaisesRegex(RuntimeError,'Linked generated'):self.check()
 def test_snapshot_or_identity_tamper_rejected(self):
  (self.evidence/'after-build.json').write_text('{}')
  with self.assertRaisesRegex(ValueError,'snapshots changed'):self.check()
  (self.evidence/'after-build.json').write_text((self.evidence/'compiler-inputs.json').read_text())
  with self.assertRaisesRegex(ValueError,'identity differs'):M.native_inventory(self.sources,self.evidence,dict(self.identity,run_id='11'))
 def test_staged_index_change_rejected_even_with_restored_working_bytes(self):
  p=self.root/'source.swift';p.write_text('changed');self.git('add','source.swift');p.write_text('let value = 1\n')
  with self.assertRaisesRegex(ValueError,'index changed'):self.check()
 def test_missing_compiler_snapshot_rejected(self):
  for name in ('compiler-inputs.json','after-build.json'):
   p=self.evidence/name;saved=p.read_bytes();p.unlink()
   with self.assertRaisesRegex(ValueError,'Missing or linked'):self.check()
   p.write_bytes(saved)
 def test_tracked_mode_change_rejected(self):
  p=self.root/'source.swift';p.chmod(0o755)
  with self.assertRaisesRegex(ValueError,'mode/content changed'):self.check()
 def test_staged_mode_change_rejected(self):
  self.git('update-index','--chmod=+x','source.swift')
  with self.assertRaisesRegex(ValueError,'tracked LC'):self.check()
 def test_focused_default_still_rejects_generated_overlay(self):
  import importlib.util
  path=ROOT/'ci/multitask-launch/contract.py';spec=importlib.util.spec_from_file_location('_focused_pristine_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  with mock.patch.object(module,'manifest',return_value={'commit':self.commit}):
   with self.assertRaisesRegex(ValueError,'not clean'):module.inventory(self.root)

class WorkflowTests(unittest.TestCase):
 def test_required_guest_step_precedes_build_and_packaging(self):
  s=(ROOT/'.github/workflows/cold-vpn-native.yml').read_text();blocks=s.split('      - name: ')[1:]
  guest=next(b for b in blocks if 'id: multitask_tests\n' in b)
  self.assertNotIn('continue-on-error',guest);self.assertNotIn('if:',guest);self.assertIn('set -euo pipefail',guest)
  self.assertIn('multitask_receipt.py run --sources sources --evidence evidence/multitask-launch --output evidence/multitask-launch-tests.json',guest)
  self.assertIn('replay_retained_native_evidence.py --source sources/LiveContainer --evidence evidence/multitask-launch',guest)
  self.assertLess(s.index('id: multitask_tests'),s.index('id: livecontainer_build'))
  final=next(b for b in blocks if b.startswith('Package same-run reviewed candidate'))
  self.assertIn('if: success()',final);self.assertIn('--cold-vpn --multitask',final)
  upload=next(b for b in blocks if b.startswith('Upload experimental candidate IPA'))
  self.assertIn('if: success()',upload)
  # GitHub's success() is false after a mandatory earlier failed step, even if
  # independent diagnostic compilation continues under explicit prerequisites.
  for failure in ('guest-completion','attachment-assertion','guest-receipt'):
   outcomes={'source_gate':'success','multitask_tests':'failure','livecontainer_build':'success'}
   self.assertFalse(all(v=='success' for v in outcomes.values()),failure)
 def test_receipt_package_and_quick_chain_cannot_omit_guest_hash(self):
  record=(ROOT/'ci/issues-native/record.py').read_text();package=(ROOT/'ci/issues-native/package_candidate.py').read_text()
  self.assertIn("os.environ.get('GITHUB_REF') == 'refs/heads/validation/cold-vpn'",record)
  self.assertIn("verify_multitask(E / 'multitask-launch-tests.json'",record)
  self.assertIn("receipt.get('evidence_sha256', {}).get(multitask_receipt.name) != sha(multitask_receipt)",package)
  self.assertIn("provenance['multitask_launch_regression_sha256'] = sha(multitask_receipt)",package)
  for file in ('acquire.py','independent_verify.py'):
   text=(ROOT/'ci/cold-vpn-zopfli'/file).read_text();self.assertIn('multitask-launch-tests.json',text);self.assertIn('multitask_launch_regression_sha256',text);self.assertIn('verify_portable',text)
  quick=(ROOT/'.github/workflows/cold-vpn-zopfli.yml').read_text();self.assertIn('ci/issues-native/multitask_receipt.py ci/multitask-launch/{attachment_contract,diagnostics_contract}.py workspace/',quick)
if __name__=='__main__':unittest.main()
