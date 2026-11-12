"""Synthetic rejection/orchestration controls. These never claim native execution."""
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
import copy
import importlib.util
import os
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile
from ci.qualification.native import refresh_truth_receipt as C
from tests.ci.native.test_refresh_truth_receipt import fixture


class NativeQualificationControls(unittest.TestCase):
    def setUp(self):
        self.value = fixture()

    def reject_log(self, owner, log):
        value = copy.deepcopy(self.value)
        value['parts']['swift']['logs'][owner] = log
        value['part_sha256']['swift'] = C.digest(value['parts']['swift'])
        with self.assertRaises((ValueError, TypeError, KeyError)):
            C.verify_portable(value, value['source_graph'], value['identity'])

    def test_new_suites_cannot_use_only_unittest_or_skip(self):
        for owner in ('SideStore-preservation-serialization', 'SideStore-preservation-outcome', 'LiveContainer-postdispatch-wire'):
            for log in ('', 'Ran 3 tests in 0.1s\nOK\n', 'Ran 3 tests in 0.1s\nOK (skipped=1)\n'):
                with self.subTest(owner=owner, log=log): self.reject_log(owner, log)

    def test_serialization_native_markers_and_guards_exact(self):
        owner = 'SideStore-preservation-serialization'
        log = self.value['parts']['swift']['logs'][owner]
        for marker in (*C.SERIALIZATION_MARKERS, '37 serialization source guards passed; Swift/device not executed'):
            for count in (0, 2):
                with self.subTest(marker=marker, count=count): self.reject_log(owner, log.replace(marker+'\n', (marker+'\n')*count))
        self.reject_log(owner, log+'Ran 1 test in 0.1s\nOK\n')

    def test_actual_serialization_generator_receipt_closure(self):
        # These workflow controls run after exact source acquisition. Local
        # reviewed worktrees use SS; native workflows use sources/SideStore.
        harness = (_CIPath(__file__).resolve().parents[3])
        configured = os.environ.get('MAINTAINED_SIDESTORE_TEST_SOURCE')
        roots = ((Path(configured),) if configured else ()) + (harness.parent/'sources'/'SideStore', harness.parent/'SS')
        source = next((p for p in roots if (p/'tests/preservation_serialization/generate_fixture.py').is_file()), None)
        self.assertIsNotNone(source, 'actual acquired SideStore generator is required')
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, '-B', str(source/'tests/preservation_serialization/generate_fixture.py'), '--repo', str(source), '--out', tmp], check=True, capture_output=True, text=True)
            row = json.loads((Path(tmp)/'sources.json').read_text())
            self.assertEqual(set(row['inputs']), set(C.SERIALIZATION_INPUTS))
            self.assertEqual(row['source_guard_count'], 37)
            for path, digest in row['inputs'].items():
                self.assertEqual(C.sha(source/path), digest)
            self.assertEqual(C.sha(Path(tmp)/'preservation-serialization.swift'), row['generated_sha256'])
            # Synthetic markers exercise portable validation only. The source
            # generator explicitly reports no Swift or device execution.
            log = '37 serialization source guards passed; Swift/device not executed\n'+json.dumps(row)+'\n'+'\n'.join(C.SERIALIZATION_MARKERS)+'\n'
            C.suite_tests('SideStore-preservation-serialization', log)
            for omitted in ('tests/preservation_serialization/generate_fixture.py','tests/preservation_serialization/test_contract.py'):
                bad = copy.deepcopy(row); bad['inputs'].pop(omitted)
                with self.assertRaisesRegex(ValueError, 'source input closure'):
                    C.suite_tests('SideStore-preservation-serialization', log.replace(json.dumps(row), json.dumps(bad)))

    def test_actual_native_integration_claim_schema(self):
        # Exact finite JSON from official run41516599781 attempt1 artifact
        # 13175558545 SHA256 ec4bc0e8ec94b1c438b899b02279e02d3242c311ad2bf65879ceada2a9bb1ec8.
        # No profiles, identifiers or runtime values are retained in this fixture.
        row = json.loads(((_CIPath(__file__).resolve().parents[3]/'tests/ci/native/fixtures/preservation-integration-80.json')).read_text())
        self.assertEqual(row['cases'], list(C.PRESERVATION_INTEGRATION_CASES))
        good = self.value['parts']['swift']['logs']['SideStore-preservation']
        old = next(x for x in good.splitlines() if x.startswith('{') and json.loads(x)['schema']=='preservation-refresh-integration-v1')
        log = good.replace(old, json.dumps(row))
        # The frozen 80-case artifact remains historical proof under its frozen
        # validator. It must never satisfy the successor's additional native gate.
        spec = importlib.util.spec_from_file_location('historical_refresh_truth',
            _CIPath(__file__).resolve().parents[3]/'ci/packaging/zopfli/reviewed-native/refresh_truth_receipt.py')
        historical = importlib.util.module_from_spec(spec); spec.loader.exec_module(historical)
        historical.suite_tests('SideStore-preservation', log.replace('Ran 7 tests', 'Ran 5 tests'))
        self.reject_log('SideStore-preservation', log)
        # Only the synthetic fixture below extends that schema to current cases.
        row = {**row, 'cases': list(C.CURRENT_PRESERVATION_INTEGRATION_CASES)}
        log = good.replace(old, json.dumps(row))
        C.suite_tests('SideStore-preservation', log)
        for key in ('cms_verified','platform_tls_executed','portal_executed'):
            for bad in (True, 0, None, 'false'):
                changed = copy.deepcopy(row); changed[key] = bad
                self.reject_log('SideStore-preservation', log.replace(json.dumps(row), json.dumps(changed)))
            changed = copy.deepcopy(row); changed.pop(key)
            self.reject_log('SideStore-preservation', log.replace(json.dumps(row), json.dumps(changed)))
            duplicate = json.dumps(row)[:-1]+', '+json.dumps(key)+': false}'
            self.reject_log('SideStore-preservation', log.replace(json.dumps(row), duplicate))
        changed = copy.deepcopy(row); changed['unexpected_claim'] = False
        self.reject_log('SideStore-preservation', log.replace(json.dumps(row), json.dumps(changed)))
        policy = next(x for x in log.splitlines() if x.startswith('{') and json.loads(x)['schema']=='preservation-refresh-policy-v1')
        changed = json.loads(policy); changed['cms_verified'] = False
        self.reject_log('SideStore-preservation', log.replace(policy, json.dumps(changed)))

    def test_prior_serialization_inventory_cannot_claim_success(self):
        owner = 'SideStore-preservation-serialization'
        log = self.value['parts']['swift']['logs'][owner]
        self.assertEqual(len(C.SERIALIZATION_MARKERS), 13)
        for marker in C.SERIALIZATION_MARKERS:
            if marker.startswith('PASS_composed_preentry_'):
                log = log.replace(marker+'\n', '')
        log = log.replace('37 serialization source guards passed', '25 serialization source guards passed')
        row = json.loads(next(x for x in log.splitlines() if x.startswith('{')))
        old = json.dumps(row); row['source_guard_count'] = 25
        log = log.replace(old, json.dumps(row))
        self.reject_log(owner, log)

    def test_outcome_and_postdispatch_bridge_completion_exact(self):
        for owner, marker in [('SideStore-preservation-outcome','PASS 7 actual outcome cases and SS-to-LC wire'), ('SideStore-refresh-failure','PRESERVATION_POSTDISPATCH_REAL_BRIDGE_PASS')]:
            log = self.value['parts']['swift']['logs'][owner]
            for count in (0, 2): self.reject_log(owner, log.replace(marker+'\n',(marker+'\n')*count))

    def test_wire_result_cases_claims_and_source_seal_exact(self):
        owner = 'LiveContainer-postdispatch-wire'
        log = self.value['parts']['swift']['logs'][owner]
        row = json.loads(next(x for x in log.splitlines() if x.startswith('{')))
        header = log[:log.index('{')]
        self.reject_log(owner, log+json.dumps(row)+'\n')
        for key, value in [('status','STATIC_PASS'),('cases',list(reversed(C.POSTDISPATCH_CASES))),('cases',list(C.POSTDISPATCH_CASES[:1])),('device_executed',True),('network_executed',True),('inputs',{}),('extra','unowned')]:
            changed = copy.deepcopy(row); changed[key] = value
            self.reject_log(owner, header+json.dumps(changed)+'\n')
        for key in C.POSTDISPATCH_INPUTS:
            changed = copy.deepcopy(row); changed['inputs'].pop(key)
            self.reject_log(owner, header+json.dumps(changed)+'\n')
            changed = copy.deepcopy(row); changed['inputs'][key] = 'e'*64
            self.reject_log(owner, header+json.dumps(changed)+'\n')

    def test_serialization_source_receipt_cannot_claim_native_or_change_hash(self):
        owner = 'SideStore-preservation-serialization'
        log = self.value['parts']['swift']['logs'][owner]
        row = json.loads(next(x for x in log.splitlines() if x.startswith('{')))
        header = log[:log.index('{')]
        for key,value in [('source_guard_count',25),('source_guard_count',36),('source_guard_count',True),('swift_executed',True),('device_executed',True),('inputs',{}),('generated_sha256','invalid')]:
            changed = copy.deepcopy(row); changed[key] = value
            self.reject_log(owner, header+json.dumps(changed)+'\n')
        for key in C.SERIALIZATION_INPUTS:
            changed = copy.deepcopy(row); changed['inputs'][key] = 'e'*64
            self.reject_log(owner, header+json.dumps(changed)+'\n')

    def test_all_host_runtime_cases_required(self):
        owner='LiveContainer'; log=self.value['parts']['swift']['logs'][owner]
        self.assertGreaterEqual(len(C.LC_TRUTH_CASES),9)
        for name in C.LC_TRUTH_CASES:
            self.reject_log(owner,log.replace('PASS '+name+'\n',''))

    def test_all_suite_python_counts_match_reviewed_source_inventory(self):
        # Independently enumerated by AST audit of every actual suite entrypoint.
        # Shell entries execute their sibling test_contract.py explicitly; the
        # serialization argparse runner must never fabricate a unittest count.
        expected = {
            'LiveContainer':6, 'LiveContainer-postdispatch-wire':2,
            'LiveContainer-observations':5, 'LiveContainer-legacy':4,
            'SideStore':8, 'SideStore-preservation':7,
            'SideSign-portal-parser':3, 'LiveContainer-portal-wire':2,
            'SideStore-preservation-origin':5, 'SideStore-preservation-der':6,
            'SideStore-preservation-outcome':3,
            'SideStore-preservation-serialization':None,
            'SideStore-reuse-lifecycle':7, 'SideStore-refresh-failure':6,
            'SideStore-direct':4, 'SideStore-legacy-hooks':4,
            'SideStore-legacy-coordinator':4, 'SideStore/Dependencies/minimuxer':6,
        }
        self.assertEqual({key:spec[1] for key,spec in C.SUITES.items()},expected)
        for owner in ('SideStore-preservation','SideStore-preservation-origin'):
            log=self.value['parts']['swift']['logs'][owner]
            C.suite_tests(owner,log)
            count=C.SUITES[owner][1]
            self.reject_log(owner,log.replace('Ran '+str(count)+' tests','Ran '+str(count-1)+' tests'))

    def test_successor_inventory_remains_exact_and_source_owned(self):
        self.assertEqual((len(C.PRESERVATION_POLICY_CASES),len(C.PRESERVATION_INTEGRATION_CASES),len(C.ORIGIN_TRANSPORT_CASES)),(55,80,58))
        for key in C.POSTDISPATCH_INPUTS:
            self.assertIn(key,{o+'/'+p for o,paths in C.INPUTS.items() for p in paths})
        for key in C.SERIALIZATION_INPUTS: self.assertIn(key,C.INPUTS['SideStore'])
        self.assertIsNone(C.SUITES['SideStore-preservation-serialization'][1])

    def test_workflows_run_rejection_controls(self):
        root=(_CIPath(__file__).resolve().parents[3])
        for name, job in (('native-build.yml', None), ('focused-diagnostics.yml', 'cold_vpn'), ('focused-diagnostics.yml', 'refresh_truth')):
            self.assertIn('python3 -B harness/tests/ci/native/test_preservation_native_qualification.py',workflow_job(root/'.github/workflows'/name, job))
        runner=((root/'ci/qualification/refresh/run.py')).read_text()
        for key in ('V3_REQUIRE_SWIFT','V3_HOST_SOURCE_ROOT','V3_SIDESTORE_SOURCE_ROOT'): self.assertIn(key,runner)


if __name__ == '__main__': unittest.main(verbosity=2)
