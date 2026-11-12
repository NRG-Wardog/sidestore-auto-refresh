"""Exact test-only successor controls; these are source proofs, not native runs."""
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
from ci.qualification.native import preservation_evidence_owner_successor as P
from ci.qualification.native import preservation_evidence_projection as evidence
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as prior

COMMITS = {
    'LiveContainer': ('f59be2462026a70a957d309963ef91a9f29d893b', 'c82033f094a57bcc86166c14b0297f0607321c69'),
    'SideStore': ('d2bf28820367a882fe0f1db93184a830db83c243', '9ba862f47d8a32e0548924d4a087b0e1e853b0d0'),
}
PATHS = {
    'LiveContainer': {'.ci/refresh-observations/test_refresh_observations.py', '.github/workflows/portal-identity-contract.yml'},
    'SideStore': {'.github/workflows/preservation-portal-contract.yml', 'tests/preservation_origin/generate_fixture.py',
                  'tests/preservation_origin/test_contract.py'},
}


def graph_pair():
    before = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
    specs = P.specifications()['owners']
    for owner, spec in specs.items():
        before['owners'][owner][1] = spec['before_commit']
        before['reviewed_deltas'][owner]['tree'] = spec['before_tree']
        for path, change in spec['changes'].items():
            before['reviewed_deltas'][owner]['files'][path] = change['before_blob']
    after = copy.deepcopy(before)
    for owner, spec in specs.items():
        after['owners'][owner][1] = spec['after_commit']
        after['reviewed_deltas'][owner]['tree'] = spec['after_tree']
        for path, change in spec['changes'].items():
            after['reviewed_deltas'][owner]['files'][path] = change['after_blob']
    return before, after


class EvidenceOwnerSuccessorTests(unittest.TestCase):
    def test_full_source_suite_and_synthetic_controls_have_mandatory_ci_callers(self):
        from ci.workflow_jobs import workflow_job
        before = '          python3 -B harness/tests/ci/native/test_preservation_evidence_projection.py --sources sources\n'
        added = '          python3 -B harness/tests/ci/native/test_preservation_evidence_owner_successor.py --sources sources\n'
        for workflow, job in (('native-build.yml', None), ('focused-diagnostics.yml', 'cold_vpn'),
                              ('focused-diagnostics.yml', 'refresh_truth')):
            text = workflow_job(ROOT / '.github/workflows' / workflow, job)
            with self.subTest(workflow=workflow, job=job):
                self.assertEqual(text.count(added), 1)
                self.assertEqual(text.count(before + added), 1)
                self.assertNotIn('continue-on-error', text)
        text = (ROOT / '.github/workflows/source-integrity-contract.yml').read_text()
        before = '          python3 -B -m unittest discover -s tests/ci/native -p test_preservation_evidence_projection.py -v\n'
        added = '          python3 -B -m unittest discover -s tests/ci/native -p test_preservation_evidence_owner_successor.py -v\n'
        self.assertEqual(text.count(added), 1)
        self.assertEqual(text.count(before + added), 1)
        self.assertNotIn('continue-on-error', text)

    def test_frozen_manifests_and_exact_test_only_closure(self):
        self.assertEqual(evidence.sha(evidence.MANIFEST.read_bytes()),
                         '009ecb09fbed59f728e2d05d021e59a3c382f0e1b4439edb62aa691b31dbb424')
        self.assertEqual(portal.sha(portal.MANIFEST.read_bytes()),
                         'e8a48d420664880432fd04594351fd1ddaf9901606e435be67caef9d609beda4')
        self.assertEqual(portal.sha(Path(portal.__file__).read_bytes()),
                         '97d4f13600589092d43b3b577511b62dac899c7fb11b7668f36c731147143263')
        self.assertEqual(P.MANIFEST_SHA256, 'f223280b970613a887e87237715c05461ecb977e7b8d51a5fdfae36f61445c41')
        self.assertEqual(P.sha(P.MANIFEST.read_bytes()), P.MANIFEST_SHA256)
        specs = P.specifications()['owners']
        self.assertEqual(set(specs), set(COMMITS))
        for owner, spec in specs.items():
            self.assertEqual((spec['before_commit'], spec['after_commit']), COMMITS[owner])
            self.assertEqual(set(spec['changes']), PATHS[owner])
            self.assertEqual(len(spec['delta']), len(PATHS[owner]))
            for row in spec['delta']:
                metadata, path = row.split('\t')
                old_mode, new_mode, old_blob, new_blob, status = metadata.split()
                self.assertEqual((old_mode, new_mode, status), (':100644', '100644', 'M'))
                self.assertIn(path, PATHS[owner])
                self.assertEqual((old_blob, new_blob), (spec['changes'][path]['before_blob'], spec['changes'][path]['after_blob']))

    def test_manifest_digest_rejects_even_whitespace_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.json'
            path.write_bytes(P.MANIFEST.read_bytes() + b'\n')
            with patch.object(P, 'MANIFEST', path), self.assertRaises(ValueError): P.specifications()

    def test_repinned_manifest_still_rejects_product_paths_modes_status_and_pins(self):
        original = P.specifications()
        for kind in ('product', 'extra', 'mode', 'status', 'pin', 'missing'):
            value = copy.deepcopy(original)
            owner = value['owners']['LiveContainer']
            if kind == 'product':
                key = next(iter(owner['changes']))
                owner['changes']['SideStoreSupport/SideStore.swift'] = owner['changes'].pop(key)
            elif kind == 'extra': owner['changes']['extra.py'] = copy.deepcopy(next(iter(owner['changes'].values())))
            elif kind == 'missing': owner['changes'].pop(next(iter(owner['changes'])))
            elif kind == 'mode': owner['delta'][0] = owner['delta'][0].replace(':100644 100644', ':100644 100755', 1)
            elif kind == 'status': owner['delta'][0] = owner['delta'][0].replace(' M\t', ' A\t', 1)
            else: owner['after_commit'] = '0' * 40
            raw = json.dumps(value).encode()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'manifest.json'; path.write_bytes(raw)
                with patch.object(P, 'MANIFEST', path), patch.object(P, 'MANIFEST_SHA256', P.sha(raw)):
                    with self.subTest(kind=kind), self.assertRaises(ValueError): P.specifications()

    def test_graph_inverse_preserves_complete_prior_graph_and_production_proofs(self):
        before, after = graph_pair(); saved = copy.deepcopy(after)
        self.assertEqual(P.normalized_graph(after), before)
        self.assertEqual(after, saved)
        self.assertEqual(P.normalized_graph(before), before)
        for owner, commits in COMMITS.items():
            self.assertEqual(P.evidence_commit(after, owner), commits[0])
            self.assertEqual(P.proof_commit(after, owner), portal.OWNER_COMMITS[owner][1])
        for path, spec in evidence.specifications()['files'].items():
            owner, relative = path.split('/', 1)
            self.assertEqual(after['reviewed_deltas'][owner]['files'][relative], spec['after_blob'])
            self.assertEqual(before['reviewed_deltas'][owner]['files'][relative], spec['after_blob'])

    def test_unknown_partial_pin_tree_blob_extra_and_production_graph_mutations_fail(self):
        before, after = graph_pair()
        for owner in COMMITS:
            for kind in ('pin', 'partial', 'tree', 'blob', 'extra', 'product'):
                bad = copy.deepcopy(after)
                if kind == 'pin': bad['owners'][owner][1] = '0' * 40
                elif kind == 'partial':
                    bad['owners'][owner] = copy.deepcopy(before['owners'][owner])
                    bad['reviewed_deltas'][owner] = copy.deepcopy(before['reviewed_deltas'][owner])
                elif kind == 'tree': bad['reviewed_deltas'][owner]['tree'] = '0' * 40
                else:
                    relative = 'Unreviewed.swift' if kind == 'extra' else sorted(PATHS[owner])[0]
                    if kind == 'product': relative = (portal.HOST if owner == 'LiveContainer' else portal.SERVICE).split('/', 1)[1]
                    bad['reviewed_deltas'][owner]['files'][relative] = '0' * 40
                with self.subTest(owner=owner, kind=kind), self.assertRaises(ValueError): P.normalized_graph(bad)


def actual_source_controls(sources):
    class ActualSourceControls(unittest.TestCase):
        def test_current_objects_exact_parent_delta_working_bytes_and_unchanged_production(self):
            before, after = graph_pair()
            binding = P.validate_sources(sources, after)
            self.assertEqual(binding['current_owners'], {o: v[1] for o, v in COMMITS.items()})
            self.assertEqual(binding['evidence_owners'], {o: v[0] for o, v in COMMITS.items()})
            self.assertEqual(binding['proof_owners'], {o: portal.OWNER_COMMITS[o][1] for o in COMMITS})
            self.assertEqual(binding['evidence_binding']['current_owners'], binding['evidence_owners'])
            self.assertEqual(len(binding['test_owner_successor']['current_inputs']), 17)
            self.assertIs(binding['native_execution'], False)
            self.assertIs(binding['evidence_binding']['native_execution'], False)
            for owner, spec in P.specifications()['owners'].items():
                def git(*args): return prior.git(sources / owner, *args)
                self.assertEqual(git('rev-parse', 'HEAD').decode().strip(), spec['after_commit'])
                self.assertEqual(prior.commit_info(sources / owner, spec['after_commit']), (spec['after_tree'], [spec['before_commit']]))
                delta = git('diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames', '-r', spec['before_commit'], spec['after_commit']).decode().splitlines()
                self.assertEqual(delta, spec['delta'])
                for relative, change in spec['changes'].items():
                    data = (sources / owner / relative).read_bytes()
                    self.assertEqual(data, git('cat-file', 'blob', spec['after_commit'] + ':' + relative))
                    self.assertEqual((P.sha(data), P.blob(data)), (change['after_sha256'], change['after_blob']))
            for path, spec in evidence.specifications()['files'].items():
                owner, relative = path.split('/', 1)
                data = (sources / path).read_bytes()
                self.assertEqual(P.sha(data), spec['after_sha256'])
                for commit in COMMITS[owner]:
                    self.assertEqual(data, prior.git(sources / owner, 'cat-file', 'blob', commit + ':' + relative))
                restored = evidence.restore_bytes(path, data)
                self.assertEqual(restored, prior.git(sources / owner, 'cat-file', 'blob', spec['before_commit'] + ':' + relative))

        def test_current_working_test_and_production_mutations_are_rejected_without_disk_edits(self):
            _, after = graph_pair(); original_read = portal.read_source
            targets = {o + '/' + path for o, paths in PATHS.items() for path in paths}
            targets |= {o + '/' + path for o, spec in evidence.specifications()['owners'].items()
                        for path in spec['changes']}
            self.assertEqual(len(targets), 17)
            for target in sorted(targets):
                def changed(root, path):
                    data = original_read(root, path)
                    return data + b'unreviewed mutation\n' if path == target else data
                with patch.object(portal, 'read_source', side_effect=changed):
                    with self.subTest(path=target), self.assertRaises(ValueError): P.validate_sources(sources, after)

        def test_repinned_test_bytes_still_require_committed_git_equality(self):
            specs = P.specifications(); _, after = graph_pair()
            owner = 'LiveContainer'; relative = sorted(PATHS[owner])[0]; target = owner + '/' + relative
            original_read = portal.read_source
            mutation = original_read(sources, target) + b'unreviewed mutation\n'
            repinned = copy.deepcopy(specs)
            repinned['owners'][owner]['changes'][relative].update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
            after['reviewed_deltas'][owner]['files'][relative] = P.blob(mutation)
            def changed(root, path): return mutation if path == target else original_read(root, path)
            with patch.object(P, 'specifications', return_value=repinned), patch.object(portal, 'read_source', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'working/Git bytes'): P.validate_sources(sources, after)

        def test_wrong_head_parent_full_delta_and_mode_are_rejected(self):
            _, after = graph_pair(); original_git = prior.git; original_info = prior.commit_info
            for kind in ('head', 'parent', 'delta', 'mode'):
                def git(root, *args):
                    raw = original_git(root, *args)
                    if Path(root).name == 'LiveContainer':
                        if kind == 'head' and args == ('rev-parse', 'HEAD'): return (COMMITS['LiveContainer'][0] + '\n').encode()
                        if args[0] == 'diff-tree':
                            if kind == 'mode': return raw.replace(b':100644 100644', b':100644 100755', 1)
                            if kind == 'delta': return raw + b':100644 100644 ' + b'0' * 40 + b' ' + b'1' * 40 + b' M\tUnreviewed.swift\n'
                    return raw
                def info(root, commit):
                    tree, parents = original_info(root, commit)
                    if kind == 'parent' and commit == COMMITS['LiveContainer'][1]: parents = ['0' * 40]
                    return tree, parents
                with patch.object(prior, 'git', side_effect=git), patch.object(prior, 'commit_info', side_effect=info):
                    with self.subTest(kind=kind), self.assertRaises(ValueError): P.validate_sources(sources, after)

        def test_committed_evidence_replay_requires_exact_thirteen_raw_inputs(self):
            before, after = graph_pair()
            specs = evidence.specifications()
            inputs = {owner + '/' + relative: prior.git(sources / owner, 'cat-file', 'blob', spec['after_commit'] + ':' + relative)
                      for owner, spec in specs['owners'].items() for relative in spec['changes']}
            self.assertEqual(len(inputs), 13)
            self.assertEqual(evidence.validate_committed_sources(sources, before, inputs),
                             P.validate_sources(sources, after)['evidence_binding'])
            for path in inputs:
                for kind in ('missing', 'text', 'changed'):
                    bad = dict(inputs)
                    if kind == 'missing': bad.pop(path)
                    elif kind == 'text': bad[path] = bad[path].decode()
                    else: bad[path] += b'unreviewed mutation\n'
                    with self.subTest(path=path, kind=kind), self.assertRaises(ValueError):
                        evidence.validate_committed_sources(sources, before, bad)
            with self.assertRaises(ValueError):
                evidence.validate_committed_sources(sources, before, {**inputs, 'extra': b'new'})
            # A locally repinned SHA/blob cannot replace an actual historical object.
            target = portal.HOST
            owner, relative = target.split('/', 1)
            bad = dict(inputs); bad[target] += b'unreviewed mutation\n'
            repinned = copy.deepcopy(specs)
            repinned['owners'][owner]['changes'][relative].update(after_sha256=P.sha(bad[target]), after_blob=P.blob(bad[target]))
            graph = copy.deepcopy(before)
            graph['reviewed_deltas'][owner]['files'][relative] = P.blob(bad[target])
            with patch.object(evidence, 'specifications', return_value=repinned):
                with self.assertRaisesRegex(ValueError, 'working bytes differ'):
                    evidence.validate_committed_sources(sources, graph, bad)
            # A historical proof is not a substitute for the current-source entry point.
            with self.assertRaisesRegex(ValueError, 'checkout pin differs'):
                evidence.validate_sources(sources, before)

        def test_owner_chain_checks_current_binding_before_frozen_inverse(self):
            with patch.object(P, 'validate_sources', side_effect=ValueError('current binding rejected')):
                with self.assertRaisesRegex(ValueError, 'current binding rejected'): P.validate_owner_chain(sources)

    return unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--sources', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceOwnerSuccessorTests)
    if args.sources is not None: suite.addTests(actual_source_controls(args.sources.resolve(strict=True)))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
