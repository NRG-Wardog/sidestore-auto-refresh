from pathlib import Path
import unittest
class WorkflowTests(unittest.TestCase):
 def test_budget_scope_and_success_gates(self):
  text=(Path(__file__).resolve().parents[2]/'.github/workflows/zopfli-package.yml').read_text()
  self.assertIn('contents: read',text);self.assertNotIn('actions: write',text);self.assertNotIn('actions: read',text)
  self.assertIn('runs-on: ubuntu-24.04',text);self.assertIn('timeout-minutes: 30',text)
  self.assertIn('timeout --kill-after=5s 1600s python3 -B workspace/recompress.py',text)
  self.assertIn('ulimit -v 2097152',text);self.assertIn('timeout --kill-after=5s 60s /usr/bin/cc',text)
  self.assertNotIn('continue-on-error',text);self.assertNotIn('apt-get',text);self.assertNotIn('pip install',text)
  self.assertLess(text.index('test_*.py'),text.index('python3 -B ci/issue55-zopfli/acquire.py'))
  self.assertLess(text.index('workspace/independent_verify.py --root workspace 2>&1'),text.index('name: zopfli-qualified-'))
  upload=text[text.index('- name: Upload qualified'):text.index('- name: Retain bounded')]
  self.assertIn('if: success()',upload)
  evidence=text[text.index('- name: Retain bounded'):]
  self.assertIn("!cancelled() && (steps.controls.outcome == 'success' || steps.controls.outcome == 'failure')",evidence);self.assertNotIn('always()',evidence);self.assertNotIn('.ipa',evidence);self.assertNotIn('workspace/\n',evidence)
 def test_fixed_branch_and_action_pins(self):
  text=(Path(__file__).resolve().parents[2]/'.github/workflows/zopfli-package.yml').read_text()
  self.assertIn('branches: [validation/zopfli-package]',text)
  self.assertIn("github.ref == 'refs/heads/validation/zopfli-package'",text)
  self.assertIn('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',text)
  self.assertEqual(text.count('actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'),2)
  self.assertIn('persist-credentials: false',text)
if __name__=='__main__':unittest.main(verbosity=2)
