"""Finite inverse controls; --sources requires real current owner Git blobs.

These are source/projection checks. Portal parsing, selection and wire runtime
execution remain separate, mandatory owner-native lanes.
"""
import sys
from pathlib import Path
ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file())
sys.path.insert(0, str(ROOT))
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ci.qualification.native import portal_identity_projection as P
from ci.qualification.native import portal_owner_successor as current_owner
from ci.qualification.native import owner_fixes_projection as owner
from ci.qualification.native import preservation_selection_projection as selection
from ci.qualification.native import direct_host_projection as host
from ci.qualification.native import direct_service_projection as service
from ci.qualification.native import preservation_host_projection as preservation_host
from ci.qualification.native import preservation_localization_projection as localization
from ci.qualification.native import russian_setup_projection as russian
from ci.qualification.native import refresh_truth_setup_projection as truth
from ci.qualification.native import refresh_observation_setup_projection as observation
from ci.diagnostics.verify_setup_state_recovery import host_bundle_variant
from tests.ci.native import test_preservation_localization_projection as old_localization
from tests.ci.native import test_preservation_successor_projection as old_preservation


class PortalIdentityProjectionTests(unittest.TestCase):
    def test_manifest_binds_whole_files_and_unchanged_predecessors(self):
        specs = P.specifications()
        self.assertEqual(set(specs), {host.PATH, service.PATH, selection.PATH})
        graph = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
        for path, spec in specs.items():
            component, relative = path.split('/', 1)
            proof = current_owner.proof_commit(graph, component)
            self.assertIn(proof, P.OWNER_COMMITS[component])
            era = 'after' if proof == spec['after_commit'] else 'before'
            self.assertEqual(graph['reviewed_deltas'][component]['files'][relative], spec[era + '_blob'])
            for key in ('before_sha256', 'after_sha256'):
                self.assertRegex(spec[key], '^[a-f0-9]{64}$')
            for key in ('before_blob', 'after_blob'):
                self.assertRegex(spec[key], '^[a-f0-9]{40}$')
            self.assertTrue(spec['edits'])
        for path in (host.PATH, service.PATH):
            for field in ('sha256', 'blob', 'commit'):
                self.assertEqual(specs[path]['before_' + field], owner.specifications()[path]['after_' + field])
        self.assertEqual(specs[selection.PATH]['before_sha256'], selection.SHA)
        self.assertEqual(P.sha(owner.MANIFEST.read_bytes()), '73ca8c60a12b8cd416acd7e431794d86bec8947bbaa2ed92ed00543da87e0dee')
        self.assertEqual(P.sha(Path(owner.__file__).read_bytes()), '51538bf92fb8e5d9fe10614e4dbdfea777dd43eda4aaaf4b589a401147fa511d')

    def test_manifest_tampering_is_not_an_authorized_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_bytes(P.MANIFEST.read_bytes() + b'\n')
            with patch.object(P, 'MANIFEST', manifest), self.assertRaisesRegex(ValueError, 'Unreviewed.*manifest'):
                P.specifications()

    def test_inverse_rejects_repinned_unrelated_changes_and_wrong_blobs(self):
        before, after = b'kept\nold\nretained\n', b'kept\nnew\nretained\n'
        spec = {'before_sha256': P.sha(before), 'after_sha256': P.sha(after),
                'before_blob': P.blob(before), 'after_blob': P.blob(after),
                'edits': [{'after_lines': [1, 2], 'before': 'old\n', 'after': 'new\n'}]}
        with patch.object(P, 'specifications', return_value={'source': spec}):
            self.assertEqual(P.restore_bytes('source', before), before)
            self.assertEqual(P.restore_bytes('source', after), before)
            with self.assertRaisesRegex(ValueError, 'Unreviewed'):
                P.restore_bytes('source', after + b'\n')
            for key, data in (('before_blob', before), ('after_blob', after)):
                original = spec[key]; spec[key] = '0' * 40
                with self.subTest(key=key), self.assertRaises(ValueError):
                    P.restore_bytes('source', data)
                spec[key] = original
            changed = after.replace(b'retained', b'unrelated mutation')
            spec.update(after_sha256=P.sha(changed), after_blob=P.blob(changed))
            with self.assertRaisesRegex(ValueError, 'inverse changed historical'):
                P.restore_bytes('source', changed)
            spec['edits'][0]['after'] = 'unreviewed delta\n'
            with self.assertRaisesRegex(ValueError, 'delta mismatch'):
                P.restore_bytes('source', changed)

    def test_selection_keeps_exact_prior_descriptors_cases_and_actual_hash(self):
        original = old_preservation.PreservationSelectionSuccessor().base()
        current = copy.deepcopy(original)
        current['input_sha256'][P.SELECTION] = P.specifications()[P.SELECTION]['after_sha256']
        before = copy.deepcopy(current)
        result = selection.add(current)
        self.assertEqual(current, before)
        prior = selection.add(original)
        self.assertEqual(P.selection_normalized(result), prior)
        self.assertEqual(selection.normalized(result), selection.normalized(prior))
        self.assertEqual(result['input_sha256'], current['input_sha256'])
        self.assertEqual(result[selection.KEY], prior[selection.KEY])
        self.assertEqual(result[selection.prior.KEY], prior[selection.prior.KEY])
        self.assertEqual(result['case_names'], prior['case_names'])
        self.assertEqual(result['delivered_baseline'], prior['delivered_baseline'])
        self.assertEqual(result['additive_suite_inputs'], prior['additive_suite_inputs'])
        for mutate in (
            lambda c: c.pop(P.SELECTION_KEY),
            lambda c: c[P.SELECTION_KEY].update(extra=True),
            lambda c: c[P.SELECTION_KEY].update(git_blob='0' * 40),
            lambda c: c['input_sha256'].__setitem__(P.SELECTION, '0' * 64),
            lambda c: c['input_sha256'].__setitem__(P.SELECTION, selection.SHA),
            lambda c: c['input_sha256'].__setitem__(selection.MAPPER, '0' * 64),
            lambda c: c.pop(selection.KEY),
            lambda c: c.pop(selection.prior.KEY),
        ):
            bad = copy.deepcopy(result); mutate(bad)
            with self.assertRaises(ValueError): selection.inputs(bad, {'transport_fixture': ['frozen']})
        with self.assertRaises(ValueError): selection.add(result)

    def test_new_host_retains_all_localization_cases_and_receipts(self):
        helper = old_localization.LocalizationSuccessor(); helper.setUp(); self.addCleanup(helper.doCleanups)
        value = json.loads((old_localization.HERE / 'setup-state-recovery-contract.json').read_text())
        for module in (russian, truth, observation):
            value['input_sha256'].update(module.extension()[0]['candidate_inputs'])
        value['input_sha256'].update(localization.PINS)
        actual = P.specifications()[host.PATH]['after_sha256']
        value['input_sha256'][host.PATH] = actual
        result = localization.add(observation.add(truth.add(russian.add(value))))
        self.assertEqual(localization.variant(result), localization.CURRENT)
        self.assertEqual(result['input_sha256'][host.PATH], actual)
        self.assertEqual(result['delivered_baseline'], value['delivered_baseline'])
        self.assertEqual(len(result['case_names']['setup-localization']), 2079)
        prior = copy.deepcopy(value)
        prior['input_sha256'][host.PATH] = P.specifications()[host.PATH]['before_sha256']
        prior = localization.add(observation.add(truth.add(russian.add(prior))))
        self.assertEqual(P.normalize(result, host.PATH), prior)
        for kind in ('hash', 'case', 'receipt'):
            bad = copy.deepcopy(result)
            if kind == 'hash': bad['input_sha256'][host.PATH] = '0' * 64
            elif kind == 'case': bad['case_names']['setup-localization'][0] = 'unreviewed-case'
            else: bad[observation.TAG]['manifest_sha256'] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(ValueError): localization.variant(bad)

    def test_new_service_retains_all_preservation_cases_and_receipts(self):
        helper = old_preservation.PreservationSuccessors(); helper.setUp(); self.addCleanup(helper.doCleanups)
        value = helper.host_base()
        value['input_sha256'][service.PATH] = P.specifications()[service.PATH]['after_sha256']
        before = copy.deepcopy(value)
        result = preservation_host.add(value)
        self.assertEqual(value, before)
        self.assertEqual(result['input_sha256'], value['input_sha256'])
        self.assertEqual(preservation_host.normalized(result)['case_names'], before['case_names'])
        self.assertEqual(host_bundle_variant(result)[1:], (35, 41))
        self.assertEqual(result[preservation_host.KEY]['inputs'], preservation_host.PINS)
        prior = copy.deepcopy(value)
        prior['input_sha256'][service.PATH] = P.specifications()[service.PATH]['before_sha256']
        self.assertEqual(P.normalize(result, service.PATH), preservation_host.add(prior))
        for kind in ('hash', 'case', 'receipt'):
            bad = copy.deepcopy(result)
            if kind == 'hash': bad['input_sha256'][service.PATH] = '0' * 64
            elif kind == 'case': bad['case_names']['host-refresh-bundle'][0] = 'unreviewed-case'
            else: bad[preservation_host.KEY]['inputs'][service.PATH] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(ValueError): host_bundle_variant(bad)

    def test_runtime_receipt_binds_new_helper_manifest_and_selection_adapter(self):
        from ci.build.project_candidate_contracts import projection_source_hashes
        value = projection_source_hashes()
        for name in ('portal_identity_projection.py', 'portal-identity-source-delta.json',
                     'preservation_selection_projection.py', 'owner_fixes_projection.py',
                     'owner-fixes-source-delta.json', 'portal_owner_successor.py'):
            relative = 'ci/qualification/native/' + name
            self.assertEqual(value[relative], P.sha((ROOT / relative).read_bytes()))

    def test_source_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = Path(tmp).resolve(); target = sources / P.HOST
            target.parent.mkdir(parents=True)
            outside = sources / 'outside'; outside.write_bytes(b'not a source')
            target.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'linked'): P.read_source(sources, P.HOST)


def actual_source_controls(sources):
    class ActualSourceControls(unittest.TestCase):
        def test_exact_owner_git_blobs_invert_with_repinned_negative_controls(self):
            binding = current_owner.validate_sources(sources)
            for path, spec in P.specifications().items():
                component, relative = path.split('/', 1)
                def git(*args):
                    return subprocess.check_output(['git', '-C', str(sources / component), *args])
                with self.subTest(path=path):
                    self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), binding['current_owners'][component])
                    self.assertEqual(binding['proof_owners'][component], spec['after_commit'])
                    data = P.read_source(sources, path)
                    self.assertEqual(data, git('show', 'HEAD:' + relative))
                    self.assertEqual(P.blob(data), spec['after_blob'])
                    prior = P.restore_bytes(path, data)
                    self.assertEqual(prior, git('show', spec['before_commit'] + ':' + relative))
                    self.assertEqual(P.restore_bytes(path, prior), prior)
                    for mutation in (data + b'\n', data.replace(b' ', b'  ', 1),
                                     data.replace(b'preservation_portal_identity', b'unreviewed_portal_identity', 1)):
                        self.assertNotEqual(mutation, data)
                        with self.assertRaises(ValueError): P.restore_bytes(path, mutation)
                        repinned = copy.deepcopy(P.specifications())
                        repinned[path].update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
                        with patch.object(P, 'specifications', return_value=repinned):
                            with self.assertRaises(ValueError): P.restore_bytes(path, mutation)
                    if path in (host.PATH, service.PATH):
                        direct = host if path == host.PATH else service
                        self.assertTrue(direct.validate_bytes(data))
                        with self.assertRaises(ValueError): direct.validate_bytes(data + b'\n')
                        self.assertEqual(P.sha(owner.restore_bytes(path, prior)),
                                         owner.specifications()[path]['before_sha256'])

        def test_current_selection_and_mapper_pass_exact_existing_upstream_route(self):
            result = selection.validate_upstream_dependencies(sources)
            self.assertEqual(result, {'preservation': selection.descriptor(),
                                      'portal_identity': P.selection_descriptor()})
            self.assertEqual(P.validate_owner_chain(sources),
                             {path: spec['before_sha256'] for path, spec in owner.specifications().items()})

    return unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--sources', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PortalIdentityProjectionTests)
    if args.sources is not None:
        suite.addTests(actual_source_controls(args.sources.resolve(strict=True)))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
