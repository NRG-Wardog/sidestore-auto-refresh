#!/usr/bin/env python3
"""Run source-owned transition controls and a real same-session simulator fixture.
No app credentials, pairing data, network or downloaded simulator runtime.
"""
import argparse,hashlib,json,os,platform,plistlib,re,shutil,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'issues-native'))
from verify_candidate_graph import load_candidate_graph

def require(ok,message):
 if not ok:raise ValueError(message)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def strict(pairs):
 d={}
 for k,v in pairs:
  require(k not in d,'Duplicate JSON key');d[k]=v
 return d
def read(path):return json.loads(path.read_text(),object_pairs_hook=strict)
def command(args,*,cwd=None,timeout=120):
 try:return subprocess.check_output([str(x) for x in args],cwd=cwd,text=True,stderr=subprocess.STDOUT,timeout=timeout)
 except subprocess.SubprocessError as error:
  detail=getattr(error,'output','') or ''
  if isinstance(detail,bytes):detail=detail.decode('utf-8','replace')
  if detail:print(detail,file=sys.stderr,flush=True)
  raise
def source_test_result(result,log,label):
 if result.returncode!=0:
  # These source-owned synthetic fixtures use no account, pairing or signing
  # material. Bound the failure tail; preserve full log in private CI evidence.
  print('SOURCE_FIXTURE_FAILURE '+label+'\n'+log.read_text()[-16000:],file=sys.stderr,flush=True)
 require(result.returncode==0,'Source-owned '+label+' tests failed')

def extract(text,start,end):
 require(text.count(start)==1 and text.count(end)==1,'Production marker closure differs')
 section=text.split(start,1)[1].split(end,1)[0]
 require(section.strip(),'Empty production section');return section

def identity():
 d={k:os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
 require(re.fullmatch('[0-9a-f]{40}',d['workflow_commit']),'Invalid workflow SHA')
 require(all(re.fullmatch('[1-9][0-9]*',d[k]) for k in ('run_id','run_attempt')),'Invalid run identity');return d

# These paths are source-owned controls, not arbitrary test programs supplied externally.
SOURCES=('LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift',
 'LiveContainerSwiftUI/Utilities/V3Localization.swift',
 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
 'Resources/Localizable.xcstrings', 'tests/test_live_language_state.py', 'tests/fixtures/live_language_state.swift', 'tests/test_live_language_root.py')

def source_hashes(root):
 result={}
 for name in SOURCES:
  path=root/name
  require(path.is_file() and not path.is_symlink() and path.resolve(strict=True).is_relative_to(root),'Missing/linked source '+name)
  result[name]=sha(path)
 return result

def recovery_hashes(sources):
 result={}
 for owner,name in [('LiveContainer','SideStoreSupport/SideStore.swift'),('SideStore','AltStore/AppDelegate.swift'),('SideStore','tests/refresh_recovery/test_ownerless_snapshot.py')]:
  root=(sources/owner).resolve(strict=True);path=root/name
  require(path.is_file() and not path.is_symlink() and path.resolve(strict=True).is_relative_to(root),'Missing/linked refresh recovery source')
  result[owner+'/'+name]=sha(path)
 return result

def runtime_number(version):
 require(type(version) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}',version),'Invalid simulator runtime version')
 parts=[int(x) for x in version.split('.')]+[0,0]
 require(parts[0]<=65535 and parts[1]<=255 and parts[2]<=255,'Runtime version out of bounds')
 return (parts[0]<<16)|(parts[1]<<8)|parts[2]

def available_device(catalog):
 require(isinstance(catalog,dict),'Invalid simulator inventory')
 runtimes=[r for r in catalog['runtimes'] if r.get('isAvailable') is True and r.get('identifier','').startswith('com.apple.CoreSimulator.SimRuntime.iOS-')]
 require(runtimes,'No installed available iOS Simulator runtime; do not download one implicitly')
 # productFamily alone is insufficient: simctl classifies iPod touch as iPhone.
 phones=[d for d in catalog['devicetypes'] if d.get('name','').startswith('iPhone ') and d.get('identifier','').startswith('com.apple.CoreSimulator.SimDeviceType.iPhone-') and d.get('productFamily')=='iPhone']
 require(phones,'No installed explicit iPhone simulator device type')
 for runtime in sorted(runtimes,key=lambda r:runtime_number(r['version']),reverse=True):
  version=runtime_number(runtime['version'])
  if version<runtime_number('16.0'):continue
  supported=None
  if 'supportedDeviceTypes' in runtime:
   rows=runtime['supportedDeviceTypes'];require(type(rows) is list,'Invalid runtime supported-device inventory')
   supported=set()
   for row in rows:
    identifier=row if type(row) is str else row.get('identifier') if type(row) is dict else None
    require(type(identifier) is str,'Invalid supported device identity');supported.add(identifier)
  devices=catalog.get('devices',{});require(type(devices) is dict,'Invalid existing simulator inventory')
  existing=devices.get(runtime['identifier'],[]);require(type(existing) is list,'Invalid runtime device inventory')
  available_types={d.get('deviceTypeIdentifier') for d in existing if type(d) is dict and d.get('isAvailable') is True and type(d.get('deviceTypeIdentifier')) is str}
  eligible=[]
  for device in phones:
   lower=device.get('minRuntimeVersion');upper=device.get('maxRuntimeVersion')
   bounds=type(lower) is int and type(upper) is int and 0<=lower<=upper<=4294967295
   if ('minRuntimeVersion' in device or 'maxRuntimeVersion' in device) and not bounds:continue
   if bounds and not lower<=version<=upper:continue
   if supported is not None:
    if device['identifier'] not in supported:continue
   else:
    # Older simctl schemas can omit explicit supported identities. Accept
    # authoritative bounds or an available existing device's exact type under
    # this runtime only. Never reuse or mutate the existing device itself.
    if not bounds and device['identifier'] not in available_types:continue
   eligible.append(device)
  if eligible:
   # Prefer the newest compatible generation by minimum runtime, not array order.
   return runtime,max(eligible,key=lambda d:(d.get('minRuntimeVersion') if type(d.get('minRuntimeVersion')) is int else 0,d['identifier']))
 raise ValueError('No installed iPhone device type has verified compatibility with an available iOS16+ runtime')

TRANSITIONS=['he','en','he','ru','he','zh-Hans','he','zh-Hant','en','he','ru','en']
def validate_runtime_result(data,root):
 require(type(data) is dict and data.get('status')=='PASS','Simulator transition fixture failed: '+str(data.get('failure','unknown')))
 for key in ('same_hosting_controller','same_state_owner','guest_window_unchanged','presented_guest_wrapper_unchanged','rapid_switch_converged','later_navigation_pass'):
  require(data.get(key) is True,'Missing same-session boundary: '+key)
 require(data.get('physical_device_execution') is False,'Incorrect runtime scope')
 require(type(data.get('transition_count')) is int and data['transition_count']==len(TRANSITIONS),'Wrong transition count')
 require(type(data.get('presented_sheet_transitions')) is int and data['presented_sheet_transitions']==4,'Missing presented sheet transitions')
 records=data.get('records');require(type(records) is list and len(records)==len(TRANSITIONS),'Missing live records')
 catalog=read(root/'Resources/Localizable.xcstrings')['strings']['Checking']['localizations']
 for language,row in zip(TRANSITIONS,records):
  require(type(row) is dict and set(row)=={'language','visible_string','direction','chrome_count'},'Invalid live record closure')
  require(row['language']==language and row['visible_string']==catalog[language]['stringUnit']['value'],'Stale or wrong live string')
  require(row['direction']==('rtl' if language=='he' else 'ltr'),'Wrong recorded native direction')
  require(type(row['chrome_count']) is int and row['chrome_count']>=3,'Missing native chrome')

def run(sources,output):
 sources=sources.resolve(strict=True);root=(sources/'LiveContainer').resolve(strict=True)
 require(output.is_dir() and not output.is_symlink(),'Owned evidence directory required');output=output.resolve(strict=True)
 work=output/'language-switch';require(not work.exists(),'Fresh language evidence required');work.mkdir()
 graph=load_candidate_graph();require(command(['git','-C',root,'rev-parse','HEAD']).strip()==graph['owners']['LiveContainer'][1],'Wrong LC source owner')
 before=source_hashes(root);recovery_before=recovery_hashes(sources)
 report={'schema':'live-language-switch-v1','status':'FAILED','run_identity':identity(),'source_graph':graph['owners'],'source_sha256':before,'refresh_recovery_source_sha256':recovery_before,'physical_device_execution':False,'simulator_execution':False}
 receipt=output/'language-switch-tests.json'
 udid=None
 try:
  require(platform.system()=='Darwin','Actual Apple UIKit/SwiftUI runtime requires macOS with installed iOS Simulator')
  recovery_log=work/'refresh-recovery-tests.log'
  with recovery_log.open('x') as stream:
   recovered=subprocess.run([sys.executable,'-B',sources/'SideStore/tests/refresh_recovery/test_ownerless_snapshot.py'],cwd=work,env={**os.environ,'REQUIRE_SWIFT_REFRESH_RECOVERY_TESTS':'1','LIVE_CONTAINER_SOURCE_ROOT':str(root)},stdout=stream,stderr=subprocess.STDOUT,timeout=180)
  source_test_result(recovered,recovery_log,'refresh recovery')
  recovery_text=recovery_log.read_text();require(re.search(r'^OK\s*$',recovery_text,re.M) is not None and re.search(r'^(?:FAILED|OK \()',recovery_text,re.M) is None,'Refresh recovery tests skipped or failed')
  report['refresh_recovery_test_log_sha256']=sha(recovery_log)
  root_log=work/'root-tests.log'
  with root_log.open('x') as stream:
   rooted=subprocess.run([sys.executable,'-B',root/'tests/test_live_language_root.py'],cwd=work,stdout=stream,stderr=subprocess.STDOUT,timeout=180)
  source_test_result(rooted,root_log,'language root')
  root_text=root_log.read_text();require(re.search(r'^OK\s*$',root_text,re.M) is not None and re.search(r'^(?:FAILED|OK \()',root_text,re.M) is None,'Language root tests skipped or failed')
  report['source_root_test_log_sha256']=sha(root_log)
  state_log=work/'state-tests.log'
  with state_log.open('x') as stream:
   tested=subprocess.run([sys.executable,'-B',root/'tests/test_live_language_state.py'],cwd=work,stdout=stream,stderr=subprocess.STDOUT,timeout=180)
  source_test_result(tested,state_log,'language state')
  state_text=state_log.read_text();require(re.search(r'^OK\s*$',state_text,re.M) is not None and re.search(r'^(?:FAILED|OK \()',state_text,re.M) is None,'Language state tests skipped or failed')
  report['source_state_test_log_sha256']=sha(state_log)
  settings=(root/SOURCES[0]).read_text();localization=(root/SOURCES[1]).read_text()
  require('// V3_LIVE_HOST_LANGUAGE_BEGIN' in settings,'Missing reviewed live state/bridge')
  generated=localization+'\n'+settings+'\n'+(HERE/'SimulatorFixture.swift').read_text()
  source=work/'main.swift';source.write_text(generated)
  inventory_text=command(['xcrun','simctl','list','-j'])
  (work/'simulator-inventory.json').write_text(inventory_text)
  catalog=json.loads(inventory_text,object_pairs_hook=strict)
  runtime,device=available_device(catalog)
  sdk=command(['xcrun','--sdk','iphonesimulator','--show-sdk-path']).strip()
  architecture=platform.machine();require(architecture in ('arm64','x86_64'),'Unsupported simulator host architecture')
  app=work/'LanguageSwitchFixture.app';app.mkdir()
  bundle='org.nrg.language-switch-fixture'
  (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':bundle,'CFBundleName':'LanguageSwitchFixture','CFBundleExecutable':'LanguageSwitchFixture','CFBundlePackageType':'APPL','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'16.0','LSRequiresIPhoneOS':True,'UIDeviceFamily':[1],'UILaunchScreen':{}}))
  catalog_strings=read(root/'Resources/Localizable.xcstrings')['strings']
  for language_code in ('en','he','ru','zh-Hans','zh-Hant'):
   directory=app/(language_code+'.lproj');directory.mkdir()
   strings={key:row['localizations'][language_code]['stringUnit']['value'] for key,row in catalog_strings.items() if 'stringUnit' in row['localizations'][language_code]}
   (directory/'Localizable.strings').write_bytes(plistlib.dumps(strings))

  compile_args=['xcrun','--sdk','iphonesimulator','swiftc','-parse-as-library','-sdk',sdk,'-target',architecture+'-apple-ios16.0-simulator',source,'-o',app/'LanguageSwitchFixture']
  (work/'compile.log').write_text(command(compile_args,timeout=180))
  # Ad-hoc signing is scoped to this synthetic fixture; production IPA untouched.
  (work/'codesign.log').write_text(command(['/usr/bin/codesign','--force','--sign','-',app]))
  udid=command(['xcrun','simctl','create','NRG-LanguageSwitch-'+os.environ['GITHUB_RUN_ID'],device['identifier'],runtime['identifier']]).strip()
  require(re.fullmatch('[A-Fa-f0-9-]{36}',udid),'Invalid created simulator identity')
  command(['xcrun','simctl','boot',udid]);command(['xcrun','simctl','bootstatus',udid,'-b'],timeout=180)
  command(['xcrun','simctl','install',udid,app])
  container=Path(command(['xcrun','simctl','get_app_container',udid,bundle,'data']).strip()).resolve(strict=True)
  def launch_result(arguments,filename,timeout):
   result=container/'Documents'/filename
   require(not result.exists() and not result.is_symlink(),'Fresh simulator result required')
   launch=command(['xcrun','simctl','launch','--terminate-running-process',udid,bundle,*arguments])
   (work/(filename+'.launch.log')).write_text(launch)
   deadline=time.monotonic()+timeout
   while not result.is_file() and time.monotonic()<deadline:time.sleep(.2)
   require(result.is_file() and not result.is_symlink(),'Simulator fixture did not complete: '+filename)
   return result
  # Same compiled production helpers + exact initially failing fixture boundary.
  # Counterfactual observations do not qualify or waive the default assertions.
  report['counterfactual_sha256']={}
  for mode,label in [('--counterfactual-no-bridge','without'),('--counterfactual-with-bridge','with')]:
   result=launch_result([mode],'counterfactual-'+label+'-bridge.json',45)
   (work/result.name).write_bytes(result.read_bytes())
   observation=read(result)
   require(observation.get('status')=='OBSERVED_COUNTERFACTUAL_NOT_QUALIFICATION','Missing counterfactual observation')
   require(observation.get('bridge_enabled') is (label=='with'),'Counterfactual bridge mode differs')
   report['counterfactual_sha256'][result.name]=sha(work/result.name)
  result=launch_result([],'language-switch-result.json',120)
  payload=result.read_bytes();(work/'simulator-result.json').write_bytes(payload)
  data=json.loads(payload,object_pairs_hook=strict)
  validate_runtime_result(data,root)
  report.update(status='PASS_SAME_SESSION_IOS_SIMULATOR_NOT_PHYSICAL_DEVICE',simulator_execution=True,simulator={'runtime':runtime,'device_type':device,'udid':udid,'sdk':sdk},fixture_result_sha256=sha(work/'simulator-result.json'),generated_source_sha256=sha(source),fixture_source_sha256=sha(HERE/'SimulatorFixture.swift'),transition_count=data['transition_count'])
  require(source_hashes(root)==before and recovery_hashes(sources)==recovery_before,'Source changed during runtime test')
 finally:
  if udid:
   # Only this invocation's created simulator may be shut down/deleted.
   for action in ('shutdown','delete'):
    try:command(['xcrun','simctl',action,udid],timeout=45)
    except subprocess.SubprocessError as e:
     detail=getattr(e,'output','') or type(e).__name__
     if isinstance(detail,bytes):detail=detail.decode('utf-8','replace')
     (work/(action+'-cleanup.log')).write_text(detail)
     report['status']='FAILED_SIMULATOR_CLEANUP'
  receipt.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 verify_receipt(receipt,sources,report['run_identity'])

def verify_receipt(path,sources,native_identity):
 require(path.is_file() and not path.is_symlink(),'Language receipt missing/linked');d=read(path)
 require(d['schema']=='live-language-switch-v1' and d['status']=='PASS_SAME_SESSION_IOS_SIMULATOR_NOT_PHYSICAL_DEVICE','Language tests did not pass')
 require(d['run_identity']==native_identity,'Language test run identity differs')
 require(d['source_graph']==load_candidate_graph()['owners'],'Language source graph differs')
 root=(sources/'LiveContainer').resolve(strict=True)
 require(d['source_sha256']==source_hashes(root),'Language source hashes differ')
 result=path.parent/'language-switch/simulator-result.json'
 require(result.is_file() and not result.is_symlink() and sha(result)==d['fixture_result_sha256'],'Simulator evidence differs')
 require(d['simulator_execution'] is True and d['physical_device_execution'] is False,'Incorrect runtime scope')
 require(d['fixture_source_sha256']==sha(HERE/'SimulatorFixture.swift'),'Fixture source differs')
 require(d['source_state_test_log_sha256']==sha(path.parent/'language-switch/state-tests.log'),'Language source test log differs')
 require(d['source_root_test_log_sha256']==sha(path.parent/'language-switch/root-tests.log'),'Language root test log differs')
 require(d['refresh_recovery_source_sha256']==recovery_hashes(sources),'Refresh recovery source differs')
 require(d['refresh_recovery_test_log_sha256']==sha(path.parent/'language-switch/refresh-recovery-tests.log'),'Refresh recovery test log differs')
 validate_runtime_result(read(result),root)
 require(set(d['counterfactual_sha256'])=={'counterfactual-without-bridge.json','counterfactual-with-bridge.json'},'Counterfactual receipt closure differs')
 for name,digest in d['counterfactual_sha256'].items():
  observation=path.parent/'language-switch'/name
  require(observation.is_file() and not observation.is_symlink() and sha(observation)==digest,'Counterfactual bytes differ')
  data=read(observation)
  require(data.get('status')=='OBSERVED_COUNTERFACTUAL_NOT_QUALIFICATION' and data.get('bridge_enabled') is (name=='counterfactual-with-bridge.json'),'Counterfactual scope differs')
 return d

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.sources,a.output)
