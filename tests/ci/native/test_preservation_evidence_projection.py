"""Finite evidence inverse controls, not native execution or a device-fix claim.

Pass --sources to bind these checks to actual owner Git objects and working
bytes. Historical proof artifacts are deliberately never rewritten here.
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file())
sys.path.insert(0, str(ROOT))
from ci.qualification.native import preservation_evidence_projection as P
from ci.qualification.native import preservation_evidence_owner_successor as current_owner
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import preservation_selection_projection as selection
from tests.ci.native import test_preservation_successor_projection as old_preservation

HOST = 'LiveContainer/SideStoreSupport/SideStore.swift'
SERVICE = 'SideStore/AltStore/AppDelegate.swift'
SELECTION = 'SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift'
FETCH = 'SideStore/SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift'
COMMITS = {
    'LiveContainer': ('511e25b647c80bd070bef31e80b1020cfd4a5b7b', 'f59be2462026a70a957d309963ef91a9f29d893b'),
    'SideStore': ('b61990276f574c0f5a3dee95fd816ba957d50c6c', 'd2bf28820367a882fe0f1db93184a830db83c243'),
}
SIDESIGN = 'SideStore/Dependencies/SideSign'


def graph_pair():
    """Derive both finite eras without changing the checked-in graph."""
    specs = P.specifications()
    before = current_owner.normalized_graph(json.loads((ROOT / 'ci/build/candidate-graph.json').read_text()))
    for owner, spec in specs['owners'].items():
        before['owners'][owner][1] = spec['before_commit']
        before['reviewed_deltas'][owner]['tree'] = spec['before_tree']
        files = before['reviewed_deltas'][owner]['files']
        for line in spec['delta']:
            metadata, path = line.split('\t')
            old_mode, new_mode, old_blob, new_blob, status = metadata.split()
            if old_mode == ':000000':
                files.pop(path, None)
            else:
                files[path] = old_blob
    after = copy.deepcopy(before)
    for owner, spec in specs['owners'].items():
        after['owners'][owner][1] = spec['after_commit']
        after['reviewed_deltas'][owner]['tree'] = spec['after_tree']
        for line in spec['delta']:
            metadata, path = line.split('\t')
            old_mode, new_mode, old_blob, new_blob, status = metadata.split()
            if new_mode == '000000':
                after['reviewed_deltas'][owner]['files'].pop(path, None)
            else:
                after['reviewed_deltas'][owner]['files'][path] = new_blob
    return before, after


class PreservationEvidenceProjectionTests(unittest.TestCase):
    def test_full_source_suite_and_synthetic_negatives_have_mandatory_ci_callers(self):
        from ci.workflow_jobs import workflow_job
        before = '          python3 -B harness/tests/ci/native/test_portal_identity_projection.py --sources sources\n'
        added = '          python3 -B harness/tests/ci/native/test_preservation_evidence_projection.py --sources sources\n'
        for workflow, job in (('native-build.yml', None), ('focused-diagnostics.yml', 'cold_vpn'),
                              ('focused-diagnostics.yml', 'refresh_truth')):
            text = workflow_job(ROOT / '.github/workflows' / workflow, job)
            with self.subTest(workflow=workflow, job=job):
                self.assertEqual(text.count(added), 1)
                self.assertEqual(text.count(before + added), 1)
                self.assertNotIn('continue-on-error', text)
        text = (ROOT / '.github/workflows/source-integrity-contract.yml').read_text()
        before = '          python3 -B -m unittest discover -s tests/ci/native -p test_portal_owner_successor.py -v\n'
        added = '          python3 -B -m unittest discover -s tests/ci/native -p test_preservation_evidence_projection.py -v\n'
        self.assertEqual(text.count(added), 1)
        self.assertEqual(text.count(before + added), 1)
        self.assertNotIn('continue-on-error', text)

    def test_manifest_and_immutable_prior_provenance(self):
        specs = P.specifications()
        self.assertEqual(set(specs['files']), {HOST, SERVICE, SELECTION, FETCH})
        self.assertEqual(set(specs['owners']), set(COMMITS))
        self.assertEqual(P.MANIFEST_SHA256, '009ecb09fbed59f728e2d05d021e59a3c382f0e1b4439edb62aa691b31dbb424')
        self.assertEqual(P.sha(P.MANIFEST.read_bytes()), P.MANIFEST_SHA256)
        self.assertEqual(P.sha(portal.MANIFEST.read_bytes()),
                         'e8a48d420664880432fd04594351fd1ddaf9901606e435be67caef9d609beda4')
        self.assertEqual(P.sha(Path(portal.__file__).read_bytes()),
                         '97d4f13600589092d43b3b577511b62dac899c7fb11b7668f36c731147143263')
        self.assertEqual(sum(len(s['delta']) for s in specs['owners'].values()), 13)
        for owner, spec in specs['owners'].items():
            self.assertEqual((spec['before_commit'], spec['after_commit']), COMMITS[owner])
            self.assertRegex(spec['before_reviewed_delta_sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(spec['commit_chain'])
        for path, spec in specs['files'].items():
            self.assertEqual((spec['before_commit'], spec['after_commit']), COMMITS[path.split('/', 1)[0]])
            for era in ('before', 'after'):
                self.assertRegex(spec[era + '_sha256'], r'^[0-9a-f]{64}$')
                self.assertRegex(spec[era + '_blob'], r'^[0-9a-f]{40}$')
            self.assertTrue(spec['edits'])
            if path in portal.specifications():
                for field in ('sha256', 'blob'):
                    self.assertEqual(spec['before_' + field], portal.specifications()[path]['after_' + field])

    def test_manifest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'manifest.json'
            target.write_bytes(P.MANIFEST.read_bytes() + b'\n')
            with patch.object(P, 'MANIFEST', target), self.assertRaises(ValueError):
                P.specifications()

    def test_finite_inverse_checks_full_sha_blob_hunks_and_spans(self):
        before, after = b'kept\nold\nretained\n', b'kept\nnew\nretained\n'
        spec = {'before_sha256': P.sha(before), 'after_sha256': P.sha(after),
                'before_blob': P.blob(before), 'after_blob': P.blob(after),
                'edits': [{'after_lines': [1, 2], 'before': 'old\n', 'after': 'new\n'}]}
        manifest = {'files': {'source': spec}}
        with patch.object(P, 'specifications', return_value=manifest):
            self.assertEqual(P.restore_bytes('source', before), before)
            self.assertEqual(P.restore_bytes('source', after), before)
            self.assertEqual(P.restore_if_successor('source', after), before)
            for era, data in (('before', before), ('after', after)):
                for field, length in (('sha256', 64), ('blob', 40)):
                    key = era + '_' + field
                    saved = spec[key]; spec[key] = '0' * length
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        P.restore_bytes('source', data)
                    spec[key] = saved
            for span in ([-1, 2], [2, 1], [1, 20], [True, 2], ['1', 2]):
                spec['edits'][0]['after_lines'] = span
                with self.subTest(span=span), self.assertRaises(ValueError):
                    P.restore_bytes('source', after)
            spec['edits'][0]['after_lines'] = [1, 2]
            spec['edits'].append(copy.deepcopy(spec['edits'][0]))
            with self.assertRaises(ValueError): P.restore_bytes('source', after)
            spec['edits'].pop()
            spec['edits'][0]['after'] = 'wrong hunk\n'
            with self.assertRaises(ValueError): P.restore_bytes('source', after)
            spec['edits'][0]['after'] = 'new\n'
            mutation = after.replace(b'retained', b'unrelated mutation')
            with self.assertRaises(ValueError): P.restore_bytes('source', mutation)
            spec.update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
            with self.assertRaises(ValueError): P.restore_bytes('source', mutation)

    def test_normalization_preserves_every_prior_graph_field_without_mutation(self):
        before, after = graph_pair()
        saved = copy.deepcopy(after)
        self.assertEqual(P.normalized_graph(after), before)
        self.assertEqual(after, saved)
        self.assertEqual(P.normalized_graph(before), before)
        self.assertEqual(before['owners'][SIDESIGN][1], '082571590a6019972045a41a886caf4f27e66683')
        for owner, commits in COMMITS.items():
            self.assertEqual(P.proof_commit(after, owner), portal.OWNER_COMMITS[owner][1])

    def test_graph_rejects_unreviewed_pins_trees_blobs_and_partial_upgrade(self):
        before, after = graph_pair()
        for owner in COMMITS:
            for kind in ('pin', 'tree', 'blob', 'partial', 'extra'):
                bad = copy.deepcopy(after)
                if kind == 'pin': bad['owners'][owner][1] = '0' * 40
                elif kind == 'tree': bad['reviewed_deltas'][owner]['tree'] = '0' * 40
                elif kind == 'blob':
                    files = bad['reviewed_deltas'][owner]['files']
                    files[next(iter(files))] = '0' * 40
                elif kind == 'extra': bad['reviewed_deltas'][owner]['files']['unreviewed.swift'] = '0' * 40
                else:
                    bad['owners'][owner] = copy.deepcopy(before['owners'][owner])
                    bad['reviewed_deltas'][owner] = copy.deepcopy(before['reviewed_deltas'][owner])
                with self.subTest(owner=owner, kind=kind), self.assertRaises(ValueError):
                    P.normalized_graph(bad)
        bad = copy.deepcopy(after); bad['owners'][SIDESIGN][1] = '0' * 40
        with self.assertRaises(ValueError): P.normalized_graph(bad)

    def test_selection_retains_prior_descriptors_cases_and_receipts(self):
        base = old_preservation.PreservationSelectionSuccessor().base()
        base['input_sha256'][SELECTION] = portal.specifications()[SELECTION]['after_sha256']
        prior = selection.add(base)
        current = copy.deepcopy(prior)
        current['input_sha256'][SELECTION] = P.specifications()['files'][SELECTION]['after_sha256']
        current[P.SELECTION_KEY] = P.selection_descriptor()
        saved = copy.deepcopy(current)
        self.assertEqual(P.selection_normalized(current), prior)
        self.assertEqual(current, saved)
        for key in ('case_names', 'delivered_baseline', 'additive_suite_inputs', selection.KEY, portal.SELECTION_KEY):
            self.assertEqual(current[key], prior[key])
        for kind in ('missing', 'extra', 'blob', 'hash', 'stale'):
            bad = copy.deepcopy(current)
            if kind == 'missing': bad.pop(P.SELECTION_KEY)
            elif kind == 'extra': bad[P.SELECTION_KEY]['extra'] = True
            elif kind == 'blob': bad[P.SELECTION_KEY]['git_blob'] = '0' * 40
            elif kind == 'hash': bad['input_sha256'][SELECTION] = '0' * 64
            else: bad['input_sha256'][SELECTION] = P.specifications()['files'][SELECTION]['before_sha256']
            with self.subTest(kind=kind), self.assertRaises(ValueError): P.selection_normalized(bad)


def actual_source_controls(sources):
    class ActualSourceControls(unittest.TestCase):
        def test_current_git_objects_parent_chain_and_prior_receipt(self):
            before, after = graph_pair()
            binding = current_owner.validate_sources(sources)
            result = binding.get('evidence_binding', binding)
            self.assertIn('prior_binding', result)
            self.assertFalse(result['native_execution'])
            self.assertEqual(result['current_owners'], {owner: commits[1] for owner, commits in COMMITS.items()})
            self.assertEqual(result['proof_owners'], {owner: portal.OWNER_COMMITS[owner][1] for owner in COMMITS})
            self.assertEqual(result['prior_binding']['current_owners'], {owner: commits[0] for owner, commits in COMMITS.items()})
            self.assertEqual(result['prior_binding']['proof_owners'], result['proof_owners'])
            self.assertFalse(result['prior_binding']['native_execution'])
            current_owner.validate_owner_chain(sources)

        def test_standalone_owner_chain_rejects_every_bad_or_old_successor_input(self):
            reader = portal.read_source
            for path, spec in P.specifications()['files'].items():
                data = reader(sources, path)
                for changed in (data + b'\n', P.restore_bytes(path, data)):
                    with self.subTest(path=path, sha256=P.sha(changed)):
                        with patch.object(portal, 'read_source', side_effect=lambda root, name: changed if name == path else reader(root, name)):
                            with self.assertRaises(ValueError):
                                current_owner.validate_owner_chain(sources)

        def test_all_thirteen_changed_paths_and_exact_parent_edges(self):
            current_owner.validate_sources(sources)
            seen = []
            for owner, spec in P.specifications()['owners'].items():
                def git(*args):
                    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(sources / owner), *args])
                self.assertEqual(git('rev-parse', spec['after_commit'] + '^{tree}').decode().strip(), spec['after_tree'])
                self.assertEqual(git('rev-parse', spec['before_commit'] + '^{tree}').decode().strip(), spec['before_tree'])
                for entry in spec['commit_chain']:
                    self.assertEqual(git('rev-list', '--parents', '-n', '1', entry['commit']).decode().split(),
                                     [entry['commit'], *entry['parents']])
                    self.assertEqual(git('rev-parse', entry['commit'] + '^{tree}').decode().strip(), entry['tree'])
                delta = git('diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames',
                            '-r', spec['before_commit'], spec['after_commit']).decode().splitlines()
                self.assertEqual(delta, spec['delta'])
                for row in delta:
                    metadata, relative = row.split('\t')
                    new_blob = metadata.split()[3]
                    path = owner + '/' + relative
                    seen.append(path)
                    # Historical fixture bytes stay immutable. Current fixture
                    # successor bytes were independently bound above.
                    data = git('show', spec['after_commit'] + ':' + relative)
                    self.assertEqual(P.blob(data), new_blob)
                    current = (sources / path).read_bytes()
                    self.assertEqual(current, git('show', 'HEAD:' + relative))
                    if current != data:
                        self.assertIn(relative, current_owner.TEST_PATHS[owner])
            self.assertEqual(len(seen), 13)
            self.assertEqual(len(set(seen)), 13)

        def test_actual_four_blobs_invert_exactly_and_reject_repinned_mutations(self):
            specs = P.specifications()
            for path, spec in specs['files'].items():
                owner, relative = path.split('/', 1)
                def git(*args):
                    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(sources / owner), *args])
                with self.subTest(path=path):
                    graph = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
                    self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), graph['owners'][owner][1])
                    self.assertEqual(current_owner.evidence_commit(graph, owner), spec['after_commit'])
                    data = (sources / path).read_bytes()
                    self.assertEqual(data, git('show', spec['after_commit'] + ':' + relative))
                    self.assertEqual(P.sha(data), spec['after_sha256'])
                    self.assertEqual(P.blob(data), spec['after_blob'])
                    prior = P.restore_bytes(path, data)
                    self.assertEqual(prior, git('show', spec['before_commit'] + ':' + relative))
                    self.assertEqual(P.restore_bytes(path, prior), prior)
                    mutations = [data + b'\n', data.replace(b' ', b'  ', 1)]
                    for old, new in ((b'1_048_576', b'2_097_152'), (b'profileSizeLimit', b'noProfileLimit'),
                                     (b'teamMismatch', b'ignoreMismatch'), (b'prefixMismatch', b'ignorePrefix')):
                        if old in data: mutations.append(data.replace(old, new, 1))
                    if path == SELECTION: self.assertIn(b'1_048_576', data)
                    for mutation in mutations:
                        self.assertNotEqual(data, mutation)
                        with self.assertRaises(ValueError): P.restore_bytes(path, mutation)
                        repinned = copy.deepcopy(specs)
                        repinned['files'][path].update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
                        with patch.object(P, 'specifications', return_value=repinned), self.assertRaises(ValueError):
                            P.restore_bytes(path, mutation)

    return unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PreservationEvidenceProjectionTests)
    if args.sources is not None:
        suite.addTests(actual_source_controls(args.sources.resolve(strict=True)))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
