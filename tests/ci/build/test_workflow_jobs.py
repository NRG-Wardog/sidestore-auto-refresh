"""Source-level dispatcher isolation and fail-closed parsing checks."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/build/contracts'))
from pathlib import Path as _CIPath
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job

ROOT=(_CIPath(__file__).resolve().parents[3])
JOBS={'language':20,'multitask':22,'cold_vpn':27,'startup_upgrade':25,'refresh_truth':30}

class WorkflowJobTests(unittest.TestCase):
    def read(self,text,job):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.yml';path.write_text(text)
            return workflow_job(path,job)
    def test_selector_returns_only_requested_job_and_shared_policy(self):
        text='name: fixture\npermissions:\n  contents: read\njobs:\n  alpha:\n    name: first\n  beta:\n    name: second\n'
        selected=self.read(text,'alpha')
        self.assertIn('contents: read',selected)
        self.assertIn('name: first',selected)
        self.assertNotIn('beta:',selected)
        self.assertNotIn('name: second',selected)
        self.assertEqual(self.read(text,None),text)
    def test_missing_duplicate_and_unsupported_layout_fail_closed(self):
        for text,job in [
            ('name: empty\n','alpha'),
            ('jobs:\n  alpha:\n    name: first\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\n    name: first\n','absent'),
            ('name: fixture\njobs:\n  alpha:\n  alpha:\n','alpha'),
            ('name: fixture\njobs:\n   alpha:\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\npermissions:\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\n  bad-job:\n    run: leaked\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\n  Bad:\n    run: leaked\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\n    run:\tbad\n','alpha'),
            ('name: fixture\njobs:\n  alpha:\n','../alpha')]:
            with self.subTest(text=text,job=job),self.assertRaises(ValueError):self.read(text,job)
    def test_dispatch_is_explicit_readonly_bounded_and_not_delivery(self):
        path=ROOT/'.github/workflows/focused-diagnostics.yml'
        text=path.read_text()
        self.assertIn('workflow_dispatch:',text)
        self.assertNotIn('  push:',text)
        trigger=text.split('  pull_request:\n',1)[1].split('  workflow_dispatch:',1)[0]
        paths=[line.strip().removeprefix('- ').strip(chr(34)) for line in trigger.splitlines() if line.strip().startswith('- ')]
        self.assertEqual(paths,['.github/workflows/focused-diagnostics.yml','ci/workflow_jobs.py','tests/ci/build/test_workflow_jobs.py'])
        self.assertNotIn('refs/heads/validation/',text)
        self.assertIn('options: [language, multitask, cold_vpn, startup_upgrade, refresh_truth, all]',text)
        self.assertEqual(text.count('    runs-on: macos-26'),len(JOBS))
        for job,timeout in JOBS.items():
            with self.subTest(job=job):
                selected=workflow_job(path,job)
                expected="github.repository == 'NRG-Wardog/sidestore-auto-refresh' && (github.event_name == 'pull_request' || (github.event_name == 'workflow_dispatch' && (inputs.suite == '"+job+"' || inputs.suite == 'all')))"
                self.assertIn('    if: '+expected+'\n',selected)
                self.assertIn(f"(inputs.suite == '{job}' || inputs.suite == 'all')",selected)
                self.assertIn(f'timeout-minutes: {timeout}',selected)
                self.assertIn(f'group: focused-diagnostics-${{{{ github.ref }}}}-{job}',selected)
                self.assertIn('cancel-in-progress: false',selected)
                self.assertIn('contents: read',selected)
                self.assertIn('persist-credentials: false',selected)
                self.assertIn('ref: ${{ github.sha }}',selected)
                self.assertIn('set -euo pipefail',selected)
                self.assertNotIn('continue-on-error',selected)
                self.assertNotIn('package_candidate.py',selected)
                self.assertNotIn('xcodebuild -project',selected)
                self.assertEqual(selected.count('    steps:'),1)

if __name__=='__main__':unittest.main()
