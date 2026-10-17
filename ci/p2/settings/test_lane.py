import copy, importlib.util, json, os, plistlib, re, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p2_settings_lane',HERE/'run.py');lane=importlib.util.module_from_spec(spec);spec.loader.exec_module(lane)
class SettingsLaneTests(unittest.TestCase):
 def valid_results(self):
  return {'result':'Passed','passedTests':5,'failedTests':0,'skippedTests':0,'totalTestCount':5},{'testNodes':[{'nodeType':'Test Case','name':name+'()','nodeIdentifier':'SettingsInteractionTests/'+name+'()','result':'Passed'} for name in lane.METHODS]}
 def test_exact_five_native_cases_and_strict_counts(self):
  summary,tests=self.valid_results();self.assertEqual(lane.verify_results(summary,tests)['testsRun'],5)
  for field,value in [('result','Failed'),('passedTests',4),('passedTests',True),('passedTests',5.0),('failedTests',1),('skippedTests',1),('totalTestCount',6)]:
   altered=copy.deepcopy(summary);altered[field]=value
   with self.subTest(field=field,value=value),self.assertRaises(ValueError):lane.verify_results(altered,tests)
 def test_native_case_identity_does_not_admit_duplicates_or_skips(self):
  summary,tests=self.valid_results()
  for kind in ['missing','duplicate','extra','wrong-class','wrong-name','skipped','failed']:
   altered=copy.deepcopy(tests);rows=altered['testNodes']
   if kind=='missing':rows.pop()
   elif kind=='duplicate':rows[-1]=copy.deepcopy(rows[0])
   elif kind=='extra':rows.append(copy.deepcopy(rows[0]))
   elif kind=='wrong-class':rows[0]['nodeIdentifier']='OtherTests/'+lane.METHODS[0]+'()'
   elif kind=='wrong-name':rows[0]['name']='testOther()'
   else:rows[0]['result']=kind.title()
   with self.subTest(kind=kind),self.assertRaises(ValueError):lane.verify_results(summary,altered)
 def test_strict_json_rejects_duplicate_nonfinite_and_overflow(self):
  for value in ['{"n":NaN}','{"n":Infinity}','{"n":-Infinity}','{"n":1e400}','{"n":-1e400}','{"k":1,"k":2}','{bad']:
   with self.subTest(value=value),self.assertRaises(ValueError):lane.strict_json(value)
 def test_project_has_distinct_app_and_external_ui_test_targets(self):
  project=(HERE/'project/project.pbxproj').read_text();scheme=(HERE/'project/P2Settings.xcscheme').read_text()
  for value in ['com.apple.product-type.application','com.apple.product-type.bundle.ui-testing','TEST_TARGET_NAME = P2SettingsHost','ExpectedStrings.swift','SettingsInteractionTests.swift','Localizable.xcstrings','PBXResourcesBuildPhase']:
   self.assertIn(value,project)
  for value in ['BUNDLE_LOADER','TEST_HOST =','bundle.unit-test']:self.assertNotIn(value,project)
  self.assertIn('parallelizable="NO"',scheme)
  info=plistlib.loads((HERE/'project/Info.plist').read_bytes());self.assertIn('UIApplicationSceneManifest',info)
 def test_synthetic_expectation_generator_is_catalog_bound(self):
  catalog={'strings':{key:{'localizations':{language:{'stringUnit':{'value':language+':'+key}} for language in lane.LANGUAGES}} for key in lane.EXPECTED_KEYS}}
  value=lane.expected_strings(catalog,'a'*40)
  self.assertIn('static let builderCommit = "'+'a'*40+'"',value)
  for key in lane.EXPECTED_KEYS:
   for language in lane.LANGUAGES:self.assertIn(language+':'+key,value)
  self.assertIn('preconditionFailure',value)
  with self.assertRaises(ValueError):lane.expected_strings(catalog,'unknown')
  del catalog['strings']['Language']['localizations']['he']
  with self.assertRaises(KeyError):lane.expected_strings(catalog,'a'*40)
 def test_all_source_and_builder_seals_match(self):
  data=json.loads((HERE/'inputs.json').read_text());self.assertEqual(data['methods'],list(lane.METHODS))
  source=Path(os.environ.get('P2_SETTINGS_SOURCE',str(lane.ROOT.parent/'maintained-source')))
  for base,key in [(source,'sourceFiles'),(lane.ROOT,'builderFiles')]:
   for name,digest in data[key].items():self.assertEqual(lane.sha(base/name),digest,name)
  self.assertEqual(data['sourceCommit'],lane.SOURCE);self.assertEqual(data['sourceTree'],lane.TREE)
  self.assertIn('inheritedSourceManifestSHA256',data);self.assertNotIn('sourceManifestSHA256',data)
  workflow=(lane.ROOT/'.github/workflows/p2-settings-interaction.yml').read_text();self.assertIn(lane.sha(HERE/'inputs.json'),workflow)
  for value in ['branches: [validation/p2-alert-lifecycle]','cancel-in-progress: false','contents: read','if: always()','persist-credentials: false','ci/p2/settings/**']:self.assertIn(value,workflow)
 def test_preparation_preserves_exact_declarations_and_app_target_inputs(self):
  source=Path(os.environ.get('P2_SETTINGS_SOURCE',str(lane.ROOT.parent/'maintained-source')))
  with tempfile.TemporaryDirectory() as temporary:
   output=Path(temporary)/'fixture';lane.prepare(source,output)
   identity=json.loads((output/'source-identity.json').read_text());sealed=json.loads((HERE/'inputs.json').read_text())
   for path,digest in identity['sourceFiles'].items():self.assertEqual(digest,sealed['sourceFiles'][path],path)
   app=(output/'Sources/App.swift').read_text()
   for path in ['LiveContainerSwiftUI/Utilities/V3Localization.swift','LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift']:
    self.assertIn((source/path).read_text(),app)
   self.assertIn('@MainActor\nprivate final class V3NativeAlertPresentation',app)
   self.assertIn('public struct TextFieldAlertModifier',app);self.assertIn('class SceneDelegate:',app)
   self.assertNotIn('presentationController?.delegate =',app)
   self.assertFalse(identity['rootReferenceCompiled']);self.assertTrue(identity['fixtureOwnsRootCompositionAndWindowAttachment'])
   info=plistlib.loads((output/'P2Settings.xcodeproj/Info.plist').read_bytes())
   self.assertEqual(info['LCBuilderCommit'],identity['builderCommit'])
   self.assertIn(identity['builderCommit'],(output/'Sources/ExpectedStrings.swift').read_text())
   for path,digest in identity['generatedFiles'].items():self.assertEqual(lane.sha(output/path),digest,path)
 def test_native_failures_remain_independent_of_missing_observations(self):
  summary,tests=self.valid_results();summary['failedTests']=1
  with patch.object(lane,'verify_observations',side_effect=ValueError('missing observations')) as observations:
   result=lane.evaluate_native_evidence(summary,tests,Path('.'),{},'returncode65')
  observations.assert_called_once();self.assertEqual(result['status'],'failed');self.assertEqual(len(result['errors']),3)
  self.assertEqual(result['nativeCounts']['failedTests'],1)
 def valid_observations(self,directory):
  """Synthetic parser-only fixture, never native evidence or visual acceptance."""
  import struct, zlib, uuid
  catalog={'strings':{key:{'localizations':{language:{'stringUnit':{'value':language+':'+key}} for language in lane.LANGUAGES}} for key in lane.EXPECTED_KEYS}}
  commit='a'*40
  def text(key,language):return catalog['strings'][key]['localizations'][language]['stringUnit']['value']
  def message(language):return text('lc.webView.pageLaunch %@',language).replace('%@',lane.OPAQUE_NAME)
  viewport={'x':0,'y':0,'width':400,'height':800}
  frame={'x':10,'y':10,'width':90,'height':40}
  def tap(label,identifier=''):return {'identifier':identifier,'label':label,'hittable':True,'frame':dict(frame)}
  def picker(language,before):return {'picker':tap(text('Language',before),'settings.language'),'selection':tap(lane.NATIVE_NAMES[language]),'languageBefore':before}
  token=0
  def state(language='en',category='large',clipboard='<unmatched>',clipcount=0):
   nonlocal token
   token+=1
   value={'schema':'p2-settings-state-v1','processToken':str(uuid.UUID(int=token)),'sessionToken':str(uuid.UUID(int=100+token)),
    'language':language,'hostDefaultLanguage':language,'locale':language,'direction':'rtl' if language=='he' else 'ltr',
    'processArguments':['/synthetic/P2SettingsHost']+(['--largest'] if category=='accessibilityExtraExtraExtraLarge' else []),'languageEnvironment':{'LANG':'en_US.UTF-8'},
    'contentSizeCategory':category,'text':'','originalMessage':message(language),'confirmOutcome':'none','textOutcome':'none',
    'textResult':'nil','textPresented':False,'clipboardValue':clipboard,'clipboardChangeCount':clipcount}
   value.update({key:0 for key in lane.COUNTERS});return value
  def selected(value,language):return dict(value,language=language,hostDefaultLanguage=language,locale=language,direction='rtl' if language=='he' else 'ltr')
  reports={};manifest=[]
  def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
  png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',100,100,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*300)*100))+chunk(b'IEND',b'')
  for case,method in lane.CASES.items():
   category='accessibilityExtraExtraExtraLarge' if case=='settings-largest' else 'large'
   start=state(category=category);start['hostDefaultLanguage']='<missing>'
   current=selected(start,'he');bootstrap={'before':start,'after':copy.deepcopy(current),'pickerTap':picker('he','en')}
   rows=[];settings=case.startswith('settings-')
   for language in lane.LANGUAGES:
    if settings:
     current['text']=lane.SAFE_TEXT;before=copy.deepcopy(current);picks=picker(language,before['language']);current=selected(current,language)
     for phase in ('live','relaunched'):
      if phase=='relaunched':
       before=copy.deepcopy(current);current=state(language,category,current['clipboardValue'],current['clipboardChangeCount'])
      stem=case+'-'+language+'-'+phase
      first=dict(frame,x=150 if language=='he' else 10);last=dict(frame,x=10 if language=='he' else 150)
      rows.append({'language':language,'phase':phase,'verified':True,'before':copy.deepcopy(before),'after':copy.deepcopy(current),
       'expectedText':lane.SAFE_TEXT if phase=='live' else '', 'expectedMessage':message(language),'displayedMessage':message(language),
       'directionFirstFrame':first,'directionLastFrame':last,'directionViewport':dict(viewport),'pickerFrame':dict(frame),'messageFrame':dict(frame),
       'viewport':dict(viewport),'pickerLabel':text('Language',language),'pickerValue':lane.NATIVE_NAMES[language],'pickerTap':copy.deepcopy(picks),
       'terminationObserved':phase=='relaunched','launchArguments':['--largest'] if case=='settings-largest' else [],'launchEnvironment':{},
       'attachments':[stem+'-direction.png',stem+'-direction.txt',stem+'.png',stem+'.txt']})
    else:
     if case=='textfield-buttons':current['text']=lane.SAFE_TEXT
     pick_before=copy.deepcopy(current);current=selected(current,language);pick_after=copy.deepcopy(current);picks=picker(language,pick_before['language'])
     for action in (('copy','ok') if case=='error-buttons' else ('confirm','cancel')):
      before=copy.deepcopy(current);stem=case+'-'+language+'-'+action
      actions=[text('lc.common.ok',language),text('lc.common.copy' if case=='error-buttons' else 'lc.common.cancel',language)]
      key='lc.common.copy' if action=='copy' else ('lc.common.cancel' if action=='cancel' else 'lc.common.ok')
      trigger={'confirmation-buttons':'p2-open-confirmation','error-buttons':'p2-open-error','textfield-buttons':'p2-open-textfield'}[case]
      row={'language':language,'action':action,'verified':True,'before':before,
       'pickerBefore':copy.deepcopy(pick_before),'pickerAfter':copy.deepcopy(pick_after),'pickerTap':copy.deepcopy(picks),
       'displayedTitle':text('lc.common.error',language),'displayedActions':actions,'allButtonLabels':actions[:],
       'tapTarget':tap(text(key,language)),'triggerTap':tap('Fixture trigger',trigger),'viewport':dict(viewport),
       'alertDismissed':True,'drainSeconds':0.8,'noResurrection':True,
       'attachments':[stem+'-before.png',stem+'-before.txt',stem+'-after.png',stem+'-after.txt']}
      if case=='confirmation-buttons':
       current['confirmStarted']+=1;current['confirmCompleted']+=1;current['confirmOutcome']='true' if action=='confirm' else 'false'
       row.update(displayedMessage=text('lc.webView.installAppDesc',language),expectedResult=current['confirmOutcome'],actualResult=current['confirmOutcome'])
      elif case=='error-buttons':
       current['errorStarted']+=1
       if action=='copy':current['clipboardChangeCount']+=1;current['clipboardValue']=lane.RAW_ERROR+'\nError ID: SS-NATIVE-BANNER\nbuilder_commit='+commit
       error_id='\u2066SS-NATIVE-BANNER\u2069' if language=='he' else 'SS-NATIVE-BANNER'
       row.update(displayedMessage=text('Some details are unavailable in the selected language.',language)+'\n'+text('Error ID:',language)+' '+error_id,
        expectedResult='dismissed',actualResult='dismissed',expectedClipboard=current['clipboardValue'],clipboardDelta=1 if action=='copy' else 0)
      else:
       current['textStarted']+=1;current['textCompleted']+=1;current['textOutcome']=action
       entered=lane.SAFE_TEXT+' added';current['textResult']=entered if action=='confirm' else 'nil'
       row.update(displayedMessage='',expectedResult=current['textResult'],actualResult=current['textResult'],initialText=lane.SAFE_TEXT,
        enteredText=entered,outerTextAfter=lane.SAFE_TEXT,typedPayload=' added',inputTap=tap('Fixture value'))
      row['after']=copy.deepcopy(current);rows.append(row)
   data={'schema':'p2-settings-interaction-v1','evidenceCase':case,'method':method,'completed':True,'failures':[],
    'observations':rows,'lastStage':'complete','bootstrap':bootstrap,'finalState':copy.deepcopy(current)}
   reports[case]=data;group={'testIdentifier':'SettingsInteractionTests/'+method+'()','attachments':[]}
   for name in [case+'.json']+[name for row in rows for name in row['attachments']]:
    group['attachments'].append({'suggestedHumanReadableName':name,'exportedFileName':name,'isAssociatedWithFailure':False})
    (directory/name).write_bytes(png if name.endswith('.png') else (('Application: synthetic parser-only hierarchy. '+('x'*70)).encode() if name.endswith('.txt') else json.dumps(data).encode()))
   manifest.append(group)
  (directory/'manifest.json').write_text(json.dumps(manifest))
  return catalog,commit,reports,manifest
 def test_observations_require_all_forty_rows_and_exact_associated_evidence(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   result=lane.verify_observations(root,catalog,commit)
   self.assertEqual((result['observationCount'],result['screenshotCount'],result['hierarchyCount']),(40,80,80))
   self.assertEqual(set(result['cases']),set(lane.CASES))
 def test_observations_reject_missing_duplicate_types_and_tampered_semantics(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   # Every mutation starts from a fresh valid case, and the baseline is restored
   # before the next mutation so an earlier defect cannot mask a weak assertion.
   mutations=[
    ('settings-default',lambda d:d['observations'].pop()),
    ('settings-default',lambda d:d['observations'].__setitem__(1,copy.deepcopy(d['observations'][0]))),
    ('settings-default',lambda d:d.update(completed=False)),
    ('settings-default',lambda d:d.update(finalState={})),
    ('settings-default',lambda d:d['bootstrap']['pickerTap']['picker'].update(hittable=False)),
    ('settings-default',lambda d:d['observations'][0].update(launchArguments=['--language=he'])),
    ('settings-default',lambda d:d['observations'][1].update(launchEnvironment={'AppleLanguages':'he'})),
    ('settings-default',lambda d:d['observations'][1].update(terminationObserved=False)),
    ('settings-default',lambda d:d['observations'][1]['after'].update(processArguments=['/synthetic/P2SettingsHost','-AppleLanguages','he'])),
    ('settings-default',lambda d:d['observations'][1]['after'].update(processArguments=['/synthetic/P2SettingsHost','--language=he'])),
    ('settings-default',lambda d:d['observations'][1]['after'].update(processArguments=[])),
    ('settings-default',lambda d:d['observations'][1]['after'].update(languageEnvironment={'AppleLocale':'he'})),
    ('settings-default',lambda d:d['observations'][1]['after'].update(languageEnvironment={'SECRET_TOKEN':'forbidden'})),
    ('settings-default',lambda d:d['observations'][1]['after'].update(languageEnvironment={'LANG':'he_IL.UTF-8'})),
    ('settings-largest',lambda d:d['observations'][1]['after'].update(processArguments=['/synthetic/P2SettingsHost'])),
    ('settings-default',lambda d:d['observations'][1]['after'].update(processToken=d['observations'][1]['before']['processToken'])),
    ('settings-default',lambda d:d['observations'][0]['after'].update(hostDefaultLanguage='he')),
    ('settings-default',lambda d:d['observations'][0]['before'].update(originalMessage=lane.OPAQUE_NAME+' corrupt')),
    ('settings-default',lambda d:d['observations'][0].update(displayedMessage='English leak')),
    ('settings-default',lambda d:d['observations'][2]['directionFirstFrame'].update(x=0)),
    ('settings-largest',lambda d:d['observations'][0]['after'].update(contentSizeCategory='large')),
    ('settings-largest',lambda d:d['observations'][0]['directionViewport'].update(width=float('inf'))),
    ('settings-largest',lambda d:d['observations'][0]['pickerFrame'].update(x=500)),
    ('confirmation-buttons',lambda d:d['observations'][0]['after'].update(confirmCompleted=True)),
    ('confirmation-buttons',lambda d:d['observations'][0]['after'].update(confirmCompleted=2)),
    ('confirmation-buttons',lambda d:d['observations'][0]['tapTarget'].update(label='Wrong')),
    ('confirmation-buttons',lambda d:d['observations'][0]['triggerTap'].update(identifier='wrong')),
    ('confirmation-buttons',lambda d:d['observations'][0].update(noResurrection=False)),
    ('confirmation-buttons',lambda d:d['observations'][0].update(drainSeconds=0.2)),
    ('confirmation-buttons',lambda d:d['observations'][0]['pickerAfter'].update(sessionToken='00000000-0000-0000-0000-000000000099')),
    ('confirmation-buttons',lambda d:d['observations'][1]['pickerTap']['selection'].update(label='wrong')),
    ('error-buttons',lambda d:d['observations'][0]['after'].update(clipboardValue='translated diagnostic')),
    ('error-buttons',lambda d:d['observations'][0].update(clipboardDelta=1.0)),
    ('error-buttons',lambda d:d['observations'][1]['after'].update(clipboardChangeCount=2)),
    ('error-buttons',lambda d:d['observations'][0].update(allButtonLabels=['Unexpected'])),
    ('textfield-buttons',lambda d:d['observations'][0].update(enteredText='missing marker')),
    ('textfield-buttons',lambda d:(d['observations'][0].update(enteredText=' added',expectedResult=' added',actualResult=' added'),d['observations'][0]['after'].update(textResult=' added'))),
    ('textfield-buttons',lambda d:(d['observations'][0].update(enteredText='Corrupted original added',expectedResult='Corrupted original added',actualResult='Corrupted original added'),d['observations'][0]['after'].update(textResult='Corrupted original added'))),
    ('textfield-buttons',lambda d:d['observations'][0].update(typedPayload='other')),
    ('textfield-buttons',lambda d:d['observations'][0]['inputTap'].update(hittable=False)),
    ('textfield-buttons',lambda d:d['observations'][0]['after'].update(textPresented=True)),
    ('textfield-buttons',lambda d:d['observations'][1].update(actualResult=lane.SAFE_TEXT)),
    ('textfield-buttons',lambda d:d['observations'][0].update(attachments=[])),
   ]
   for index,(case,mutate) in enumerate(mutations):
    data=copy.deepcopy(reports[case]);mutate(data);path=root/(case+'.json');path.write_text(json.dumps(data))
    with self.subTest(index=index,case=case),self.assertRaises((ValueError,KeyError,TypeError)):lane.verify_observations(root,catalog,commit)
    path.write_text(json.dumps(reports[case]));self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_actual_text_insertion_position_is_flexible_without_losing_original_bytes(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   for position in (0,5,len(lane.SAFE_TEXT)):
    report=copy.deepcopy(reports['textfield-buttons']);entered=lane.SAFE_TEXT[:position]+' added'+lane.SAFE_TEXT[position:]
    report['observations'][0].update(enteredText=entered,expectedResult=entered,actualResult=entered)
    report['observations'][0]['after']['textResult']=entered
    report['observations'][1]['before']['textResult']=entered
    (root/'textfield-buttons.json').write_text(json.dumps(report))
    with self.subTest(position=position):self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_attachments_fail_closed_on_duplicates_association_and_extra_reports(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   def write(value):(root/'manifest.json').write_text(json.dumps(value))
   for mode in ('missing','wrong-case','failure','path-escape','duplicate-export','duplicate-name','extra-report','invalid-png','invalid-ax'):
    changed=copy.deepcopy(manifest);restore=None
    if mode=='missing':changed[0]['attachments'].pop()
    elif mode=='wrong-case':changed[0]['testIdentifier']='SettingsInteractionTests/testSettingsLargest()'
    elif mode=='failure':changed[0]['attachments'][1]['isAssociatedWithFailure']=True
    elif mode=='path-escape':changed[0]['attachments'][1]['exportedFileName']='../missing.png'
    elif mode=='duplicate-export':changed[0]['attachments'].append(dict(changed[0]['attachments'][1],suggestedHumanReadableName='other.png'))
    elif mode in ('duplicate-name','extra-report'):
     name='additional.json';(root/name).write_text(json.dumps(reports['settings-default']))
     changed[0]['attachments'].append({'suggestedHumanReadableName':'settings-default.json' if mode=='duplicate-name' else name,'exportedFileName':name,'isAssociatedWithFailure':False})
    else:
     path=root/changed[0]['attachments'][1 if mode=='invalid-png' else 2]['exportedFileName'];restore=(path,path.read_bytes());path.write_bytes(b'broken')
    write(changed)
    with self.subTest(mode=mode),self.assertRaises(ValueError):lane.verify_observations(root,catalog,commit)
    if restore:restore[0].write_bytes(restore[1])
    write(manifest);self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_actual_tap_fixture_does_not_dispatch_hidden_actions_or_override_language(self):
  app=(HERE/'App.swift.in').read_text();tests=(HERE/'SettingsInteractionTests.swift.in').read_text()
  for value in ['V3LanguageSettingsSection()','V3Localization.hostDefaults','presenter.presentConfirmation(','.showError(.raw(SettingsFixtureModel.rawError))','.textFieldAlert(isPresented: $model.textPresented','suffix.count == 40','"processArguments": process.arguments','process.environment[key].map']:
   self.assertIn(value,app)
  for value in ['option.tap()','target.tap()','input.typeText(typedPayload)','app.terminate()','self.app.state == .notRunning','lastPickerTap','Array(string(after, "textResult").utf8)','Thread.sleep(forTimeInterval: 0.8)','expected("Language", currentLanguage)']:
   self.assertIn(value,tests)
  for value in ['V3AppLanguage.persist(','.finish(','.accessibilityActivate(','.press(forDuration:','launchEnvironment =','removePersistentDomain','UIPasteboard.general.string =']:
   self.assertNotIn(value,app+tests)
  self.assertNotRegex(app+tests,r'(?:AppleLanguages|AppleLocale)"\]\s*=(?!=)')

if __name__=='__main__':unittest.main()
