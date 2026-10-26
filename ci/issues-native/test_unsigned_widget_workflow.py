"""Workflow source controls only, not native strip/runtime evidence."""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
RECOVERY=(ROOT/'.github/workflows/recover-native-package.yml').read_text()
NORMAL=(ROOT/'.github/workflows/issues-native.yml').read_text()

class WidgetWorkflowTests(unittest.TestCase):
 def test_only_recovery_packaging_opts_in(self):
  command='python3 harness/ci/issues-native/package_candidate.py --recover-fixed-native --strip-unsigned-widget 2>&1 | tee package-evidence/package.log'
  self.assertEqual(RECOVERY.count(command),1)
  self.assertNotIn('--strip-unsigned-widget',NORMAL)
  for name in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'):
   self.assertNotIn('export '+name+'=',RECOVERY)
   self.assertNotIn(name+':',RECOVERY)
 def test_actual_macos_controls_precede_artifact_download_and_never_skip(self):
  start=RECOVERY.index("          python3 - <<'PY_WIDGET_TESTS'")
  end=RECOVERY.index('          PY_WIDGET_TESTS',start)
  block=RECOVERY[start:end]
  self.assertLess(end,RECOVERY.index('      - name: Authenticate fixed original artifacts'))
  self.assertIn("sys.platform != 'darwin'",block)
  self.assertIn('tests.countTestCases() == 0',block)
  self.assertIn('not result.wasSuccessful() or result.skipped',block)
  code='\n'.join(line[10:] for line in block.splitlines()[1:])
  tree=ast.parse(code)
  loops=[node for node in ast.walk(tree) if isinstance(node,ast.For) and isinstance(node.target,ast.Name) and node.target.id=='pattern']
  self.assertEqual(len(loops),1)
  self.assertEqual(ast.literal_eval(loops[0].iter),('test_unsigned_widget_optimization.py', 'test_widget_signature_policy.py'))
 def test_widget_sidecar_is_success_only_fresh_and_separate(self):
  block=RECOVERY.split('      - name: Retain original widget and matching debug symbols separately\n',1)[1].split('      - name:',1)[0]
  self.assertIn('if: success()',block)
  self.assertIn('path: widget-diagnostics/',block)
  self.assertIn('if-no-files-found: error',block)
  self.assertNotIn('mkdir -p widget-diagnostics',RECOVERY)
  self.assertNotIn('diagnostic size-diagnostics',RECOVERY)
  self.assertNotIn('> evidence/',RECOVERY)
  self.assertIn('package-evidence/',RECOVERY.split('Retain recovery diagnostics without changing original evidence',1)[1])
  self.assertNotIn('always()',RECOVERY)

if __name__=='__main__':unittest.main(verbosity=2)
