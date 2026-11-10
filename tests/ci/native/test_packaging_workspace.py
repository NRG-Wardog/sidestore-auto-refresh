"""Actual packaging orchestration with inert products and fake external tools."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import hashlib,json,os,plistlib,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest import mock
from ci.packaging.native import packaging_workspace as W
ROOT=(_CIPath(__file__).resolve().parents[3])
sys.path.insert(0,str(ROOT/'scripts'))
import package_livecontainer_combined as P
from packaging_adapters import CURRENT_OWNER_PACKAGING_RULES, CURRENT_OWNER_PACKAGING_SHA256, patch_surface
REAL_RUN = subprocess.run
class WorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.source=self.base/'source';self.source.mkdir();self.work=self.base/'work';self.output=self.base/'output.ipa'
  for name in W.ASSETS:
   path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'controlled asset\n')
  (self.source/'.github/build_github.sh').write_bytes((ROOT/'tests/fixtures/upstream-packaging/12e77fad-build_github.sh.txt').read_bytes())
  self.host=self.base/'LiveContainer.app';self.side=self.base/'SideStore.app'
  for product in (self.host,self.side):product.mkdir();(product/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':product.stem}));(product/product.stem).write_bytes(b'inert binary')
  generated=self.source/'GeneratedDependencies/OpenSSL35';generated.mkdir(parents=True);(generated/'synthetic-input.bin').write_bytes(b'untouched generated input')
  self.script=ROOT/'scripts/package_livecontainer_combined.py';self.before=W.inventory(self.source);self.invocations=[]
  self.host_before=W.inventory(self.host);self.side_before=W.inventory(self.side)
 def tearDown(self):self.temp.cleanup()
 def fake_tool(self,args,**kw):
  self.invocations.append(args)
  if args[0]==sys.executable:
   values={args[i]:Path(args[i+1]) for i in range(2,len(args),2)}
   self.assertEqual(values['--root'],self.work);self.assertFalse(self.work.is_relative_to(self.source))
   P.package(values['--root'],values['--host'],values['--side'],values['--output'])
  elif args[0]=='zip':Path(args[2]).write_bytes(b'inert side archive')
  elif args[0]=='bash':
   self.assertEqual(kw['cwd'],self.work)
   REAL_RUN(['bash','-n',str(args[1])],check=True)
   for name in W.ASSETS:self.assertEqual((self.work/name).read_bytes(),(self.source/name).read_bytes())
   for directory in ('Payload','tmp'):(self.work/directory).mkdir()
   (self.work/'LiveContainer+SideStore.ipa').write_bytes(b'inert final archive')
  else:raise AssertionError('Unexpected external execution '+repr(args))
  return subprocess.CompletedProcess(args,0)
 def execute(self):
  def converter(path):path.write_bytes(b'inert converter');return {'synthetic':True}
  with mock.patch.object(W.subprocess,'run',side_effect=self.fake_tool),mock.patch.object(P,'build_dylibify',side_effect=converter),mock.patch.object(P,'optimize_ipa_size',return_value={'synthetic':True}),mock.patch.object(P,'verify',return_value={'synthetic':True}):
   return W.run(self.source,self.host,self.side,self.output,self.work,self.script)
 def test_actual_packager_writes_only_isolated_workspace(self):
  receipt=self.execute();self.assertTrue(receipt['source_unchanged']);self.assertEqual(set(receipt['asset_closure']),set(W.ASSETS));self.assertEqual(W.inventory(self.source),self.before)
  verification=json.loads(self.output.with_suffix('.verification.json').read_text());self.assertEqual(verification['upstream_packaging_sha256'],CURRENT_OWNER_PACKAGING_SHA256);self.assertEqual(verification['packaging_adapter_rules'],patch_surface(CURRENT_OWNER_PACKAGING_RULES))
  self.assertEqual(self.output.read_bytes(),b'inert final archive');self.assertEqual(W.inventory(self.host),self.host_before);self.assertEqual(W.inventory(self.side),self.side_before)
  for name in ('combined.xcarchive','patched-side','PatchedSideStore.ipa','verified-dylibify','combined-build.sh','Payload','tmp','LiveContainer+SideStore.ipa'):self.assertTrue((self.work/name).exists());self.assertFalse((self.source/name).exists())
 def test_unknown_current_recipe_rejected_before_shell_or_output(self):
  recipe=self.source/'.github/build_github.sh';recipe.write_text(recipe.read_text()+'\n# unreviewed recipe\n')
  before=W.inventory(self.source)
  with self.assertRaisesRegex(ValueError,'Upstream packaging anchor changed'):self.execute()
  self.assertFalse(any(args[0]=='bash' for args in self.invocations));self.assertFalse(self.output.exists())
  self.assertEqual(W.inventory(self.source),before)

 def test_pinned_owner_recipe_executes_with_inert_tools_and_real_archive_operations(self):
  # Read and compare the acquired production owner's exact Git blob. The native
  # workflow keeps owners beside harness/, while product tests set the variable.
  owner=Path(os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE',Path.cwd()/'sources/LiveContainer'))
  pin=json.loads((ROOT/'ci/build/candidate-graph.json').read_text())['owners']['LiveContainer'][1]
  self.assertEqual(subprocess.check_output(['git','-C',str(owner),'rev-parse','HEAD'],text=True).strip(),pin)
  recipe=subprocess.check_output(['git','-C',str(owner),'show',pin+':.github/build_github.sh'])
  self.assertEqual((owner/'.github/build_github.sh').read_bytes(),recipe)
  self.assertEqual((self.source/'.github/build_github.sh').read_bytes(),recipe)
  self.assertEqual(hashlib.sha256(recipe).hexdigest(),CURRENT_OWNER_PACKAGING_SHA256)
  def put(root,name,data=b'inert fixture'):
   path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  for product in (self.host,self.side):
   put(product,'_CodeSignature/CodeResources')
   put(product,'_CodeSignature/nested/_CodeSignature/CodeResources')
  put(self.host,'Frameworks/SideStoreSupport.framework/SideStoreSupport')
  (self.host/'PlugIns').mkdir()
  put(self.side,'PlugIns/AltWidgetExtension.appex/AltWidgetExtension')
  put(self.side,'PlugIns/AltWidgetExtension.appex/_CodeSignature/CodeResources')
  put(self.side,'PlugIns/AltWidgetExtension.appex/Info.plist',plistlib.dumps({'CFBundleExecutable':'AltWidgetExtension'}))
  for name in ('Intents.intentdefinition','ViewApp.intentdefinition'):put(self.side,name)
  put(self.side,'Metadata.appintents/extract.actionsdata',b'9SideStore20RefreshAllAppsIntentV 9SideStore26RefreshAllAppsWidgetIntentV')
  # Only native signing, converter/entitlement callbacks, PlistBuddy and BSD sed
  # are test doubles. Bash plus cp/mv/find/rm/zip/unzip execute the complete
  # adapted recipe, including the cleanup and both real archive commands.
  tools=self.base/'tools';tools.mkdir();tool_log=self.base/'tool-calls.jsonl'
  fake=('#!'+sys.executable+'\n'+
        'import json,os,pathlib,sys\n'
        'name=pathlib.Path(sys.argv[0]).name\n'
        'with open(os.environ["PACKAGING_TEST_LOG"],"a") as log: log.write(json.dumps([name,*sys.argv[1:]])+"\\n")\n'
        'if name=="sed":\n'
        '    _,old,new,_=sys.argv[3].split("/")\n'
        '    path=pathlib.Path(sys.argv[4]); path.write_text(path.read_text().replace(old,new))\n')
  for name in ('ldid','python3','PlistBuddy','sed'):
   path=tools/name;path.write_text(fake);path.chmod(0o755)
  def converter(path):
   path.write_text('#!/bin/sh\ncp "$1" "$2"\n');path.chmod(0o755);return {'synthetic':True}
  def run(args,**kw):
   if args[0]==sys.executable:
    values={args[i]:Path(args[i+1]) for i in range(2,len(args),2)}
    P.package(values['--root'],values['--host'],values['--side'],values['--output'])
    return subprocess.CompletedProcess(args,0)
   if args[0]=='bash':
    script=Path(args[1]);adapted=script.read_text();self.assertEqual(adapted,P.adapt(recipe.decode()))
    REAL_RUN(['bash','-n',str(script)],check=True)
    # Redirect only the fixed absolute platform-tool path for this Linux/macOS
    # harness. Keep the real generated script unmodified for receipt identity.
    runnable=self.work/'inert-tools-build.sh'
    runnable.write_text(adapted.replace('/usr/libexec/PlistBuddy','"'+str(tools/'PlistBuddy')+'"'))
    env=dict(kw['env'],PATH=str(tools)+os.pathsep+os.environ['PATH'],PACKAGING_TEST_LOG=str(tool_log))
    return REAL_RUN(['bash',str(runnable)],cwd=kw['cwd'],env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   return REAL_RUN(args,**kw)
  before_host=W.inventory(self.host);before_side=W.inventory(self.side)
  with mock.patch.object(W.subprocess,'run',side_effect=run),mock.patch.object(P,'build_dylibify',side_effect=converter),mock.patch.object(P,'optimize_ipa_size',return_value={'synthetic':True}),mock.patch.object(P,'verify',return_value={'synthetic':True}):
   receipt=W.run(self.source,self.host,self.side,self.output,self.work,self.script)
  self.assertTrue(receipt['source_unchanged']);self.assertEqual(W.inventory(self.source),self.before)
  self.assertEqual(W.inventory(self.host),before_host);self.assertEqual(W.inventory(self.side),before_side)
  self.assertFalse((self.work/'.zsign_cache').exists());self.assertFalse(list((self.work/'Payload').rglob('_CodeSignature')))
  self.assertEqual(self.output.read_bytes(),(self.work/'LiveContainer+SideStore.ipa').read_bytes())
  verification=json.loads(self.output.with_suffix('.verification.json').read_text())
  self.assertEqual(verification['upstream_packaging_sha256'],CURRENT_OWNER_PACKAGING_SHA256)
  self.assertEqual(verification['packaging_adapter_rules'],patch_surface(CURRENT_OWNER_PACKAGING_RULES))
  self.assertEqual(verification['packaging_adapter_sha256'],hashlib.sha256((ROOT/'scripts/packaging_adapters.py').read_bytes()).hexdigest())
  with zipfile.ZipFile(self.output) as archive:
   names=archive.namelist();host='Payload/LiveContainer.app/';side=host+'Frameworks/SideStoreApp.framework/'
   self.assertTrue(all(name.startswith('Payload/') for name in names))
   self.assertFalse(any('_CodeSignature' in name for name in names))
   self.assertIn(host+'LiveContainer',names);self.assertIn(side+'SideStore',names)
   self.assertIn(host+'Frameworks/SideStoreSupport.framework/SideStoreSupport',names)
   self.assertIn(host+'PlugIns/LiveWidgetExtension.appex/LiveWidgetExtension',names)
   self.assertNotIn(side+'Intents.intentdefinition',names);self.assertNotIn(side+'ViewApp.intentdefinition',names)
   self.assertNotIn(side+'Metadata.appintents/extract.actionsdata',names)
   self.assertEqual(archive.read(host+'Metadata.appintents/extract.actionsdata'),b'16SideStoreSupport20RefreshAllAppsIntentV 16SideStoreSupport26RefreshAllAppsWidgetIntentV')
  with zipfile.ZipFile(self.work/'LiveContainer.ipa') as archive:
   self.assertFalse(any('SideStoreSupport.framework' in name or 'SideStoreApp.framework' in name for name in archive.namelist()))
  calls=[json.loads(line) for line in tool_log.read_text().splitlines()]
  callbacks=[row[2] for row in calls if row[0]=='python3']
  self.assertEqual(callbacks,['--validate-dylibify-input','--verify-dylibify','--prepare-entitlements'])
  self.assertEqual(len([row for row in calls if row[0]=='ldid']),2)

 def test_unknown_source_file_during_packaging_rejected(self):
  original=self.fake_tool
  def tamper(args,**kw):
   result=original(args,**kw)
   if args[0]=='bash':(self.source/'unexpected.swift').write_text('unreviewed')
   return result
  self.fake_tool=tamper
  with self.assertRaisesRegex(ValueError,'Source changed'):self.execute()
 def test_source_asset_mutation_rejected(self):
  original=self.fake_tool
  def tamper(args,**kw):
   result=original(args,**kw)
   if args[0]=='bash':(self.source/'entitlements.xml').write_text('changed')
   return result
  self.fake_tool=tamper
  with self.assertRaisesRegex(ValueError,'Source changed'):self.execute()
 def test_scratch_asset_mutation_rejected(self):
  original=self.fake_tool
  def tamper(args,**kw):
   result=original(args,**kw)
   if args[0]=='bash':(self.work/'entitlements.xml').write_text('changed')
   return result
  self.fake_tool=tamper
  with self.assertRaisesRegex(ValueError,'asset changed'):self.execute()
 def test_source_symlink_asset_rejected(self):
  p=self.source/'entitlements.xml';p.unlink();p.symlink_to(self.source/'.github/build_github.sh')
  with self.assertRaisesRegex(ValueError,'Linked packaging'):self.execute()
 def test_scratch_within_source_or_existing_rejected(self):
  self.work=self.source/'scratch'
  with self.assertRaisesRegex(ValueError,'outside source'):self.execute()
  self.work=self.base/'work';self.work.mkdir()
  with self.assertRaisesRegex(ValueError,'fresh'):self.execute()
 def test_output_within_source_rejected(self):
  self.output=self.source/'output.ipa'
  with self.assertRaisesRegex(ValueError,'outside source'):self.execute()
 def test_tool_failure_preserves_source_and_is_not_swallowed(self):
  self.fake_tool=lambda *a,**k:(_ for _ in ()).throw(subprocess.CalledProcessError(1,['controlled']))
  with self.assertRaises(subprocess.CalledProcessError):self.execute()
  self.assertEqual(W.inventory(self.source),self.before)
 def test_actual_entitlement_callback_reads_only_staged_asset_closure(self):
  self.execute()
  app=self.work/'entitlement-fixture.app';app.mkdir()
  paths={'':'entitlements.xml','PlugIns/LiveProcess.appex':'LiveProcess/LiveProcess.entitlements','PlugIns/ShareExtension.appex':'ShareExtension/ShareExtension.entitlements','PlugIns/LaunchAppExtension.appex':'LaunchAppExtension/LaunchAppExtension.entitlements','PlugIns/LiveWidgetExtension.appex':'.github/sidelc/LiveWidgetExtension_adhoc.xml'}
  for index,(relative,asset) in enumerate(paths.items()):
   bundle=app/relative;bundle.mkdir(parents=True,exist_ok=True);(bundle/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Fixture'+str(index),'CFBundleIdentifier':'example.fixture.'+str(index),'ALTAppGroups':['group.fixture']}))
   (self.work/asset).write_bytes(plistlib.dumps({'application-identifier':'$(AppIdentifierPrefix)$(PRODUCT_BUNDLE_IDENTIFIER)','com.apple.security.application-groups':['$(APP_GROUP_SIDESTORE)']}))
  with mock.patch.object(P.subprocess,'run') as fake_ldid:P.prepare_entitlements(self.work,app)
  self.assertEqual(fake_ldid.call_count,5)
  for args in fake_ldid.call_args_list:self.assertEqual(args.args[0][0],'ldid');self.assertTrue(args.kwargs['check'])
  self.assertEqual(W.inventory(self.source),self.before)

 def test_native_orchestration_uses_isolated_helper(self):
  text=((ROOT/'ci/packaging/native/package_candidate.py')).read_text()
  self.assertIn('workspace_receipt = run_isolated_packager(',text)
  self.assertNotIn("'--root', 'sources/LiveContainer'",text)
  self.assertLess(text.index('workspace_receipt = run_isolated_packager('),text.rindex('verify_multitask('))
  self.assertLess(text.rindex('verify_multitask('),text.index("Path('diagnostic/provenance.json').write_text"))
  workflow=(ROOT/'.github/workflows/native-build.yml').read_text();self.assertLess(workflow.index('test_packaging_workspace.py'),workflow.index('id: livecontainer_build'))
if __name__=='__main__':unittest.main()
