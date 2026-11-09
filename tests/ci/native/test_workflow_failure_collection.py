"""Assert the actual workflow's finite prerequisite and failure propagation graph.

Uses only stdlib, and supports only the deliberately small condition grammar in
this workflow. This models scheduling, not native test/build execution.
"""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
from pathlib import Path
import re
import unittest

WORKFLOW = (_CIPath(__file__).resolve().parents[3]/'.github/workflows/native-build.yml')
# Explicit reviewed maintained DAG; None means the success-only default.
EXPECTED = {'owned_oda_tests': ['source_gate', 'preservation_cms'], 'issue_tests': ['source_gate', 'preservation_cms'], 'upstream_tests': ['source_gate', 'preservation_cms'], 'minimuxer_tests': ['source_gate', 'preservation_cms'], 'vpn_tests': ['source_gate', 'preservation_cms'], 'acquisition_tests': ['source_gate', 'preservation_cms'], 'vendor_tests': ['source_gate', 'preservation_cms'], 'comprehensive_tests': ['source_gate', 'preservation_cms'], 'setup_tests': ['source_gate', 'preservation_cms'], 'russian_tests': ['source_gate', 'preservation_cms'], 'build_tools': ['source_gate', 'preservation_cms'], 'rust_tests': ['source_gate', 'preservation_cms', 'build_tools'], 'transport_build': ['source_gate', 'preservation_cms', 'build_tools'], 'abi_inspection': ['source_gate', 'preservation_cms', 'transport_build'], 'openssl_inputs': ['source_gate', 'preservation_cms'], 'package_resolution': ['source_gate', 'abi_inspection', 'openssl_inputs'], 'coverage_settings': ['source_gate', 'package_resolution'], 'livecontainer_build': ['source_gate', 'package_resolution'], 'sidestore_build': ['source_gate', 'package_resolution'], 'initialize_evidence': None, 'preservation_cms': ['source_gate'], 'refresh_truth_swift': None, 'schema_tests': None, 'database_recovery_tests': None, 'multitask_tests': None, 'cold_vpn_tests': None, 'startup_tests': ['source_gate', 'preservation_cms'], 'language_switch_tests': ['source_gate', 'preservation_cms'], 'refresh_truth_rust': ['source_gate', 'preservation_cms', 'build_tools'], 'same_run_host_qualification': ['source_gate', 'openssl_inputs']}
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
TELEMETRY_CONDITION = "${{ !cancelled() && steps.source_gate.outcome == 'success' && (steps.schema_tests.outcome == 'failure' || steps.cold_vpn_tests.outcome == 'failure' || steps.language_switch_tests.outcome == 'failure' || steps.startup_tests.outcome == 'failure' || steps.owned_oda_tests.outcome == 'failure' || steps.issue_tests.outcome == 'failure' || steps.upstream_tests.outcome == 'failure' || steps.minimuxer_tests.outcome == 'failure' || steps.vpn_tests.outcome == 'failure' || steps.acquisition_tests.outcome == 'failure' || steps.vendor_tests.outcome == 'failure' || steps.comprehensive_tests.outcome == 'failure' || steps.setup_tests.outcome == 'failure' || steps.russian_tests.outcome == 'failure') }}"


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
    if deps is None: return 'success()'
    return "${{ !cancelled()" + ''.join(" && steps." + dep + ".outcome == 'success'" for dep in deps) + ' }}'


def simulate(rows, failures=(), cancelled=False, event="workflow_dispatch"):
    if event not in ("workflow_dispatch", "pull_request"):
        raise ValueError("Unsupported workflow event")
    states = {}
    failed = False
    for row in rows:
        condition = row['condition']
        if condition == 'always()':
            admitted = True
        elif condition == 'success()':
            admitted = not failed and not cancelled
        elif condition == "github.event_name == 'pull_request'":
            # GitHub applies the implicit success() guard to an event-only if.
            admitted = event == 'pull_request' and not failed and not cancelled
        else:
            # Parse only boolean combinations of literal step outcomes and cancellation.
            if not condition.startswith('${{ ') or not condition.endswith(' }}'):
                raise ValueError('Unsupported scheduling expression')
            raw = condition[4:-3].replace('!cancelled()', str(not cancelled))
            raw = re.sub(r"steps\.(\w+)\.outcome == '(success|failure|skipped)'",
                         lambda m: str(states.get(m[1]) == m[2]), raw)
            raw = raw.replace('&&', ' and ').replace('||', ' or ')
            import ast
            node = ast.parse(raw.strip(), mode='eval')
            def boolean(n):
                if isinstance(n, ast.Constant) and type(n.value) is bool: return n.value
                if isinstance(n, ast.BoolOp) and isinstance(n.op, (ast.And, ast.Or)):
                    values = [boolean(v) for v in n.values]
                    return all(values) if isinstance(n.op, ast.And) else any(values)
                raise ValueError('Unsupported scheduling expression')
            admitted = boolean(node.body)
        key = row.get('id', row['name'])
        outcome = ('failure' if key in failures else 'success') if admitted else 'skipped'
        states[key] = outcome
        failed |= outcome == 'failure'
    return states, failed


class FailureCollectionTests(unittest.TestCase):
    def setUp(self):
        self.text, self.rows = steps()

    def test_pr_tree_identity_failure_blocks_native_products_but_retains_evidence(self):
        identity = 'Verify PR merge tree equals the reviewed head tree'
        manual, _ = simulate(self.rows)
        self.assertEqual(manual[identity], 'skipped')
        passed, failed = simulate(self.rows, event='pull_request')
        self.assertFalse(failed)
        self.assertEqual(passed[identity], 'success')
        rejected, failed = simulate(self.rows, (identity,), event='pull_request')
        self.assertTrue(failed)
        self.assertEqual(rejected['source_gate'], 'skipped')
        self.assertEqual(rejected['livecontainer_build'], 'skipped')
        for name in FINAL:self.assertEqual(rejected[name], 'skipped')
        self.assertEqual(rejected[EVIDENCE], 'success')
        cancelled, _ = simulate(self.rows, cancelled=True, event='pull_request')
        self.assertEqual(cancelled[identity], 'skipped')
        with self.assertRaises(ValueError):simulate(self.rows, event='unknown')

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
        self.assertEqual(by_name[EVIDENCE]['condition'], expression(['initialize_evidence']))
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
        for failure in [key for key, deps in EXPECTED.items() if deps == ['source_gate', 'preservation_cms'] and key not in {'build_tools', 'openssl_inputs'}] + ['rust_tests', 'livecontainer_build', 'sidestore_build']:
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
            'source_gate': set(EXPECTED) - {'initialize_evidence'},
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

    def test_every_maintained_gate_failure_blocks_all_products(self):
        for failure in ('source_gate', *EXPECTED):
            with self.subTest(failure=failure):
                states, failed = simulate(self.rows, [failure])
                self.assertTrue(failed)
                self.assertEqual(states[failure], 'failure')
                for final in FINAL:
                    self.assertEqual(states[final], 'skipped')
                self.assertEqual(states[EVIDENCE], 'skipped' if failure == 'initialize_evidence' else 'success')

    def test_scheduler_rejects_unknown_expressions(self):
        for condition in ("${{ github.event_name == 'push' }}", "${{ true }}", "${{ success() }}", "${{ __import__('os') }}"):
            with self.subTest(condition=condition), self.assertRaises((ValueError, SyntaxError)):
                simulate([{'name': 'untrusted', 'condition': condition}])

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
        self.assertEqual(states[EVIDENCE], 'skipped')


class ColdCMSFailFastTests(unittest.TestCase):
    """Scheduling only: no skipped crypto suite can qualify downstream work."""
    def setUp(self):
        text = WORKFLOW.with_name('native-build.yml').read_text()
        self.text = text
        self.rows = {}
        for part in text.split('      - name: ')[1:]:
            row = {'name': part.splitlines()[0], 'condition': 'success()', 'text': part}
            for line in part.splitlines()[1:]:
                match = re.fullmatch(r'        (id|if): (.+)', line)
                if match:
                    row['condition' if match[1] == 'if' else 'id'] = match[2]
            self.rows[row.get('id', row['name'])] = row
        self.expected = {key: ['source_gate', 'preservation_cms'] for key in (
            'startup_tests', 'language_switch_tests', 'owned_oda_tests', 'issue_tests',
            'upstream_tests', 'minimuxer_tests', 'vpn_tests', 'acquisition_tests',
            'vendor_tests', 'comprehensive_tests', 'setup_tests', 'russian_tests', 'build_tools')}
        for key in ('rust_tests', 'refresh_truth_rust', 'transport_build'):
            self.expected[key] = ['source_gate', 'preservation_cms', 'build_tools']
        self.expected['abi_inspection'] = ['source_gate', 'preservation_cms', 'transport_build']

    def test_exact_prerequisite_addition_preserves_existing_dependencies(self):
        self.assertEqual(self.rows['preservation_cms']['condition'], expression(['source_gate']))
        for key, deps in self.expected.items():
            self.assertEqual(self.rows[key]['condition'], expression(deps), key)
        for key in ('refresh_truth_swift', 'schema_tests', 'multitask_tests', 'cold_vpn_tests'):
            self.assertEqual(self.rows[key]['condition'], 'success()')
        self.assertEqual(self.rows['openssl_inputs']['condition'], expression(['source_gate', 'preservation_cms']))

    def test_crypto_failure_skip_or_cancel_blocks_all_expensive_tail(self):
        for cms in ('failure', 'skipped', None):
            for deps in self.expected.values():
                states = {key: 'success' for key in deps}
                states['preservation_cms'] = cms
                self.assertFalse(all(states.get(key) == 'success' for key in deps))
        # Successful prerequisites still require every unchanged command block.
        for key, deps in self.expected.items():
            self.assertTrue(all({d: 'success' for d in deps}[d] == 'success' for d in deps))
            self.assertIn('        run: |\n', self.rows[key]['text'])
            self.assertTrue(self.rows[key]['condition'].startswith('${{ !cancelled() && '))
        self.assertNotIn('continue-on-error', self.text)
        self.assertNotRegex(self.text, r'\|\|\s*(true|:|exit\s+0)')

    def test_failure_evidence_survives_and_release_remains_success_only(self):
        row = self.rows['Preserve early exact CMS build failure diagnostics']
        self.assertEqual(row['condition'], "${{ !cancelled() && steps.preservation_cms.outcome == 'failure' }}")
        self.assertEqual(self.rows[EVIDENCE]['condition'], expression(['initialize_evidence']))
        for name in FINAL:
            self.assertEqual(self.rows[name]['condition'], 'success()', name)
        self.assertLess(self.text.index(row['name']), self.text.index('id: startup_tests'))

    def test_control_is_mandatory_in_focused_and_full_source_gates(self):
        command = 'python3 -B harness/tests/ci/native/test_workflow_failure_collection.py'
        for filename, job, boundary in (('native-build.yml', None, 'id: preservation_cms'), ('focused-diagnostics.yml', 'refresh_truth', 'id: swift_tests')):
            text = workflow_job(WORKFLOW.with_name(filename), job)
            self.assertEqual(text.count(command), 1)
            self.assertLess(text.index(command), text.index(boundary))


if __name__ == '__main__':
    unittest.main(verbosity=2)
