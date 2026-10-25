import ast,importlib.util,types,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
def module(name):
 spec=importlib.util.spec_from_file_location('ui_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class DriverTests(unittest.TestCase):
 def test_exact_native_command_budget(self):
  tree=ast.parse((HERE/'run.py').read_text())
  method=next(n for n in ast.walk(tree)if isinstance(n,ast.FunctionDef)and n.name=='native_command')
  scope={};exec(compile(ast.Module(body=[method],type_ignores=[]),'native_command','exec'),scope)
  argv=scope['native_command'](types.SimpleNamespace(generated=Path('/tmp/one.xctestrun'),device='11111111-2222-3333-4444-555555555555',native_result=Path('/tmp/native.xcresult')))
  runtime=module('runtime')
  self.assertTrue(runtime.reviewed_ui_command(argv));self.assertEqual(runtime.command_budget(argv,690,1000),690)
  self.assertEqual(runtime.command_budget(argv,690,30),30)
  self.assertEqual(runtime.command_budget(argv,690,1000,True),240)
  changed=argv.copy();changed[-1]='-only-testing:OtherTest'
  self.assertEqual(runtime.command_budget(changed,690,1000),240)
 def test_artifact_unpinned_fails_closed(self):
  with self.assertRaisesRegex(ValueError,'pins are not established'):module('artifact').authenticate(None,None,None,None,None)
 def test_source_proof_failure_and_change(self):
  driver=module('run');events=[]
  def write(name,value):events.append(name)
  def operation():events.append('operation')
  with self.assertRaisesRegex(ValueError,'before failed'):
   driver.source_checked_execution(lambda:(_ for _ in ()).throw(ValueError('before failed')),write,operation)
  self.assertEqual(events,[])
  proofs=iter([{'tree':'a'},{'tree':'b'}])
  with self.assertRaisesRegex(ValueError,'source changed'):
   driver.source_checked_execution(lambda:next(proofs),write,operation)
  self.assertEqual(events,['fresh-source-before.json','operation','fresh-source-after.json'])
 def test_retention_and_control_guards(self):
  source=(HERE/'run.py').read_text();swift=(HERE/'HostSettingsProbeTests.swift.in').read_text()
  self.assertIn("or probe.report['retentionErrors'] or probe.report['diagnosticCaptureErrors']",source)
  self.assertIn('if commands.all_settled:super().harvest_results()',source)
  self.assertIn("matches[0].get('state')=='Shutdown'",source)
  self.assertIn('XCTAssertEqual(before, "0"',swift)
  self.assertIn('capture("preference-" + name)',swift)
  self.assertIn('XCTAssertEqual(control.value as? String, "1"',swift)
if __name__=='__main__':unittest.main()
