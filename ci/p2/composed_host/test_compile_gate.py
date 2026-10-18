from pathlib import Path
import importlib.util,unittest,tempfile,os,types
HERE=Path(__file__).resolve().parent
def load(name):
 s=importlib.util.spec_from_file_location('host_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
run=load('run');runtime=load('runtime')
class GateTests(unittest.TestCase):
 def test_exact_build_and_budget(self):
  argv=run.build_command(Path('/tmp/source'),Path('/tmp/derived'),'11111111-2222-3333-4444-555555555555')+['-resultBundlePath',Path('/tmp/build.xcresult'),'build']
  self.assertTrue(runtime.reviewed_build(argv));self.assertEqual(runtime.command_budget(argv,1500,1900),1500)
  self.assertEqual(runtime.command_budget(argv,1500,100),100);self.assertEqual(runtime.command_budget(argv,1500,1900,True),240)
  generic=argv.copy();generic[10]='generic/platform=iOS Simulator'
  malformed=argv.copy();malformed[10]='platform=iOS Simulator,id=wrong'
  for changed in [generic,malformed,argv+['CODE_SIGNING_ALLOWED=NO'],['xcodebuild','build'],argv[:-1],['bash']]:self.assertEqual(runtime.command_budget(changed,1500,1900),240)
 def test_input_seal_and_manifest(self):
  digest=run.sha(HERE/'inputs.json');data=run.verify_inputs(digest)
  self.assertIn('ci/p2/host_probe/run.py',data['files'])
  with self.assertRaises(ValueError):run.verify_inputs('0'*64)
 def test_source_identity(self):
  self.assertIn("prior.git=lambda root,*args:cmd(['git','-C',root,*args])",(HERE/'run.py').read_text())
  self.assertEqual(run.SOURCE,'e22de6df412a0977f7430b2218960ca3ee9585d5');self.assertEqual(run.TREE,'4b973e2bbf482953e298c41c7a7227cb60b30718')
class PeerLinkTests(unittest.TestCase):
 def test_exact_missing_generated_link(self):
  prior=run.load('link_contract',run.ROOT/'ci/p2/host_probe/run.py')
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);(root/'AltStore/Resources').mkdir(parents=True)
   name='AltStore/Resources/SideBackup.ipa';(root/name).symlink_to('../../build/SideBackup.ipa')
   rows={name:{'mode':'120000','type':'blob','gitObject':'54a080c4e8a5029ad8d22842c16acde4d6d01b43'}}
   fallback=lambda *args:'strict resolver'
   receipt=run.peer_link_destination(prior,root,name,rows,fallback)
   self.assertEqual(receipt,{'classification':'committed-dangling-generated-resource','target':'build/SideBackup.ipa','consumed':False})
   self.assertEqual(run.peer_link_destination(prior,root,'other',rows,fallback),'strict resolver')
   for changed in [{name:{**rows[name],'gitObject':'0'*40}},{**rows,'build/new':{'mode':'100644'}}]:
    with self.assertRaises(ValueError):run.peer_link_destination(prior,root,name,changed,fallback)
   (root/'build').mkdir();(root/'build/SideBackup.ipa').write_text('unexpected')
   with self.assertRaises(ValueError):run.peer_link_destination(prior,root,name,rows,fallback)
   (root/'build/SideBackup.ipa').unlink();(root/'build').rmdir();(root/'build').symlink_to('/tmp')
   with self.assertRaises(ValueError):run.peer_link_destination(prior,root,name,rows,fallback)
 def test_scoped_resolver_restored_on_failure(self):
  resolver=lambda *args:None;deps={'original':True}
  prior=types.SimpleNamespace(DEPENDENCIES=deps,source_link_destination=resolver)
  def fail(*args,**kwargs):
   self.assertIsNot(prior.source_link_destination,resolver)
   raise ValueError('expected')
  prior.checkout_proof=fail
  with self.assertRaises(ValueError):run.owner_proof(prior,Path('/tmp'),run.PEER)
  self.assertIs(prior.source_link_destination,resolver);self.assertIs(prior.DEPENDENCIES,deps)
if __name__=='__main__':unittest.main()
