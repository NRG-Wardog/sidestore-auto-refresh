"""Source-level scheduling checks; not native UI evidence."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/qualification/language'))
from pathlib import Path as _CIPath
from pathlib import Path
import hashlib
import sys
sys.path.insert(0,str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job

import unittest
ROOT=(_CIPath(__file__).resolve().parents[3])
class WorkflowTests(unittest.TestCase):
 def test_native_workflow_retains_tests_and_failure_gates(self):
  new=(ROOT/'.github/workflows/native-build.yml').read_text()
  for name in ('startup_tests','owned_oda_tests','issue_tests','upstream_tests','minimuxer_tests','vpn_tests','russian_tests','rust_tests'):
   self.assertIn('id: '+name,new)
  self.assertIn('workflow_dispatch:',new)
  self.assertIn("github.event_name == 'workflow_dispatch'",new)
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
  # Freeze the reviewed unchanged native command blocks without retaining a dead workflow.
  expected = {'Compile unsigned LiveContainer app and extensions': '52ad0c456a4070f8e9ced43346407a05bf885218dcccd3332e50731390158c1c', 'Compile unsigned SideStore app and extensions': '9b305963c7078f2fc3934f931645039aa29654cc0486fca9203a419567f3f428'}
  for title, digest in expected.items():
   block=new.split('      - name: '+title+'\n')[1].split('      - name: ',1)[0]
   self.assertEqual(hashlib.sha256(block.encode()).hexdigest(),digest)
 def test_focused_runtime_has_no_native_rebuild_or_delivery(self):
  text=workflow_job(ROOT/'.github/workflows/focused-diagnostics.yml', 'language')
  self.assertIn("inputs.suite == 'language'",text)
  self.assertIn('record.py acquire',text)
  self.assertIn('verify_candidate_graph.py',text)
  self.assertIn('run_language_switch_tests.py',text)
  self.assertNotIn('package_candidate.py',text)
  self.assertNotIn('cargo ',text)
  self.assertNotIn('brew ',text)
  self.assertNotIn('continue-on-error',text)
  self.assertIn('evidence/language-switch/*.json',text)
if __name__=='__main__':unittest.main(verbosity=2)
