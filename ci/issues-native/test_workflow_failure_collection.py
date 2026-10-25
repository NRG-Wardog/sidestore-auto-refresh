"""Assert the actual workflow's finite prerequisite and failure propagation graph.

Uses only stdlib, and supports only the deliberately small condition grammar in
this workflow. This models scheduling, not native test/build execution.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/issues-native.yml'
EXPECTED = {
    'issue_tests': ['source_gate'], 'upstream_tests': ['source_gate'],
    'minimuxer_tests': ['source_gate'], 'vpn_tests': ['source_gate'],
    'acquisition_tests': ['source_gate'], 'vendor_tests': ['source_gate'],
    'comprehensive_tests': ['source_gate'], 'setup_tests': ['source_gate'],
    'build_tools': ['source_gate'], 'rust_tests': ['source_gate', 'build_tools'],
    'transport_build': ['source_gate', 'build_tools'],
    'abi_inspection': ['source_gate', 'transport_build'],
    'package_resolution': ['source_gate', 'abi_inspection'],
    'livecontainer_build': ['source_gate', 'package_resolution'],
    'sidestore_build': ['source_gate', 'package_resolution'],
}
FINAL = (
    'Verify unchanged compiler inputs and record outputs',
    'Retain exact unsigned app products for diagnostic packaging',
    'Upload unsigned LiveContainer and matching compile evidence',
    'Upload unsigned SideStore and matching compile evidence',
    'Package same-run reviewed candidate without public release',
    'Upload experimental candidate IPA and provenance',
)
EVIDENCE = 'Preserve evidence, including failures (not app products)'


def steps():
    text = WORKFLOW.read_text()
    result = []
    for part in text.split('      - name: ')[1:]:
        lines = part.splitlines()
        row = {'name': lines[0], 'condition': 'success()'}
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

    def test_each_independent_suite_failure_still_collects_other_suites(self):
        for failure in [key for key, deps in EXPECTED.items() if deps == ['source_gate'] and key != 'build_tools'] + ['rust_tests', 'livecontainer_build', 'sidestore_build']:
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
            'build_tools': {'rust_tests', 'transport_build', 'abi_inspection', 'package_resolution', 'livecontainer_build', 'sidestore_build'},
            'transport_build': {'abi_inspection', 'package_resolution', 'livecontainer_build', 'sidestore_build'},
            'abi_inspection': {'package_resolution', 'livecontainer_build', 'sidestore_build'},
            'package_resolution': {'livecontainer_build', 'sidestore_build'},
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
