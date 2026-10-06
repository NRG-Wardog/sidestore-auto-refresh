"""Explicit CI routing and no-package focused lane regression tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/select_livecontainer_lane.py'
SPEC = importlib.util.spec_from_file_location('select_livecontainer_lane', SCRIPT)
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


class SignInLaneRoutingTests(unittest.TestCase):
    def test_complete_supported_decision_table(self):
        expected = [
            ('push', router.AUDIT_BRANCH, '', 'preflight'),
            *[('push', ref, '', 'release') for ref in sorted(router.RELEASE_PUSH_BRANCHES)],
            *[('workflow_dispatch', ref, mode, lane)
              for ref in (router.AUDIT_BRANCH, 'refs/heads/main', 'refs/tags/manual-build')
              for mode, lane in (('', 'release'), ('release', 'release'), ('preflight', 'preflight'), ('input-diagnostic', 'input-diagnostic'))],
        ]
        for event, ref, mode, lane in expected:
            with self.subTest(event=event, ref=ref, mode=mode):
                self.assertEqual(router.select_lane(event, ref, mode), lane)

    def test_audit_diagnostic_selection_is_finite_and_restorable(self):
        for lane in ('preflight', 'input-diagnostic'):
            with patch.object(router, 'AUDIT_PUSH_LANE', lane):
                self.assertEqual(router.select_lane('push', router.AUDIT_BRANCH, ''), lane)
                self.assertEqual(router.select_lane('workflow_dispatch', router.AUDIT_BRANCH, ''), 'release')
        with patch.object(router, 'AUDIT_PUSH_LANE', 'release'), self.assertRaises(ValueError):
            router.select_lane('push', router.AUDIT_BRANCH, '')

    def test_unknown_inputs_and_events_fail_closed(self):
        for event, ref, mode in (
            ('pull_request', router.AUDIT_BRANCH, ''), ('', '', ''),
            ('push', 'refs/heads/main', ''), ('push', router.AUDIT_BRANCH, 'release'),
            ('push', router.AUDIT_BRANCH, 'preflight'),
            ('workflow_dispatch', router.AUDIT_BRANCH, 'Release'),
            ('workflow_dispatch', router.AUDIT_BRANCH, 'unknown'),
            ('workflow_dispatch', router.AUDIT_BRANCH, 'release\nlane=preflight'),
            ('workflow_dispatch', '', 'release'),
            ('workflow_dispatch', 'refs/heads/', 'release'),
            ('workflow_dispatch', 'refs/tags/', 'release'),
        ):
            with self.subTest(event=event, ref=ref, mode=mode), self.assertRaises(ValueError):
                router.select_lane(event, ref, mode)

    def test_empty_dispatch_form_on_selected_audit_branch_selects_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            env = dict(os.environ, CI_EVENT_NAME='workflow_dispatch', CI_REF=router.AUDIT_BRANCH,
                       CI_MODE='', GITHUB_OUTPUT=str(output))
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), 'lane=release\n')
            output.unlink()
            env['CI_MODE'] = 'unknown'
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_actual_workflow_shell_routes_empty_and_diagnostic_arguments_portably(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        step = workflow.split('      - name: Execute selected sign-in test lane\n', 1)[1].split('      - name:', 1)[0]
        body = step.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in body.splitlines() if line.startswith('          '))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'builder/scripts').mkdir(parents=True)
            (root / 'artifacts/logs').mkdir(parents=True)
            target = root / 'builder/scripts/run_p0_signin_preflight.py'
            target.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            for lane, expected in [('preflight', ['--output', 'artifacts/p0-preflight']),
                                   ('input-diagnostic', ['--output', 'artifacts/p0-preflight', '--input-diagnostic'])]:
                result = subprocess.run(['/bin/bash', '-c', script], cwd=root,
                    env=dict(os.environ, CI_SIGNIN_LANE=lane), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
            result = subprocess.run(['/bin/bash', '-c', script], cwd=root,
                env=dict(os.environ, CI_SIGNIN_LANE='unknown'), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('Unsupported sign-in lane', result.stderr)

    def test_workflow_gates_release_and_preflight_without_path_or_skip_filters(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        preflight = workflow.split('  signin-preflight:\n', 1)[1].split('  source-and-host-build:\n', 1)[0]
        release = workflow.split('  source-and-host-build:\n', 1)[1]
        route = workflow.split('  select-lane:\n', 1)[1].split('  signin-preflight:\n', 1)[0]
        self.assertIn("if: needs.select-lane.outputs.lane == 'preflight'", preflight)
        self.assertIn("if: needs.select-lane.outputs.lane == 'release'", release)
        for job in (preflight, release):
            self.assertIn('needs: select-lane', job)
        self.assertIn('runs-on: ubuntu-latest', route)
        self.assertIn("CI_MODE: ${{ github.event.inputs.mode || '' }}", route)
        self.assertIn('default: release', workflow)
        self.assertIn('type: choice', workflow)
        self.assertIn('branches: [fix/combined-refresh-build-and-runtime, fix/v3.0.3-auth-errors, fix/v3.1.0-audit]', workflow)
        self.assertNotIn('paths-ignore:', workflow)
        self.assertNotIn('skip ci', workflow.lower())
        self.assertIn('run_required_tests.py', preflight)
        self.assertIn('uses: maxim-lobanov/setup-xcode@v1.6.0', preflight)
        self.assertIn('xcode-version: "26.4"', preflight)
        self.assertIn("--pattern 'test_p0_signin*.py'", preflight)
        self.assertIn('run_p0_signin_preflight.py', preflight)
        self.assertIn('Input focus diagnostic (not acceptance)', preflight)
        self.assertIn('input-diagnostic) set -- --input-diagnostic', preflight)
        self.assertIn('Unsupported sign-in lane', preflight)
        self.assertNotIn('--input-diagnostic', release)
        self.assertIn('if: always()', preflight)
        self.assertIn('if-no-files-found: error', preflight)
        self.assertNotIn('continue-on-error:', preflight)
        for forbidden in ('verify_candidate_ipa.py', 'package_combined_ipa.py', '.ipa',
                          'build_livecontainer_host.sh', 'build_embedded_sidestore.sh'):
            self.assertNotIn(forbidden, preflight)
        self.assertIn('verify_candidate_ipa.py', release)
        self.assertIn('--require-p0-signin', release)
        self.assertIn('timeout-minutes: 210', release)


if __name__ == '__main__':
    unittest.main()
