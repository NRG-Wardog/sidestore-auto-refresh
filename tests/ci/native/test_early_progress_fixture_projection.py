import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
    from ci.native_paths import native_file, REPO_ROOT
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy
from pathlib import Path
import unittest
from ci.qualification.native import early_progress_fixture_projection as p

class EarlyProgressFixtureTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            'input_sha256': {p.prior.API: 'a'*64, **{k: 'b'*64 for k in p.prior.TRUTH_SUPPORT}, **p.PINS},
            'suite_inputs': {'early-refresh-boundaries': {'production': ['original'], 'support': ['one'], 'fixture': list(p.PINS)}},
            'additive_suite_inputs': {'profile-preconditions': {'support': ['one', p.GENERATOR, 'doubles']}}}
        self.value = {'schema': 'early-refresh-portable-contract-v1', 'status': 'STATIC_PASS_RUNTIME_NOT_RUN',
                      'checks': p.prior.static_names(), 'check_count': 121, 'expected_generic_baseline_cases': 12,
                      'variants': ['baseline', 'candidate'], 'swift_compiled': False, 'swift_executed': False}

    def test_exact_successor_normalizes_only_two_hashes_and_marker(self):
        before = copy.deepcopy(self.raw)
        current = p.add(self.raw)
        self.assertEqual(self.raw, before)
        p.verify_static(self.value, current)
        normalized = p.normalized(current)
        expected = copy.deepcopy(current)
        expected.pop(p.KEY)
        expected['input_sha256'][p.GENERATOR] = p.prior.GENERATOR_SHA
        expected['input_sha256'][p.prior.TEST_SOURCE] = p.prior.TEST_SOURCE_SHA
        self.assertEqual(normalized, expected)
        self.assertEqual(normalized[p.prior.KEY]['generator_sha256'], p.prior.GENERATOR_SHA)
        for profile, name, key in ((False, 'early-refresh-boundaries', 'suite_inputs'), (True, 'profile-preconditions', 'additive_suite_inputs')):
            self.assertEqual(p.inputs(current, self.raw[key][name], profile), current[key][name])
        self.assertEqual(len(self.value['checks']), 121)
        self.assertEqual(current[p.KEY]['runtime_case_count'], 24)

    def test_each_current_pin_missing_or_tampered_rejected(self):
        for path in p.PINS:
            for missing in (False, True):
                for bound in (False, True):
                    changed = p.add(self.raw) if bound else copy.deepcopy(self.raw)
                    if missing: changed['input_sha256'].pop(path)
                    else: changed['input_sha256'][path] = '0'*64
                    with self.assertRaises(ValueError):
                        p.normalized(changed) if bound else p.add(changed)

    def test_missing_unknown_or_duplicate_binding_rejected(self):
        current = p.add(self.raw)
        with self.assertRaises(ValueError): p.normalized(self.raw)
        with self.assertRaises(ValueError): p.add(current)
        for key in (p.KEY, p.prior.KEY):
            changed = copy.deepcopy(current); changed.pop(key)
            with self.assertRaises(ValueError): p.normalized(changed)
        changed = copy.deepcopy(current); changed[p.KEY]['fixture_sha256'][p.GENERATOR] = '0'*64
        with self.assertRaises(ValueError): p.normalized(changed)
        changed = copy.deepcopy(current); changed[p.prior.KEY]['generator_sha256'] = p.GENERATOR_SHA
        with self.assertRaises(ValueError): p.normalized(changed)
        for path in (p.prior.API, *p.prior.TRUTH_SUPPORT):
            changed = copy.deepcopy(current); changed['input_sha256'][path] = '0'*64
            with self.assertRaises(ValueError): p.normalized(changed)

    def test_exact_static_controls_cannot_be_replaced_reordered_or_claim_runtime(self):
        current = p.add(self.raw)
        names = self.value['checks']
        for bad in (names[:-1], names[::-1], names[:-1]+[names[0]], names+['extra']):
            with self.assertRaises(ValueError): p.verify_static({**self.value, 'checks': bad, 'check_count': len(bad)}, current)
        for field, value in (('check_count', True), ('swift_compiled', True), ('swift_executed', True)):
            with self.assertRaises(ValueError): p.verify_static({**self.value, field: value}, current)

    def test_historical_readiness_contract_remains_unchanged(self):
        old = copy.deepcopy(self.raw)
        for path in p.PINS: old['input_sha256'][path] = 'c'*64
        old['input_sha256'][p.GENERATOR] = p.prior.GENERATOR_SHA
        old['input_sha256'][p.prior.TEST_SOURCE] = p.prior.TEST_SOURCE_SHA
        bound = p.prior.add(old)
        self.assertEqual(p.add(old), bound)
        self.assertEqual(p.normalized(bound), bound)
        p.verify_static(self.value, bound)

    def test_mandatory_workflow_and_routes(self):
        root = (_CIPath(__file__).resolve().parents[3])
        for name, job in (('focused-diagnostics.yml', 'refresh_truth'), ('native-build.yml', None), ('focused-diagnostics.yml', 'cold_vpn')):
            text = workflow_job(root/'.github/workflows'/name, job)
            self.assertEqual(text.count('python3 -B harness/tests/ci/native/test_early_progress_fixture_projection.py'), 1)
        for name in ('verify_setup_state_recovery.py', 'project_candidate_contracts.py'):
            self.assertIn('from ci.qualification.native.early_progress_fixture_projection import ', native_file(name).read_text())

if __name__ == '__main__': unittest.main(verbosity=2)
