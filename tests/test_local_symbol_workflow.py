from pathlib import Path
import unittest
import ast

ROOT=Path(__file__).resolve().parents[1]

class LocalWorkflowTests(unittest.TestCase):
    def test_no_scope_expansion_no_retry_and_exact_two_read_attempts(self):
        workflow=(ROOT/'.github/workflows/local-symbol-diagnostic.yml').read_text()
        self.assertIn('permissions:\n  contents: read',workflow)
        self.assertNotIn('actions: read',workflow)
        self.assertNotIn('contents: write',workflow)
        self.assertEqual(workflow.count('curl --fail'),2)
        self.assertEqual(workflow.count('--retry 0 --max-time 180'),2)
        self.assertIn('/artifacts/12613142238/zip',workflow)
        self.assertIn('/artifacts/12613886029/zip',workflow)
        self.assertNotIn('continue-on-error',workflow)
    def test_no_application_build_or_product_upload(self):
        workflow=(ROOT/'.github/workflows/local-symbol-diagnostic.yml').read_text()
        self.assertNotIn('xcodebuild -project',workflow)
        self.assertNotIn('package_candidate.py',workflow)
        self.assertIn("path.suffix not in ('.json','.log')",workflow)
        self.assertIn('path: evidence/',workflow)
        self.assertNotIn('path: local-symbol-products',workflow)
        self.assertIn('result.skipped',workflow)
    def test_native_tool_calls_have_finite_timeouts(self):
        tree=ast.parse((ROOT/'scripts/experiment_local_symbols.py').read_text())
        runs=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
              and isinstance(n.func.value,ast.Name) and n.func.value.id=='subprocess' and n.func.attr=='run']
        self.assertEqual(len(runs),3)
        self.assertEqual(sorted(next(k.value.value for k in n.keywords if k.arg=='timeout') for n in runs),[30,120,120])

if __name__=='__main__':unittest.main()
