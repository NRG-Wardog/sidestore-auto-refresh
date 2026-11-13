"""Mapping/prompt source proofs only; no S15 or native/device-success claim."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file())
sys.path.insert(0, str(ROOT))
from ci.qualification.native import functional_recovery_projection as P
from ci.qualification.native import preservation_evidence_owner_successor as previous
from ci.qualification.native import preservation_evidence_projection as evidence
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as G
from ci.qualification.native import setup_prompt_fixture_projection as prompt_fixture

COMMITS = {
    'LiveContainer': ('c82033f094a57bcc86166c14b0297f0607321c69', '68561eb6dbbc5b6e0bfe4591401af1cb4f500d04'),
    'SideStore': ('9ba862f47d8a32e0548924d4a087b0e1e853b0d0', '9bfb1822e9153c622ef8cda88cd4247038a8feeb'),
    G.SIDESIGN: ('082571590a6019972045a41a886caf4f27e66683', '594d3426794d40c7bfde6ace0fba9d6309c214a6'),
}


def graph_pair():
    specs = P.specifications()['owners']
    before = json.loads((ROOT / 'ci/build/candidate-graph.json').read_text())
    for owner, spec in specs.items():
        before['owners'][owner][1] = spec['before_commit']
        row = before['reviewed_deltas'][owner]; row['tree'] = spec['before_tree']
        for path, change in spec['changes'].items():
            if change['before_graph_present']: row['files'][path] = change['before_graph_blob']
            else: row['files'].pop(path, None)
    after = copy.deepcopy(before)
    for owner, spec in specs.items():
        after['owners'][owner][1] = spec['after_commit']
        row = after['reviewed_deltas'][owner]; row['tree'] = spec['after_tree']
        for path, change in spec['changes'].items(): row['files'][path] = change['after_blob']
    return before, after


class FunctionalRecoveryTests(unittest.TestCase):
    def test_exact_narrowed_owners_frozen_provenance_and_historical_closure(self):
        self.assertEqual(P.OWNER_COMMITS, COMMITS)
        for module, digest in (
            (previous, 'f223280b970613a887e87237715c05461ecb977e7b8d51a5fdfae36f61445c41'),
            (evidence, '009ecb09fbed59f728e2d05d021e59a3c382f0e1b4439edb62aa691b31dbb424'),
            (portal, 'e8a48d420664880432fd04594351fd1ddaf9901606e435be67caef9d609beda4')):
            self.assertEqual(P.sha(module.MANIFEST.read_bytes()), digest)
        self.assertEqual(len(previous.committed_paths()), 17)
        side = {G.SIDESIGN + '/' + p for p in G.TEST_PATHS}
        self.assertEqual(P.historical_paths(), previous.committed_paths() | side)
        self.assertEqual(len(P.historical_paths()), 20)

    def test_finite_inverse_sha_blob_hunk_span_and_repinned_unrelated_mutation(self):
        before, after = b'keep\nold\nretained\n', b'keep\nnew\nretained\n'
        spec = {'before_sha256': P.sha(before), 'after_sha256': P.sha(after),
                'before_blob': P.blob(before), 'after_blob': P.blob(after),
                'edits': [{'after_lines': [1, 2], 'before': 'old\n', 'after': 'new\n'}]}
        with patch.object(P, 'specifications', return_value={'files': {'source': spec}}):
            self.assertEqual(P.restore_bytes('source', before), before)
            self.assertEqual(P.restore_bytes('source', after), before)
            for era, data in (('before', before), ('after', after)):
                for field, size in (('sha256', 64), ('blob', 40)):
                    key = era + '_' + field; saved = spec[key]; spec[key] = '0' * size
                    with self.subTest(key=key), self.assertRaises(ValueError): P.restore_bytes('source', data)
                    spec[key] = saved
            for span in ([-1, 2], [2, 1], [1, 20], [True, 2], ['1', 2]):
                spec['edits'][0]['after_lines'] = span
                with self.subTest(span=span), self.assertRaises(ValueError): P.restore_bytes('source', after)
            spec['edits'][0]['after_lines'] = [1, 2]
            spec['edits'].append(copy.deepcopy(spec['edits'][0]))
            with self.assertRaises(ValueError): P.restore_bytes('source', after)
            spec['edits'].pop(); spec['edits'][0]['after'] = 'wrong\n'
            with self.assertRaises(ValueError): P.restore_bytes('source', after)
            spec['edits'][0]['after'] = 'new\n'
            changed = after.replace(b'retained', b'unrelated mutation')
            spec.update(after_sha256=P.sha(changed), after_blob=P.blob(changed))
            with self.assertRaisesRegex(ValueError, 'historical source'): P.restore_bytes('source', changed)

    def test_added_file_empty_inverse_and_pure_contract_normalization(self):
        after = b'new file\n'
        spec = {'before_sha256': P.sha(b''), 'after_sha256': P.sha(after), 'before_blob': P.blob(b''),
                'after_blob': P.blob(after), 'edits': [{'after_lines': [0, 1], 'before': '', 'after': 'new file\n'}]}
        contract = {'input_sha256': {'added': P.sha(after), 'untouched': 'frozen'}, 'receipt': ['keep']}
        saved = copy.deepcopy(contract)
        with patch.object(P, 'specifications', return_value={'files': {'added': spec}}):
            self.assertEqual(P.restore_bytes('added', after), b'')
            self.assertEqual(P.restore_if_successor('added', after), b'')
            result = P.normalize(contract, 'added')
            self.assertEqual(result['input_sha256']['added'], P.sha(b''))
            self.assertEqual(result['receipt'], contract['receipt'])
            self.assertEqual(contract, saved)

    def test_pinned_manifest_and_tampering(self):
        specs = P.specifications()
        self.assertEqual(P.MANIFEST_SHA256, 'cadefc4dfcd682db70948d20c47f4bec86b2ff7c05b032e373fdcd20ae44ee8f')
        self.assertEqual(P.sha(P.MANIFEST.read_bytes()), P.MANIFEST_SHA256)
        self.assertEqual(len(specs['files']), 16)
        self.assertEqual(sum(len(s['delta']) for s in specs['owners'].values()), 17)
        for owner, spec in specs['owners'].items():
            self.assertEqual((spec['before_commit'], spec['after_commit']), COMMITS[owner])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'manifest.json'; target.write_bytes(P.MANIFEST.read_bytes() + b'\n')
            with patch.object(P, 'MANIFEST', target), self.assertRaises(ValueError): P.specifications()

    def test_graph_inverse_is_complete_pure_and_rejects_unrelated_changes(self):
        before, after = graph_pair(); saved = copy.deepcopy(after)
        self.assertEqual(P.normalized_graph(after), before)
        self.assertEqual(after, saved)
        self.assertEqual(P.normalized_graph(before), before)
        for owner in COMMITS:
            for kind in ('partial', 'pin', 'tree', 'blob', 'extra', 'baseline'):
                bad = copy.deepcopy(after)
                if kind == 'partial': bad['owners'][owner][1] = COMMITS[owner][0]
                elif kind == 'pin': bad['owners'][owner][1] = '0' * 40
                elif kind == 'tree': bad['reviewed_deltas'][owner]['tree'] = '0' * 40
                elif kind == 'baseline': bad['reviewed_deltas'][owner]['baseline'] = '0' * 40
                else:
                    path = next(iter(bad['reviewed_deltas'][owner]['files'])) if kind == 'blob' else 'Unreviewed.swift'
                    bad['reviewed_deltas'][owner]['files'][path] = '0' * 40
                with self.subTest(owner=owner, kind=kind), self.assertRaises(ValueError): P.normalized_graph(bad)

    def test_contract_builder_must_run_current_binding_and_owner_chain_first(self):
        from ci.build import project_candidate_contracts as builder
        with patch.object(P, 'validate_sources', side_effect=ValueError('current proof required')) as check:
            with self.assertRaisesRegex(ValueError, 'current proof required'):
                builder.contracts(ROOT, ROOT / 'not-created-by-test')
            check.assert_called_once_with(ROOT)
        with patch.object(P, 'validate_sources', return_value={}) as check, \
             patch.object(P, 'validate_owner_chain', side_effect=ValueError('owner inverse required')) as chain:
            with self.assertRaisesRegex(ValueError, 'owner inverse required'):
                builder.contracts(ROOT, ROOT / 'not-created-by-test')
            check.assert_called_once_with(ROOT)
            chain.assert_called_once_with(ROOT)

    def test_runtime_receipt_and_required_workflow_callers_include_new_proof(self):
        from ci.build.project_candidate_contracts import projection_source_hashes
        hashes = projection_source_hashes()
        for name in ('functional_recovery_projection.py', 'functional-recovery-source-delta.json'):
            path = 'ci/qualification/native/' + name
            self.assertEqual(hashes[path], P.sha((ROOT / path).read_bytes()))
        command = 'python3 -B harness/tests/ci/native/test_functional_recovery_projection.py --sources sources'
        for workflow, minimum in (('native-build.yml', 1), ('focused-diagnostics.yml', 2)):
            text = (ROOT / '.github/workflows' / workflow).read_text()
            prior_command = 'python3 -B harness/tests/ci/native/test_preservation_evidence_owner_successor.py --sources sources'
            self.assertGreaterEqual(text.count(prior_command + '\n          ' + command), minimum)
        text = (ROOT / '.github/workflows/source-integrity-contract.yml').read_text()
        self.assertIn('python3 -B -m unittest discover -s tests/ci/native -p test_preservation_evidence_owner_successor.py -v\n          '
                      'python3 -B -m unittest discover -s tests/ci/native -p test_functional_recovery_projection.py -v', text)

    def test_setup_prompt_wrapper_exact_pins_command_and_descriptor_guards(self):
        W = prompt_fixture
        self.assertEqual(W.PINS, {
            W.TEST: '1222f013816035a0ec590c6557cb7b07e6a37983d0cb77dd5ecf5ca1f0e1b655',
            W.GENERATOR: '154c18a26e69a8be402ff61a563f57c28991f48d1fe690a02252406a70874e36'})
        contract = {'input_sha256': {P.LOCALIZATION: W.descriptor()['current_sha256']}}
        original = [sys.executable, '-B', ROOT / 'sources' / W.TEST]
        expected = [sys.executable, '-B', Path(W.__file__).resolve(), '--sources', ROOT / 'sources']
        self.assertEqual(W.command(contract, original), expected)
        self.assertEqual(W.command({'input_sha256': {P.LOCALIZATION: W.descriptor()['historical_sha256']}}, original), original)
        for bad in ([sys.executable, str(original[2])], [sys.executable, '-O', original[2]],
                    [sys.executable, '-B', ROOT / 'other.py'], original + ['--extra']):
            with self.subTest(command=bad), self.assertRaises(ValueError): W.command(contract, bad)
        valid = {W.KEY: W.descriptor(), 'strict_prose_and_original_copy': W.CLAIM}
        W.verify_static(valid, contract)
        for kind in ('missing', 'extra', 'sha', 'claim'):
            bad = copy.deepcopy(valid)
            if kind == 'missing': bad.pop(W.KEY)
            elif kind == 'extra': bad[W.KEY]['extra'] = True
            elif kind == 'sha': bad[W.KEY]['current_sha256'] = '0' * 64
            else: bad['strict_prose_and_original_copy'] = 'unchanged'
            with self.subTest(kind=kind), self.assertRaises(ValueError): W.verify_static(bad, contract)
        with self.assertRaises(ValueError): W.verify_static(valid, {'input_sha256': {P.LOCALIZATION: '0' * 64}})
        W.verify_static({}, {'input_sha256': {}})
        from ci.build.project_candidate_contracts import projection_source_hashes
        relative = 'ci/qualification/native/setup_prompt_fixture_projection.py'
        self.assertEqual(projection_source_hashes()[relative], P.sha((ROOT / relative).read_bytes()))

    def test_setup_prompt_renderer_requires_current_bytes_and_exact_inverse(self):
        W = prompt_fixture
        before, after = b'old\nkept\n', b'new\nkept\n'
        spec = {'before_sha256': P.sha(before), 'after_sha256': P.sha(after),
                'before_blob': P.blob(before), 'after_blob': P.blob(after),
                'edits': [{'after_lines': [0, 1], 'before': 'old\n', 'after': 'new\n'}]}
        with patch.object(P, 'specifications', return_value={'files': {P.LOCALIZATION: spec}}):
            self.assertEqual(W.restore_renderer(after.decode()), before.decode())
            for bad in (before, after + b'new mutation\n'):
                with self.assertRaises(ValueError): W.restore_renderer(bad.decode())
            mutation = after.replace(b'kept', b'unrelated')
            spec.update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
            with self.assertRaisesRegex(ValueError, 'historical source'): W.restore_renderer(mutation.decode())

    def test_owner_chain_must_validate_current_binding(self):
        with patch.object(P, 'validate_sources', side_effect=ValueError('current binding rejected')):
            with self.assertRaisesRegex(ValueError, 'current binding rejected'): P.validate_owner_chain(ROOT)


def actual_source_controls(sources):
    class ActualSourceControls(unittest.TestCase):
        def test_setup_prompt_exact_test_transform_rejects_tamper_and_repinned_boundaries(self):
            W = prompt_fixture; raw = portal.read_source(sources, W.TEST)
            projected = W.project_test(raw)
            self.assertEqual(projected, raw.decode().replace(W.ORIGINAL, W.REVIEWED, 1))
            self.assertEqual(projected.replace(W.REVIEWED, W.ORIGINAL, 1).encode(), raw)
            self.assertIn("guard depth < 9 else { return nil }", W.REVIEWED)
            with self.assertRaises(ValueError): W.project_test(raw + b'\n')
            for mutation in (raw.replace(W.ORIGINAL.encode(), b'pass\n', 1), raw + W.ORIGINAL.encode(),
                             raw + W.REVIEWED.encode()):
                with patch.dict(W.PINS, {W.TEST: P.sha(mutation)}), self.assertRaises(ValueError): W.project_test(mutation)

        def test_setup_prompt_run_enforces_current_binding_roots_and_pinned_git_bytes(self):
            W = prompt_fixture; original_read = portal.read_source
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / 'output'; baseline = Path(tmp)
                with patch.object(P, 'validate_sources', side_effect=ValueError('current binding required')):
                    with self.assertRaisesRegex(ValueError, 'current binding required'):
                        W.run(sources, sources / 'LiveContainer', baseline, out)
                with self.assertRaisesRegex(ValueError, 'candidate root'):
                    W.run(sources, sources / 'SideStore', baseline, out)
                with self.assertRaisesRegex(ValueError, 'fresh and outside'):
                    W.run(sources, sources / 'LiveContainer', baseline, sources / 'forbidden-output')
                for target in W.PINS:
                    changed = original_read(sources, target) + b'\n# unreviewed caller mutation\n'
                    def read(root, path): return changed if path == target else original_read(root, path)
                    # Bypass only the separately tested outer guard to reach the wrapper's own Git comparison.
                    with patch.object(P, 'validate_sources', return_value={}), patch.object(portal, 'read_source', side_effect=read):
                        with self.subTest(path=target), self.assertRaisesRegex(ValueError, 'working/Git bytes'):
                            W.run(sources, sources / 'LiveContainer', baseline, out)
                        with patch.dict(W.PINS, {target: P.sha(changed)}), self.assertRaisesRegex(ValueError, 'working/Git bytes'):
                            W.run(sources, sources / 'LiveContainer', baseline, out)
                self.assertFalse(out.exists())

        def test_setup_prompt_generated_fixture_keeps_actual_prefix_hashes_and_case_counts(self):
            W = prompt_fixture
            frozen = json.loads((ROOT / 'ci/qualification/native/setup-state-recovery-contract.json').read_text())
            baseline_spec = frozen['delivered_baseline']
            baseline_commit = baseline_spec['owners']['LiveContainer']['commit']
            paths = {'ui': 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                     'localization': 'LiveContainerSwiftUI/Utilities/V3Localization.swift',
                     'catalog': 'Resources/Localizable.xcstrings',
                     'display': 'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift',
                     'identity_diagnostics': 'SideStoreSupport/SideStore.swift'}
            with tempfile.TemporaryDirectory() as tmp:
                baseline = Path(tmp) / 'baseline'; baseline.mkdir()
                output = Path(tmp) / 'output'
                for relative in paths.values():
                    data = G.git(sources / 'LiveContainer', 'cat-file', 'blob', baseline_commit + ':' + relative)
                    self.assertEqual(P.sha(data), baseline_spec['input_sha256']['LiveContainer/' + relative])
                    target = baseline / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
                W.run(sources, sources / 'LiveContainer', baseline, output)
                manifest = json.loads((output / 'sources.json').read_text())
                static = json.loads((output / 'static-contract.json').read_text())
                current = portal.read_source(sources, P.LOCALIZATION)
                self.assertTrue((output / 'fixture.swift').read_bytes().startswith(current + b'\n'))
                self.assertNotEqual(P.sha(current), W.descriptor()['historical_sha256'])
                expected_hashes = {key: P.sha(portal.read_source(sources, 'LiveContainer/' + path)) for key, path in paths.items()}
                self.assertEqual(manifest['source_sha256'], expected_hashes)
                self.assertEqual(static['source_sha256'], expected_hashes)
                self.assertEqual(manifest['expected_native_case_count'], 2079)
                self.assertEqual(len(set(manifest['expected_case_names'])), 2079)
                self.assertEqual((manifest['key_count'], manifest['localized_value_count']), (216, 1080))
                self.assertEqual(manifest['status'], 'generated_not_compiled')
                self.assertEqual(static['native_renderer_cases'], 'generated_not_executed')
                self.assertEqual(static['swift_build'], 'not_run')
                W.verify_static(static, {'input_sha256': {P.LOCALIZATION: P.sha(current)}})
                for name, digest in manifest['output_sha256'].items():
                    self.assertEqual(P.sha((output / name).read_bytes()), digest)
                for name in ('generate_setup_localization_fixture.py', 'test_setup_localization_contract.py'):
                    path = 'LiveContainer/.ci/comprehensive-diagnostics/' + name
                    self.assertEqual(manifest['fixture_inputs'][name], W.PINS[path])
                self.assertFalse((output / 'cases.json').exists())

        def test_full_current_closure_parent_chain_delta_and_inverse_git_equality(self):
            specs = P.specifications(); before, after = graph_pair()
            binding = P.validate_sources(sources, after)
            self.assertEqual(binding['current_owners'], {o: pair[1] for o, pair in COMMITS.items()})
            self.assertIs(binding['functional_successor']['native_execution'], False)
            self.assertIs(binding['functional_successor']['behavioral_equivalence_claimed'], False)
            self.assertEqual(set(binding['functional_successor']['current_inputs']), P.historical_paths() | set(specs['files']))
            for owner, spec in specs['owners'].items():
                root = sources / owner
                self.assertEqual(G.git(root, 'rev-parse', 'HEAD').decode().strip(), spec['after_commit'])
                self.assertEqual(G.git(root, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames', '-r', spec['before_commit'], spec['after_commit']).decode().splitlines(), spec['delta'])
                for node in spec['commit_chain']:
                    self.assertEqual(G.commit_info(root, node['commit']), (node['tree'], node['parents']))
            for path, spec in specs['files'].items():
                owner, relative = P.owner_path(path); data = portal.read_source(sources, path)
                self.assertEqual(data, G.git(sources / owner, 'cat-file', 'blob', spec['after_commit'] + ':' + relative))
                old = P.restore_bytes(path, data)
                if specs['owners'][owner]['changes'][relative]['before_mode'] == '000000': self.assertEqual(old, b'')
                else: self.assertEqual(old, G.git(sources / owner, 'cat-file', 'blob', spec['before_commit'] + ':' + relative))
                changed = data + b'unreviewed mutation\n'
                with self.assertRaises(ValueError): P.restore_bytes(path, changed)
                repinned = copy.deepcopy(specs)
                repinned['files'][path].update(after_sha256=P.sha(changed), after_blob=P.blob(changed))
                with patch.object(P, 'specifications', return_value=repinned), self.assertRaises(ValueError): P.restore_bytes(path, changed)

        def test_all_current_paths_reject_mutations_and_repinned_working_bytes(self):
            _, graph = graph_pair(); specs = P.specifications(); original = portal.read_source
            for target in P.historical_paths() | set(specs['files']):
                def changed(root, path):
                    data = original(root, path)
                    return data + b'unreviewed mutation\n' if path == target else data
                with patch.object(portal, 'read_source', side_effect=changed):
                    with self.subTest(path=target), self.assertRaisesRegex(ValueError, 'working/Git bytes'): P.validate_sources(sources, graph)
                if target in specs['files']:
                    repinned = copy.deepcopy(specs); mutation = original(sources, target) + b'unreviewed mutation\n'
                    repinned['files'][target].update(after_sha256=P.sha(mutation), after_blob=P.blob(mutation))
                    with patch.object(P, 'specifications', return_value=repinned), patch.object(portal, 'read_source', side_effect=changed):
                        with self.subTest(repinned=target), self.assertRaisesRegex(ValueError, 'working/Git bytes'): P.validate_sources(sources, graph)

        def test_wrong_head_parent_tree_delta_and_mode_are_rejected(self):
            _, graph = graph_pair(); original_git = G.git; original_info = G.commit_info
            for target in COMMITS:
                for kind in ('head', 'parent', 'tree', 'delta', 'mode'):
                    def git(root, *args):
                        raw = original_git(root, *args)
                        if Path(root) == sources / target:
                            if kind == 'head' and args == ('rev-parse', 'HEAD'): return b'wrong-head\n'
                            if args[0] == 'diff-tree':
                                if kind == 'delta': return raw + b':100644 100644 ' + b'0' * 40 + b' ' + b'1' * 40 + b' M\tUnreviewed.swift\n'
                                if kind == 'mode': return raw.replace(b'100644', b'100755', 1)
                        return raw
                    def info(root, commit):
                        tree, parents = original_info(root, commit)
                        if Path(root) == sources / target and commit == COMMITS[target][1]:
                            if kind == 'parent': parents = ['0' * 40]
                            if kind == 'tree': tree = '0' * 40
                        return tree, parents
                    with patch.object(G, 'git', side_effect=git), patch.object(G, 'commit_info', side_effect=info):
                        with self.subTest(owner=target, kind=kind), self.assertRaises(ValueError): P.validate_sources(sources, graph)

        def test_exact_seventeen_plus_three_replay_and_all_invalid_input_closures(self):
            before, after = graph_pair(); owner_inputs, side_inputs = {}, {}
            for path in P.historical_paths():
                owner, relative = P.owner_path(path)
                data = G.git(sources / owner, 'cat-file', 'blob', COMMITS[owner][0] + ':' + relative)
                if owner == G.SIDESIGN: side_inputs[relative] = data
                else: owner_inputs[path] = data
            self.assertEqual((len(owner_inputs), len(side_inputs)), (17, 3))
            binding = previous.validate_committed_sources(sources, before, owner_inputs, sidesign_proof_inputs=side_inputs)
            self.assertEqual(binding, P.validate_sources(sources, after)['historical_binding'])
            for which, inputs in (('owner', owner_inputs), ('side', side_inputs)):
                for target in inputs:
                    for kind in ('missing', 'text', 'changed'):
                        bad = dict(inputs)
                        if kind == 'missing': bad.pop(target)
                        elif kind == 'text': bad[target] = bad[target].decode()
                        else: bad[target] += b'unreviewed mutation\n'
                        with self.subTest(which=which, path=target, kind=kind), self.assertRaises(ValueError):
                            previous.validate_committed_sources(sources, before, bad if which == 'owner' else owner_inputs,
                                sidesign_proof_inputs=bad if which == 'side' else side_inputs)
                bad = {**inputs, 'extra': b'new'}
                with self.assertRaises(ValueError):
                    previous.validate_committed_sources(sources, before, bad if which == 'owner' else owner_inputs,
                        sidesign_proof_inputs=bad if which == 'side' else side_inputs)
            with self.assertRaisesRegex(ValueError, 'checkout'): previous.validate_sources(sources, before)

    return unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--sources', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FunctionalRecoveryTests)
    if args.sources is not None: suite.addTests(actual_source_controls(args.sources.resolve(strict=True)))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
