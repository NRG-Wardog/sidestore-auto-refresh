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
   self.assertIn((HERE/'ClipboardProbeApp.swift.in').read_text(),app)
   self.assertTrue((output/'Sources/SettingsInteractionTests.swift').read_text().startswith((HERE/'ClipboardProbeRunner.swift.in').read_text()+'\n\n'))
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
   value={'schema':'p2-settings-state-v1','processToken':str(uuid.UUID(int=token)).upper(),'sessionToken':str(uuid.UUID(int=100+token)).upper(),
    'language':language,'hostDefaultLanguage':language,'locale':language,'direction':'rtl' if language=='he' else 'ltr',
    'processIdentifier':100+token,'processArguments':['/synthetic/P2SettingsHost']+(['--largest'] if category=='accessibilityExtraExtraExtraLarge' else []),'languageEnvironment':{'LANG':'en_US.UTF-8'},
    'contentSizeCategory':category,'text':'','originalMessage':message(language),'confirmOutcome':'none','textOutcome':'none',
    'textResult':'nil','textPresented':False,'clipboardValue':clipboard,'clipboardChangeCount':clipcount,'clipboardProbeReady':True}
   value.update({key:0 for key in lane.COUNTERS});return value
  def selected(value,language):return dict(value,language=language,hostDefaultLanguage=language,locale=language,direction='rtl' if language=='he' else 'ltr')
  reports={};manifest=[]
  def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
  png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',100,100,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*300)*100))+chunk(b'IEND',b'')
  for case,method in lane.CASES.items():
   category='accessibilityExtraExtraExtraLarge' if case=='settings-largest' else 'large'
   start=state(category=category);start['hostDefaultLanguage']='<missing>'
   current=selected(start,'he');bootstrap={'before':start,'after':copy.deepcopy(current),'pickerTap':picker('he','en')}
   rows=[];inputs=[];settings=case.startswith('settings-');clock=0.0
   probe_requests=[];probe_acks=[];trace_rows=[];app_clock=1000.0;turn=0
   def trace_event(kind,request=0,advance=0.1):
    nonlocal app_clock,turn
    app_clock+=advance;turn+=1
    trace_rows.append({'schema':'p2-app-clipboard-trace-v1','sequence':len(trace_rows)+1,
     'processToken':current['processToken'],'sessionToken':current['sessionToken'],'nonce':current['sessionToken'],
     'processIdentifier':current['processIdentifier'],'uptime':app_clock,'runLoopTurn':turn,
     'kind':kind,'requestSequence':request,'count':current['clipboardChangeCount'],'failure':''})
   trace_event('initial')
   def probe(stage):
    nonlocal clock
    clock+=0.02;request_time=clock;clock+=0.08;ack_time=clock;clock+=0.01
    request_id=len(probe_requests)+1
    request={'nonce':current['sessionToken'],'requestSequence':request_id,'stage':stage,
     'runnerProcessIdentifier':2,'requestUptime':request_time,'ackUptime':ack_time,'returnUptime':clock}
    probe_requests.append(request)
    probe_acks.append({'nonce':current['sessionToken'],'requestSequence':request_id,'runnerUptime':ack_time,'runnerProcessIdentifier':2})
    trace_event('request',request_id);trace_event('periodic',request_id);trace_event('acknowledged-sample',request_id)
    return copy.deepcopy(request)

   def receipt(value):
    nonlocal clock
    clock+=0.01
    return {'source':'XCTest-runner UIPasteboard.general.changeCount','processIdentifier':2,'count':value,'uptime':clock}
   def entered_input(current):
    before=copy.deepcopy(current);first=receipt(current['clipboardChangeCount'])
    current['text']=lane.SAFE_TEXT;current['clipboardChangeCount']+=3
    last=receipt(current['clipboardChangeCount'])
    inputs.append({'before':before,'after':copy.deepcopy(current),'typingBefore':first,'typingAfter':last,'typedPayload':lane.SAFE_TEXT})
   for language in lane.LANGUAGES:
    if settings:
     entered_input(current);before=copy.deepcopy(current);picks=picker(language,before['language']);current=selected(current,language)
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
     if case=='textfield-buttons' and language=='en':entered_input(current)
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
       row.update(displayedMessage='\n'.join([text('Some details are unavailable in the selected language.',language)]*2)+'\n'+text('Error ID:',language)+' '+error_id,
        expectedResult='dismissed',actualResult='dismissed',expectedClipboard=current['clipboardValue'],clipboardDelta=1 if action=='copy' else 0)
      else:
       typing_before=receipt(0);p0=probe(language+'-'+action+'-typing-before');current['clipboardChangeCount']+=2
       typing_after=receipt(0);p1=probe(language+'-'+action+'-typing-after');action_before=receipt(0);p2=probe(language+'-'+action+'-action-before')
       clock+=0.01;tap_time=clock;tap_returned=receipt(0);p3=probe(language+'-'+action+'-tap-returned')
       clock+=0.81
       for _ in range(9):trace_event('periodic')
       action_after=receipt(0);p4=probe(language+'-'+action+'-action-after')
       current['textStarted']+=1;current['textCompleted']+=1;current['textOutcome']=action
       entered=lane.SAFE_TEXT+' added';current['textResult']=entered if action=='confirm' else 'nil'
       row.update(displayedMessage='',expectedResult=current['textResult'],actualResult=current['textResult'],initialText=lane.SAFE_TEXT,
        enteredText=entered,outerTextAfter=lane.SAFE_TEXT,typedPayload=' added',inputTap=tap('Fixture value'),
        typingBefore=typing_before,typingAfter=typing_after,actionBefore=action_before,tapReturned=tap_returned,actionAfter=action_after,probeTypingBefore=p0,probeTypingAfter=p1,probeActionBefore=p2,probeTapReturned=p3,probeActionAfter=p4,actionTapUptime=tap_time)
      row['messageSamples']=[{'sequence':1,'uptime':float(len(rows)+1),'samplingStart':float(len(rows)+1)-0.01,'deadline':float(len(rows)+1)-0.01+10,'alertCount':1,'labels':[row['displayedTitle'],row['displayedMessage']]}] if row['displayedMessage'] else []
      row['after']=copy.deepcopy(current);rows.append(row)
   snapshots=[];sequences={};identities={}
   expected_states=[('initial',start),('bootstrap-he',bootstrap['after'])]
   expected_states += [(('selected-' if row['phase']=='live' else 'relaunched-')+row['language'],row['after']) for row in rows] if settings else [('selected-'+row['language'],row['pickerAfter']) for row in rows[::2]]
   for stage,value in expected_states:
    process=value['processToken'];sequences[process]=sequences.get(process,0)+1
    if process not in identities:identities[process]=(str(uuid.uuid4()),str(uuid.uuid4()))
    window,host=identities[process];layer_id=str(uuid.uuid4())
    view={'identity':window,'parent':'','class':'UIWindow','layerIdentity':layer_id,'frame':dict(viewport),'bounds':dict(viewport),
     'transform':[1,0,0,1,0,0],'semantic':0,'effectiveDirection':1 if value['language']=='he' else 0,'hidden':False,'alpha':1.0,'identifier':''}
    identity_matrix=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
    layer={'identity':layer_id,'parent':'','class':'CALayer','transform':identity_matrix[:],'sublayerTransform':identity_matrix[:],
     'bounds':dict(viewport),'position':[0,0],'anchorPoint':[0.5,0.5],'hidden':False,'geometryFlipped':False,'opacity':1.0}
    snapshot=dict(schema='p2-passive-layout-v1' ,source='app-process public UIKit read-only traversal',complete=True,sequence=sequences[process],
     windowIdentity=window,hostIdentity=host,views=[view],layers=[layer],**{key:value[key] for key in ('processToken','sessionToken','language','locale','direction')})
    snapshots.append({'stage':stage,'state':copy.deepcopy(value),'snapshot':snapshot,'outsideFirstFrame':dict(frame),'outsideLastFrame':dict(frame,x=150),'viewport':dict(viewport)})
   progress_keys=('language','action','before','typingBefore','typingAfter','actionBefore','tapReturned','actionAfter','probeTypingBefore','probeTypingAfter','probeActionBefore','probeTapReturned','probeActionAfter','actionTapUptime','after')
   progress=[{key:copy.deepcopy(row[key]) for key in progress_keys} for row in rows] if case=='textfield-buttons' else []
   data={'schema':'p2-settings-interaction-v1' ,'evidenceCase':case,'method':method,'completed':True,'failures':[],
    'clipboardRequests':probe_requests,'clipboardAcknowledgements':probe_acks,'clipboardFailures':[],
    'observations':rows,'modalProgress':progress,'messageProgress':[row['messageSamples'] for row in rows if row.get('messageSamples')],'inputReceipts':inputs,'layoutSnapshots':snapshots,'lastStage':'complete','bootstrap':bootstrap,'finalState':copy.deepcopy(current)}
   trace_directory=directory/'clipboard-traces';trace_directory.mkdir(exist_ok=True)
   states=[start]+[state for row in rows for state in (row['before'],row['after'])]
   for value in states:
    file=trace_directory/(value['sessionToken'].upper()+'.jsonl')
    if file.exists():continue
    initial={'schema':'p2-app-clipboard-trace-v1','sequence':1,'processToken':value['processToken'],
     'sessionToken':value['sessionToken'].upper(),'nonce':value['sessionToken'].upper(),'processIdentifier':value['processIdentifier'],
     'uptime':1000.0,'runLoopTurn':0,'kind':'initial','requestSequence':0,'count':value['clipboardChangeCount'],'failure':''}
    file.write_text(json.dumps(initial)+'\n')
   if case=='textfield-buttons':
    (trace_directory/(current['sessionToken']+'.jsonl')).write_text(''.join(json.dumps(x)+'\n' for x in trace_rows))
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
 def test_typing_receipts_reject_gaps_writes_and_forged_action_boundaries(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   mutations=[
    ('settings-default',lambda d:d.pop('inputReceipts')),
    ('settings-default',lambda d:d['inputReceipts'].pop()),
    ('settings-default',lambda d:d['inputReceipts'][0]['before'].update(clipboardChangeCount=99)),
    ('settings-default',lambda d:d['inputReceipts'][0]['typingAfter'].update(count=-1)),
    ('settings-default',lambda d:d['observations'][0]['after'].update(clipboardChangeCount=d['observations'][0]['after']['clipboardChangeCount']+1)),
    ('textfield-buttons',lambda d:d['observations'][0]['actionBefore'].update(count=99)),
    ('textfield-buttons',lambda d:d['observations'][0]['tapReturned'].update(count=99)),
    ('textfield-buttons',lambda d:d['observations'][0]['actionAfter'].update(count=d['observations'][0]['actionAfter']['count']+1)),
    ('textfield-buttons',lambda d:d['observations'][0]['after'].update(clipboardChangeCount=d['observations'][0]['after']['clipboardChangeCount']+1)),
    ('textfield-buttons',lambda d:d['observations'][0]['typingAfter'].update(count=-1)),
    ('textfield-buttons',lambda d:d['observations'][0]['typingBefore'].update(source='app count inferred')),
    ('textfield-buttons',lambda d:d['observations'][0]['actionBefore'].update(uptime=0)),
    ('textfield-buttons',lambda d:d['observations'][0]['actionAfter'].update(count=True)),
    ('textfield-buttons',lambda d:d['observations'][0]['typingBefore'].update(processIdentifier=d['observations'][0]['before']['processIdentifier'])),
   ]
   for index,(case,mutate) in enumerate(mutations):
    data=copy.deepcopy(reports[case]);mutate(data);path=root/(case+'.json');path.write_text(json.dumps(data))
    with self.subTest(index=index),self.assertRaises((ValueError,KeyError,TypeError)):lane.verify_observations(root,catalog,commit)
    path.write_text(json.dumps(reports[case]));self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_driver_counts_are_diagnostic_without_relaxing_same_app_generations(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   for case in ('settings-default','settings-largest','textfield-buttons'):
    report=copy.deepcopy(reports[case])
    for item in report['inputReceipts']:item['typingBefore']['count']=70;item['typingAfter']['count']=73
    (root/(case+'.json')).write_text(json.dumps(report))
   self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
   for case,key in [('settings-default','after'),('textfield-buttons','after')]:
    report=json.loads((root/(case+'.json')).read_text());field='clipboardChangeCount' if key=='after' else 'count'
    report['observations'][0][key][field]+=1;(root/(case+'.json')).write_text(json.dumps(report))
    with self.subTest(case=case),self.assertRaises(ValueError):lane.verify_observations(root,catalog,commit)
    (root/(case+'.json')).write_text(json.dumps(reports[case]));self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_passive_layout_requires_complete_ordered_identity_bound_snapshots(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   mutations=[lambda d:d.pop('layoutSnapshots'),lambda d:d['layoutSnapshots'].pop(),
    lambda d:d['layoutSnapshots'][1].update(stage='initial'),
    lambda d:d['layoutSnapshots'][1]['snapshot'].update(complete=False),
    lambda d:d['layoutSnapshots'][1]['snapshot'].update(sequence=1),
    lambda d:d['layoutSnapshots'][1]['snapshot'].update(hostIdentity='00000000-0000-0000-0000-000000000099'),
    lambda d:d['layoutSnapshots'][1]['snapshot'].update(language='en'),
    lambda d:d['layoutSnapshots'][1]['snapshot']['views'][0].update(transform=[1,0,0,float('inf'),0,0]),
    lambda d:d['layoutSnapshots'][1]['snapshot']['views'][0].update(effectiveDirection=1.0),
    lambda d:d['layoutSnapshots'][1]['snapshot']['layers'][0].update(transform=[1,0]),
    lambda d:d['layoutSnapshots'][1]['snapshot']['layers'][0].update(sublayerTransform=[float('nan')]*16),
    lambda d:d['layoutSnapshots'][1]['snapshot']['layers'][0].update(presentationTransform=[1]*16),
    lambda d:d['layoutSnapshots'][1]['snapshot']['views'][0].update(layerIdentity='00000000-0000-0000-0000-000000000099'),
    lambda d:d['layoutSnapshots'][1]['snapshot']['views'].append(copy.deepcopy(d['layoutSnapshots'][1]['snapshot']['views'][0])),
    lambda d:d['observations'][0]['directionFirstFrame'].update(x=200),
   ]
   for index,mutate in enumerate(mutations):
    data=copy.deepcopy(reports['settings-default']);mutate(data);path=root/'settings-default.json';path.write_text(json.dumps(data))
    with self.subTest(index=index),self.assertRaises((ValueError,KeyError,TypeError)):lane.verify_observations(root,catalog,commit)
    path.write_text(json.dumps(reports['settings-default']));self.assertEqual(lane.verify_observations(root,catalog,commit)['status'],'passed')
 def test_two_unknown_error_lines_require_two_exact_fallbacks(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   report=copy.deepcopy(reports['error-buttons']);row=report['observations'][0]
   row['displayedMessage']=row['displayedMessage'].split('\n',1)[1]
   (root/'error-buttons.json').write_text(json.dumps(report))
   with self.assertRaises(ValueError):lane.verify_observations(root,catalog,commit)
 def test_actual_text_insertion_position_is_flexible_without_losing_original_bytes(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   for position in (0,5,len(lane.SAFE_TEXT)):
    report=copy.deepcopy(reports['textfield-buttons']);entered=lane.SAFE_TEXT[:position]+' added'+lane.SAFE_TEXT[position:]
    report['observations'][0].update(enteredText=entered,expectedResult=entered,actualResult=entered)
    report['observations'][0]['after']['textResult']=entered
    report['observations'][1]['before']['textResult']=entered
    report['modalProgress'][0]['after']['textResult']=entered
    report['modalProgress'][1]['before']['textResult']=entered
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
  self.assertIn(r'alert.staticTexts.allElementsBoundByIndex.map(\.label)',tests)
  self.assertNotIn('alert.staticTexts[message]',tests)
  self.assertLess(tests.index('inputReceipts[receiptIndex]["after"] = after'),tests.index('"App clipboard counter regressed during outer typing"'))
  self.assertLess(tests.index('modalProgress[progressIndex]["probeActionBefore"] = probeActionBefore'),tests.index('let actionTapUptime'))
  for value in ['option.tap()','target.tap()','input.typeText(typedPayload)','app.terminate()','self.app.state == .notRunning','lastPickerTap','Array(string(after, "textResult").utf8)','Thread.sleep(forTimeInterval: 0.8)','expected("Language", currentLanguage)']:
   self.assertIn(value,tests)
  for value in ['view.transform =','semanticContentAttribute =','setNeedsLayout(','.id(', 'V3AppLanguage.persist(','.finish(','.accessibilityActivate(','.press(forDuration:','launchEnvironment =','removePersistentDomain','UIPasteboard.general.string =']:
   self.assertNotIn(value,app+tests)
  self.assertNotRegex(app+tests,r'(?:AppleLanguages|AppleLocale)"\]\s*=(?!=)')


 def test_exact_alert_label_enumeration_requires_unique_utf8_observation(self):
  import copy
  expected='first paragraph\nsecond paragraph\nError ID: SS-NATIVE-BANNER';title='Error'
  samples=[{'sequence':1,'uptime':1.0,'samplingStart':1.0,'deadline':11.0,'alertCount':1,'labels':[title]},
   {'sequence':2,'uptime':1.1,'samplingStart':1.0,'deadline':11.0,'alertCount':1,'labels':[title,expected]}]
  self.assertEqual(lane.validate_message_samples(samples,expected,title),samples)
  mutations=[lambda x:x[-1]['labels'].append(expected),lambda x:x[-1]['labels'].remove(expected),
   lambda x:x[-1].update(labels=[title,expected.replace('second paragraph','changed paragraph')]),
   lambda x:x[-1].update(labels=[title,expected.replace('SS-NATIVE-BANNER','SS-OTHER')]),
   lambda x:x[-1].update(labels=[title,expected.replace('first paragraph\n','')]),
   lambda x:x[-1].update(uptime=float('inf')),lambda x:x[-1].update(sequence=1),
   lambda x:x[-1].update(alertCount=2),lambda x:x[0]['labels'].append(expected),
   lambda x:x[-1].update(uptime=600),lambda x:x[-1].update(uptime=11.00001),
   lambda x:x[-1].update(samplingStart=0),lambda x:x[-1].update(deadline=600),
   lambda x:x[0].update(samplingStart=float('nan')),lambda x:x[0].pop('deadline')]
  for mutate in mutations:
   bad=copy.deepcopy(samples);mutate(bad)
   with self.assertRaises((ValueError,KeyError)):lane.validate_message_samples(bad,expected,title)
  with self.assertRaises(ValueError):lane.validate_message_samples([],expected,title)
  with self.assertRaises(ValueError):lane.validate_message_samples([{'sequence':1,'uptime':1,'samplingStart':1.0,'deadline':11.0,'alertCount':1,'labels':[title,'e\u0301']}],'é',title)
  template=(HERE/'SettingsInteractionTests.swift.in').read_text()
  self.assertIn(r'alert.staticTexts.allElementsBoundByIndex.map(\.label)',template)
  self.assertIn('Array($0.utf8) == Array(message.utf8)',template)
  self.assertLess(template.index('now <= deadline'),template.index('if matches.count == 1'))
  bad=copy.deepcopy(samples);bad[-1]['uptime']=11.0
  lane.validate_message_samples(bad,expected,title)
  self.assertNotIn('NSPredicate(format: "label == %@", message)',template)


 def test_causal_clipboard_receipts_reject_missing_stale_and_pretap_acknowledgements(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   traces=lane.CLIPBOARD.read(root/'clipboard-traces',lane.strict_json);original=reports['textfield-buttons']
   lane.CLIPBOARD.verify_case(original,original['observations'],traces)
   mutations=[lambda d:d['clipboardRequests'].pop(),lambda d:d['clipboardAcknowledgements'].pop(),
    lambda d:d['clipboardAcknowledgements'][1].update(requestSequence=1),
    lambda d:d['clipboardAcknowledgements'][0].update(nonce='wrong'),
    lambda d:d['clipboardAcknowledgements'][0].update(runnerProcessIdentifier=2.0),
    lambda d:d['clipboardRequests'][0].update(returnUptime=float('inf')),
    lambda d:d['clipboardRequests'][2].update(ackUptime=100),
    lambda d:d['observations'][0].update(actionTapUptime=0),
    lambda d:d['observations'][0].update(probeActionBefore=d['observations'][0]['probeTypingAfter']),
    lambda d:d['clipboardFailures'].append('native-unavailable')]
   for index,mutate in enumerate(mutations):
    value=copy.deepcopy(original);mutate(value)
    with self.subTest(index=index),self.assertRaises((ValueError,KeyError,TypeError)):
     lane.CLIPBOARD.verify_case(value,value['observations'],traces)
    lane.CLIPBOARD.verify_case(original,original['observations'],traces)
   # Runner counters may differ, but the actual app-generation trace still decides.
   value=copy.deepcopy(original)
   for row in value['observations']:
    for key in ('typingBefore','typingAfter','actionBefore','tapReturned','actionAfter'):row[key]['count']=987
   lane.CLIPBOARD.verify_case(value,value['observations'],traces)

 def test_same_app_trace_rejects_writes_gaps_and_forged_baselines(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   original=lane.CLIPBOARD.read(root/'clipboard-traces',lane.strict_json);data=reports['textfield-buttons'];nonce=data['observations'][0]['before']['sessionToken']
   def check(traces):lane.CLIPBOARD.verify_case(data,data['observations'],traces)
   mutations=[lambda t:t[nonce]['acks'][1].update(count=999),
    lambda t:t[nonce]['acks'][3].update(count=t[nonce]['acks'][3]['count']+1),
    lambda t:t[nonce]['acks'][4].update(count=t[nonce]['acks'][4]['count']+1),
    lambda t:t[nonce]['acks'][5].update(count=t[nonce]['acks'][5]['count']+1),
    lambda t:t[nonce]['acks'][3].update(processIdentifier=99999),
    lambda t:t[nonce]['acks'].pop(3)]
   for index,mutate in enumerate(mutations):
    traces=copy.deepcopy(original);mutate(traces)
    with self.subTest(index=index),self.assertRaises((ValueError,KeyError,TypeError)):check(traces)
    check(original)
   traces=copy.deepcopy(original);start=traces[nonce]['acks'][2]['sequence']
   # A write hidden between acknowledgement endpoints is still rejected.
   traces[nonce]['rows'][start]['count']+=1
   with self.assertRaises(ValueError):check(traces)
   traces=copy.deepcopy(original)
   for row in traces[nonce]['rows'][start:]:row['uptime']+=1
   with self.assertRaises(ValueError):check(traces)

 def test_app_trace_parser_and_export_are_bounded_and_fail_closed(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);catalog,commit,reports,manifest=self.valid_observations(root)
   folder=root/'clipboard-traces';nonce=reports['textfield-buttons']['observations'][0]['before']['sessionToken'];path=folder/(nonce+'.jsonl');raw=path.read_bytes()
   rows=[json.loads(x) for x in raw.splitlines()]
   mutations=[lambda x:x.pop(2),lambda x:x[1].update(sequence=1),lambda x:x[1].update(nonce='wrong'),
    lambda x:x[1].update(processIdentifier=123),lambda x:x[1].update(uptime=float('inf')),
    lambda x:x[2].update(requestSequence=64),lambda x:x[3].update(runLoopTurn=x[1]['runLoopTurn']),
    lambda x:x[3].update(failure='trace-write-failed')]
   for index,mutate in enumerate(mutations):
    value=copy.deepcopy(rows);mutate(value);path.write_text(''.join(json.dumps(x)+'\n' for x in value))
    with self.subTest(index=index),self.assertRaises((ValueError,KeyError,TypeError)):lane.CLIPBOARD.read(folder,lane.strict_json)
    path.write_bytes(raw);lane.CLIPBOARD.read(folder,lane.strict_json)
   path.write_bytes(raw[:-1])
   with self.assertRaises(ValueError):lane.CLIPBOARD.read(folder,lane.strict_json)
   path.write_bytes(raw)
   container=root/'synthetic-container';source=container/'Library/Caches/P2ClipboardTrace';source.mkdir(parents=True)
   (source/path.name).write_bytes(raw)
   inventory=lane.CLIPBOARD.retain(container,root/'retained');self.assertEqual(set(inventory),{path.name})
   (source/'unexpected.txt').write_text('not a trace')
   with self.assertRaises(ValueError):lane.CLIPBOARD.retain(container,root/'rejected')
   (source/'unexpected.txt').unlink();(source/'linked.jsonl').symlink_to(path)
   with self.assertRaises(ValueError):lane.CLIPBOARD.retain(container,root/'rejected-link')

 def test_trace_recovery_survives_missing_bundle_and_every_xcresult_export_failure(self):
  for mode in ('missing','summary','tests','attachments'):
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);calls=[]
    if mode!='missing':(root/'settings.xcresult').mkdir()
    def command(*argv,**kwargs):
     calls.append(argv)
     if mode in argv:raise ValueError('synthetic '+mode+' failure')
     return json.dumps({})
    with patch.object(lane,'recover_clipboard_traces',return_value=None) as recover:
     result=lane.collect_native_evidence(command,root/'app',{'udid':'owned-device'},root,{},'original native failure')
    recover.assert_called_once()
    self.assertEqual(result['status'],'failed')
    self.assertTrue(any('original native failure' in error for error in result['errors']))
    self.assertTrue(any(('No XCTest' if mode=='missing' else 'export') in error for error in result['errors']))
    if mode!='missing':self.assertEqual(len(calls),3)

 def test_trace_recovery_retains_unexpected_termination_failure_and_strict_absent_process(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);app=root/'app';app.mkdir();(app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.sidestore.fixture.p2settings'}))
   container=root/'container';source=container/'Library/Caches/P2ClipboardTrace';source.mkdir(parents=True)
   name='00000000-0000-4000-8000-000000000001.jsonl';(source/name).write_bytes(b'{"synthetic":"trace"}\n')
   evidence=root/'evidence';evidence.mkdir();calls=[]
   def command(*argv,**kwargs):
    calls.append((argv,kwargs))
    if 'terminate' in argv:raise ValueError('unexpected termination failure')
    return str(container)
   with patch.object(lane.time,'sleep'):
    error=lane.recover_clipboard_traces(command,app,{'udid':'owned-device'},evidence)
   self.assertIn('unexpected termination failure',error)
   self.assertEqual((evidence/'clipboard-traces'/name).read_bytes(),(source/name).read_bytes())
   self.assertEqual(calls[1][0],('xcrun','simctl','terminate','owned-device','org.sidestore.fixture.p2settings'))
   self.assertEqual(calls[1][1],{'allow_missing_process':True})
  argv=['xcrun','simctl','terminate','owned-device','org.sidestore.fixture.p2settings']
  message='An error was encountered processing the command (domain=NSPOSIXErrorDomain, code=3): found nothing to terminate'
  self.assertTrue(lane.absent_synthetic_process(argv,3,message))
  for args,code,text in [(argv,1,message),(argv,3,'unknown failure'),(argv[:-1]+['another.app'],3,message),(['xcrun','simctl','shutdown','owned-device','org.sidestore.fixture.p2settings'],3,message)]:
   self.assertFalse(lane.absent_synthetic_process(args,code,text))

 def test_fixture_handshake_preserves_real_tap_and_launch_contract(self):
  app=(HERE/'ClipboardProbeApp.swift.in').read_text();runner=(HERE/'ClipboardProbeRunner.swift.in').read_text();tests=(HERE/'SettingsInteractionTests.swift.in').read_text()
  for text in ('CFNotificationCenterAddObserver','CFNotificationCenterRemoveEveryObserver','requestSequence'):self.assertIn(text,app+runner)
  self.assertLess(app.index('record(kind: "acknowledged-sample"'),app.index('CFNotificationCenterPostNotification'))
  self.assertIn('try output.synchronize()',app);self.assertIn('turn > pending.turn',app)
  self.assertIn('RunLoop.current.run(mode: .default',runner);self.assertNotIn('Thread.sleep(',runner)
  self.assertIn('SettingsClipboardProbeRunner(nonce: string(try state(), "sessionToken"))',tests)
  block=tests[tests.index('let probeActionBefore ='):]
  self.assertLess(block.index('try clipboardProbe.sample'),block.index('target.tap()'))
  for forbidden in ('UIPasteboard.general.string =','accessibilityActivate','presentConfirmation','.finish(','.id(', 'notify_set_state'):
   self.assertNotIn(forbidden,app+runner)

if __name__=='__main__':unittest.main()
