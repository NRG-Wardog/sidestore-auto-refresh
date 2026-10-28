from pathlib import Path
import json,tempfile,unittest,copy,sys,io,contextlib,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_language_switch_tests as R
class RunnerTests(unittest.TestCase):
 def test_device_selection_requires_verified_runtime_compatibility(self):
  with self.assertRaises(ValueError):R.available_device({'runtimes':[],'devicetypes':[]},'26.5')
  runtime={'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-26-5','version':'26.5','isAvailable':True}
  device={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-17','name':'iPhone 17','productFamily':'iPhone','minRuntimeVersion':R.runtime_number('26.0'),'maxRuntimeVersion':4294967295}
  ipod={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPod-touch--7th-generation-','name':'iPod touch (7th generation)','productFamily':'iPhone','minRuntimeVersion':R.runtime_number('12.2'),'maxRuntimeVersion':R.runtime_number('15.9')}
  old={**device,'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-6s','name':'iPhone 6s','minRuntimeVersion':R.runtime_number('9.0'),'maxRuntimeVersion':R.runtime_number('15.9')}
  future={**device,'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-future','name':'iPhone future','minRuntimeVersion':R.runtime_number('27.0')}
  self.assertEqual(R.available_device({'runtimes':[runtime],'devicetypes':[future,device,old,ipod]},'26.5'),(runtime,device))
  for devices in ([ipod],[old],[future],[{k:v for k,v in device.items() if 'RuntimeVersion' not in k}]):
   with self.assertRaises(ValueError):R.available_device({'runtimes':[runtime],'devicetypes':devices},'26.5')
  explicit={**runtime,'supportedDeviceTypes':[{'identifier':device['identifier']}]}
  self.assertEqual(R.available_device({'runtimes':[explicit],'devicetypes':[future,device,old,ipod]},'26.5'),(explicit,device))
  with self.assertRaises(ValueError):R.available_device({'runtimes':[{**runtime,'supportedDeviceTypes':[]}],'devicetypes':[device]},'26.5')
  with self.assertRaises(ValueError):R.available_device({'runtimes':[runtime],'devicetypes':[{**device,'minRuntimeVersion':True}]},'26.5')
  unbounded={k:v for k,v in device.items() if 'RuntimeVersion' not in k}
  existing={'deviceTypeIdentifier':device['identifier'],'isAvailable':True,'udid':'EXISTING-DO-NOT-USE'}
  catalog={'runtimes':[runtime],'devicetypes':[unbounded,ipod],'devices':{runtime['identifier']:[existing]}}
  self.assertEqual(R.available_device(catalog,'26.5'),(runtime,unbounded))
  for devices in ({'other-runtime':[existing]},{runtime['identifier']:[{**existing,'isAvailable':False}]}):
   with self.assertRaises(ValueError):R.available_device({**catalog,'devices':devices},'26.5')
  with self.assertRaises(ValueError):R.available_device({**catalog,'devicetypes':[{**unbounded,'minRuntimeVersion':True}]},'26.5')
 def selection_fixture(self):
  device={'identifier':'com.apple.CoreSimulator.SimDeviceType.iPhone-17','name':'iPhone 17','productFamily':'iPhone','minRuntimeVersion':R.runtime_number('26.0'),'maxRuntimeVersion':4294967295}
  def runtime(version):return {'identifier':'com.apple.CoreSimulator.SimRuntime.iOS-'+version.replace('.','-'),'version':version,'buildversion':'23E100','isAvailable':True,'supportedDeviceTypes':[device['identifier']]}
  catalog={'runtimes':[runtime(v) for v in ['26.5','26.4','26.4.1','26.3.9']],'devicetypes':[device]}
  return catalog,device
 def test_sdk_family_selects_highest_patch_not_newest_global(self):
  catalog,device=self.selection_fixture()
  for runtimes in [catalog['runtimes'],list(reversed(catalog['runtimes']))]:
   selected,_=R.available_device({**catalog,'runtimes':runtimes},'26.4')
   self.assertEqual(selected['version'],'26.4.1')
   self.assertEqual(R.available_device({**catalog,'runtimes':runtimes},'26.4.2')[0],selected)
 def test_runtime_tie_is_deterministic_and_duplicate_identities_fail(self):
  catalog,device=self.selection_fixture();runtime=catalog['runtimes'][2]
  other={**runtime,'identifier':runtime['identifier']+'-B','buildversion':'23E101'}
  for rows in [[runtime,other],[other,runtime]]:
   self.assertEqual(R.available_device({**catalog,'runtimes':rows},'26.4')[0],other)
  with self.assertRaises(ValueError):R.available_device({**catalog,'runtimes':[runtime,runtime]},'26.4')
  with self.assertRaises(ValueError):R.available_device({**catalog,'devicetypes':[device,device]},'26.4')
 def test_missing_unavailable_wrong_family_or_malformed_sdk_fails_closed(self):
  catalog,device=self.selection_fixture()
  for sdk in ['26','26.4beta','26.4.1.2','26.4\n',26.4,True,'15.4','65536.0','26.256','26.-1']:
   with self.subTest(sdk=sdk),self.assertRaises(ValueError):R.available_device(catalog,sdk)
  for runtimes in [[],[catalog['runtimes'][0]],[{**r,'isAvailable':False} for r in catalog['runtimes']]]:
   with self.assertRaises(ValueError):R.available_device({**catalog,'runtimes':runtimes},'26.4')
 def test_selection_receipt_binds_inventory_sdk_build_and_executed_identity(self):
  catalog,device=self.selection_fixture();runtime,_=R.available_device(catalog,'26.4')
  evidence=R.selection_evidence(json.dumps(catalog),'26.4','/Applications/Xcode.app/SDKs/iPhoneSimulator26.4.sdk',runtime,device)
  report={'simulator_selection':evidence,'simulator':{'runtime':runtime,'device_type':device,'sdk':evidence['sdk_path']}}
  R.verify_selection(report)
  alias=copy.deepcopy(report);alias['simulator']['sdk']='/Applications/Xcode.app/SDKs/iPhoneSimulator.sdk';alias['simulator_selection']['sdk_path']=alias['simulator']['sdk'];R.verify_selection(alias)
  for key,value in [('policy_version','old'),('sdk_version','26.5'),('runtime_version','26.5'),('runtime_build','other'),('runtime_identifier','other'),('inventory_sha256','0'*64),('sdk_path','relative.sdk'),('sdk_path','/Applications/Xcode.app/SDKs/iPhoneSimulator26.5.sdk'),('inventory_json','{}')]:
   changed=copy.deepcopy(report);changed['simulator_selection'][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):R.verify_selection(changed)
  changed=copy.deepcopy(report);changed['simulator']['runtime']=catalog['runtimes'][0]
  with self.assertRaises(ValueError):R.verify_selection(changed)
  changed=copy.deepcopy(report);changed['simulator_selection']['unknown']=True
  with self.assertRaises(ValueError):R.verify_selection(changed)
 def test_sdk_query_precedes_selection_and_no_execution_failure_fallback(self):
  source=(R.HERE/'run_language_switch_tests.py').read_text()
  self.assertIn("sdk_version=command(['xcrun','--sdk','iphonesimulator','--show-sdk-version']).strip()",source)
  self.assertLess(source.index('sdk_version=command('),source.index('runtime,device=available_device('))
  self.assertEqual(source.count('runtime,device=available_device('),1)
  self.assertEqual(source.count("'simctl','create'"),1)
  self.assertIn("architecture+'-apple-ios16.0-simulator'",source)
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
 def test_technical_direction_is_explicit_without_waiving_counterfactual(self):
  fixture=(R.HERE/'SimulatorFixture.swift').read_text()
  boundary=fixture.split('if observesCounterfactual {',1)[1].split('GuestBoundaryProbe()',1)[0]
  implicit=boundary.split('DiagnosticTableProbe(identifier:',1)[0]
  self.assertIn('DiagnosticTableProbe().frame(height: 1)',implicit)
  self.assertNotIn('.environment(',implicit)
  default=boundary.split('} else {',1)[1]
  self.assertIn('.environment(\\.layoutDirection, .leftToRight)',default)
  self.assertIn('try require(technicalTable.semanticContentAttribute == .forceLeftToRight',fixture)
  self.assertIn('try require(standaloneTable.semanticContentAttribute == .forceLeftToRight',fixture)
  self.assertIn('Sheet explicit technical direction changed',fixture)
  evidence=json.loads((R.HERE/'counterfactual-evidence.json').read_text())
  self.assertEqual(evidence['creation_only_table_semantic_values'],[4,3,4])
  self.assertEqual(evidence['explicit_swiftui_ltr_table_semantic_values'],[3,3,3])
  self.assertIs(evidence['production_source_changed'],False)
 def test_ios15_availability_is_mandatory_and_source_bound(self):
  runner=(R.HERE/'run_language_switch_tests.py').read_text()
  fixture=(R.HERE/'SimulatorFixture.swift').read_text()
  self.assertIn("'REQUIRE_IOS_AVAILABILITY_TESTS':'1'",runner)
  self.assertIn("source_test_result(available,availability_log",runner)
  self.assertIn('Language availability tests skipped or failed',runner)
  self.assertIn("report['source_availability_test_log_sha256']=sha(availability_log)",runner)
  self.assertIn("d['source_availability_test_log_sha256']==sha(path.parent/'language-switch/availability-tests.log')",runner)
  self.assertIn('tests/test_live_language_availability.py',R.SOURCES)
  for name in ['AppSceneViewController','DecoratedAppSceneViewController','VirtualWindowsHostView']:
   self.assertIn('MultitaskSupport/'+name+'.h',R.SOURCES)
   self.assertRegex(fixture,r'@available\(iOS 16\.0, \*\)\n(?:final )?class '+name+r':')
  self.assertLess(runner.index("availability_log=work/"),runner.index("inventory_text=command("))
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
