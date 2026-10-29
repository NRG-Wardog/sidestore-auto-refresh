"""Synthetic end-to-end runner/wiring controls; no native execution claim."""
import copy,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
import refresh_truth_receipt as C
from test_refresh_truth_receipt import fixture
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('refresh_truth_runner',ROOT/'ci/refresh-truth/run.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
class OrchestrationTests(unittest.TestCase):
 def test_all_phases_from_canonical_alternate_directory_and_exact_command_options(self):
  value=fixture();source=value['parts']['swift']['source'];graph={'owners':value['source_graph']};calls=[]
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();sources=root/'sources';sources.mkdir();out=root/'unrelated'/'evidence';out.mkdir(parents=True)
   def fake_command(args,cwd,log,env,timeout):
    self.assertEqual(cwd,out);self.assertFalse(Path(cwd).is_relative_to(sources));self.assertFalse(log.exists())
    if args[0]=='cargo':
     key=args[args.index('-p')+1];text=value['parts']['rust']['logs'][key]
     self.assertEqual(args[args.index('--manifest-path')+1],str(sources/'idevice/Cargo.toml'))
     self.assertIn('--locked',args);self.assertIn('--no-default-features',args)
     self.assertEqual(env['CARGO_TARGET_DIR'],str(out/'rust-target'))
     if key=='idevice-ffi':self.assertIn('misagent,openssl,rsd,core_device_proxy,tunnel_tcp_stack,heartbeat',args)
    else:
     script=Path(args[2]);key=next(k for k,(p,_) in C.SUITES.items() if script==sources/C.SUITE_OWNERS[k]/p)
     text=value['parts']['swift']['logs'][key]
     self.assertEqual(env['V3_REQUIRE_SWIFT'],'1');self.assertEqual(env['REQUIRE_NATIVE_PROFILE_READBACK'],'1')
    log.write_text(text);calls.append(args);return text
   with mock.patch.object(R,'load_candidate_graph',return_value=graph),mock.patch.object(R,'run_identity',return_value=value['identity']),mock.patch.object(C,'collect',return_value=source),mock.patch.object(R,'compiler',return_value=value['parts']['swift']['compiler']),mock.patch.object(R,'command',side_effect=fake_command):
    R.run('swift',sources,out);R.run('rust',sources,out);R.run('seal',sources,out)
   result=C.read(out/'refresh-truth-tests.json');C.verify_portable(result,graph['owners'],value['identity'])
   self.assertEqual(len(calls),len(C.SUITES)+2)
   self.assertIs(result['physical_device_execution'],False)
 def test_failed_part_cannot_seal_even_when_other_part_passed(self):
  value=fixture()
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();sources=root/'sources';sources.mkdir();out=root/'evidence';out.mkdir()
   for kind,part in value['parts'].items():
    part=copy.deepcopy(part)
    if kind=='swift':part['status']='FAILED'
    (out/('refresh-truth-'+kind+'.json')).write_text(json.dumps(part))
   with mock.patch.object(R,'load_candidate_graph',return_value={'owners':value['source_graph']}),mock.patch.object(R,'run_identity',return_value=value['identity']),mock.patch.object(C,'collect',return_value=value['parts']['swift']['source']),self.assertRaisesRegex(ValueError,'not passed'):R.run('seal',sources,out)
   self.assertFalse((out/'refresh-truth-tests.json').exists())
 def test_swift_failure_runs_every_other_owner_once_but_never_passes(self):
  value=fixture();source=value['parts']['swift']['source'];calls=[]
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();sources=root/'sources';sources.mkdir();out=root/'evidence';out.mkdir()
   def fake(args,cwd,log,env,timeout):
    script=Path(args[2]);owner=next(k for k,(p,_) in C.SUITES.items() if script==sources/C.SUITE_OWNERS[k]/p)
    calls.append(owner);self.assertEqual(timeout,600)
    if owner=='SideStore':log.write_text('native fixture compiler error\n');raise ValueError('source-owned fixture command failed')
    text=value['parts']['swift']['logs'][owner];log.write_text(text);return text
   with mock.patch.object(R,'load_candidate_graph',return_value={'owners':value['source_graph']}),mock.patch.object(R,'run_identity',return_value=value['identity']),mock.patch.object(C,'collect',return_value=source) as collected,mock.patch.object(R,'compiler',return_value=value['parts']['swift']['compiler']),mock.patch.object(R,'command',side_effect=fake):
    with self.assertRaisesRegex(ValueError,'independent Swift qualification failed'):R.run('swift',sources,out)
   self.assertEqual(calls,list(C.SUITES));self.assertEqual(collected.call_count,len(C.SUITES)+2)
   result=C.read(out/'refresh-truth-swift.json');self.assertEqual(result['status'],'FAILED');self.assertEqual(set(result['logs']),set(C.SUITES))
   with self.assertRaisesRegex(ValueError,'not passed'):C.validate_part(result,'swift',value['source_graph'],value['identity'])
   self.assertFalse((out/'refresh-truth-tests.json').exists())
 def test_changed_source_stops_before_next_owner_and_cannot_pass(self):
  value=fixture();source=value['parts']['swift']['source'];calls=[]
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();sources=root/'sources';sources.mkdir();out=root/'evidence';out.mkdir()
   def fake(args,cwd,log,env,timeout):
    calls.append(args);text=value['parts']['swift']['logs']['LiveContainer'];log.write_text(text);return text
   with mock.patch.object(R,'load_candidate_graph',return_value={'owners':value['source_graph']}),mock.patch.object(R,'run_identity',return_value=value['identity']),mock.patch.object(C,'collect',side_effect=[source,{'changed':True}]),mock.patch.object(R,'compiler',return_value=value['parts']['swift']['compiler']),mock.patch.object(R,'command',side_effect=fake):
    with self.assertRaisesRegex(ValueError,'source changed after native refresh suite'):R.run('swift',sources,out)
   self.assertEqual(len(calls),1);self.assertEqual(C.read(out/'refresh-truth-swift.json')['status'],'FAILED')
 def test_missing_swift_log_is_failure_not_permission_to_skip_owner(self):
  value=fixture();calls=[]
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();sources=root/'sources';sources.mkdir();out=root/'evidence';out.mkdir()
   def fake(args,cwd,log,env,timeout):
    calls.append(args);raise OSError('synthetic process launch failure')
   with mock.patch.object(R,'load_candidate_graph',return_value={'owners':value['source_graph']}),mock.patch.object(R,'run_identity',return_value=value['identity']),mock.patch.object(C,'collect',return_value=value['parts']['swift']['source']),mock.patch.object(R,'compiler',return_value=value['parts']['swift']['compiler']),mock.patch.object(R,'command',side_effect=fake):
    with self.assertRaisesRegex(ValueError,'required suite log missing'):R.run('swift',sources,out)
   self.assertEqual(len(calls),len(C.SUITES));self.assertEqual(C.read(out/'refresh-truth-swift.json')['status'],'FAILED')
 def test_native_and_package_have_mandatory_same_source_receipt(self):
  workflow=(ROOT/'.github/workflows/cold-vpn-native.yml').read_text()
  self.assertIn('id: refresh_truth_swift',workflow);self.assertIn('id: refresh_truth_rust',workflow)
  self.assertLess(workflow.index('id: refresh_truth_swift'),workflow.index('id: livecontainer_build'))
  self.assertLess(workflow.index('id: refresh_truth_rust'),workflow.index('id: transport_build'))
  self.assertIn("test '${{ steps.refresh_truth_swift.outcome }}' = success",workflow)
  self.assertIn('refresh-truth-failure-${{ github.sha }}',workflow)
  record=(ROOT/'ci/issues-native/record.py').read_text();self.assertIn("verify_refresh_truth(E / 'refresh-truth-tests.json'",record)
  package=(ROOT/'ci/issues-native/package_candidate.py').read_text();self.assertEqual(package.count('verify_refresh_truth(refresh_truth_receipt,'),2)
  self.assertIn("provenance['refresh_truth_regression_sha256'] = sha(refresh_truth_receipt)",package)
  for name in ('acquire.py','independent_verify.py'):
   text=(ROOT/'ci/cold-vpn-zopfli'/name).read_text();self.assertIn('refresh-truth-tests.json',text);self.assertIn('refresh_truth_regression_sha256',text);self.assertIn('verify_refresh_truth',text)
  quick=(ROOT/'.github/workflows/cold-vpn-zopfli.yml').read_text();self.assertIn('ci/issues-native/refresh_truth_receipt.py workspace/',quick);self.assertIn('workspace/refresh-truth-tests.json',quick)
 def test_focused_swift_failure_upload_is_early_bounded_and_does_not_seal(self):
  text=(ROOT/'.github/workflows/refresh-truth-preflight.yml').read_text()
  self.assertLess(text.index('id: swift_tests'),text.index('id: swift_failure_diagnostics'))
  self.assertLess(text.index('id: swift_failure_diagnostics'),text.index('id: rust_tools'))
  block=text[text.index('        id: swift_failure_diagnostics'):text.index('      - name: Execute existing cold VPN source-owned regression and deployment typecheck')]
  self.assertIn("!cancelled() && steps.source_gate.outcome == 'success' && steps.swift_tests.outcome == 'failure'",block)
  self.assertIn('actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',block)
  self.assertEqual([line.strip() for line in block.splitlines() if line.strip().startswith('evidence/')],['evidence/refresh-truth*.json','evidence/refresh-truth*.log'])
  self.assertIn('if-no-files-found: error',block);self.assertNotIn('run:',block)
  self.assertNotIn('always()',block);self.assertNotIn('continue-on-error',text)
  self.assertIn("test '${{ steps.swift_tests.outcome }}' = success",text)
  self.assertIn("test '${{ steps.rust_tests.outcome }}' = success",text)
 def test_existing_cold_vpn_runner_is_mandatory_and_failure_evidence_is_retained(self):
  text=(ROOT/'.github/workflows/refresh-truth-preflight.yml').read_text()
  block=text[text.index('      - name: Execute existing cold VPN'):text.index('      - name: Select exact previously qualified Rust compiler')]
  self.assertIn("!cancelled() && steps.source_gate.outcome == 'success'",block)
  self.assertIn('python3 -B harness/ci/cold-vpn/run_cold_vpn_tests.py --sources sources --output evidence',block)
  self.assertLess(block.index('cmp evidence/acquired.json evidence/before-cold-fixtures.json'),block.index('run_cold_vpn_tests.py'))
  self.assertIn('unittest discover -s harness/ci/cold-vpn',text)
  self.assertIn("steps.cold_vpn_tests.outcome == 'failure'",block)
  self.assertIn('evidence/cold-vpn/*.log',block);self.assertIn('evidence/cold-vpn/*.json',block)
  self.assertIn('if-no-files-found: error',block)
  self.assertLess(text.index('id: cold_vpn_tests'),text.index('id: rust_tools'))
  self.assertIn("test '${{ steps.cold_vpn_tests.outcome }}' = success",text)
 def test_focused_workflow_has_all_independent_stages_without_skipping_native(self):
  text=(ROOT/'.github/workflows/refresh-truth-preflight.yml').read_text()
  for stage in ('swift_tests','rust_tests','cold_vpn_tests','comprehensive_tests','setup_tests','russian_tests'):
   self.assertIn("test '${{ steps."+stage+".outcome }}' = success",text)
  for command in ('verify_comprehensive_diagnostics.py --sources', 'verify_setup_state_recovery.py run --sources', 'verify_russian_localization.py --live'):
   self.assertIn(command,text)
  for stage in ('comprehensive_tests','setup_tests','russian_tests'):
   self.assertIn('cmp evidence/acquired.json evidence/before-'+stage+'.json',text)
   self.assertIn('refresh-truth-'+stage+'-failure-',text)
  self.assertIn('cmp evidence/acquired.json evidence/after-fixtures.json',text)
  self.assertNotIn('continue-on-error',text);self.assertNotIn('|| true',text)
if __name__=='__main__':unittest.main(verbosity=2)
