"""Exact graph-owner successor controls; synthetic Git objects are not native proof."""
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
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as P


class FrozenProvenanceTests(unittest.TestCase):
    def test_portal_manifest_and_original_provenance_remain_frozen(self):
        self.assertEqual(portal.sha(portal.MANIFEST.read_bytes()),
                         'e8a48d420664880432fd04594351fd1ddaf9901606e435be67caef9d609beda4')
        self.assertEqual(portal.OWNER_COMMITS['SideStore'][1], '0eba6008c724c9f86a287b696748a74aef25f289')
        self.assertEqual(portal.sha(Path(portal.__file__).read_bytes()),
                         '97d4f13600589092d43b3b577511b62dac899c7fb11b7668f36c731147143263')
        for path, spec in portal.specifications().items():
            if path.startswith('SideStore/'):
                self.assertEqual(spec['after_commit'], '0eba6008c724c9f86a287b696748a74aef25f289')


class OwnerSuccessorControls(unittest.TestCase):
    def git(self, owner, *args):
        return subprocess.check_output(['git', '-C', str(self.sources / owner),
            '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Source fixture',
            '-c', 'user.email=source-fixture@example.invalid', *args], stderr=subprocess.DEVNULL).decode().strip()

    def put(self, owner, relative, data):
        path = self.sources / owner / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, owner):
        self.git(owner, 'commit', '-qm', 'Synthetic source fixture')
        return self.git(owner, 'rev-parse', 'HEAD')

    def new_store_commit(self, link, extra=None, parent=None):
        self.git('SideStore', 'checkout', '-q', '--detach', parent or self.old_store)
        self.git('SideStore', 'update-index', '--add', '--cacheinfo', '160000,' + link + ',' + P.GITLINK)
        if extra:
            self.put('SideStore', extra, b'unreviewed source change\n')
            self.git('SideStore', 'add', extra)
        return self.commit('SideStore')

    def delta(self, owner):
        rows = self.git(owner, 'ls-tree', '-r', 'HEAD').splitlines()
        return {'tree': self.git(owner, 'rev-parse', 'HEAD^{tree}'),
                'files': {row.split('\t')[1]: row.split()[2] for row in rows}}

    def update_store_graph(self, commit):
        self.spec['commit'] = commit
        self.spec['tree'] = self.git('SideStore', 'rev-parse', commit + '^{tree}')
        self.graph['owners']['SideStore'][1] = commit
        self.graph['reviewed_deltas']['SideStore'] = self.delta('SideStore')

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.sources = Path(temp.name).resolve()
        for owner in ('LiveContainer', 'SideStore', P.SIDESIGN):
            (self.sources / owner).mkdir(parents=True, exist_ok=True)
            self.git(owner, 'init', '-q')
        for path in P.TEST_PATHS:
            if not path.endswith('.json'):
                self.put(P.SIDESIGN, path, b'old reviewed test\n')
        self.put(P.SIDESIGN, 'Sources/Portal.swift', b'unchanged product\n')
        self.git(P.SIDESIGN, 'add', '.'); self.old_side = self.commit(P.SIDESIGN)
        for path in P.TEST_PATHS:
            self.put(P.SIDESIGN, path, b'new reviewed test\n')
        self.git(P.SIDESIGN, 'add', '.'); self.new_side = self.commit(P.SIDESIGN)
        self.files = {portal.HOST: b'unchanged host\n', portal.SERVICE: b'unchanged service\n',
                      portal.SELECTION: b'unchanged selection\n'}
        for path, data in self.files.items():
            owner, relative = path.split('/', 1)
            self.put(owner, relative, data); self.git(owner, 'add', relative)
        self.host = self.commit('LiveContainer')
        self.git('SideStore', 'update-index', '--add', '--cacheinfo', '160000,' + self.old_side + ',' + P.GITLINK)
        self.old_store = self.commit('SideStore')
        self.new_store = self.new_store_commit(self.new_side)
        self.spec = {'commit': self.new_store, 'parent': self.old_store,
                     'tree': self.git('SideStore', 'rev-parse', 'HEAD^{tree}'),
                     'sidesign_commit': self.new_side, 'sidesign_parent': self.old_side,
                     'sidesign_tree': self.git(P.SIDESIGN, 'rev-parse', 'HEAD^{tree}')}
        owners = {'LiveContainer': ('1' * 40, self.host), 'SideStore': ('2' * 40, self.old_store)}
        specs = {path: {'after_commit': owners[path.split('/', 1)[0]][1],
                       'before_commit': owners[path.split('/', 1)[0]][0],
                       'after_sha256': portal.sha(data), 'after_blob': portal.blob(data)}
                 for path, data in self.files.items()}
        self.file_specs = specs
        self.graph = {'owners': {'LiveContainer': ['NRG-Wardog/LiveContainer', self.host],
            'SideStore': ['NRG-Wardog/SideStore', self.new_store],
            P.SIDESIGN: ['NRG-Wardog/SideSign', self.new_side]},
            'reviewed_deltas': {owner: self.delta(owner) for owner in ('LiveContainer', 'SideStore', P.SIDESIGN)}}
        for patcher in (patch.object(P, 'specification', return_value=self.spec),
                        patch.object(portal, 'OWNER_COMMITS', owners),
                        patch.object(portal, 'specifications', return_value=specs)):
            patcher.start(); self.addCleanup(patcher.stop)

    def test_exact_test_only_gitlink_successor_binds_current_and_original_proof(self):
        before = copy.deepcopy(self.graph)
        result = P.validate_sources(self.sources, self.graph)
        self.assertEqual(self.graph, before)
        self.assertEqual(result['current_owners']['SideStore'], self.new_store)
        self.assertEqual(result['proof_owners']['SideStore'], self.old_store)
        self.assertEqual(result['gitlink_successor'], self.spec)
        self.assertIs(result['native_execution'], False)
        for path, data in self.files.items():
            self.assertEqual(result['inputs'][path]['sha256'], portal.sha(data))
            self.assertEqual(result['inputs'][path]['git_blob'], portal.blob(data))

    def test_committed_predecessor_inputs_require_exact_closure_and_git_bytes(self):
        expected = P.validate_sources(self.sources, self.graph)
        self.assertEqual(P.validate_committed_sources(self.sources, self.graph, self.files), expected)
        for path in self.files:
            for value in (None, self.files[path].decode(), self.files[path] + b'unreviewed\n'):
                inputs = dict(self.files)
                if value is None:
                    inputs.pop(path)
                else:
                    inputs[path] = value
                with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                    P.validate_committed_sources(self.sources, self.graph, inputs)
        with self.assertRaises(ValueError):
            P.validate_committed_sources(self.sources, self.graph, {**self.files, 'extra': b'new'})
        changed = dict(self.files)
        changed[portal.SERVICE] += b'unreviewed\n'
        self.file_specs[portal.SERVICE].update(after_sha256=portal.sha(changed[portal.SERVICE]),
                                             after_blob=portal.blob(changed[portal.SERVICE]))
        with self.assertRaisesRegex(ValueError, 'proof Git bytes differ'):
            P.validate_committed_sources(self.sources, self.graph, changed)

    def test_unknown_or_mismatched_graph_pin_tree_and_blob_fail(self):
        for mutate in (
            lambda g: g['owners']['SideStore'].__setitem__(1, '0' * 40),
            lambda g: g['owners'][P.SIDESIGN].__setitem__(1, self.old_side),
            lambda g: g['owners']['SideStore'].__setitem__(1, self.old_store),
            lambda g: g['reviewed_deltas']['SideStore'].__setitem__('tree', '0' * 40),
            lambda g: g['reviewed_deltas']['SideStore']['files'].__setitem__(P.GITLINK, self.old_side),
            lambda g: g['reviewed_deltas'][P.SIDESIGN].__setitem__('tree', '0' * 40),
            lambda g: g['reviewed_deltas']['SideStore']['files'].__setitem__('AltStore/AppDelegate.swift', '0' * 40),
            lambda g: g['reviewed_deltas'][P.SIDESIGN]['files'].__setitem__(P.TEST_PATHS[0], '0' * 40),
        ):
            bad = copy.deepcopy(self.graph); mutate(bad)
            with self.assertRaises(ValueError): P.validate_sources(self.sources, bad)

    def test_raw_proof_and_test_working_bytes_cannot_be_replaced(self):
        for path in (*self.files, P.SIDESIGN + '/' + P.TEST_PATHS[1]):
            file = self.sources / path; before = file.read_bytes()
            file.write_bytes(before + b'unrelated mutation\n')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'working bytes differ'):
                P.validate_sources(self.sources, self.graph)
            file.write_bytes(before)
        self.assertEqual(P.validate_sources(self.sources, self.graph)['proof_owners']['SideStore'], self.old_store)

    def test_repinned_working_bytes_still_require_actual_git_object_equality(self):
        data = self.files[portal.SERVICE] + b'unrelated mutation\n'
        (self.sources / portal.SERVICE).write_bytes(data)
        self.file_specs[portal.SERVICE].update(after_sha256=portal.sha(data), after_blob=portal.blob(data))
        self.graph['reviewed_deltas']['SideStore']['files']['AltStore/AppDelegate.swift'] = portal.blob(data)
        with self.assertRaisesRegex(ValueError, 'proof Git bytes differ'):
            P.validate_sources(self.sources, self.graph)

    def test_unrelated_committed_delta_rejected_even_when_pin_and_tree_are_repinned(self):
        malicious = self.new_store_commit(self.new_side, 'Unreviewed.swift')
        self.update_store_graph(malicious)
        with self.assertRaisesRegex(ValueError, 'sole gitlink delta'):
            P.validate_sources(self.sources, self.graph)

    def test_a_descendant_is_not_the_exact_single_parent_successor(self):
        self.git('SideStore', 'commit', '--allow-empty', '-qm', 'Unreviewed descendant')
        self.update_store_graph(self.git('SideStore', 'rev-parse', 'HEAD'))
        with self.assertRaisesRegex(ValueError, 'exact parent/tree'):
            P.validate_sources(self.sources, self.graph)

    def test_changed_gitlink_mode_rejected_even_when_pin_and_tree_are_repinned(self):
        self.git('SideStore', 'checkout', '-q', '--detach', self.old_store)
        ordinary_blob = self.git('SideStore', 'rev-parse', 'HEAD:AltStore/AppDelegate.swift')
        self.git('SideStore', 'update-index', '--add', '--cacheinfo', '100644,' + ordinary_blob + ',' + P.GITLINK)
        self.update_store_graph(self.commit('SideStore'))
        self.graph['reviewed_deltas']['SideStore']['files'][P.GITLINK] = self.new_side
        with self.assertRaisesRegex(ValueError, 'sole gitlink delta'):
            P.validate_sources(self.sources, self.graph)

    def test_sidesign_product_change_rejected_even_with_both_successor_pins_repinned(self):
        self.git(P.SIDESIGN, 'checkout', '-q', '--detach', self.old_side)
        for path in P.TEST_PATHS:
            self.put(P.SIDESIGN, path, b'new reviewed test\n')
        self.put(P.SIDESIGN, 'Sources/Portal.swift', b'unreviewed product\n')
        self.git(P.SIDESIGN, 'add', '.')
        bad_side = self.commit(P.SIDESIGN)
        self.spec['sidesign_commit'] = bad_side
        self.spec['sidesign_tree'] = self.git(P.SIDESIGN, 'rev-parse', 'HEAD^{tree}')
        self.graph['owners'][P.SIDESIGN][1] = bad_side
        self.graph['reviewed_deltas'][P.SIDESIGN] = self.delta(P.SIDESIGN)
        self.update_store_graph(self.new_store_commit(bad_side))
        with self.assertRaisesRegex(ValueError, 'unreviewed path'):
            P.validate_sources(self.sources, self.graph)

    def test_sidesign_descendant_is_not_the_exact_test_only_parent_edge(self):
        self.git(P.SIDESIGN, 'commit', '--allow-empty', '-qm', 'Unreviewed dependency descendant')
        descendant = self.git(P.SIDESIGN, 'rev-parse', 'HEAD')
        self.spec['sidesign_commit'] = descendant
        self.graph['owners'][P.SIDESIGN][1] = descendant
        self.graph['reviewed_deltas'][P.SIDESIGN] = self.delta(P.SIDESIGN)
        self.update_store_graph(self.new_store_commit(descendant))
        with self.assertRaisesRegex(ValueError, 'SideSign parent/tree'):
            P.validate_sources(self.sources, self.graph)

    def test_prior_proof_owner_remains_valid_without_rewriting_its_identity(self):
        self.git('SideStore', 'checkout', '-q', '--detach', self.old_store)
        graph = copy.deepcopy(self.graph)
        graph['owners']['SideStore'][1] = self.old_store
        graph['reviewed_deltas']['SideStore'] = self.delta('SideStore')
        self.git(P.SIDESIGN, 'checkout', '-q', '--detach', self.old_side)
        graph['owners'][P.SIDESIGN][1] = self.old_side
        graph['reviewed_deltas'][P.SIDESIGN] = self.delta(P.SIDESIGN)
        result = P.validate_sources(self.sources, graph)
        self.assertIsNone(result['gitlink_successor'])
        self.assertEqual(result['proof_owners'], result['current_owners'])

    def test_checkout_pin_and_linked_owner_fail(self):
        self.git('SideStore', 'checkout', '-q', '--detach', self.old_store)
        with self.assertRaisesRegex(ValueError, 'checkout pin differs'):
            P.validate_sources(self.sources, self.graph)
        host = self.sources / 'LiveContainer'; moved = self.sources / 'moved-host'
        host.rename(moved); host.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked portal owner'):
            P.validate_sources(self.sources, self.graph)


if __name__ == '__main__':
    unittest.main()
