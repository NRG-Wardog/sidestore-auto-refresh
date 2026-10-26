"""Source-level scheduling checks; not native UI evidence."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
class WorkflowTests(unittest.TestCase):
 def test_native_workflow_retains_tests_and_failure_gates(self):
  old=(ROOT/'.github/workflows/issue56-native.yml').read_text()
  new=(ROOT/'.github/workflows/language-switch-native.yml').read_text()
  for name in ('startup_tests','owned_oda_tests','issue_tests','upstream_tests','minimuxer_tests','vpn_tests','russian_tests','rust_tests'):
   self.assertIn('id: '+name,new)
  self.assertIn("github.ref == 'refs/heads/validation/language-switch'",new)
  self.assertIn('id: language_switch_tests',new)
  self.assertIn("steps.language_switch_tests.outcome == 'failure'",new)
  self.assertIn("steps.startup_tests.outcome == 'failure'",new)
  self.assertNotIn('continue-on-error',new)
  for title in ('Package same-run reviewed candidate without public release','Upload experimental candidate IPA and provenance'):
   part=new.split('      - name: '+title+'\n')[1].split('      - name: ',1)[0]
   self.assertIn('        if: success()\n',part)
  self.assertLess(new.index('id: language_switch_tests'),new.index('id: livecontainer_build'))
  self.assertLess(new.index('Retain immediate language and recovery failure diagnostics'),new.index('id: owned_oda_tests'))
  immediate=new.split('      - name: Retain immediate language and recovery failure diagnostics\n')[1].split('      - name: ',1)[0]
  self.assertIn("steps.language_switch_tests.outcome == 'failure'",immediate)
  self.assertIn('evidence/language-switch/*.log',immediate)
  self.assertIn('evidence/language-switch/*.json',immediate)
  self.assertNotIn('path: evidence/',immediate)
  # Exact unchanged native command blocks are a parity guard, not proof of execution.
  for title in ('Compile unsigned LiveContainer app and extensions','Compile unsigned SideStore app and extensions'):
   self.assertEqual(old.split('      - name: '+title+'\n')[1].split('      - name: ',1)[0],new.split('      - name: '+title+'\n')[1].split('      - name: ',1)[0])
 def test_focused_runtime_has_no_native_rebuild_or_delivery(self):
  text=(ROOT/'.github/workflows/language-switch-ui.yml').read_text()
  self.assertIn("refs/heads/validation/language-switch-ui",text)
  self.assertIn('record.py acquire',text)
  self.assertIn('verify_candidate_graph.py',text)
  self.assertIn('run_language_switch_tests.py',text)
  self.assertNotIn('package_candidate.py',text)
  self.assertNotIn('cargo ',text)
  self.assertNotIn('brew ',text)
  self.assertNotIn('continue-on-error',text)
  self.assertIn('evidence/language-switch/*.json',text)
if __name__=='__main__':unittest.main(verbosity=2)
