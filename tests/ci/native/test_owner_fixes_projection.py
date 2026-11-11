"""Finite inverse controls; --sources also exercises the exact acquired owner blobs."""
import sys
from pathlib import Path
ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file())
sys.path.insert(0, str(ROOT))
import copy
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ci.qualification.native import owner_fixes_projection as P
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as current_owner
from ci.qualification.native import direct_host_projection as host
from ci.qualification.native import direct_service_projection as service
from ci.qualification.native import preservation_host_projection as preservation_host
from ci.qualification.native import preservation_localization_projection as localization
from ci.qualification.native import russian_setup_projection as russian
from ci.qualification.native import refresh_truth_setup_projection as truth
from ci.qualification.native import refresh_observation_setup_projection as observation
from ci.diagnostics.verify_setup_state_recovery import host_bundle_variant
from tests.ci.native import test_preservation_localization_projection as old_localization
from tests.ci.native import test_preservation_successor_projection as old_host


class OwnerFixProjectionTests(unittest.TestCase):
    def test_manifest_matches_final_graph_and_prior_direct_layers(self):
        graph = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
        specs = P.specifications()
        self.assertEqual(set(specs), {host.PATH, service.PATH,
            'LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift'})
        for path, spec in specs.items():
            owner, relative = path.split('/', 1)
            proof = current_owner.proof_commit(graph, owner)
            expected = portal.specifications().get(path, spec) if proof == portal.OWNER_COMMITS[owner][1] else spec
            self.assertEqual(expected['after_blob'], graph['reviewed_deltas'][owner]['files'][relative])
            self.assertTrue(spec['edits'])
        for direct in (host, service):
            self.assertEqual(specs[direct.PATH]['before_sha256'], direct.specification()['after_sha256'])

    def test_inverse_rejects_unrelated_changes_even_when_after_hash_is_repinned(self):
        before = b'kept\nold\nretained\n'; after = b'kept\nnew\nretained\n'
        spec = {'before_sha256': P.sha(before), 'after_sha256': P.sha(after),
                'before_blob': P.blob(before), 'after_blob': P.blob(after),
                'edits': [{'after_lines': [1, 2], 'before': 'old\n', 'after': 'new\n'}]}
        with patch.object(P, 'specifications', return_value={'source': spec}):
            self.assertEqual(P.restore_bytes('source', after), before)
            self.assertEqual(P.restore_bytes('source', before), before)
            with self.assertRaisesRegex(ValueError, 'Unreviewed'):
                P.restore_bytes('source', after + b'\n')
            changed = after.replace(b'retained', b'unrelated mutation')
            spec.update(after_sha256=P.sha(changed), after_blob=P.blob(changed))
            with self.assertRaisesRegex(ValueError, 'inverse changed historical'):
                P.restore_bytes('source', changed)
            spec['edits'][0]['after'] = 'not the reviewed edit\n'
            with self.assertRaisesRegex(ValueError, 'delta mismatch'):
                P.restore_bytes('source', changed)

    def test_normalization_is_exact_nonmutating_and_preserves_receipt_fields(self):
        for direct in (host, service):
            with self.subTest(path=direct.PATH):
                value = {'input_sha256': {direct.PATH: P.specifications()[direct.PATH]['after_sha256']},
                         'delivered_baseline': {'sha': 'unchanged'}, 'case_names': ['original'],
                         'receipt': {'identity': 'frozen'}}
                before = copy.deepcopy(value)
                result = direct.normalize(value)
                self.assertEqual(value, before)
                self.assertEqual(result['input_sha256'][direct.PATH], direct.specification()['before_sha256'])
                for key in ('delivered_baseline', 'case_names', 'receipt'):
                    self.assertEqual(result[key], value[key])
                value['input_sha256'][direct.PATH] = '0' * 64
                self.assertEqual(direct.normalize(value), value)

    def test_final_host_keeps_full_localization_chain_and_rejects_case_mutation(self):
        helper = old_localization.LocalizationSuccessor(); helper.setUp(); self.addCleanup(helper.doCleanups)
        value = json.loads((old_localization.HERE / 'setup-state-recovery-contract.json').read_text())
        for module in (russian, truth, observation):
            value['input_sha256'].update(module.extension()[0]['candidate_inputs'])
        value['input_sha256'].update(localization.PINS)
        actual = P.specifications()[host.PATH]['after_sha256']
        value['input_sha256'][host.PATH] = actual
        before = copy.deepcopy(value)
        result = localization.add(observation.add(truth.add(russian.add(value))))
        self.assertEqual(value, before)
        self.assertEqual(localization.variant(result), localization.CURRENT)
        self.assertEqual(result['input_sha256'][host.PATH], actual)
        self.assertEqual(result['delivered_baseline'], value['delivered_baseline'])
        self.assertEqual(len(result['case_names']['setup-localization']), 2079)
        for kind in ('hash', 'case', 'receipt'):
            bad = copy.deepcopy(result)
            if kind == 'hash': bad['input_sha256'][host.PATH] = '0' * 64
            elif kind == 'case': bad['case_names']['setup-localization'][0] = 'unreviewed-case'
            else: bad[observation.TAG]['manifest_sha256'] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                localization.variant(bad)

    def test_final_service_keeps_preservation_cases_and_historical_descriptor(self):
        helper = old_host.PreservationSuccessors(); helper.setUp(); self.addCleanup(helper.doCleanups)
        value = helper.host_base()
        value['input_sha256'][service.PATH] = P.specifications()[service.PATH]['after_sha256']
        before = copy.deepcopy(value)
        result = preservation_host.add(value)
        self.assertEqual(value, before)
        self.assertEqual(result['input_sha256'], value['input_sha256'])
        self.assertEqual(preservation_host.normalized(result)['case_names'], before['case_names'])
        self.assertEqual(host_bundle_variant(result)[1:], (35, 41))
        self.assertEqual(result[preservation_host.KEY]['inputs'], preservation_host.PINS)
        for kind in ('hash', 'case', 'receipt'):
            bad = copy.deepcopy(result)
            if kind == 'hash': bad['input_sha256'][service.PATH] = '0' * 64
            elif kind == 'case': bad['case_names']['host-refresh-bundle'][0] = 'unreviewed-case'
            else: bad[preservation_host.KEY]['inputs'][service.PATH] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                host_bundle_variant(bad)


def actual_source_controls(sources):
    """Explicit source-required lane: no synthetic fallback or prerequisite skip."""
    class ActualSourceControls(unittest.TestCase):
        def test_actual_owner_blobs_invert_and_reject_unrelated_mutations(self):
            current_owner.validate_sources(sources)
            for path, spec in P.specifications().items():
                with self.subTest(path=path):
                    owner, relative = path.split('/', 1)
                    root = sources / owner
                    def git(*args):
                        return subprocess.check_output(['git', '-C', str(root), *args])
                    graph = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
                    self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), graph['owners'][owner][1])
                    self.assertIn(current_owner.proof_commit(graph, owner), portal.OWNER_COMMITS[owner])
                    data = (sources / path).read_bytes()
                    self.assertEqual(data, git('show', 'HEAD:' + relative))
                    owner_data = portal.restore_if_successor(path, data) if path in portal.specifications() else data
                    self.assertEqual(P.blob(owner_data), spec['after_blob'])
                    prior = P.restore_bytes(path, owner_data)
                    self.assertEqual(prior, git('show', spec['before_commit'] + ':' + relative))
                    for mutation in (owner_data + b'\n', owner_data.replace(b' ', b'  ', 1)):
                        with self.assertRaises(ValueError): P.restore_bytes(path, mutation)
                        repinned = copy.deepcopy(P.specifications())
                        repinned[path].update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
                        with patch.object(P, 'specifications', return_value=repinned):
                            with self.assertRaises(ValueError): P.restore_bytes(path, mutation)
                    if path in (host.PATH, service.PATH):
                        direct = host if path == host.PATH else service
                        self.assertTrue(direct.validate_bytes(data))
                        with self.assertRaises(ValueError): direct.validate_bytes(data + b'\n')

        def test_complete_projection_retains_actual_hashes_baselines_and_receipts(self):
            from ci.build.project_candidate_contracts import contracts
            from ci.build.verify_current_bindings import verify
            from ci.diagnostics import verify_comprehensive_diagnostics as comprehensive
            from ci.diagnostics import verify_setup_state_recovery as setup
            self.assertEqual(verify(sources)['status'], 'PASS')
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                receipt = contracts(sources, output)
                self.assertEqual(contracts(sources, output, verify=True), receipt)
                for name, validator, inventory in (
                    ('comprehensive-diagnostics-contract.json', comprehensive.load_contract, comprehensive.source_inventory),
                    ('setup-state-recovery-contract.json', setup.load_contract, setup.inventory)):
                    contract = validator(output / name)
                    frozen = json.loads((ROOT / 'ci/qualification/native' / name).read_text())
                    for key in ('delivered_baseline', 'deferred_baseline'):
                        if key in frozen:
                            self.assertEqual(contract[key], frozen[key])
                    inventory(sources, contract)
                    for path, spec in P.specifications().items():
                        if path in contract['input_sha256']:
                            self.assertEqual(contract['input_sha256'][path], P.sha((sources / path).read_bytes()))
                    self.assertEqual(receipt['contracts'][name]['frozen_contract_sha256'],
                                     P.sha((ROOT / 'ci/qualification/native' / name).read_bytes()))
    return unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--sources', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OwnerFixProjectionTests)
    if args.sources is not None:
        suite.addTests(actual_source_controls(args.sources.resolve(strict=True)))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
