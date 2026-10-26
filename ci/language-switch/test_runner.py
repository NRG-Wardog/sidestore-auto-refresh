from pathlib import Path
import json,tempfile,unittest,copy,sys,io,contextlib,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_language_switch_tests as R
class RunnerTests(unittest.TestCase):
 def test_device_selection_requires_verified_runtime_compatibility(self):
  with self.assertRaises(ValueError):R.available_device({'runtimes':[],'devicetypes':[]})
  runtime={'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-26-5','version':'26.5','isAvailable':True}
  device={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-17','name':'iPhone 17','productFamily':'iPhone','minRuntimeVersion':R.runtime_number('26.0'),'maxRuntimeVersion':4294967295}
  ipod={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPod-touch--7th-generation-','name':'iPod touch (7th generation)','productFamily':'iPhone','minRuntimeVersion':R.runtime_number('12.2'),'maxRuntimeVersion':R.runtime_number('15.9')}
  old={**device,'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-6s','name':'iPhone 6s','minRuntimeVersion':R.runtime_number('9.0'),'maxRuntimeVersion':R.runtime_number('15.9')}
  future={**device,'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-future','name':'iPhone future','minRuntimeVersion':R.runtime_number('27.0')}
  self.assertEqual(R.available_device({'runtimes':[runtime],'devicetypes':[future,device,old,ipod]}),(runtime,device))
  for devices in ([ipod],[old],[future],[{k:v for k,v in device.items() if 'RuntimeVersion' not in k}]):
   with self.assertRaises(ValueError):R.available_device({'runtimes':[runtime],'devicetypes':devices})
  explicit={**runtime,'supportedDeviceTypes':[{'identifier':device['identifier']}]}
  self.assertEqual(R.available_device({'runtimes':[explicit],'devicetypes':[future,device,old,ipod]}),(explicit,device))
  with self.assertRaises(ValueError):R.available_device({'runtimes':[{**runtime,'supportedDeviceTypes':[]}],'devicetypes':[device]})
  with self.assertRaises(ValueError):R.available_device({'runtimes':[runtime],'devicetypes':[{**device,'minRuntimeVersion':True}]})
  unbounded={k:v for k,v in device.items() if 'RuntimeVersion' not in k}
  existing={'deviceTypeIdentifier':device['identifier'],'isAvailable':True,'udid':'EXISTING-DO-NOT-USE'}
  catalog={'runtimes':[runtime],'devicetypes':[unbounded,ipod],'devices':{runtime['identifier']:[existing]}}
  self.assertEqual(R.available_device(catalog),(runtime,unbounded))
  for devices in ({'other-runtime':[existing]},{runtime['identifier']:[{**existing,'isAvailable':False}]}):
   with self.assertRaises(ValueError):R.available_device({**catalog,'devices':devices})
  with self.assertRaises(ValueError):R.available_device({**catalog,'devicetypes':[{**unbounded,'minRuntimeVersion':True}]})
 def test_actual_transition_proof_and_stale_wrong_direction_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();(root/'Resources').mkdir()
   strings={l:{'stringUnit':{'value':'visible-'+l}} for l in set(R.TRANSITIONS)}
   (root/'Resources/Localizable.xcstrings').write_text(json.dumps({'strings':{'Checking':{'localizations':strings}}}))
   data={'status':'PASS','same_hosting_controller':True,'same_state_owner':True,'guest_window_unchanged':True,'presented_guest_wrapper_unchanged':True,'rapid_switch_converged':True,'later_navigation_pass':True,'physical_device_execution':False,'transition_count':12,'presented_sheet_transitions':4,'records':[{'language':l,'visible_string':'visible-'+l,'direction':'rtl' if l=='he' else 'ltr','chrome_count':3} for l in R.TRANSITIONS]}
   R.validate_runtime_result(data,root)
   for key,value in [('same_hosting_controller',False),('same_state_owner',False),('guest_window_unchanged',False),('presented_guest_wrapper_unchanged',False),('transition_count',True),('presented_sheet_transitions',0),('physical_device_execution',True)]:
    bad=copy.deepcopy(data);bad[key]=value
    with self.assertRaises(ValueError):R.validate_runtime_result(bad,root)
   for key,value in [('visible_string','visible-he'),('direction','rtl'),('chrome_count',True)]:
    bad=copy.deepcopy(data);bad['records'][1][key]=value
    with self.assertRaises(ValueError):R.validate_runtime_result(bad,root)
 def test_source_failure_tail_is_bounded_and_does_not_mask_failure(self):
  with tempfile.TemporaryDirectory() as tmp:
   log=Path(tmp)/'synthetic.log';log.write_text('a'*20000+'synthetic failure')
   stream=io.StringIO()
   with contextlib.redirect_stderr(stream),self.assertRaises(ValueError):
    R.source_test_result(subprocess.CompletedProcess([],1),log,'synthetic')
   self.assertLess(len(stream.getvalue()),16100)
   self.assertTrue(stream.getvalue().endswith('synthetic failure\n'))
   R.source_test_result(subprocess.CompletedProcess([],0),log,'synthetic')
 def test_counterfactual_observations_never_replace_default_qualification(self):
  runner=(R.HERE/'run_language_switch_tests.py').read_text()
  self.assertIn("('--counterfactual-no-bridge','without')",runner)
  self.assertIn("('--counterfactual-with-bridge','with')",runner)
  self.assertIn("result=launch_result([],'language-switch-result.json',120)",runner)
  self.assertIn('validate_runtime_result(data,root)',runner)
  self.assertIn('OBSERVED_COUNTERFACTUAL_NOT_QUALIFICATION',runner)
  self.assertIn("report['counterfactual_sha256'][result.name]",runner)
 def test_duplicate_json_key_rejected(self):
  with self.assertRaises(ValueError):R.strict([('status','FAILED'),('status','PASS')])
 def test_runtime_source_not_fake_model_and_same_session_required(self):
  fixture=(R.HERE/'SimulatorFixture.swift').read_text()
  runner=(R.HERE/'run_language_switch_tests.py').read_text()
  self.assertIn("generated=localization+'\\n'+settings",runner)
  self.assertIn('language.select(selected)',fixture)
  self.assertIn('if bridgeEnabled { V3HostLanguageUIKitBridge(language: language.selected) }',fixture)
  self.assertIn('var bridgeEnabled = true',fixture)
  self.assertIn('bridgeEnabled: !withoutBridge',fixture)
  self.assertIn('host.presentedViewController',fixture)
  self.assertIn('model.path=[]',fixture)
  self.assertNotIn('.id(language',fixture)
  self.assertIn('VirtualWindowsHostView',fixture)
  self.assertIn('class AppSceneViewController: UIViewController',fixture)
  self.assertIn('class DecoratedAppSceneViewController: UIViewController',fixture)
  self.assertIn('host.presentedViewController === wrapper',fixture)
if __name__=='__main__':unittest.main(verbosity=2)
