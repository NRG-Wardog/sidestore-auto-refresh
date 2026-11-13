"""Current portal gate negatives and actual-source extraction closure, not device proof."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import argparse
import contextlib
import copy
import importlib.util
import io
import json
import re
import tempfile
import unittest
from unittest import mock
from ci.qualification.native import refresh_truth_receipt as C
from tests.ci.native.test_refresh_truth_receipt import fixture
from tests.ci.native.test_refresh_truth_orchestration import R
from tests.ci.native.test_workflow_failure_collection import steps, simulate, FINAL, EVIDENCE
from ci.workflow_jobs import workflow_job

PORTAL_INPUTS = {
    'LiveContainer': ('tests/portal_identity/test_contract.py', 'tests/portal_identity/cases.swift',
                      '.ci/comprehensive-diagnostics/generate_wire_fixture.py', 'SideStoreSupport/SideStore.swift'),
    'SideStore': ('tests/preservation_refresh/PortalRequestDoubles.swift',
                  'SideStore/Core/Operations/PipelineExecutor.swift',
                  'tests/refresh_failure/pipeline_harness.swift', 'AltStore/AppDelegate.swift',
                  'SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift',
                  'SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift',
                  'tests/preservation_refresh/generate_fixture.py', 'tests/preservation_refresh/test_contract.py',
                  'tests/preservation_refresh/expected-cases.json', 'tests/preservation_refresh/IntegrationCases.swift'),
    'SideStore/Dependencies/SideSign': ('Sources/Models/AppID.swift',
        'Sources/DeveloperPortal/AppIDLookupResponse.swift', 'Sources/DeveloperPortal/AppIDs.swift',
        'Sources/DeveloperPortal/DeveloperPortalAPI.swift', '.ci/appid-lookup/run.sh',
        '.ci/appid-lookup/test_source.py', '.ci/appid-lookup/RuntimeTests.swift', '.github/workflows/build-all.yml'),
}
SOURCES = None

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PortalGateTests(unittest.TestCase):
    def setUp(self):
        self.value = fixture()

    def verify(self, value):
        value['part_sha256'] = {key: C.digest(part) for key, part in value['parts'].items()}
        return C.verify_portable(value, self.value['source_graph'], self.value['identity'])

    def reject_log(self, owner, log):
        value = copy.deepcopy(self.value)
        value['parts']['swift']['logs'][owner] = log
        with self.assertRaises((ValueError, KeyError, TypeError)):
            self.verify(value)

    def test_new_native_lanes_have_exact_owner_commands_and_counts(self):
        self.assertEqual(C.SUITES['SideSign-portal-parser'], ('.ci/appid-lookup/run.sh', 3))
        self.assertEqual(C.SUITE_OWNERS['SideSign-portal-parser'], 'SideStore/Dependencies/SideSign')
        self.assertEqual(C.SUITES['LiveContainer-portal-wire'], ('tests/portal_identity/test_contract.py', 2))
        self.assertEqual(C.SUITE_OWNERS['LiveContainer-portal-wire'], 'LiveContainer')
        self.assertEqual(C.SUITES['SideStore-preservation'], ('tests/preservation_refresh/run.sh', 7))
        self.assertEqual(len(C.PORTAL_WIRE_CASES), 32)
        self.assertEqual(len(C.EVIDENCE_WIRE_CASES), 35)
        self.assertEqual(len(C.CURRENT_PORTAL_WIRE_CASES), 67)
        self.assertEqual(C.PORTAL_PARSER_MARKER, 'PASS: 172 strict App ID parser assertions')
        for owner in ('SideSign-portal-parser', 'LiveContainer-portal-wire', 'SideStore-preservation'):
            value = copy.deepcopy(self.value)
            value['parts']['swift']['logs'].pop(owner)
            with self.assertRaisesRegex(ValueError, 'suite log closure'):
                self.verify(value)

    def test_previous_inventories_remain_exact_prefixes_and_no_case_is_replaced(self):
        self.assertEqual(len(C.PRESERVATION_INTEGRATION_CASES), 80)
        self.assertEqual(len(C.OBSERVATION_CASES), 189)
        self.assertEqual(len(C.PRESERVATION_POLICY_CASES), 55)
        self.assertEqual(len(C.LEGACY_HOST_CASES), 81)
        self.assertEqual(C.CURRENT_PRESERVATION_INTEGRATION_CASES[:80], C.PRESERVATION_INTEGRATION_CASES)
        self.assertEqual(C.CURRENT_OBSERVATION_CASES[:189], C.OBSERVATION_CASES)
        self.assertEqual(len(C.CURRENT_PRESERVATION_INTEGRATION_CASES), 110)
        self.assertEqual(len(C.CURRENT_OBSERVATION_CASES), 216)
        self.assertEqual(len(C.EVIDENCE_INTEGRATION_CASES), 12)
        self.assertEqual(C.CURRENT_PRESERVATION_INTEGRATION_CASES[98:], C.EVIDENCE_INTEGRATION_CASES)
        self.assertEqual(C.CURRENT_PORTAL_WIRE_CASES[:32], C.PORTAL_WIRE_CASES)
        self.assertEqual(C.CURRENT_PORTAL_WIRE_CASES[32:], C.EVIDENCE_WIRE_CASES)
        # Exact previously qualified inventories, never re-pinned for the successor.
        self.assertEqual(C.digest(C.CURRENT_PRESERVATION_INTEGRATION_CASES[:98]),
                         '4d3aabdb763b102689cccbfe6c001cd97962219b0b23d6b9400764cead44309b')
        self.assertEqual(C.digest(C.PORTAL_WIRE_CASES),
                         'a9b00010903bc1c2f86373221950c0ce4745e1e9dd1b66142b7bf6fe04471a1a')
        self.assertEqual(C.digest(C.CURRENT_OBSERVATION_CASES),
                         'a6d2cafcedf8e48ac7eb193469162965d79c7028c0dcc9a2ed2f2cf86a145e13')
        for cases in (C.CURRENT_PRESERVATION_INTEGRATION_CASES, C.CURRENT_OBSERVATION_CASES):
            self.assertEqual(len(cases), len(set(cases)))

    def test_parser_static_only_wrong_assertion_count_and_duplicate_native_rejected(self):
        owner = 'SideSign-portal-parser'
        good = self.value['parts']['swift']['logs'][owner]
        for count in (0, 171, 173):
            self.reject_log(owner, good.replace('172 strict', str(count) + ' strict'))
        for replacement in ('', C.PORTAL_PARSER_MARKER + '\n' + C.PORTAL_PARSER_MARKER):
            self.reject_log(owner, good.replace(C.PORTAL_PARSER_MARKER, replacement))
        for tail in (' (skipped=1)', ' (expected failures=1)'):
            self.reject_log(owner, good.replace('OK\n', 'OK' + tail + '\n'))
        self.reject_log(owner, good.replace('Ran 3 tests', 'Ran 2 tests'))
        self.reject_log(owner, good + 'PASS: static substitute\n')

    def test_wire_each_emitted_case_and_completion_must_execute_exactly(self):
        owner = 'LiveContainer-portal-wire'
        good = self.value['parts']['swift']['logs'][owner]
        for case in C.CURRENT_PORTAL_WIRE_CASES:
            with self.subTest(case=case):
                self.reject_log(owner, good.replace('PASS ' + case + '\n', '', 1))
                self.reject_log(owner, good + 'PASS ' + case + '\n')
        for replacement in ('', 'PORTAL_WIRE_PASS\nPORTAL_WIRE_PASS'):
            self.reject_log(owner, good.replace('PORTAL_WIRE_PASS', replacement))
        lines = good.splitlines(); first = lines.index('PASS ' + C.PORTAL_WIRE_CASES[0])
        lines[first], lines[first+1] = lines[first+1], lines[first]
        self.reject_log(owner, '\n'.join(lines) + '\n')
        self.reject_log(owner, good.replace('OK\n', 'OK (skipped=1)\n'))

    def test_each_additive_preservation_and_observation_case_is_mandatory(self):
        owner = 'SideStore-preservation'
        good = self.value['parts']['swift']['logs'][owner]
        before = next(line for line in good.splitlines() if line.startswith('{') and
                      json.loads(line)['schema'] == 'preservation-refresh-integration-v1')
        for case in C.PORTAL_INTEGRATION_CASES + C.EVIDENCE_INTEGRATION_CASES:
            row = json.loads(before); row['cases'].remove(case)
            with self.subTest(case=case):
                self.reject_log(owner, good.replace(before, json.dumps(row)))
        self.reject_log(owner, good.replace('Ran 7 tests', 'Ran 6 tests'))
        owner = 'LiveContainer-observations'; good = self.value['parts']['swift']['logs'][owner]
        for case in C.PORTAL_OBSERVATION_CASES:
            with self.subTest(case=case):
                self.reject_log(owner, good.replace('PASS ' + case + '\n', '', 1))

    def test_prior_portal_success_cannot_substitute_for_evidence_native_execution(self):
        owner = 'LiveContainer-portal-wire'
        good = self.value['parts']['swift']['logs'][owner]
        prior = good
        for case in C.EVIDENCE_WIRE_CASES:
            prior = prior.replace('PASS ' + case + '\n', '', 1)
        self.reject_log(owner, prior)
        self.reject_log(owner, prior + 'PASS evidence_static_only\n')
        # Moving a new case before a previously qualified case is also invalid.
        first_new = 'PASS ' + C.EVIDENCE_WIRE_CASES[0] + '\n'
        self.reject_log(owner, first_new + good.replace(first_new, '', 1))
        owner = 'SideStore-preservation'
        good = self.value['parts']['swift']['logs'][owner]
        before = next(line for line in good.splitlines() if line.startswith('{') and
                      json.loads(line)['schema'] == 'preservation-refresh-integration-v1')
        original = json.loads(before)
        for cases in (list(C.CURRENT_PRESERVATION_INTEGRATION_CASES[:98]),
                      list(reversed(C.CURRENT_PRESERVATION_INTEGRATION_CASES)),
                      list(C.CURRENT_PRESERVATION_INTEGRATION_CASES) + [C.EVIDENCE_INTEGRATION_CASES[0]]):
            changed = {**original, 'cases': cases}
            self.reject_log(owner, good.replace(before, json.dumps(changed)))
        for count in (5, 6, 8):
            self.reject_log(owner, good.replace('Ran 7 tests', 'Ran ' + str(count) + ' tests'))
        for status in ('OK (skipped=1)', 'OK (expected failures=1)'):
            self.reject_log(owner, good.replace('OK\n', status + '\n'))

    def test_all_cross_owner_inputs_required_in_both_native_parts(self):
        for owner, paths in PORTAL_INPUTS.items():
            for path in paths:
                self.assertIn(path, C.INPUTS[owner])
                for kind in ('swift', 'rust'):
                    value = copy.deepcopy(self.value)
                    value['parts'][kind]['source'] = copy.deepcopy(value['parts'][kind]['source'])
                    value['parts'][kind]['source']['source_sha256'].pop(owner + '/' + path)
                    with self.subTest(owner=owner, path=path, kind=kind), self.assertRaisesRegex(ValueError, 'source input closure'):
                        self.verify(value)

    def test_missing_native_compiler_is_failure_not_a_skip(self):
        with mock.patch.object(R.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'required native compiler absent: swiftc'):
                R.compiler('swiftc')

    def test_new_runtime_failure_preserves_log_runs_other_suites_and_never_seals(self):
        for failing in ('SideSign-portal-parser', 'LiveContainer-portal-wire', 'SideStore-preservation'):
            calls = []; value = self.value
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); sources = root/'sources'; sources.mkdir()
                output = root/'evidence'; output.mkdir()
                def command(args, cwd, log, env, timeout):
                    script = Path(args[2])
                    owner = next(key for key, (path, _) in C.SUITES.items()
                                 if script == sources/C.SUITE_OWNERS[key]/path)
                    calls.append(owner)
                    # Reject inherited unrelated peer roots, including a real existing directory.
                    self.assertEqual(env['SIDESTORE_SOURCE_ROOT'], str(sources/'SideStore'))
                    self.assertEqual(env['V3_SIDESTORE_SOURCE_ROOT'], str(sources/'SideStore'))
                    self.assertEqual(env['V3_HOST_SOURCE_ROOT'], str(sources/'LiveContainer'))
                    text = 'native compiler/runtime failed\n' if owner == failing else value['parts']['swift']['logs'][owner]
                    log.write_text(text)
                    if owner == failing:
                        raise ValueError('native fixture failed')
                    return text
                with mock.patch.dict('os.environ', {'SIDESTORE_SOURCE_ROOT': str(root)}), \
                     mock.patch.object(R, 'load_candidate_graph', return_value={'owners': value['source_graph']}), \
                     mock.patch.object(R, 'run_identity', return_value=value['identity']), \
                     mock.patch.object(C, 'collect', return_value=value['parts']['swift']['source']), \
                     mock.patch.object(R, 'compiler', return_value=value['parts']['swift']['compiler']), \
                     mock.patch.object(R, 'command', side_effect=command):
                    with self.assertRaisesRegex(ValueError, 'independent Swift qualification failed'):
                        R.run('swift', sources, output)
                self.assertEqual(calls, list(C.SUITES))
                report = C.read(output/'refresh-truth-swift.json')
                self.assertEqual(report['status'], 'FAILED')
                self.assertEqual(report['logs'][failing], 'native compiler/runtime failed\n')
                self.assertEqual(set(report['logs']), set(C.SUITES))
                with self.assertRaisesRegex(ValueError, 'part not passed'):
                    C.validate_part(report, 'swift', value['source_graph'], value['identity'])
                self.assertFalse((output/'refresh-truth-tests.json').exists())

    def test_workflows_require_actual_source_proofs_and_gate_controls_before_native(self):
        for name, job, lane in (('native-build.yml', None, 'id: refresh_truth_swift'),
                                ('focused-diagnostics.yml', 'refresh_truth', 'id: swift_tests')):
            text = workflow_job(ROOT/'.github/workflows'/name, job)
            proof = 'python3 -B harness/tests/ci/native/test_portal_identity_projection.py --sources sources'
            controls = 'python3 -B harness/tests/ci/native/test_portal_native_gates.py --sources sources'
            self.assertEqual(text.count(proof), 1)
            self.assertEqual(text.count(controls), 1)
            self.assertLess(text.index('record.py acquire'), text.index(proof))
            self.assertLess(text.index(proof), text.index(lane))
            self.assertLess(text.index(controls), text.index('python3 -B harness/ci/qualification/refresh/run.py swift'))
            self.assertNotIn('continue-on-error', text)
        _, rows = steps()
        states, failed = simulate(rows, ['refresh_truth_swift'])
        self.assertTrue(failed)
        for final in FINAL:
            self.assertEqual(states[final], 'skipped')
        self.assertEqual(states['Preserve early refresh truth failure evidence'], 'success')
        self.assertEqual(states[EVIDENCE], 'success')

# Explicit --sources runs these checks on the acquired owners. No missing-source
# skip or optional native success path is admitted by either workflow.
class PortalSourceClosureTests(unittest.TestCase):
    def test_exact_current_native_manifests_match_source_without_replacing_previous_cases(self):
        expected = json.loads((SOURCES/'SideStore/tests/preservation_refresh/expected-cases.json').read_text())
        self.assertEqual(expected['policy'], list(C.PRESERVATION_POLICY_CASES))
        self.assertEqual(expected['integration'], list(C.CURRENT_PRESERVATION_INTEGRATION_CASES))
        cases = (SOURCES/'LiveContainer/.ci/refresh-observations/cases.swift').read_text()
        names = re.findall(r'"((?:portal|origin)_[a-z0-9_]+)"\)', cases)
        self.assertEqual(tuple(name for name in names if name.startswith('portal_')), C.PORTAL_OBSERVATION_CASES)
        legacy = load(SOURCES/'LiveContainer/.ci/legacy-replacement/test_contract.py', 'portal_legacy')
        self.assertEqual(sorted(legacy.expected_names()), list(C.LEGACY_HOST_CASES))

    def test_preservation_generated_input_manifest_is_inside_receipt_closure(self):
        generator = load(SOURCES/'SideStore/tests/preservation_refresh/generate_fixture.py', 'portal_preservation')
        reads = set(); original = Path.read_text
        def read(path, *args, **kwargs):
            reads.add(path.resolve().relative_to(SOURCES/'SideStore').as_posix())
            return original(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(Path, 'read_text', read):
                generator.generate(Path(directory))
            inputs = json.loads((Path(directory)/'sources.json').read_text())['inputs']
        self.assertTrue(reads <= set(inputs), 'every actual extracted input must be declared')
        self.assertIn('SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift', reads)
        self.assertIn('SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift', reads)
        self.assertIn('tests/preservation_refresh/IntegrationCases.swift', reads)
        self.assertIn('tests/preservation_refresh/PortalRequestDoubles.swift', inputs)
        for path, digest in inputs.items():
            self.assertIn(path, C.INPUTS['SideStore'])
            self.assertEqual(digest, C.sha(SOURCES/'SideStore'/path))

    def test_changed_observation_extraction_reads_only_bound_actual_sources(self):
        module = load(SOURCES/'LiveContainer/.ci/refresh-observations/test_refresh_observations.py', 'portal_observations')
        reads = set(); original = Path.read_text
        def read(path, *args, **kwargs):
            reads.add(path.resolve().relative_to(SOURCES).as_posix())
            return original(path, *args, **kwargs)
        with mock.patch.dict('os.environ', {'SIDESTORE_SOURCE_ROOT': str(SOURCES/'SideStore')}), \
             mock.patch.object(Path, 'read_text', read):
            generated, cases = module.ObservationTests().fixture_source()
        required = {'SideStore/tests/refresh_failure/pipeline_harness.swift',
                    'SideStore/SideStore/Core/Operations/PipelineExecutor.swift',
                    'SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift'}
        self.assertTrue(required <= reads)
        self.assertTrue(reads <= {owner+'/'+path for owner, paths in C.INPUTS.items() for path in paths})
        self.assertIn('portal_all_fourteen_actual_producer_wrapper_manifest_whole_log', cases)
        self.assertIn('func lcStructuredSigningFailure(', generated)
        self.assertIn('enum V3RefreshAllFailureDiagnostics {', generated)

    def test_evidence_wire_extraction_and_peer_parity_use_bound_actual_sources(self):
        owner = SOURCES/'LiveContainer'
        module = load(owner/'tests/portal_identity/test_contract.py', 'evidence_wire_contract')
        reads = set(); original = Path.read_text
        def read(path, *args, **kwargs):
            reads.add(path.resolve().relative_to(SOURCES).as_posix())
            return original(path, *args, **kwargs)
        self.assertTrue((SOURCES/'SideStore/AltStore/AppDelegate.swift').is_file())
        with mock.patch.dict('os.environ', {'SIDESTORE_SOURCE_ROOT': str(SOURCES/'SideStore')}), \
             mock.patch.object(Path, 'read_text', read):
            module.PortalIdentityTests().test_source_contract()
            generated = module.g.pure_model(module.HOST.read_text())
            cases = (owner/'tests/portal_identity/cases.swift').read_text()
        self.assertEqual(reads, {'LiveContainer/SideStoreSupport/SideStore.swift',
                                'LiveContainer/tests/portal_identity/cases.swift',
                                'SideStore/AltStore/AppDelegate.swift'})
        self.assertTrue(reads <= {key+'/'+path for key, paths in C.INPUTS.items() for path in paths})
        for witness in ('preservation_evidence', 'preservationEvidenceKinds',
                        'evidence?.mutationDispatched == false'):
            self.assertIn(witness, generated)
        # These source checks are mandatory alongside, never instead of, Swift.
        self.assertIn('Native Swift toolchain required, do not skip in CI',
                      (owner/'tests/portal_identity/test_contract.py').read_text())
        names = re.findall(r'"(evidence_[a-z0-9_]+)"\)', cases)
        expanded = []
        counts = {'evidence_invalid_optional_row_preserves_primary': 3,
                  'evidence_mismatched_row_does_not_manufacture_diagnosis': 6}
        for name in names:
            expanded.extend([name] * counts.get(name, 1))
        self.assertEqual(tuple(expanded), C.EVIDENCE_WIRE_CASES)
        self.assertIn('let evidenceInvalidValues: [Any] = ["SECRET", true, ["account": "SECRET"]]', cases)
        self.assertIn('for value in evidenceInvalidValues {', cases)
        for pair in ('("scope", "host")', '("dispatched", "yes")', '("dispatched", "unknown")',
                     '("operation", "completed")', '("guard", "preservationPortalIdentityUnavailable")',
                     '("failureKind", "noVPN")'):
            self.assertIn(pair, cases[cases.index('for (key, value) in', cases.index('for value in evidenceInvalidValues')):])

    def test_native_owner_commands_and_wire_expansion_are_bound(self):
        sign = SOURCES/'SideStore/Dependencies/SideSign'
        script = (sign/'.ci/appid-lookup/run.sh').read_text()
        for path in ('Sources/Models/AppID.swift', 'Sources/DeveloperPortal/AppIDLookupResponse.swift', '.ci/appid-lookup/RuntimeTests.swift'):
            self.assertIn('"$ROOT/'+path+'"', script)
            self.assertIn(path, C.INPUTS['SideStore/Dependencies/SideSign'])
        self.assertIn('swiftc -swift-version 6', script)
        text = (SOURCES/'LiveContainer/tests/portal_identity/cases.swift').read_text()
        names = re.findall(r'"(portal_[a-z0-9_]+)"\)', text)
        self.assertEqual(tuple(names[:-2] + [names[-2]]*3 + [names[-1]]*6), C.PORTAL_WIRE_CASES)
        self.assertIn('let invalidValues: [Any] = ["SECRET", true, ["account": "SECRET"]]', text)
        for pair in ('("scope", "host")', '("dispatched", "yes")', '("dispatched", "unknown")',
                     '("operation", "completed")', '("guard", "preservationEvidenceUnavailable")', '("failureKind", "noVPN")'):
            self.assertIn(pair, text)

def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(PortalGateTests)
    if SOURCES is not None:
        suite.addTests(loader.loadTestsFromTestCase(PortalSourceClosureTests))
    return suite

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path)
    args, remaining = parser.parse_known_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PortalGateTests)
    if args.sources is not None:
        SOURCES = args.sources.resolve(strict=True)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PortalSourceClosureTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
