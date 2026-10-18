from pathlib import Path
import importlib.util,unittest
HERE=Path(__file__).resolve().parent
def load(name):
 s=importlib.util.spec_from_file_location('host_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
run=load('run');runtime=load('runtime')
class GateTests(unittest.TestCase):
 def test_exact_build_and_budget(self):
  argv=run.build_command(Path('/tmp/source'),Path('/tmp/derived'))+['-resultBundlePath',Path('/tmp/build.xcresult'),'build']
  self.assertTrue(runtime.reviewed_build(argv));self.assertEqual(runtime.command_budget(argv,1500,1900),1500)
  self.assertEqual(runtime.command_budget(argv,1500,100),100);self.assertEqual(runtime.command_budget(argv,1500,1900,True),240)
  for changed in [argv+['CODE_SIGNING_ALLOWED=NO'],['xcodebuild','build'],argv[:-1],['bash']]:self.assertEqual(runtime.command_budget(changed,1500,1900),240)
 def test_input_seal_and_manifest(self):
  digest=run.sha(HERE/'inputs.json');data=run.verify_inputs(digest)
  self.assertIn('ci/p2/host_probe/run.py',data['files'])
  with self.assertRaises(ValueError):run.verify_inputs('0'*64)
 def test_source_identity(self):
  self.assertIn("prior.git=lambda root,*args:cmd(['git','-C',root,*args])",(HERE/'run.py').read_text())
  self.assertEqual(run.SOURCE,'f46a3e14c0752714144aa076355663c79a54f1a9');self.assertEqual(run.TREE,'f18b17b69ef3bdd061328c4bf4d14e8b0cc5f330')
if __name__=='__main__':unittest.main()
