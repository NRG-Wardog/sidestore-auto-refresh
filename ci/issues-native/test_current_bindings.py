"""Synthetic manifest-tamper controls, not runtime/product qualification."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
import sys

import verify_current_bindings as M


class CurrentBindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.harness = self.root / 'harness'
        self.sources = self.root / 'sources'
        (self.harness / 'ci/issues-native').mkdir(parents=True)
        (self.harness / 'ci/multitask-launch').mkdir()
        (self.sources / 'LiveContainer/Resources').mkdir(parents=True)
        self.file = self.sources / 'LiveContainer/Resources/Localizable.xcstrings'
        self.file.write_text('synthetic catalog')
        self.graph = {'owners': {'LiveContainer': ['NRG-Wardog/LiveContainer', 'a' * 40]}}
        self.multitask = {'schema': 'multitask-launch-source-v1', 'repository': 'NRG-Wardog/LiveContainer',
                          'commit': 'a' * 40, 'baseline_app_builder': 'b' * 40}
        self.manifest = {'candidate_inputs': {'LiveContainer/Resources/Localizable.xcstrings': hashlib.sha256(self.file.read_bytes()).hexdigest()}}
        self.write('ci/multitask-launch/source.json', self.multitask)
        for name in M.HASH_MANIFESTS:
            self.write('ci/issues-native/' + name, self.manifest)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.harness / name).write_text(json.dumps(value))

    def verify(self):
        return M.verify(self.sources, harness=self.harness, graph=self.graph)

    def test_consistent_model_passes_without_claiming_native_execution(self):
        result = self.verify()
        self.assertEqual(result['status'], 'PASS')
        self.assertIs(result['native_execution'], False)
        self.assertIs(result['historical_contracts_rewritten'], False)

    def test_stale_multitask_pin_fails_before_runtime(self):
        self.multitask['commit'] = 'c' * 40
        self.write('ci/multitask-launch/source.json', self.multitask)
        with self.assertRaisesRegex(ValueError, 'source pin differs'):
            self.verify()

    def test_changed_catalog_and_cross_manifest_hash_fail(self):
        self.file.write_text('changed catalog')
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()
        self.manifest['candidate_inputs']['LiveContainer/Resources/Localizable.xcstrings'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.write('ci/issues-native/' + M.HASH_MANIFESTS[0], self.manifest)
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()

    def test_unknown_owner_escape_and_invalid_digest_rejected(self):
        for path, digest in [('SideStore/secret', 'a'*64), ('../outside', 'a'*64),
                             ('LiveContainer/Resources/Localizable.xcstrings', 'main')]:
            self.write('ci/issues-native/' + M.HASH_MANIFESTS[0], {'candidate_inputs': {path: digest}})
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.verify()

    def test_linked_source_rejected_even_with_matching_content(self):
        outside = self.root / 'outside'
        self.file.rename(outside)
        self.file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'linked current-source'):
            self.verify()

    def test_linked_owner_directory_rejected(self):
        owner = self.sources / 'LiveContainer'
        moved = self.root / 'moved-owner'
        owner.rename(moved)
        owner.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked current-source ancestor'):
            self.verify()

    def test_copied_cli_uses_its_owned_harness_from_alternate_working_directory(self):
        # All temporary paths are canonical: macOS /var aliases are not
        # interpreted as evidence of a linked source-owner directory.
        module_root = Path(M.__file__).resolve().parent
        for name in ('verify_current_bindings.py', 'verify_candidate_graph.py', 'candidate-graph.json'):
            shutil.copy2(module_root / name, self.harness / 'ci/issues-native' / name)
        actual_graph = json.loads((module_root / 'candidate-graph.json').read_text())
        self.multitask['repository'], self.multitask['commit'] = actual_graph['owners']['LiveContainer']
        self.write('ci/multitask-launch/source.json', self.multitask)
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        output = unrelated / 'bindings.json'
        subprocess.run([sys.executable, '-B', str(self.harness / 'ci/issues-native/verify_current_bindings.py'),
                        '--sources', str(self.sources), '--output', str(output)],
                       cwd=unrelated, check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(output.read_text())['status'], 'PASS')

    def test_duplicate_manifest_keys_rejected(self):
        (self.harness / 'ci/multitask-launch/source.json').write_text('{"commit":"a","commit":"b"}')
        with self.assertRaisesRegex(ValueError, 'duplicate manifest key'):
            self.verify()


if __name__ == '__main__':
    unittest.main(verbosity=2)
