import copy, importlib.util, json, os, plistlib, re, tempfile, unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p2_alert_lane',HERE/'run.py');lane=importlib.util.module_from_spec(spec);spec.loader.exec_module(lane)
class AlertLaneTests(unittest.TestCase):
 def valid(self):
  return {'result':'Passed','passedTests':7,'failedTests':0,'skippedTests':0,'totalTestCount':7},{'testNodes':[{'name':'All tests','nodeType':'Test Plan','children':[{'nodeType':'Test Case','name':name+'()','nodeIdentifier':'NativeAlertLanguageTests/'+name+'()','result':'Passed'} for name in lane.METHODS]}]}
 def test_exact_seven_passes_and_rejects_incomplete_counts(self):
  summary,tests=self.valid();self.assertEqual(lane.verify_results(summary,tests)['testsRun'],7)
  for key,value in [('passedTests',6),('failedTests',1),('skippedTests',1),('totalTestCount',8),('passedTests',True),('result','Failed')]:
   altered=copy.deepcopy(summary);altered[key]=value
   with self.subTest(key=key,value=value),self.assertRaises(ValueError):lane.verify_results(altered,tests)
 def test_rejects_duplicate_missing_wrong_or_failed_test_nodes(self):
  summary,tests=self.valid()
  for kind in ['duplicate','missing','extra','wrong-class','wrong-method','mismatched-name','skipped','failed']:
   altered=copy.deepcopy(tests);rows=altered['testNodes'][0]['children']
   if kind=='duplicate':rows[-1]=copy.deepcopy(rows[0])
   elif kind=='missing':rows.pop()
   elif kind=='extra':rows.append(copy.deepcopy(rows[0]))
   elif kind=='wrong-class':rows[0]['nodeIdentifier']=rows[0]['nodeIdentifier'].replace('NativeAlertLanguageTests','OtherTests')
   elif kind=='wrong-method':rows[0]['nodeIdentifier']='NativeAlertLanguageTests/testOther()'
   elif kind=='mismatched-name':rows[0]['name']='testOther()'
   else:rows[0]['result']=kind.title()
   with self.subTest(kind=kind),self.assertRaises(ValueError):lane.verify_results(summary,altered)
 def test_template_strict_paths_and_explicit_simulation(self):
  text=(HERE/'NativeAlertLanguageTests.swift.in').read_text()
  self.assertEqual(tuple(re.findall(r'^    func (test\w+)\(',text,re.M)),lane.METHODS)
  rapid=text.split('    func '+lane.METHODS[0],1)[1].split('    private func finishAtReplacementBoundary',1)[0]
  before=rapid.split('let current = try XCTUnwrap(fixture.current)',1)[0]
  self.assertNotIn('fixtureRefreshLanguage()',before)
  for value in ['assertRendered(current, language: .hebrew)','XCTAssertTrue(fixture.presenter.presentedViewController === current)',
    'XCTAssertEqual(fixture.outcomes, ["confirm"])','try await until','throw NSError','await drainTransitions()',
    'XCTAssertEqual(completionCount, 1)','XCTAssertEqual(staleCallbackCount, 0)','Array(display.original.utf8)',
    'NotificationCenter.default.post(name: UIScene.didDisconnectNotification','direct finish callback simulation']:
   self.assertIn(value,text)
 def test_project_is_serial_hosted_unit_target_with_real_scene(self):
  project=(HERE/'project/project.pbxproj').read_text();scheme=(HERE/'project/P2Alert.xcscheme').read_text()
  for value in ['com.apple.product-type.bundle.unit-test','TEST_HOST = "$(BUILT_PRODUCTS_DIR)/P2AlertHost.app/P2AlertHost"','BUNDLE_LOADER = "$(TEST_HOST)"','SWIFT_VERSION = 5.0','PBXResourcesBuildPhase','Localizable.xcstrings']:
   self.assertIn(value,project)
  self.assertIn('parallelizable="NO"',scheme)
  plist=plistlib.loads((HERE/'project/Info.plist').read_bytes())
  self.assertIn('UIApplicationSceneManifest',plist);self.assertIn('UIWindowSceneDelegate',(HERE/'App.swift').read_text())
 def test_device_selection_is_explicit_and_fail_closed(self):
  inventory={'devices':{'com.apple.CoreSimulator.SimRuntime.iOS-26-4':[{'name':'iPhone 17','udid':'synthetic','isAvailable':True,'state':'Shutdown'}]}}
  self.assertEqual(lane.select_device(inventory)['udid'],'synthetic')
  for other in [{'devices':{}},{'devices':{'com.apple.CoreSimulator.SimRuntime.iOS-16-0':inventory['devices']['com.apple.CoreSimulator.SimRuntime.iOS-26-4']}}]:
   with self.assertRaises(ValueError):lane.select_device(other)
 def test_sealed_files_and_distinct_workflow(self):
  inputs=json.loads((HERE/'inputs.json').read_text())
  self.assertEqual(inputs['methods'],list(lane.METHODS));self.assertEqual(inputs['sourceCommit'],lane.SOURCE)
  for name,digest in inputs['builderFiles'].items():self.assertEqual(lane.sha(lane.ROOT/name),digest,name)
  workflow=(lane.ROOT/'.github/workflows/p2-alert-lifecycle.yml').read_text()
  self.assertIn(lane.sha(HERE/'inputs.json'),workflow)
  for value in ['branches: [validation/p2-alert-lifecycle]','cancel-in-progress: false','contents: read','if: always()','persist-credentials: false']:
   self.assertIn(value,workflow)
  self.assertNotIn('workflow_dispatch:',workflow)
 def test_preparation_preserves_exact_production_declarations(self):
  source=Path(os.environ.get('P2_ALERT_SOURCE',str(lane.ROOT.parents[1]/'p2-error-localization/source')))
  if not source.is_dir():self.fail('Pinned source required for extraction check; no skip permitted')
  with tempfile.TemporaryDirectory() as temporary:
   path=Path(temporary);lane.prepare(source,path/'fixture')
   identity=json.loads((path/'fixture/source-identity.json').read_text())
   sealed=json.loads((HERE/'inputs.json').read_text())
   for name,digest in identity['sourceFiles'].items():self.assertEqual(digest,sealed['sourceFiles'][name])
   generated=(path/'fixture/Sources/NativeAlertLanguageTests.swift').read_text()
   self.assertIn((source/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text(),generated)
   self.assertIn('@MainActor\nprivate final class V3NativeAlertPresentation',generated)
   self.assertEqual(lane.sha(path/'fixture/Sources/Localizable.xcstrings'),sealed['sourceFiles']['Resources/Localizable.xcstrings'])
 def test_observations_require_exact_association_counts_and_completeness(self):
  catalog={'strings':{key:{'localizations':{lang:{'stringUnit':{'value':key+'-'+lang}} for lang in ['en','he','zh-Hans','zh-Hant']}} for key in ['lc.common.error','lc.webView.installAppDesc']}}
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);manifest=[]
   for name,method in lane.OBSERVATIONS.items():
    value={'evidenceCase':name}
    if name.startswith('async-error-'):
     language=name.removeprefix('async-error-');value.update(deliveryCount=1,staleCallbackCount=0,title='lc.common.error-'+language,direction=4 if language=='he' else 3,message='Fixture % app • 日本語 • עברית SS-NATIVE-BANNER',original='Original Fixture % app • 日本語 • עברית')
    elif name=='confirmation-replaced':value.update(completionCount=1,resultIsNil=True)
    else:
     outcomes={'rapid-observer':['confirm'],'boundary-confirm':['confirm'],'boundary-cancel':['cancel'],'external-dismissal':[],'scene-notification':['unavailable']}[name]
     value.update(callbackCount=len(outcomes),outcomes=outcomes)
     if name=='rapid-observer':value.update(replacements=2,renderedTitle='lc.common.error-he',renderedBody='lc.webView.installAppDesc-he',direction=4,preservedText='user text: % @ : / • עברית • 日本語')
    (root/(name+'.json')).write_text(json.dumps(value))
    manifest.append({'testIdentifier':'NativeAlertLanguageTests/'+method+'()','attachments':[{'suggestedHumanReadableName':name+'_0_fixture.json','exportedFileName':name+'.json','isAssociatedWithFailure':False}]})
   def save(rows): (root/'manifest.json').write_text(json.dumps(rows))
   save(manifest);self.assertEqual(lane.verify_observations(root,catalog)['observationCount'],10)
   baseline={path.name:path.read_bytes() for path in root.glob('*.json')}
   for kind in ['missing','duplicate','wrong-association','failure','path-escape','wrong-count','float-direction-rapid','float-direction-async']:
    for name,data in baseline.items():(root/name).write_bytes(data)
    rows=copy.deepcopy(manifest)
    if kind=='missing':rows.pop()
    elif kind=='duplicate':rows.append(copy.deepcopy(rows[0]))
    elif kind=='wrong-association':rows[0]['testIdentifier']='NativeAlertLanguageTests/testOther()'
    elif kind=='failure':rows[0]['attachments'][0]['isAssociatedWithFailure']=True
    elif kind=='path-escape':rows[0]['attachments'][0]['exportedFileName']='../outside.json'
    elif kind=='wrong-count':
     path=root/'rapid-observer.json';value=json.loads(path.read_text());value['callbackCount']=2;path.write_text(json.dumps(value))
    elif kind.startswith('float-direction-'):
     path=root/('rapid-observer.json' if kind.endswith('rapid') else 'async-error-he.json');value=json.loads(path.read_text());value['direction']=4.0;path.write_text(json.dumps(value))
    save(rows)
    with self.subTest(kind=kind),self.assertRaises(ValueError):lane.verify_observations(root,catalog)
 def test_json_rejects_nonfinite_duplicate_and_malformed_values(self):
  for text in ['{"n":NaN}','{"n":Infinity}','{"n":-Infinity}','{"n":1e1000}','{"n":1e400}','{"n":-1e400}','{"a":1,"a":2}','{bad']:
   with self.subTest(text=text),self.assertRaises(ValueError):lane.strict_json(text)
if __name__=='__main__':unittest.main()
