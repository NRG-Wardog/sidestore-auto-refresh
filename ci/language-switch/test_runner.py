from pathlib import Path
import json,tempfile,unittest,copy,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_language_switch_tests as R
class RunnerTests(unittest.TestCase):
 def test_device_selection_rejects_missing_installed_runtime(self):
  with self.assertRaises(ValueError):R.available_device({'runtimes':[],'devicetypes':[]})
  runtime={'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-26-4','version':'26.4','isAvailable':True}
  device={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-17','name':'iPhone 17','productFamily':'iPhone'}
  self.assertEqual(R.available_device({'runtimes':[runtime],'devicetypes':[device]}),(runtime,device))
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
 def test_duplicate_json_key_rejected(self):
  with self.assertRaises(ValueError):R.strict([('status','FAILED'),('status','PASS')])
 def test_runtime_source_not_fake_model_and_same_session_required(self):
  fixture=(R.HERE/'SimulatorFixture.swift').read_text()
  runner=(R.HERE/'run_language_switch_tests.py').read_text()
  self.assertIn("generated=localization+'\\n'+settings",runner)
  self.assertIn('language.select(selected)',fixture)
  self.assertIn('.background(V3HostLanguageUIKitBridge(language: language.selected))',fixture)
  self.assertIn('host.presentedViewController',fixture)
  self.assertIn('model.path=[]',fixture)
  self.assertNotIn('.id(language',fixture)
  self.assertIn('VirtualWindowsHostView',fixture)
  self.assertIn('class AppSceneViewController: UIViewController',fixture)
  self.assertIn('class DecoratedAppSceneViewController: UIViewController',fixture)
  self.assertIn('host.presentedViewController === wrapper',fixture)
if __name__=='__main__':unittest.main(verbosity=2)
