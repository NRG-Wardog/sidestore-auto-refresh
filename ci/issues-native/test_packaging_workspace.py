"""Actual packaging orchestration with inert products and fake external tools."""
import json,plistlib,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
import packaging_workspace as W
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import package_livecontainer_combined as P
class WorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.source=self.base/'source';self.source.mkdir();self.work=self.base/'work';self.output=self.base/'output.ipa'
  for name in W.ASSETS:
   path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'controlled asset\n')
  (self.source/'.github/build_github.sh').write_bytes((ROOT/'tests/fixtures/upstream-packaging/433dd73-build_github.sh.txt').read_bytes())
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
  self.assertEqual(self.output.read_bytes(),b'inert final archive');self.assertEqual(W.inventory(self.host),self.host_before);self.assertEqual(W.inventory(self.side),self.side_before)
  for name in ('combined.xcarchive','patched-side','PatchedSideStore.ipa','verified-dylibify','combined-build.sh','Payload','tmp','LiveContainer+SideStore.ipa'):self.assertTrue((self.work/name).exists());self.assertFalse((self.source/name).exists())
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
  text=(ROOT/'ci/issues-native/package_candidate.py').read_text()
  self.assertIn('workspace_receipt = run_isolated_packager(',text)
  self.assertNotIn("'--root', 'sources/LiveContainer'",text)
  self.assertLess(text.index('workspace_receipt = run_isolated_packager('),text.rindex('verify_multitask('))
  self.assertLess(text.rindex('verify_multitask('),text.index("Path('diagnostic/provenance.json').write_text"))
  workflow=(ROOT/'.github/workflows/cold-vpn-native.yml').read_text();self.assertLess(workflow.index('test_packaging_workspace.py'),workflow.index('id: livecontainer_build'))
if __name__=='__main__':unittest.main()
