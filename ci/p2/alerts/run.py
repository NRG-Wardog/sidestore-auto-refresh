#!/usr/bin/env python3
"""Execute seven isolated hosted UIKit lifecycle cases; never full-app admission."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, platform, re, shutil, subprocess, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE='eb10cedb772bc0496d67edd7a269679e24826b9f'
TREE='d6960a99a2f022a333c364fa4d28e264568a28ca'
ANCHORS=('private final class V3NativeAlertPresentation','extension UIViewController','enum LCNativeErrorSite','enum LCNativeErrorPresentation')
METHODS=(
 'testRapidLanguageChangesPreserveTextAndRejectStaleAction',
 'testConfirmAtReplacementBoundaryCompletesOnce',
 'testCancelAtReplacementBoundaryCompletesOnce',
 'testExternalBindingDismissalCannotResurrectOrCallAction',
 'testPresenterDisappearanceResolvesUnavailableOnce',
 'testShowErrorResolvesPendingConfirmationBeforeReplacingIt',
 'testAsyncErrorAfterLanguageSwitchUsesLatestLanguageAndKeepsOriginal',
)
LIMITS=['Direct finish-callback simulation, no actual action-button taps',
 'Synthetic scene notification, no OS-driven scene destruction',
 'No arbitrary unmanaged programmatic-dismissal completion proof',
 'Standalone defaults/bundle fallback; no guest-host capture lifecycle',
 'No full Settings persistence/relaunch, whole-app layout or Objective-C alert race proof',
 'No real LiveContainer target packaging, combined P0/P1 integration, IPA or device admission',
 'No runtime plural-count selection, keyboard IME or VoiceOver acceptance',
 'Existing OS-owned scheduled payload/intent language limits remain']
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def require(value,message):
 if not value:raise ValueError(message)
def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path); value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def verify_inputs(host,approved):
 require(sha(HERE/'inputs.json')==approved,'Unapproved lifecycle inputs')
 data=json.loads((HERE/'inputs.json').read_text())
 require(data['sourceCommit']==SOURCE and data['sourceTree']==TREE,'Wrong source identity')
 require(git(host,'rev-parse','HEAD')==SOURCE and git(host,'rev-parse','HEAD^{tree}')==TREE,'Wrong published source checkout')
 require(not git(host,'status','--porcelain','--untracked-files=all'),'Source checkout is dirty')
 for base,field in [(host,'sourceFiles'),(ROOT,'builderFiles')]:
  for name,digest in data[field].items():require(sha(base/name)==digest,'Input hash differs: '+name)
 require(data['methods']==list(METHODS),'Wrong mandatory XCTest methods')
 return data

def prepare(host,output):
 sys.path.insert(0,str(ROOT/'scripts'))
 from run_p0_signin_rendering import declaration
 source=output/'Sources';source.mkdir(parents=True)
 localization=host/'LiveContainerSwiftUI/Utilities/V3Localization.swift'
 extension=host/'LiveContainerSwiftUI/Utilities/ViewExtensions.swift'
 catalog=host/'Resources/Localizable.xcstrings'
 extracted={anchor:declaration(extension.read_text(),anchor) for anchor in ANCHORS}
 require(extracted[ANCHORS[0]].startswith('@MainActor\n'),'Lost actor annotation')
 template=(HERE/'NativeAlertLanguageTests.swift.in').read_text()
 require(tuple(re.findall(r'^    func (test\w+)\(',template,re.M))==METHODS,'Template method drift')
 generated=source/'NativeAlertLanguageTests.swift'
 generated.write_text('\n\n'.join(['import UIKit\nimport ObjectiveC\n',localization.read_text(),*extracted.values(),template]))
 shutil.copyfile(HERE/'App.swift',source/'App.swift');shutil.copyfile(catalog,source/'Localizable.xcstrings')
 project=output/'P2Alert.xcodeproj';project.mkdir()
 for name in ['project.pbxproj','Info.plist']:shutil.copyfile(HERE/'project'/name,project/name)
 scheme=project/'xcshareddata/xcschemes';scheme.mkdir(parents=True)
 shutil.copyfile(HERE/'project/P2Alert.xcscheme',scheme/'P2Alert.xcscheme')
 original=output/'original-source'
 for path in [localization,extension,catalog]:
  destination=original/path.relative_to(host);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
 identity={'sourceCommit':SOURCE,'sourceTree':TREE,'sourceFiles':{str(p.relative_to(host)):sha(p) for p in [localization,extension,catalog]},
  'declarations':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in extracted.items()},'templateSHA256':sha(HERE/'NativeAlertLanguageTests.swift.in'),
  'generatedSHA256':sha(generated),'methods':list(METHODS),'scope':LIMITS}
 write(output/'source-identity.json',identity)
 return project

def verify_results(summary,tests):
 for key,value in [('passedTests',7),('failedTests',0),('skippedTests',0),('totalTestCount',7)]:
  require(type(summary.get(key)) is int and summary[key]==value,'Invalid XCTest count: '+key)
 require(summary.get('result')=='Passed','XCTest did not pass')
 rows=[]
 def walk(node):
  if isinstance(node,dict):
   if node.get('nodeType')=='Test Case':rows.append(node)
   for value in node.values():walk(value)
  elif isinstance(node,list):
   for value in node:walk(value)
 walk(tests)
 require(len(rows)==7,'Missing or duplicate XCTest case nodes')
 names=[]
 for row in rows:
  require(row.get('result')=='Passed','Nonpassing XCTest method')
  identifier=row.get('nodeIdentifier','')
  require(isinstance(identifier,str),'Invalid XCTest identifier')
  match=re.fullmatch(r'(?:P2AlertTests/)?NativeAlertLanguageTests/(test\w+)\(\)',identifier)
  require(match is not None,'Unexpected XCTest class or method identity: '+identifier)
  name=match.group(1);require(row.get('name')==name+'()','XCTest name/identity mismatch');names.append(name)
 require(len(set(names))==7 and set(names)==set(METHODS),'Missing, duplicate or unexpected test name')
 return {'status':'passed','testsRun':7,'skipped':0,'methods':sorted(names),'scope':LIMITS}



def strict_json(text):
 def pairs(items):
  output={}
  for key,value in items:
   require(key not in output,'Duplicate JSON key');output[key]=value
  return output
 def invalid(value):raise ValueError('Non-finite JSON number: '+value)
 def finite(value):
  number=float(value);require(math.isfinite(number),'Non-finite JSON number');return number
 return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid,parse_float=finite)

OBSERVATIONS={
 'rapid-observer':METHODS[0], 'boundary-confirm':METHODS[1], 'boundary-cancel':METHODS[2],
 'external-dismissal':METHODS[3], 'scene-notification':METHODS[4], 'confirmation-replaced':METHODS[5],
 **{'async-error-'+language:METHODS[6] for language in ['en','he','zh-Hans','zh-Hant']},
}
def verify_observations(directory,catalog):
 manifest=strict_json((directory/'manifest.json').read_text())
 require(isinstance(manifest,list),'Unexpected attachment manifest')
 found={}
 for row in manifest:
  require(isinstance(row,dict) and isinstance(row.get('attachments'),list),'Malformed attachment group')
  for attachment in row['attachments']:
   require(isinstance(attachment,dict),'Malformed attachment row')
   suggested=attachment.get('suggestedHumanReadableName','');require(isinstance(suggested,str),'Malformed attachment name')
   matches=[name for name in OBSERVATIONS if suggested.startswith(name+'_') or suggested==name+'.json']
   if not matches:
    filename=attachment.get('exportedFileName','')
    if isinstance(filename,str) and filename.endswith('.json'):
     path=(directory/filename).resolve();require(path.is_relative_to(directory.resolve()) and path.is_file(),'Unsafe attachment')
     extra=strict_json(path.read_text())
     require(not isinstance(extra,dict) or 'evidenceCase' not in extra,'Unexpected observation name')
    continue
   require(len(matches)==1,'Ambiguous observation name')
   name=matches[0]
   require(name not in found,'Duplicate observation: '+name)
   require(row.get('testIdentifier')=='NativeAlertLanguageTests/'+OBSERVATIONS[name]+'()','Wrong observation test association')
   require(attachment.get('isAssociatedWithFailure') is False,'Failure-associated observation')
   filename=attachment.get('exportedFileName');require(isinstance(filename,str),'Missing exported observation filename')
   path=(directory/filename).resolve();require(path.is_relative_to(directory.resolve()) and path.is_file(),'Unsafe or missing observation')
   value=strict_json(path.read_text());require(isinstance(value,dict) and value.get('evidenceCase')==name,'Observation payload/name mismatch')
   def count(key,expected):require(type(value.get(key)) is int and value[key]==expected,'Wrong observation count: '+name+'/'+key)
   if name.startswith('async-error-'):
    language=name.removeprefix('async-error-');count('deliveryCount',1);count('staleCallbackCount',0)
    require(value.get('title')==catalog['strings']['lc.common.error']['localizations'][language]['stringUnit']['value'],'Wrong observed localized title')
    require(type(value.get('direction')) is int and value['direction']==(4 if language=='he' else 3),'Wrong observed direction')
    require(isinstance(value.get('original'),str) and 'Fixture % app • 日本語 • עברית' in value['original'],'Lost original diagnostic')
    require(isinstance(value.get('message'),str) and 'Fixture % app • 日本語 • עברית' in value.get('message','') and 'SS-NATIVE-BANNER' in value['message'],'Lost opaque argument or diagnostic ID')
   elif name=='confirmation-replaced':count('completionCount',1);require(value.get('resultIsNil') is True,'Confirmation not cancelled')
   else:
    expected={'rapid-observer':['confirm'],'boundary-confirm':['confirm'],'boundary-cancel':['cancel'],'external-dismissal':[],'scene-notification':['unavailable']}[name]
    count('callbackCount',len(expected));require(value.get('outcomes')==expected,'Wrong terminal callback outcome')
    if name=='rapid-observer':
     require(type(value.get('replacements')) is int and value['replacements']>=2,'No observed alert replacement')
     require(value.get('renderedTitle')==catalog['strings']['lc.common.error']['localizations']['he']['stringUnit']['value'],'Wrong final Hebrew title')
     require(value.get('renderedBody')==catalog['strings']['lc.webView.installAppDesc']['localizations']['he']['stringUnit']['value'],'Wrong final Hebrew body')
     require(type(value.get('direction')) is int and value['direction']==4 and value.get('preservedText')=='user text: % @ : / • עברית • 日本語','Lost direction or text')
   found[name]={'testIdentifier':row['testIdentifier'],'filename':filename,'sha256':sha(path),'observation':value}
 require(set(found)==set(OBSERVATIONS),'Missing lifecycle observations')
 return {'status':'passed','observationCount':len(found),'observations':found}

def evaluate_native_evidence(summary, tests, attachments, catalog, command_failure=None):
 # Keep the primary XCTest failure visible even when a crash leaves no observations.
 # These are independent required gates; retain all failures without accepting either.
 result={'status':'failed','nativeCounts':{key:summary.get(key) for key in
  ['result','passedTests','failedTests','skippedTests','totalTestCount']},'errors':[]}
 if command_failure is not None:result['errors'].append('Xcodebuild test command failed: '+str(command_failure))
 try:result['xctest']=verify_results(summary,tests)
 except Exception as error:result['errors'].append('XCTest gate: '+str(error))
 try:result['observations']=verify_observations(attachments,catalog)
 except Exception as error:result['errors'].append('Observation gate: '+str(error))
 if not result['errors']:result['status']='passed'
 return result


def select_device(inventory):
 candidates=[]
 for runtime,devices in inventory['devices'].items():
  match=re.search(r'\.iOS-(\d+)-(\d+)(?:-(\d+))?$',runtime)
  if not match or int(match.group(1))<17:continue
  version=tuple(int(x or 0) for x in match.groups())
  for device in devices:
   if device.get('isAvailable') and device['name'].startswith('iPhone'):
    candidates.append((version,device['name'],runtime,device))
 require(candidates,'No available iPhone simulator at iOS 17 or newer')
 _,_,runtime,device=sorted(candidates,key=lambda x:(x[0],x[1]))[0]
 return {'runtime':runtime,**device}

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
  command('xcodebuild','build-for-testing','-project',project,'-scheme','P2Alert','-sdk','iphonesimulator',
   '-destination','generic/platform=iOS Simulator','-derivedDataPath',work/'DerivedData','-jobs','2',
   'CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO',timeout=600)
  sys.path.insert(0,str(ROOT/'scripts'))
  from p2_localization_contract import verify_app
  verifier=module('p2_alert_resource_verifier',ROOT/'ci/p2/preflight.py')
  app=work/'DerivedData/Build/Products/Debug-iphonesimulator/P2AlertHost.app'
  resources=verify_app(app)
  plurals=verifier.verify_exact_plural_values(app,json.loads((host/'Resources/Localizable.xcstrings').read_text()))
  write(evidence/'compiled-resources.json',{'localization':resources,'exactPluralValues':plurals,'scope':'standalone hosted XCTest app only'})
  require(resources['status']=='passed' and plurals['status']=='passed','Compiled test host resources rejected')
  runs=list((work/'DerivedData/Build/Products').glob('*.xctestrun'));require(len(runs)==1,'Ambiguous XCTest run product')
  device=select_device(inventory);write(evidence/'device.json',device)
  if device['state']!='Booted':command('xcrun','simctl','boot',device['udid']);booted_here=True
  command('xcrun','simctl','bootstatus',device['udid'],'-b',timeout=300)
  bundle=evidence/'alerts.xcresult';failure=None
  try:
   command('xcodebuild','test-without-building','-xctestrun',runs[0],'-destination','id='+device['udid'],
    '-resultBundlePath',bundle,'-parallel-testing-enabled','NO','-maximum-concurrent-test-simulator-destinations','1',
    '-test-timeouts-enabled','YES','-default-test-execution-time-allowance','90','-maximum-test-execution-time-allowance','90',
    *['-only-testing:P2AlertTests/NativeAlertLanguageTests/'+name for name in METHODS],timeout=900)
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
  require(native['status']=='passed','Native lifecycle evidence rejected; inspect all retained gate failures')
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
 require(report['status']=='passed','Isolated UIKit lifecycle lane failed; inspect retained evidence')
if __name__=='__main__':main()
