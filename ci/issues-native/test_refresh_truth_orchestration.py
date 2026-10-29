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
     if key=='idevice-ffi':self.assertIn('misagent,openssl,rsd,core_device_proxy',args)
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
 def test_focused_workflow_has_all_independent_stages_without_skipping_native(self):
  text=(ROOT/'.github/workflows/refresh-truth-preflight.yml').read_text()
  for stage in ('swift_tests','rust_tests'):
   self.assertIn("test '${{ steps."+stage+".outcome }}' = success",text)
  self.assertIn('cmp evidence/acquired.json evidence/after-fixtures.json',text)
  self.assertNotIn('continue-on-error',text);self.assertNotIn('|| true',text)
if __name__=='__main__':unittest.main(verbosity=2)
