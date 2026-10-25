"""Assert the actual workflow's finite prerequisite and failure propagation graph.

Uses only stdlib, and supports only the deliberately small condition grammar in
this workflow. This models scheduling, not native test/build execution.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/issues-native.yml'
EXPECTED = {
    'owned_oda_tests': ['source_gate'], 'issue_tests': ['source_gate'], 'upstream_tests': ['source_gate'],
    'minimuxer_tests': ['source_gate'], 'vpn_tests': ['source_gate'],
    'acquisition_tests': ['source_gate'], 'vendor_tests': ['source_gate'],
    'comprehensive_tests': ['source_gate'], 'setup_tests': ['source_gate'], 'russian_tests': ['source_gate'],
    'build_tools': ['source_gate'], 'rust_tests': ['source_gate', 'build_tools'],
    'transport_build': ['source_gate', 'build_tools'],
    'abi_inspection': ['source_gate', 'transport_build'],
    'openssl_inputs': ['source_gate'],
    'package_resolution': ['source_gate', 'abi_inspection', 'openssl_inputs'],
    'coverage_settings': ['source_gate', 'package_resolution'],
    'livecontainer_build': ['source_gate', 'package_resolution'],
    'sidestore_build': ['source_gate', 'package_resolution'],
}
FINAL = (
    'Verify unchanged compiler inputs and record outputs',
    'Retain exact unsigned app products for diagnostic packaging',
    'Upload unsigned LiveContainer and matching compile evidence',
    'Upload unsigned SideStore and matching compile evidence',
    'Verify same-run size optimizer controls without skips',
    'Package same-run reviewed candidate without public release',
    'Upload experimental candidate IPA and provenance',
    'Retain original IPA and matching debug symbols separately',
)
EVIDENCE = 'Preserve evidence, including failures (not app products)'
TELEMETRY = 'Upload early fixture failure diagnostics only'
TELEMETRY_CONDITION = "${{ !cancelled() && steps.source_gate.outcome == 'success' && (steps.owned_oda_tests.outcome == 'failure' || steps.issue_tests.outcome == 'failure' || steps.upstream_tests.outcome == 'failure' || steps.minimuxer_tests.outcome == 'failure' || steps.vpn_tests.outcome == 'failure' || steps.acquisition_tests.outcome == 'failure' || steps.vendor_tests.outcome == 'failure' || steps.comprehensive_tests.outcome == 'failure' || steps.setup_tests.outcome == 'failure' || steps.russian_tests.outcome == 'failure') }}"


def steps():
    text = WORKFLOW.read_text()
    result = []
    for part in text.split('      - name: ')[1:]:
        lines = part.splitlines()
        row = {'name': lines[0], 'condition': 'success()', 'text': part}
        for line in lines[1:]:
            match = re.fullmatch(r'        (id|if): (.+)', line)
            if match:
                row['condition' if match[1] == 'if' else 'id'] = match[2]
        result.append(row)
    return text, result


def expression(deps):
    return "${{ !cancelled()" + ''.join(" && steps." + dep + ".outcome == 'success'" for dep in deps) + ' }}'


def simulate(rows, failures=(), cancelled=False):
    states = {}
    failed = False
    for row in rows:
        condition = row['condition']
        if condition == 'always()':
            admitted = True
        elif condition == 'success()':
            admitted = not failed and not cancelled
        elif condition == TELEMETRY_CONDITION:
            admitted = (not cancelled and states.get('source_gate') == 'success' and
                        any(states.get(key) == 'failure' for key in ('owned_oda_tests', 'issue_tests', 'upstream_tests', 'minimuxer_tests', 'vpn_tests', 'acquisition_tests', 'vendor_tests', 'comprehensive_tests', 'setup_tests', 'russian_tests')))
        elif condition == "${{ !cancelled() && steps.rust_tests.outcome == 'failure' }}":
            admitted = not cancelled and states.get('rust_tests') == 'failure'
        elif condition == "${{ !cancelled() && steps.coverage_settings.outcome == 'failure' }}":
            admitted = not cancelled and states.get('coverage_settings') == 'failure'
        else:
            deps = re.findall(r"steps\.(\w+)\.outcome == 'success'", condition)
            if condition != expression(deps):
                raise ValueError('Unsupported scheduling expression')
            admitted = not cancelled and all(states.get(dep) == 'success' for dep in deps)
        key = row.get('id', row['name'])
        outcome = ('failure' if key in failures else 'success') if admitted else 'skipped'
        states[key] = outcome
        failed |= outcome == 'failure'
    return states, failed


class FailureCollectionTests(unittest.TestCase):
    def setUp(self):
        self.text, self.rows = steps()

    def test_exact_prerequisites_and_fail_closed_artifact_gates(self):
        by_id = {r['id']: r for r in self.rows if 'id' in r}
        self.assertEqual(len(by_id), sum('id' in r for r in self.rows), 'duplicate step IDs')
        self.assertEqual(len({r['name'] for r in self.rows}), len(self.rows), 'duplicate step names')
        self.assertEqual(set(by_id), {'source_gate', *EXPECTED})
        self.assertEqual(by_id['source_gate']['condition'], 'success()')
        for name, deps in EXPECTED.items():
            self.assertEqual(by_id[name]['condition'], expression(deps), name)
        by_name = {r['name']: r for r in self.rows}
        for name in FINAL:
            self.assertEqual(by_name[name]['condition'], 'success()', name)
        self.assertEqual(by_name[EVIDENCE]['condition'], 'always()')
        self.assertNotIn('continue-on-error', self.text)
        self.assertNotRegex(self.text, r'\|\|\s*(true|:|exit\s+0)')

    def test_early_telemetry_is_failure_only_evidence_only_and_precedes_builds(self):
        by_name = {r['name']: r for r in self.rows}
        row = by_name[TELEMETRY]
        self.assertEqual(row['condition'], TELEMETRY_CONDITION)
        self.assertIn('        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n', row['text'])
        self.assertEqual(re.findall(r'^          path: (.+)$', row['text'], re.M), ['evidence/'])
        self.assertIn('          name: suite-diagnostics-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}\n', row['text'])
        self.assertIn('          if-no-files-found: error\n', row['text'])
        names = [r['name'] for r in self.rows]
        setup = next(r['name'] for r in self.rows if r.get('id') == 'setup_tests')
        tools = next(r['name'] for r in self.rows if r.get('id') == 'build_tools')
        self.assertLess(names.index(setup), names.index(TELEMETRY))
        self.assertLess(names.index(TELEMETRY), names.index(tools))
        for failures, expected in [((), 'skipped'), (('source_gate',), 'skipped'),
                                   (('issue_tests',), 'success'), (('upstream_tests',), 'success'), (('owned_oda_tests',), 'success'), (('comprehensive_tests',), 'success'),
                                   (('setup_tests',), 'success'),
                                   (('comprehensive_tests', 'setup_tests', 'russian_tests'), 'success')]:
            with self.subTest(failures=failures):
                states, failed = simulate(self.rows, failures)
                self.assertEqual(states[TELEMETRY], expected)
                self.assertEqual(failed, bool(failures))
                if expected == 'success':
                    self.assertEqual(states['livecontainer_build'], 'success')
                    self.assertEqual(states['sidestore_build'], 'success')
                    for name in FINAL:
                        self.assertEqual(states[name], 'skipped')
        states, failed = simulate(self.rows, ('comprehensive_tests', TELEMETRY))
        self.assertTrue(failed)
        self.assertEqual(states[TELEMETRY], 'failure')
        self.assertEqual(states['sidestore_build'], 'success')
        states, _ = simulate(self.rows, cancelled=True)
        self.assertEqual(states[TELEMETRY], 'skipped')

    def test_settings_failure_keeps_build_evidence_but_blocks_packaging(self):
        name = 'Upload early effective-settings failure diagnostics only'
        for failures, expected in [((), 'skipped'), (('coverage_settings',), 'success'),
                                   (('package_resolution',), 'skipped')]:
            states, failed = simulate(self.rows, failures)
            self.assertEqual(states[name], expected)
            self.assertEqual(failed, bool(failures))
            if expected == 'success':
                self.assertEqual(states['livecontainer_build'], 'success')
                self.assertEqual(states['sidestore_build'], 'success')
                for final in FINAL:
                    self.assertEqual(states[final], 'skipped')
        states, _ = simulate(self.rows, ('coverage_settings',), cancelled=True)
        self.assertEqual(states[name], 'skipped')

    def test_rust_failure_logs_are_retained_before_native_compilation(self):
        name = 'Upload early Rust failure diagnostics only'
        row = next(row for row in self.rows if row['name'] == name)
        self.assertEqual(row['condition'], "${{ !cancelled() && steps.rust_tests.outcome == 'failure' }}")
        self.assertIn('actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02', row['text'])
        self.assertEqual(re.findall(r'^            (evidence/[^\n]+)$', row['text'], re.M),
                         ['evidence/idevice-tests.log', 'evidence/idevice-ffi-tests.log',
                          'evidence/idevice-listener-generated-abi.log'])
        names = [r['name'] for r in self.rows]
        self.assertLess(names.index('Run Rust ownership and packet regressions'), names.index(name))
        self.assertLess(names.index(name), names.index('Compile iOS transport and stage local framework'))
        for failures, expected in [((), 'skipped'), (('rust_tests',), 'success'), (('build_tools',), 'skipped')]:
            states, _ = simulate(self.rows, failures)
            self.assertEqual(states[name], expected)
        states, _ = simulate(self.rows, ('rust_tests',), cancelled=True)
        self.assertEqual(states[name], 'skipped')

    def test_each_independent_suite_failure_still_collects_other_suites(self):
        for failure in [key for key, deps in EXPECTED.items() if deps == ['source_gate'] and key not in {'build_tools', 'openssl_inputs'}] + ['rust_tests', 'livecontainer_build', 'sidestore_build']:
            with self.subTest(failure=failure):
                states, failed = simulate(self.rows, [failure])
                self.assertTrue(failed)
                self.assertEqual(states[failure], 'failure')
                for key in EXPECTED:
                    self.assertEqual(states[key], 'failure' if key == failure else 'success', key)
                for name in FINAL:
                    self.assertEqual(states[name], 'skipped')
                self.assertEqual(states[EVIDENCE], 'success')

    def test_source_and_tool_or_build_dependency_failures_block_dependents(self):
        blocked = {
            'source_gate': set(EXPECTED),
            'build_tools': {'rust_tests', 'transport_build', 'abi_inspection', 'package_resolution', 'coverage_settings', 'livecontainer_build', 'sidestore_build'},
            'transport_build': {'abi_inspection', 'package_resolution', 'coverage_settings', 'livecontainer_build', 'sidestore_build'},
            'abi_inspection': {'package_resolution', 'coverage_settings', 'livecontainer_build', 'sidestore_build'},
            'openssl_inputs': {'package_resolution', 'coverage_settings', 'livecontainer_build', 'sidestore_build'},
            'package_resolution': {'coverage_settings', 'livecontainer_build', 'sidestore_build'},
        }
        for failure, dependents in blocked.items():
            with self.subTest(failure=failure):
                states, failed = simulate(self.rows, [failure])
                self.assertTrue(failed)
                for key in dependents:
                    self.assertEqual(states[key], 'skipped')
                for name in FINAL:
                    self.assertEqual(states[name], 'skipped')
                self.assertEqual(states[EVIDENCE], 'success')

    def test_all_pass_allows_products_but_cancellation_never_does(self):
        states, failed = simulate(self.rows)
        self.assertFalse(failed)
        for name in FINAL:
            self.assertEqual(states[name], 'success')
        states, _ = simulate(self.rows, cancelled=True)
        for key in EXPECTED:
            self.assertEqual(states[key], 'skipped')
        for name in FINAL:
            self.assertEqual(states[name], 'skipped')
        self.assertEqual(states[EVIDENCE], 'success')


if __name__ == '__main__':
    unittest.main(verbosity=2)
