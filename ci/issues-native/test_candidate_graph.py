import copy
import json
from pathlib import Path
import tempfile
import unittest
from verify_candidate_graph import BASELINE_OWNERS, REQUIRED_DELTAS, load_candidate_graph


def fixture():
    return {'schema_version': 1, 'owners': {k: list(v) for k, v in BASELINE_OWNERS.items()},
            'reviewed_deltas': {k: {'baseline': BASELINE_OWNERS[k][1], 'tree': 'a' * 40,
                                    'files': {'AGENTS.md': None, 'Source.swift': 'b' * 40}}
                               for k in REQUIRED_DELTAS}}


class CandidateGraphTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'graph.json'
            path.write_text(value if isinstance(value, str) else json.dumps(value))
            return load_candidate_graph(path)

    def test_explicit_graph_and_deleted_file_are_accepted(self):
        self.assertEqual(self.load(fixture()), fixture())

    def test_invalid_schema_owner_repository_and_pin_reject(self):
        changes = [lambda g: g.update(schema_version=True),
                   lambda g: g.update(unreviewed=True),
                   lambda g: g['owners'].pop('jktcp'),
                   lambda g: g['owners'].update({'../escape': ['NRG-Wardog/LiveContainer', 'a' * 40]}),
                   lambda g: g['owners']['LiveContainer'].__setitem__(0, 'unreviewed/repository'),
                   lambda g: g['owners']['LiveContainer'].__setitem__(1, 'main'),
                   lambda g: g['reviewed_deltas'].pop('LiveContainer'),
                   lambda g: g['owners']['AnisetteKit'].__setitem__(1, 'c' * 40)]
        for change in changes:
            with self.subTest(change=change):
                value = fixture(); change(value)
                with self.assertRaises(ValueError): self.load(value)

    def test_explicit_pairing_source_owner_cannot_be_replaced(self):
        self.assertEqual(BASELINE_OWNERS['RemotePairingKit'],
                         ('mahee96/RemotePairingKit', 'e3f70d16c0c551540a533a39d540e78e5b0a60a8'))
        for field, value in [(0, 'unreviewed/RemotePairingKit'), (1, 'd' * 40)]:
            graph = fixture()
            graph['owners']['RemotePairingKit'][field] = value
            with self.assertRaises(ValueError): self.load(graph)
        graph = fixture()
        graph['reviewed_deltas']['RemotePairingKit'] = copy.deepcopy(graph['reviewed_deltas']['LiveContainer'])
        with self.assertRaises(ValueError): self.load(graph)

    def test_unsafe_file_paths_and_invalid_blob_reject(self):
        for path in ('/absolute', '../escape', 'path/../escape', 'path//file', 'path/./file',
                     '.git/config', 'path\\file', 'path\nfile', ''):
            value = fixture()
            value['reviewed_deltas']['LiveContainer']['files'] = {path: 'b' * 40}
            with self.subTest(path=path), self.assertRaises(ValueError): self.load(value)
        value = fixture(); value['reviewed_deltas']['LiveContainer']['files'] = {'file': 'branch'}
        with self.assertRaises(ValueError): self.load(value)

    def test_duplicate_json_keys_reject(self):
        raw = json.dumps(fixture()).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
        with self.assertRaisesRegex(ValueError, 'duplicate manifest key'): self.load(raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
