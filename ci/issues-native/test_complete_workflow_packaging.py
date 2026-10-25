"""Executable controls for complete-candidate workflow wiring, not native proof."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_workflow_failure_collection import WORKFLOW, steps, simulate, FINAL


def block(label):
    text = WORKFLOW.read_text()
    start = text.index("<<'" + label + "'")
    start = text.index('\n', start) + 1
    end = text.index('          ' + label + '\n', start)
    return textwrap.dedent(text[start:end])


class CompletePackagingWorkflowTests(unittest.TestCase):
    def test_complete_branch_only_and_size_controls_required(self):
        text, rows = steps()
        self.assertIn("branches: ['integration/complete-issues-oct25']", text)
        self.assertIn("github.ref == 'refs/heads/integration/complete-issues-oct25'", text)
        self.assertNotIn("refs/heads/integration/issues-oct25'", text)
        size = next(row for row in rows if row['name'] == 'Verify same-run size optimizer controls without skips')
        self.assertEqual(size['condition'], 'success()')
        for name in ('test_macho_stab_guard.py','test_experiment_strip_stabs.py','test_native_size_optimization.py',
                     'test_combined_packaging.py','test_ipa_size_report.py'):
            self.assertIn(name, size['text'])
        self.assertIn('tests.countTestCases() == 0', size['text'])
        self.assertIn('not result.wasSuccessful() or result.skipped', size['text'])
        self.assertLess(rows.index(size), next(i for i,row in enumerate(rows) if row['name'].startswith('Package same-run')))

    def test_retained_diagnostics_only_success_and_failures_never_publish(self):
        _, rows = steps()
        row = next(row for row in rows if row['name'] == 'Retain original IPA and matching debug symbols separately')
        self.assertEqual(row['condition'], 'success()')
        self.assertIn('path: size-diagnostics/', row['text'])
        for failure in ('coverage_settings', 'Verify same-run size optimizer controls without skips',
                        'Package same-run reviewed candidate without public release'):
            states, failed = simulate(rows, (failure,))
            self.assertTrue(failed)
            self.assertEqual(states[row['name']], 'skipped')
            self.assertEqual(states['Upload experimental candidate IPA and provenance'], 'skipped')
            if failure == 'coverage_settings':
                self.assertEqual(states['livecontainer_build'], 'success')
                self.assertEqual(states['sidestore_build'], 'success')
        self.assertNotIn('cp -R package-evidence', WORKFLOW.read_text())

    def test_actual_failure_collector_only_regular_json_logs_from_alternate_alias_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            alias = root/'alias'; actual = root/'actual'; actual.mkdir(); alias.symlink_to(actual, target_is_directory=True)
            source = alias/'package-evidence'; source.mkdir()
            for name in ('partial.ipa','symbols.zip','tool.py','secret.txt','valid.json','tool.log'):
                (source/name).write_bytes(b'controlled fixture')
            (root/'outside.json').write_bytes(b'not allowed')
            (source/'linked.json').symlink_to(root/'outside.json')
            (source/'nested').mkdir(); (source/'nested/check.log').write_bytes(b'nested')
            result = subprocess.run([sys.executable, '-c', block('PY_PACKAGE_EVIDENCE')], cwd=alias,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            target = alias/'evidence/candidate-package'
            self.assertEqual({p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()},
                             {'valid.json','tool.log','nested/check.log'})
            self.assertTrue(target.samefile(actual/'evidence/candidate-package'))

    def test_actual_exit_trap_preserves_failed_exit_and_does_not_upload_binaries(self):
        text = WORKFLOW.read_text()
        start = text.index('          collect_package_evidence() {')
        end = text.index('          trap collect_package_evidence EXIT', start) + len('          trap collect_package_evidence EXIT')
        shell = textwrap.dedent(text[start:end])
        for status in (0,7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); (root/'package-evidence').mkdir()
                (root/'package-evidence/partial.ipa').write_bytes(b'not publishable')
                result = subprocess.run(['bash','-c', 'set -euo pipefail\n'+shell+'\nexit '+str(status)],
                                        cwd=root,capture_output=True,text=True)
                self.assertEqual(result.returncode,status,result.stderr)
                target=root/'evidence/candidate-package'
                self.assertEqual(json.loads((target/'package.exit-code.json').read_text()),{'exit_code':status})
                self.assertFalse(list(target.rglob('*.ipa')))

    def test_effective_coverage_receipt_has_only_whitelisted_settings_and_no_behavior_override(self):
        _, rows = steps()
        probe = next(row for row in rows if row.get('id') == 'coverage_settings')
        self.assertIn('-showBuildSettings -json',probe['text'])
        self.assertNotRegex(probe['text'], r'(?:COVERAGE|PROGRAM_FLOW_ARCS|TEST_COVERAGE_FILES)=(?:YES|NO)')
        code = block('PY_COVERAGE')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'evidence').mkdir()
            settings={'CONFIGURATION':'Release','CLANG_ENABLE_CODE_COVERAGE':'YES',
                      'OTHER_SECRET_SETTING':'must not appear','ENABLE_TESTABILITY':'NO'}
            (root/'LiveContainer-release-settings.json').write_text(json.dumps([{'target':'LiveContainerSwiftUI','buildSettings':settings}]))
            env={**os.environ,'RUNNER_TEMP':str(root),'GITHUB_SHA':'test','GITHUB_RUN_ID':'17','GITHUB_RUN_ATTEMPT':'2'}
            result=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads((root/'evidence/LiveContainer-release-coverage-settings.json').read_text())
            self.assertFalse(report['behavior_changed'])
            self.assertNotIn('OTHER_SECRET_SETTING',json.dumps(report))
            self.assertEqual(report['targets'][0]['settings']['CLANG_ENABLE_CODE_COVERAGE'],'YES')
            self.assertEqual(len(report['targets'][0]['settings']),6)


if __name__ == '__main__': unittest.main(verbosity=2)
