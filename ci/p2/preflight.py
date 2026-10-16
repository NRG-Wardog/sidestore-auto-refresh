#!/usr/bin/env python3
"""Isolated helper and compiled-fixture proof. Never UI, host IPA or device admission."""
from __future__ import annotations
import argparse, hashlib, io, json, os, platform, plistlib, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = 'dba20ab374b510b094c82325ba6c425a675fe393'
TREE = '95bb96de797a65cffbe68ccb895fe8b2102441f1'
NATIVE_IDS = {
 'test_p2_dynamic_display.DynamicDisplayPolicyTests.test_native_selected_bundle_formats_and_preserves_identifiers',
 'test_p2_error_presentation.ErrorPresentationTests.test_native_error_locale_switch_persistence_and_async_snapshots',
 'test_p2_host_capture.HostCaptureBridgeTests.test_present_host_selectors_keep_preference_out_of_standard_domain_and_nil_fails_closed',
}
LANGUAGES = ('en', 'he', 'zh-Hans', 'zh-Hant', 'zh_CN', 'zh_TW', 'zh_HK')

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value):
 Path(path).parent.mkdir(parents=True, exist_ok=True)
 Path(path).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
def git(root, *args): return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()

def verify_inputs(host, approved):
 path=ROOT/'ci/p2/inputs.json'
 if digest(path)!=approved: raise ValueError('Unapproved preflight inputs')
 inputs=json.loads(path.read_text())
 if inputs['sourceCommit']!=CHECKPOINT or inputs['sourceTree']!=TREE: raise ValueError('Wrong checkpoint identity')
 if git(host,'rev-parse','HEAD')!=CHECKPOINT or git(host,'rev-parse','HEAD^{tree}')!=TREE: raise ValueError('Source checkout is not the published checkpoint')
 if git(host,'status','--porcelain','--untracked-files=all'): raise ValueError('Source checkout has local modifications')
 for name, expected in inputs['sourceFiles'].items():
  if digest(host/name)!=expected: raise ValueError('Changed source: '+name)
 for name, expected in inputs['builderFiles'].items():
  if digest(ROOT/name)!=expected: raise ValueError('Changed builder input: '+name)
 catalog=json.loads((host/'Resources/Localizable.xcstrings').read_text())
 if len(catalog['strings'])!=2088: raise ValueError('Wrong catalog coverage')
 return inputs

class RecordingResult(unittest.TextTestResult):
 def startTest(self,test):
  self.executed.append(test.id()); super().startTest(test)
 def __init__(self,*args,**kwargs): super().__init__(*args,**kwargs); self.executed=[]


def helper_tests(host, evidence):
 """Run exact sealed tests while preserving their real Swift inputs/products/logs."""
 if platform.system()!='Darwin' or not shutil.which('swiftc'): raise RuntimeError('Darwin Swift compiler required; skips forbidden')
 os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']=str(host)
 os.environ['V3_P2_REQUIRED']='1'
 os.environ['PYTHONDONTWRITEBYTECODE']='1'
 sys.path.insert(0,str(ROOT/'scripts')); sys.path.insert(0,str(ROOT/'tests'))
 retained=evidence/'helper-products'; retained.mkdir()
 original_temp=tempfile.TemporaryDirectory
 original_run=subprocess.run
 counter=0
 class RetainedTemporaryDirectory:
  def __init__(self,*args,**kwargs): self.name=tempfile.mkdtemp(prefix='native-',dir=retained)
  def __enter__(self): return self.name
  def __exit__(self,*args): return False
  def cleanup(self): pass
 def logged_run(*args,**kwargs):
  nonlocal counter
  counter+=1; stem=evidence/'helper-commands'/f'{counter:03d}'
  try:
   result=original_run(*args,**kwargs)
  except subprocess.CalledProcessError as error:
   write(stem.with_suffix('.json'),{'command':[str(x) for x in error.cmd], 'returncode':error.returncode, 'stdout':error.stdout, 'stderr':error.stderr})
   raise
  write(stem.with_suffix('.json'),{'command':[str(x) for x in result.args], 'returncode':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr})
  return result
 tempfile.TemporaryDirectory=RetainedTemporaryDirectory; subprocess.run=logged_run
 stream=io.StringIO()
 try:
  suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_p2_*.py')
  result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=RecordingResult).run(suite)
 finally:
  tempfile.TemporaryDirectory=original_temp; subprocess.run=original_run
  (evidence/'helper-tests.log').write_text(stream.getvalue()); print(stream.getvalue(),flush=True)
 report={'testsRun':result.testsRun,'executed':result.executed,'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'requiredNativeTests':sorted(NATIVE_IDS)}
 report['status']='passed' if result.wasSuccessful() and not result.skipped and NATIVE_IDS<=set(result.executed) and result.testsRun==35 else 'failed'
 write(evidence/'helper-tests.json',report)
 if report['status']!='passed': raise RuntimeError('Required P2 tests failed, skipped, missing, or count drifted')
 return report


def add_permission_variant(project, sources, host):
 """Fixture resource phase only; this does not establish real host packaging."""
 path=project/'project.pbxproj'; text=path.read_text()
 group='F30000000000000000000001'; build='F30000000000000000000002'
 declarations=[]; refs=[]
 for index,language in enumerate(LANGUAGES):
  ref=f'{0xF3100+index:024X}'; refs.append(ref)
  declarations.append(f'{ref} = {{isa = PBXFileReference; lastKnownFileType = text.plist.strings; name = "{language}"; path = "{language}.lproj/InfoPlist.strings"; sourceTree = "<group>";}};')
  target=sources/(language+'.lproj')/'InfoPlist.strings'; target.parent.mkdir()
  shutil.copyfile(host/'Resources'/(language+'.lproj')/'InfoPlist.strings',target)
 declarations += [f'{group} = {{isa = PBXVariantGroup; children = ({",".join(refs)},); name = InfoPlist.strings; sourceTree = "<group>";}};',f'{build} = {{isa = PBXBuildFile; fileRef = {group};}};']
 replacements=[('objects = {','objects = {\n'+'\n'.join(declarations)),('000000000000000000000083 = {isa = PBXGroup; children = (','000000000000000000000083 = {isa = PBXGroup; children = ('+group+','),('F20000000000000000000099 = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (','F20000000000000000000099 = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ('+build+','),('knownRegions = (en,Base,"zh-Hans","zh-Hant",he);','knownRegions = (en,Base,"zh-Hans","zh-Hant",he,zh_CN,zh_TW,zh_HK);')]
 for old,new in replacements:
  if text.count(old)!=1: raise ValueError('Fixture project anchor drift: '+old)
  text=text.replace(old,new,1)
 path.write_text(text)
 return {str(p.relative_to(project.parent)):digest(p) for p in [path,*[sources/(lang+'.lproj')/'InfoPlist.strings' for lang in LANGUAGES]]}



def verify_exact_plural_values(app, catalog):
 """Compare all compiled plural categories and text with the pinned source catalog."""
 from p2_localization_contract import PLURAL_KEYS, LANGUAGES as APP_LANGUAGES
 failures=[]; checked=0
 for language in APP_LANGUAGES:
  try:
   compiled=plistlib.loads((app/(language+'.lproj')/'Localizable.stringsdict').read_bytes())
   for key in PLURAL_KEYS:
    expected={category:row['stringUnit']['value'] for category,row in catalog['strings'][key]['localizations'][language]['variations']['plural'].items()}
    row=compiled[key]
    markers=re.findall(r'%(?:[1-9][0-9]*\$)?#@([A-Za-z_][A-Za-z0-9_]*)@',row['NSStringLocalizedFormatKey'])
    if len(markers)!=1: raise ValueError('Ambiguous plural variable: '+key)
    rule=row[markers[0]]
    actual={category:value for category,value in rule.items() if category not in ('NSStringFormatSpecTypeKey','NSStringFormatValueTypeKey')}
    if actual!=expected: raise ValueError('Compiled plural category/value mismatch: '+key)
    checked+=1
  except (KeyError,ValueError,TypeError,OSError,plistlib.InvalidFileException) as error:
   failures.append(language+': '+str(error))
 return {'status':'passed' if not failures and checked==24 else 'failed','failures':failures,'checkedLanguageRows':checked,'runtimePluralSelection':False}


def compile_fixture(host, evidence):
 import run_p0_signin_rendering as rendering
 from p2_localization_contract import verify_app
 from p2_permission_contract import verify_compiled_permissions
 count=0
 def command(*args,timeout=600):
  nonlocal count
  count+=1
  result=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout)
  write(evidence/'build-commands'/f'{count:03d}.json',{'command':[str(x) for x in args],'returncode':result.returncode})
  (evidence/'build-commands'/f'{count:03d}.log').write_text(result.stdout+'\n'+result.stderr)
  if result.returncode: raise RuntimeError(f'Build command {count} failed: see retained log')
  return result.stdout.strip()
 prepared=rendering.prepare(evidence,host/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',command)
 write(evidence/'fixture-prepare.json',prepared)
 build=evidence/'p0-build'; project=build/'P0SignIn.xcodeproj'; sources=build/'Sources'
 identity=add_permission_variant(project,sources,host)
 write(evidence/'permission-fixture-inputs.json',{'scope':'fixture-only; real host target packaging remains unproven','sha256':identity})
 command('xcodebuild','build-for-testing','-project',project,'-scheme','P0SignIn','-sdk','iphonesimulator','-destination','generic/platform=iOS Simulator','-derivedDataPath',build/'DerivedData','-jobs','2','CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO')
 app=build/'DerivedData/Build/Products/Debug-iphonesimulator/P0SignIn.app'
 localization=verify_app(app)
 exact_plurals=verify_exact_plural_values(app,json.loads((host/'Resources/Localizable.xcstrings').read_text()))
 permissions=verify_compiled_permissions(lambda name:(app/name).read_bytes(),{str(p.relative_to(app)) for p in app.rglob('*') if p.is_file()})
 write(evidence/'compiled-resource-proof.json',{'scope':'compiled fixture; no real host IPA or UI execution','localization':localization,'exactPluralValues':exact_plurals,'permissions':permissions})
 if localization['status']!='passed' or exact_plurals['status']!='passed' or permissions['status']!='passed': raise RuntimeError('Compiled fixture resources failed exact contract')
 return {'localization':localization,'exactPluralValues':exact_plurals,'permissions':permissions}



def retain_compiled_products(evidence):
 """Preserve partial or rejected compiler outputs, including failed resource gates."""
 products=evidence/'p0-build/DerivedData/Build/Products'
 if not products.is_dir():
  snapshot={'available':False,'reason':'No compiled products were produced'}
  write(evidence/'compiled-products-sha256.json',snapshot)
  return snapshot
 compiled=evidence/'compiled-products'
 shutil.copytree(products,compiled,symlinks=True)
 snapshot={'available':True,'files':{str(p.relative_to(compiled)):digest(p) for p in sorted(compiled.rglob('*')) if p.is_file() and not p.is_symlink()},'symlinks':{str(p.relative_to(compiled)):os.readlink(p) for p in sorted(compiled.rglob('*')) if p.is_symlink()}}
 write(evidence/'compiled-products-sha256.json',snapshot)
 return {'available':True,'fileCount':len(snapshot['files']),'symlinkCount':len(snapshot['symlinks'])}


def main():
 parser=argparse.ArgumentParser(); parser.add_argument('--host',type=Path,required=True); parser.add_argument('--approved-inputs-sha256',required=True); parser.add_argument('--evidence',type=Path,required=True); args=parser.parse_args()
 host=args.host.resolve(); evidence=args.evidence.resolve(); evidence.mkdir(parents=True,exist_ok=False)
 report={'scope':'isolated native helpers and compiled fixture resources only','sourceCommit':CHECKPOINT,'sourceTree':TREE,'builderCommit':git(ROOT,'rev-parse','HEAD'),'nativeUIExecution':False,'actualHostPackaging':False,'physicalDeviceExecution':False,'ipaAdmission':False,'status':'failed'}
 try:
  inputs=verify_inputs(host,args.approved_inputs_sha256)
  shutil.copyfile(ROOT/'ci/p2/inputs.json',evidence/'inputs.json')
  write(evidence/'toolchain.json',{'platform':platform.platform(),'xcode':subprocess.check_output(['xcodebuild','-version'],text=True),'swift':subprocess.check_output(['swiftc','--version'],text=True),'developerDir':os.environ.get('DEVELOPER_DIR')})
  report['helpers']=helper_tests(host,evidence)
  report['compiledFixture']=compile_fixture(host,evidence)
  report['status']='passed'
 except Exception as error:
  report['error']=str(error); raise
 finally:
  try:
   report['retainedProducts']=retain_compiled_products(evidence)
   if report['status']=='passed' and not report['retainedProducts']['available']: raise RuntimeError('Successful compilation produced no retained products')
  except Exception as error:
   report['status']='failed'; report['retentionError']=str(error)
  report['remainingGates']=['Combined P0/P1 successor rebase and provenance','Real host target compilation/packaging and IPA capability admission','UIKit alert replacement lifecycle cases','Settings terminate/relaunch persistence and runtime four-language switching','Full native plural/layout/screenshots and transient Copied observer reconciliation','Physical device acceptance','Existing scheduled payload and OS-controlled metadata language limits','Human-native translation proofreading']
  write(evidence/'summary.json',report)
 if report['status']!='passed': raise RuntimeError('Preflight evidence retention failed')
if __name__=='__main__': main()
