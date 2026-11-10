"""Maintained same-run widget controls; not native strip/runtime evidence."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import ast
from pathlib import Path
import unittest
ROOT = (_CIPath(__file__).resolve().parents[3])
WORKFLOW = (ROOT/'.github/workflows/native-build.yml').read_text()
class WidgetWorkflowTests(unittest.TestCase):
 def test_same_run_packaging_opts_in_without_identity_spoofing(self):
  command='python3 harness/ci/packaging/native/package_candidate.py --same-run-qualified-size --language-switch --cold-vpn --multitask 2>&1 | tee package-evidence/package.log'
  self.assertEqual(WORKFLOW.count(command),1)
  self.assertNotIn('--recover-fixed-native',WORKFLOW)
  for name in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'):
   self.assertNotIn('export '+name+'=',WORKFLOW)
   self.assertNotIn(name+':',WORKFLOW)
 def test_actual_macos_controls_precede_qualification_and_never_skip(self):
  start=WORKFLOW.index("          python3 - <<'PY_SAME_RUN_SIZE_TESTS'")
  end=WORKFLOW.index('          PY_SAME_RUN_SIZE_TESTS',start)
  block=WORKFLOW[start:end]
  self.assertLess(end,WORKFLOW.index('ci/qualification/size/qualify_host.py --built'))
  for guard in ("sys.platform != 'darwin'",'tests.countTestCases() == 0','not result.wasSuccessful() or result.skipped'):
   self.assertIn(guard,block)
  tree=ast.parse('\n'.join(line[10:] for line in block.splitlines()[1:]))
  loops=[n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='pattern']
  self.assertEqual(len(loops),1)
  self.assertEqual(ast.literal_eval(loops[0].iter),('test_unsigned_widget_optimization.py','test_widget_signature_policy.py','test_widget_local_symbols.py','test_widget_local_packaging_wiring.py','test_openssl_local_symbol_policy.py','test_openssl_local_candidate.py'))
 def test_widget_sidecar_is_success_only_fresh_and_separate(self):
  block=WORKFLOW.split('      - name: Retain original widget and matching debug symbols separately\n',1)[1].split('      - name:',1)[0]
  for guard in ('if: success()','path: widget-diagnostics/','if-no-files-found: error'):self.assertIn(guard,block)
  self.assertNotIn('mkdir -p widget-diagnostics',WORKFLOW)
  self.assertNotIn('path: diagnostic size-diagnostics',WORKFLOW)
  package=WORKFLOW.split('      - name: Package same-run reviewed candidate without public release\n',1)[1].split('      - name:',1)[0]
  self.assertIn('if: success()',package)
  self.assertIn('package-evidence/',package)
if __name__=='__main__':unittest.main(verbosity=2)
