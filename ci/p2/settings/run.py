#!/usr/bin/env python3
"""Five real-XCUITest component gates; never whole LiveContainer admission."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, platform, plistlib, re, shutil, subprocess, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE='eb10cedb772bc0496d67edd7a269679e24826b9f'
TREE='d6960a99a2f022a333c364fa4d28e264568a28ca'
LANGUAGES=('en','he','zh-Hans','zh-Hant')
METHODS=('testSettingsDefault','testSettingsLargest','testConfirmationButtons','testErrorButtons','testTextFieldButtons')
CASES=dict(zip(('settings-default','settings-largest','confirmation-buttons','error-buttons','textfield-buttons'),METHODS))
EXPECTED_KEYS=('Language','lc.common.error','lc.common.ok','lc.common.cancel','lc.common.copy',
 'lc.webView.installAppDesc','lc.webView.pageLaunch %@','Some details are unavailable in the selected language.','Error ID:')
ANCHORS=('private final class V3NativeAlertPresentation','extension UIViewController','enum LCNativeErrorSite',
 'enum LCNativeErrorPresentation','public struct TextFieldAlertModifier')
LIMITS=['Standalone exact-component host; no whole LCSettingsView or production root/model lifetime proof',
 'Fixture-owned equivalent locale/direction root and synthetic model/input state; no production auth operations',
 'Actual picker and alert-action taps are sequential; no action tap precisely during language replacement',
 'Standalone defaults/bundle fallback; no host capture across guest execution',
 'One simulator and bounded Dynamic Type screenshots; no full-app device matrix, IME or VoiceOver acceptance',
 'No OS-driven scene destruction or arbitrary unmanaged dismissal proof',
 'No real LiveContainer target packaging, combined P0/P1 integration, signed IPA or device acceptance',
 'Existing OS-owned payload and AppIntent language limits remain']
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(value,message):
 if not value:raise ValueError(message)
def write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
ALERT_LANE=module('accepted_alert_lane',HERE.parent/'alerts/run.py')
strict_json=ALERT_LANE.strict_json
select_device=ALERT_LANE.select_device

def verify_inputs(host,approved):
 require(sha(HERE/'inputs.json')==approved,'Unapproved interaction inputs')
 data=strict_json((HERE/'inputs.json').read_text())
 require(data['sourceCommit']==SOURCE and data['sourceTree']==TREE,'Wrong source identity')
 require(git(host,'rev-parse','HEAD')==SOURCE and git(host,'rev-parse','HEAD^{tree}')==TREE,'Wrong published source checkout')
 require(not git(host,'status','--porcelain','--untracked-files=all'),'Source checkout is dirty')
 for base,key in [(host,'sourceFiles'),(ROOT,'builderFiles')]:
  for name,digest in data[key].items():require(sha(base/name)==digest,'Input hash differs: '+name)
 require(data['methods']==list(METHODS),'Wrong mandatory UI-test methods')
 return data

def swift_literal(value):
 return re.sub(r'\\u([0-9a-fA-F]{4})',lambda m:'\\u{'+m.group(1)+'}',json.dumps(value,ensure_ascii=False))

def expected_strings(catalog,builder_commit):
 require(re.fullmatch('[0-9a-f]{40}',builder_commit) is not None,'Missing exact builder commit')
 rows=[]
 for language in LANGUAGES:
  values=[]
  for key in EXPECTED_KEYS:
   value=catalog['strings'][key]['localizations'][language]['stringUnit']['value']
   require(isinstance(value,str) and value,'Missing independent expected string')
   values.append(swift_literal(key)+': '+swift_literal(value))
  rows.append(swift_literal(language)+': ['+', '.join(values)+']')
 return ('import Foundation\n\nenum ExpectedStrings {\n    static let builderCommit = '+swift_literal(builder_commit)+'\n'
  '    private static let values: [String: [String: String]] = [\n        '+',\n        '.join(rows)+'\n    ]\n'
  '    static func text(_ key: String, language: String) -> String {\n'
  '        guard let value = values[language]?[key] else { preconditionFailure("Unknown expected catalog key/language") }\n'
  '        return value\n    }\n}\n')

def prepare(host,output):
 sys.path.insert(0,str(ROOT/'scripts'))
 from run_p0_signin_rendering import declaration, member
 source=output/'Sources';source.mkdir(parents=True)
 localization=host/'LiveContainerSwiftUI/Utilities/V3Localization.swift'
 settings=host/'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift'
 extension=host/'LiveContainerSwiftUI/Utilities/ViewExtensions.swift'
 delegate=host/'LiveContainerSwiftUI/App/AppDelegate.swift'
 shell=host/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
 catalog=host/'Resources/Localizable.xcstrings'
 extracted={anchor:declaration(extension.read_text(),anchor) for anchor in ANCHORS}
 extracted['class SceneDelegate']=declaration(delegate.read_text(),'class SceneDelegate')
 extracted['View.textFieldAlert']=member(extension.read_text(),'public func textFieldAlert')
 require(extracted[ANCHORS[0]].startswith('@MainActor\n'),'Lost alert actor annotation')
 reference=declaration(shell.read_text(),'struct V3UnifiedShell')
 for expression in ['store: V3Localization.hostDefaults','V3AppLanguage.resolve(language).rawValue','V3AppLanguage.resolve(language).isRightToLeft']:
  require(expression in reference,'Production locale-root reference drift')
 template=(HERE/'App.swift.in').read_text()
 tests=(HERE/'SettingsInteractionTests.swift.in').read_text()
 require(tuple(re.findall(r'^    func (test\w+)\(',tests,re.M))==METHODS,'UI-test method drift')
 fragments=['import UIKit\nimport SwiftUI\nimport ObjectiveC\n',localization.read_text(),settings.read_text(),
  *[extracted[a] for a in ANCHORS],extracted['class SceneDelegate'],
  'extension View {\n'+extracted['View.textFieldAlert']+'}\n',template]
 (source/'App.swift').write_text('\n\n'.join(fragments))
 (source/'SettingsInteractionTests.swift').write_text(tests)
 builder_commit=git(ROOT,'rev-parse','HEAD')
 (source/'ExpectedStrings.swift').write_text(expected_strings(strict_json(catalog.read_text()),builder_commit))
 shutil.copyfile(catalog,source/'Localizable.xcstrings')
 project=output/'P2Settings.xcodeproj';project.mkdir()
 shutil.copyfile(HERE/'project/project.pbxproj',project/'project.pbxproj')
 info=plistlib.loads((HERE/'project/Info.plist').read_bytes());info['LCBuilderCommit']=builder_commit
 (project/'Info.plist').write_bytes(plistlib.dumps(info,sort_keys=False))
 scheme=project/'xcshareddata/xcschemes';scheme.mkdir(parents=True)
 shutil.copyfile(HERE/'project/P2Settings.xcscheme',scheme/'P2Settings.xcscheme')
 original=output/'original-source'
 paths=[localization,settings,extension,delegate,shell,catalog]
 for path in paths:
  destination=original/path.relative_to(host);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
 identity={'sourceCommit':SOURCE,'sourceTree':TREE,'builderCommit':builder_commit,
  'sourceFiles':{str(p.relative_to(host)):sha(p) for p in paths},
  'declarations':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in extracted.items()},
  'rootReferenceSHA256':hashlib.sha256(reference.encode()).hexdigest(),
  'rootReferenceCompiled':False,'fixtureOwnsRootCompositionAndWindowAttachment':True,
  'templateSHA256':sha(HERE/'App.swift.in'),'testTemplateSHA256':sha(HERE/'SettingsInteractionTests.swift.in'),
  'generatedFiles':{str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file()},
  'methods':list(METHODS),'scope':LIMITS}
 write(output/'source-identity.json',identity)
 return project

def verify_results(summary,tests):
 for key,value in [('passedTests',5),('failedTests',0),('skippedTests',0),('totalTestCount',5)]:
  require(type(summary.get(key)) is int and summary[key]==value,'Invalid XCTest count: '+key)
 require(summary.get('result')=='Passed','XCTest did not pass')
 rows=[]
 def walk(node):
  if isinstance(node,dict):
   if node.get('nodeType')=='Test Case':rows.append(node)
   for value in node.values():walk(value)
  elif isinstance(node,list):
   for value in node:walk(value)
 walk(tests);require(len(rows)==5,'Missing or duplicate UI-test case nodes')
 names=[]
 for row in rows:
  require(row.get('result')=='Passed','Nonpassing UI-test method')
  identifier=row.get('nodeIdentifier','');require(isinstance(identifier,str),'Invalid XCTest identifier')
  match=re.fullmatch(r'(?:P2SettingsUITests/)?SettingsInteractionTests/(test\w+)\(\)',identifier)
  require(match is not None,'Unexpected UI-test class/method: '+identifier)
  name=match.group(1);require(row.get('name')==name+'()','UI-test name/identity mismatch');names.append(name)
 require(len(set(names))==5 and set(names)==set(METHODS),'Missing, duplicate or unexpected method')
 return {'status':'passed','testsRun':5,'skipped':0,'methods':sorted(names),'scope':LIMITS}


SAFE_TEXT='P2 typed text 123 % @ /'
OPAQUE_NAME='Fixture % app • 日本語 • עברית'
RAW_ERROR='fixture diagnostic % @ : / • 日本語 • עברית\nopaque=001'
NATIVE_NAMES={'en':'English','he':'עברית','zh-Hans':'简体中文','zh-Hant':'繁體中文'}
COUNTERS=('confirmStarted','confirmCompleted','errorStarted','textStarted','textCompleted')

def validate_state(value,allow_missing_default=False):
 import uuid
 require(isinstance(value,dict) and value.get('schema')=='p2-settings-state-v1','Malformed fixture state')
 for key in ('processToken','sessionToken'):
  require(isinstance(value.get(key),str),'Missing process/session token')
  uuid.UUID(value[key])
 require(value.get('language') in LANGUAGES,'Unexpected state language')
 language=value['language']
 require(value.get('hostDefaultLanguage')==language or (allow_missing_default and language=='en' and value.get('hostDefaultLanguage')=='<missing>'),'Host defaults key differs from selection')
 require(value.get('locale','').replace('_','-')==language,'State locale differs from selection')
 require(value.get('direction')==('rtl' if language=='he' else 'ltr'),'Wrong state direction')
 require(value.get('contentSizeCategory') in ('large','accessibilityExtraExtraExtraLarge'),'Wrong Dynamic Type category')
 arguments=value.get('processArguments');environment=value.get('languageEnvironment')
 require(isinstance(arguments,list) and arguments and all(isinstance(x,str) for x in arguments)
  and arguments[0].endswith('/P2SettingsHost'),'Missing actual app process arguments')
 require(all(re.match(r'^-{1,2}(?:applelanguages|applelocale|nslanguages|language|locale)(?:=|$)',x,re.I) is None for x in arguments[1:]),'Actual app process has a language override')
 require(arguments.count('--largest')==(1 if value['contentSizeCategory']=='accessibilityExtraExtraExtraLarge' else 0),'Actual launch text-size argument differs')
 require(isinstance(environment,dict) and set(environment)<=set(('LANG','LANGUAGE','LC_ALL','LC_MESSAGES'))
  and all(isinstance(x,str) for x in environment.values()),'Unexpected or unsafe actual language environment receipt')
 for key in (*COUNTERS,'clipboardChangeCount'):
  require(type(value.get(key)) is int and value[key]>=0,'Invalid state count: '+key)
 for key in ('text','originalMessage','confirmOutcome','textOutcome','textResult','clipboardValue'):
  require(isinstance(value.get(key),str),'Invalid state string: '+key)
 require(OPAQUE_NAME in value['originalMessage'],'Original message lost opaque Unicode')
 require(type(value.get('textPresented')) is bool,'Invalid binding state')
 return value

def geometry(value,viewport=None):
 import math
 require(isinstance(value,dict) and set(value)=={'x','y','width','height'},'Malformed geometry')
 require(all(type(v) in (float,int) and math.isfinite(v) for v in value.values()),'Nonfinite geometry')
 require(0<value['width']<=8192 and 0<value['height']<=8192,'Empty or oversized geometry')
 if viewport is not None:
  require(value['x']>=viewport['x']-0.5 and value['y']>=viewport['y']-0.5
   and value['x']+value['width']<=viewport['x']+viewport['width']+0.5
   and value['y']+value['height']<=viewport['y']+viewport['height']+0.5,'Control extends outside captured viewport')
 return value

def verify_observations(directory,catalog,builder_commit=None):
 builder_commit=builder_commit or git(ROOT,'rev-parse','HEAD')
 require(re.fullmatch('[0-9a-f]{40}',builder_commit) is not None,'Invalid evidence builder identity')
 def text(key,language):return catalog['strings'][key]['localizations'][language]['stringUnit']['value']
 def matching_message(value,language):
  fmt=text('lc.webView.pageLaunch %@',language)
  require(isinstance(value,str) and value in (fmt.replace('%@',OPAQUE_NAME),fmt.replace('%@','\u2068'+OPAQUE_NAME+'\u2069')),
   'Formatted message differs from exact catalog/opaque argument')
 manifest=strict_json((directory/'manifest.json').read_text());require(isinstance(manifest,list),'Malformed attachment manifest')
 attachments=[];exported=set()
 for group in manifest:
  require(isinstance(group,dict) and isinstance(group.get('attachments'),list),'Malformed attachment group')
  for attachment in group['attachments']:
   require(isinstance(attachment,dict),'Malformed attachment')
   name=attachment.get('suggestedHumanReadableName');filename=attachment.get('exportedFileName')
   require(isinstance(name,str) and isinstance(filename,str),'Invalid attachment names')
   path=(directory/filename).resolve();require(path.is_relative_to(directory.resolve()) and path.is_file(),'Unsafe/missing exported attachment')
   require(path not in exported,'Reused exported attachment file');exported.add(path)
   attachments.append((group.get('testIdentifier'),attachment,path))
 def named(name,method):
  suffix=Path(name).suffix;stem=name[:-len(suffix)]
  pattern=re.compile(re.escape(stem)+r'(?:'+re.escape(suffix)+r')?_[0-9]+_.+'+re.escape(suffix))
  matches=[row for row in attachments if row[1]['suggestedHumanReadableName']==name or pattern.fullmatch(row[1]['suggestedHumanReadableName'])]
  require(len(matches)==1,'Missing or duplicate named attachment: '+name)
  association,meta,path=matches[0]
  require(association=='SettingsInteractionTests/'+method+'()','Wrong observation attachment association')
  require(meta.get('isAssociatedWithFailure') is False,'Failure-associated required attachment')
  require(path.suffix==suffix,'Unexpected exported attachment type')
  return path
 def tap(target,viewport,label=None,identifier=None):
  require(isinstance(target,dict) and target.get('hittable') is True,'Tap target was not hittable')
  require(isinstance(target.get('identifier'),str) and isinstance(target.get('label'),str),'Missing raw tap identity')
  if label is not None:require(target['label']==label,'Wrong localized action tap label')
  if identifier is not None:require(target['identifier']==identifier,'Wrong tap control identifier')
  geometry(target.get('frame'),viewport)
 def picker(picks,language,viewport,before_language):
  require(isinstance(picks,dict) and picks.get('languageBefore')==before_language,'Missing/wrong actual picker predecessor')
  tap(picks.get('picker'),viewport,identifier='settings.language')
  require(text('Language',before_language) in picks['picker']['label'],'Picker tap label was not localized')
  tap(picks.get('selection'),viewport,label=NATIVE_NAMES[language])
 def retained_state(before,after):
  for key in ('processToken','sessionToken','text','originalMessage',*COUNTERS,'clipboardValue','clipboardChangeCount','confirmOutcome','textOutcome','textResult','textPresented','processArguments','languageEnvironment'):
   require(before[key]==after[key],'Picker changed retained state: '+key)
 result={};used=[];report_paths=set()
 for case,method in CASES.items():
  path=named(case+'.json',method);report_paths.add(path);data=strict_json(path.read_text())
  require(isinstance(data,dict) and data.get('schema')=='p2-settings-interaction-v1','Invalid interaction report schema')
  require(data.get('evidenceCase')==case and data.get('method')==method,'Wrong observation identity')
  require(data.get('completed') is True and data.get('failures')==[] and data.get('lastStage')=='complete','Incomplete/failed interaction report')
  rows=data.get('observations');require(isinstance(rows,list) and len(rows)==8,'Missing/extra language interaction row')
  settings=case.startswith('settings-');axis='phase' if settings else 'action'
  values=('live','relaunched') if settings else (('copy','ok') if case=='error-buttons' else ('confirm','cancel'))
  require([(row.get('language'),row.get(axis)) for row in rows]==[(lang,value) for lang in LANGUAGES for value in values],
   'Missing, duplicate, unordered or unexpected interaction')
  category='accessibilityExtraExtraExtraLarge' if case=='settings-largest' else 'large'
  bootstrap=data.get('bootstrap');require(isinstance(bootstrap,dict),'Missing genuine picker bootstrap')
  start=validate_state(bootstrap.get('before'),allow_missing_default=True);boot=validate_state(bootstrap.get('after'))
  require(boot['language']=='he' and start['contentSizeCategory']==boot['contentSizeCategory']==category,'Wrong bootstrap language/category')
  require(all(start[key]==0 for key in COUNTERS) and start['text']=='' and not start['textPresented'],'Bootstrap did not start fresh fixture state')
  matching_message(start['originalMessage'],start['language']);retained_state(start,boot)
  picker(bootstrap.get('pickerTap'),'he',geometry(rows[0].get('viewport')),start['language'])
  previous=boot;previous_pick=None;records=[]
  for row in rows:
   language=row['language'];value=row[axis];require(row.get('verified') is True,'Unverified interaction')
   before=validate_state(row.get('before'));after=validate_state(row.get('after'))
   require(after['language']==language,'Wrong selected/rendered language')
   category='accessibilityExtraExtraExtraLarge' if case=='settings-largest' else 'large'
   require(before['contentSizeCategory']==after['contentSizeCategory']==category,'Wrong independent text-size case')
   viewport=geometry(row.get('viewport'))
   def delta(key,expected):require(after[key]-before[key]==expected,'Wrong action count delta: '+key)
   stem=case+'-'+language+'-'+value
   if settings:
    expected_text=SAFE_TEXT if value=='live' else ''
    require(row.get('expectedText')==after['text']==expected_text,'Wrong retained/reset fixture text')
    matching_message(row.get('expectedMessage'),language)
    require(row.get('displayedMessage')==row['expectedMessage'],'Actual message differs from native explicit-locale oracle')
    require(row.get('launchArguments')==(['--largest'] if case=='settings-largest' else []),'Unexpected process launch arguments')
    require(row.get('launchEnvironment')=={},'Unexpected process launch environment override')
    require(before['processArguments']==after['processArguments'] and before['languageEnvironment']==after['languageEnvironment'],'Actual process launch inputs changed across language switch/relaunch')
    require(row.get('terminationObserved') is (value=='relaunched'),'Missing old-process termination proof')
    same_process=before['processToken']==after['processToken'];same_session=before['sessionToken']==after['sessionToken']
    require(same_process==(value=='live') and same_session==(value=='live'),'Wrong process/model lifetime')
    if value=='live':
     require(before['text']==after['text']==SAFE_TEXT and before['originalMessage']==after['originalMessage'],'Live switch changed fixture text/original bytes')
     for key in COUNTERS:delta(key,0)
     require(before['clipboardValue']==after['clipboardValue'],'Picker changed clipboard contents');delta('clipboardChangeCount',0)
    else:
     require(before['language']==language and all(after[key]==0 for key in COUNTERS),'Relaunch reset or changed wrong state')
     require(before['hostDefaultLanguage']==after['hostDefaultLanguage'],'Preference key not retained across process restart')
     matching_message(after['originalMessage'],language)
    require(after['textPresented'] is False,'Unexpected active text-field binding')
    direction_viewport=geometry(row.get('directionViewport'))
    first=geometry(row.get('directionFirstFrame'),direction_viewport);last=geometry(row.get('directionLastFrame'),direction_viewport)
    require((first['x']+first['width']/2>last['x']+last['width']/2)==(language=='he'),'Actual sibling geometry has wrong direction')
    geometry(row.get('pickerFrame'),viewport);geometry(row.get('messageFrame'),viewport)
    require(isinstance(row.get('pickerLabel'),str) and text('Language',language) in row['pickerLabel'],'Picker label is not localized')
    require(isinstance(row.get('pickerValue'),str),'Missing selected picker value')
    if value=='live':
     expected_before=dict(previous,text=SAFE_TEXT)
     require(before==expected_before,'Live switch predecessor is not prior retained process plus actual input')
     picker(row.get('pickerTap'),language,viewport,before['language']);previous_pick=row['pickerTap']
    else:
     require(before==previous,'Relaunch predecessor differs from settled live state')
     require(row.get('pickerTap')==previous_pick,'Relaunch invented a new picker tap')
    expected_attachments=[stem+'-direction.png',stem+'-direction.txt',stem+'.png',stem+'.txt']
   else:
    pick_before=validate_state(row.get('pickerBefore'));pick_after=validate_state(row.get('pickerAfter'))
    require(pick_after['language']==language and pick_before['contentSizeCategory']==pick_after['contentSizeCategory']==category,'Wrong action picker state')
    retained_state(pick_before,pick_after);picker(row.get('pickerTap'),language,viewport,pick_before['language'])
    if value==values[0]:
     expected_pick_before=dict(previous)
     if case=='textfield-buttons' and language=='en':expected_pick_before['text']=SAFE_TEXT
     require(pick_before==expected_pick_before and before==pick_after,'Action picker is not bound to immediate predecessor')
     previous_pick=(pick_before,pick_after,row['pickerTap'])
    else:
     require((pick_before,pick_after,row['pickerTap'])==previous_pick and before==previous,'Action duplicated or changed picker/predecessor evidence')
    trigger={'confirmation-buttons':'p2-open-confirmation','error-buttons':'p2-open-error','textfield-buttons':'p2-open-textfield'}[case]
    tap(row.get('triggerTap'),viewport,identifier=trigger)
    require(before['language']==language and before['processToken']==after['processToken'] and before['sessionToken']==after['sessionToken'],'Action replaced process/model or language')
    require(before['originalMessage']==after['originalMessage'],'Action rewrote original message')
    require(before['processArguments']==after['processArguments'] and before['languageEnvironment']==after['languageEnvironment'],'Action changed actual process launch receipt')
    require(before['text']==after['text'],'Action unexpectedly changed outer input binding')
    require(row.get('alertDismissed') is True and row.get('noResurrection') is True,'Missing settled alert dismissal')
    require(type(row.get('drainSeconds')) in (int,float) and row['drainSeconds']>=0.8,'Missing post-dismissal drain')
    require(row.get('displayedTitle')==text('lc.common.error',language),'Wrong localized alert title')
    expected_actions=[text('lc.common.ok',language),text('lc.common.copy' if case=='error-buttons' else 'lc.common.cancel',language)]
    require(isinstance(row.get('displayedActions'),list) and sorted(row['displayedActions'])==sorted(expected_actions),'Wrong exact alert action set')
    all_labels=row.get('allButtonLabels')
    require(isinstance(all_labels,list) and all(isinstance(x,str) for x in all_labels),'Missing raw alert button inventory')
    require(sorted([x for x in all_labels if x in expected_actions] if case=='textfield-buttons' else all_labels)==sorted(expected_actions),'Unexpected raw action button inventory')
    require(after['textPresented'] is False,'Action left presentation binding open')
    if case!='error-buttons':require(before['clipboardValue']==after['clipboardValue'],'Noncopy action changed clipboard bytes')
    for key in (('textOutcome','textResult') if case=='confirmation-buttons' else ('confirmOutcome',)):
     require(before[key]==after[key],'Action changed unrelated callback result')
    action_key='lc.common.copy' if value=='copy' else ('lc.common.cancel' if value=='cancel' else 'lc.common.ok')
    tap(row.get('tapTarget'),viewport,label=text(action_key,language))
    if case=='confirmation-buttons':
     require(row.get('displayedMessage')==text('lc.webView.installAppDesc',language),'Wrong confirmation message')
     expected='true' if value=='confirm' else 'false'
     require(row.get('expectedResult')==row.get('actualResult')==after['confirmOutcome']==expected,'Wrong real confirmation result')
     delta('confirmStarted',1);delta('confirmCompleted',1)
     for key in ('errorStarted','textStarted','textCompleted','clipboardChangeCount'):delta(key,0)
    elif case=='error-buttons':
     identifier='\u2066SS-NATIVE-BANNER\u2069' if language=='he' else 'SS-NATIVE-BANNER'
     require(row.get('displayedMessage')==text('Some details are unavailable in the selected language.',language)+'\n'+text('Error ID:',language)+' '+identifier,'Wrong localized unknown-error explanation/ID')
     require(row.get('expectedResult')==row.get('actualResult')=='dismissed','Wrong error dismissal result')
     expected=RAW_ERROR+'\nError ID: SS-NATIVE-BANNER\nbuilder_commit='+builder_commit if value=='copy' else before['clipboardValue']
     require(row.get('expectedClipboard')==after['clipboardValue']==expected,'Wrong copied original diagnostic bytes')
     count=1 if value=='copy' else 0
     require(type(row.get('clipboardDelta')) is int and row['clipboardDelta']==count,'Invalid clipboard delta')
     delta('clipboardChangeCount',count);delta('errorStarted',1)
     for key in ('confirmStarted','confirmCompleted','textStarted','textCompleted'):delta(key,0)
    else:
     require(row.get('displayedMessage')=='','Unexpected text-field message')
     entered=row.get('enteredText')
     require(isinstance(entered,str) and entered and entered.count(' added')==1 and row.get('typedPayload')==' added','Wrong actual typed modal input')
     require(entered.replace(' added','').encode('utf-8')==SAFE_TEXT.encode('utf-8'),'Native typing dropped or rewrote original input bytes')
     tap(row.get('inputTap'),viewport)
     expected=entered if value=='confirm' else 'nil'
     require(row.get('expectedResult')==row.get('actualResult')==after['textResult']==expected,'Wrong actual text-field result')
     require(row.get('initialText')==row.get('outerTextAfter')==before['text']==after['text']==SAFE_TEXT,'Wrong production input binding')
     require(after['textOutcome']==value and before['textPresented'] is False and after['textPresented'] is False,'Text-field binding did not close with expected outcome')
     delta('textStarted',1);delta('textCompleted',1)
     for key in ('confirmStarted','confirmCompleted','errorStarted','clipboardChangeCount'):delta(key,0)
    expected_attachments=[stem+'-before.png',stem+'-before.txt',stem+'-after.png',stem+'-after.txt']
   require(row.get('attachments')==expected_attachments,'Missing, extra or misplaced screenshot/AX evidence')
   retained=[]
   sys.path.insert(0,str(ROOT/'scripts'));from run_p0_signin_rendering import valid_png
   for name in expected_attachments:
    require(name not in used,'Reused screenshot/AX attachment');used.append(name)
    attachment=named(name,method);content=attachment.read_bytes()
    if name.endswith('.png'):require(valid_png(content),'Invalid retained screenshot')
    else:require(50<=len(content)<=4*1024*1024 and 'Application' in content.decode('utf-8'),'Invalid raw AX hierarchy')
    retained.append({'name':name,'filename':attachment.name,'sha256':sha(attachment)})
   previous=after
   records.append({'language':language,axis:value,'attachments':retained,'observation':row})
  require(validate_state(data.get('finalState'))==previous,'Final settled state differs from last observation')
  result[case]={'method':method,'filename':path.name,'sha256':sha(path),'observations':records}
 # Reject extra report payloads even when they use an unexpected attachment name.
 for association,meta,path in attachments:
  if path.suffix=='.json':
   data=strict_json(path.read_text())
   if isinstance(data,dict) and data.get('schema')=='p2-settings-interaction-v1':
    require(path in report_paths,'Unexpected extra interaction report')
 return {'status':'passed','cases':result,'observationCount':40,'screenshotCount':80,'hierarchyCount':80,
  'visualClippingReview':'required separately; accessibility and PNG presence do not establish visual quality','scope':LIMITS}

def evaluate_native_evidence(summary, tests, attachments, catalog, command_failure=None):
 result={'status':'failed','nativeCounts':{key:summary.get(key) for key in ['result','passedTests','failedTests','skippedTests','totalTestCount']},'errors':[]}
 if command_failure is not None:result['errors'].append('Xcodebuild test command failed: '+str(command_failure))
 try:result['xctest']=verify_results(summary,tests)
 except Exception as error:result['errors'].append('XCTest gate: '+str(error))
 try:result['observations']=verify_observations(attachments,catalog)
 except Exception as error:result['errors'].append('Observation gate: '+str(error))
 if not result['errors']:result['status']='passed'
 return result

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--host',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
 parser.add_argument('--work',type=Path,required=True);parser.add_argument('--approved-inputs-sha256',required=True)
 args=parser.parse_args();evidence=args.evidence.resolve();work=args.work.resolve();host=args.host.resolve()
 evidence.mkdir(parents=True,exist_ok=False);work.mkdir(parents=True,exist_ok=False)
 report={'status':'failed','sourceCommit':SOURCE,'sourceTree':TREE,'builderCommit':git(ROOT,'rev-parse','HEAD'),
  'inputSHA256':args.approved_inputs_sha256,'scope':LIMITS,'errors':[]}
 count=0;device=None;booted_here=False
 def command(*argv,timeout=300):
  nonlocal count
  count+=1;stem=evidence/'commands'/f'{count:03d}';stem.parent.mkdir(exist_ok=True)
  argv=[str(x) for x in argv];started=time.monotonic()
  try:result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
  except subprocess.TimeoutExpired as error:
   output=(error.stdout or b'')+(error.stderr or b'') if isinstance(error.stdout or error.stderr,bytes) else (error.stdout or '')+(error.stderr or '')
   stem.with_suffix('.log').write_text(output.decode(errors='replace') if isinstance(output,bytes) else output)
   write(stem.with_suffix('.json'),{'command':argv,'timedOut':True,'elapsedSeconds':time.monotonic()-started});raise
  stem.with_suffix('.log').write_text(result.stdout+'\n'+result.stderr)
  write(stem.with_suffix('.json'),{'command':argv,'returncode':result.returncode,'elapsedSeconds':time.monotonic()-started})
  require(result.returncode==0,'Command failed; retained log '+stem.name)
  return result.stdout.strip()
 try:
  require(platform.system()=='Darwin','Darwin/Xcode required; no skipped execution allowed')
  inputs=verify_inputs(host,args.approved_inputs_sha256);write(evidence/'inputs.json',inputs)
  for name in inputs['builderFiles']:
   target=evidence/'builder-inputs'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
  inventory=json.loads(command('xcrun','simctl','list','devices','available','--json'))
  write(evidence/'toolchain.json',{'xcode':command('xcodebuild','-version'),'swift':command('swift','--version'),
    'developerDir':os.environ.get('DEVELOPER_DIR'),'simulatorInventory':inventory})
  project=prepare(host,evidence/'fixture')
  command('xcodebuild','build-for-testing','-project',project,'-scheme','P2Settings','-sdk','iphonesimulator',
   '-destination','generic/platform=iOS Simulator','-derivedDataPath',work/'DerivedData','-jobs','2',
   'CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO',timeout=600)
  sys.path.insert(0,str(ROOT/'scripts'))
  from p2_localization_contract import verify_app
  verifier=module('p2_alert_resource_verifier',ROOT/'ci/p2/preflight.py')
  app=work/'DerivedData/Build/Products/Debug-iphonesimulator/P2SettingsHost.app'
  resources=verify_app(app)
  plurals=verifier.verify_exact_plural_values(app,json.loads((host/'Resources/Localizable.xcstrings').read_text()))
  write(evidence/'compiled-resources.json',{'localization':resources,'exactPluralValues':plurals,'scope':'standalone exact-component UI-test host only'})
  require(resources['status']=='passed' and plurals['status']=='passed','Compiled test host resources rejected')
  runs=list((work/'DerivedData/Build/Products').glob('*.xctestrun'));require(len(runs)==1,'Ambiguous XCTest run product')
  device=select_device(inventory);write(evidence/'device.json',device)
  if device['state']!='Booted':command('xcrun','simctl','boot',device['udid']);booted_here=True
  command('xcrun','simctl','bootstatus',device['udid'],'-b',timeout=300)
  bundle=evidence/'settings.xcresult';failure=None
  try:
   command('xcodebuild','test-without-building','-xctestrun',runs[0],'-destination','id='+device['udid'],
    '-resultBundlePath',bundle,'-parallel-testing-enabled','NO','-maximum-concurrent-test-simulator-destinations','1',
    '-test-timeouts-enabled','YES','-default-test-execution-time-allowance','240','-maximum-test-execution-time-allowance','240',
    *['-only-testing:P2SettingsUITests/SettingsInteractionTests/'+name for name in METHODS],timeout=1800)
  except Exception as error:failure=str(error)
  require(bundle.exists(),'No XCTest result bundle: '+str(failure))
  for kind in ['summary','tests']:
   data=json.loads(command('xcrun','xcresulttool','get','test-results',kind,'--path',bundle))
   write(evidence/('xctest-'+kind+'.json'),data)
  command('xcrun','xcresulttool','export','attachments','--path',bundle,'--output-path',evidence/'attachments')
  native=evaluate_native_evidence(json.loads((evidence/'xctest-summary.json').read_text()),
   json.loads((evidence/'xctest-tests.json').read_text()),evidence/'attachments',
   json.loads((host/'Resources/Localizable.xcstrings').read_text()),failure)
  report['nativeEvidence']=native;write(evidence/'native-evidence.json',native)
  if 'observations' in native:
   report['observations']=native['observations'];write(evidence/'observations.json',native['observations'])
  if 'xctest' in native:report['xctest']=native['xctest']
  report['errors'].extend(native['errors'])
  require(native['status']=='passed','Native interaction evidence rejected; inspect all retained gate failures')
  verify_inputs(host,args.approved_inputs_sha256)
  report['status']='passed'
 except Exception as error:
  report['errors'].append(str(error))
 finally:
  products=work/'DerivedData/Build/Products'
  if products.is_dir():
   try:
    shutil.copytree(products,evidence/'compiled-products',symlinks=True)
    write(evidence/'compiled-products-sha256.json',{str(p.relative_to(products)):sha(p) for p in sorted(products.rglob('*')) if p.is_file()})
   except Exception as error:report['errors'].append('Product retention failed: '+str(error));report['status']='failed'
  if device and booted_here:
   try:command('xcrun','simctl','shutdown',device['udid'])
   except Exception as error:report['errors'].append('Simulator shutdown failed: '+str(error));report['status']='failed'
  write(evidence/'summary.json',report)
  write(evidence/'evidence-index.json',{str(p.relative_to(evidence)):sha(p) for p in sorted(evidence.rglob('*')) if p.is_file() and p.name!='evidence-index.json'})
 require(report['status']=='passed','Isolated Settings interaction lane failed; inspect retained evidence')
if __name__=='__main__':main()
