"""Synthetic manifest-tamper controls, not runtime/product qualification."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
    from ci.native_paths import native_file, REPO_ROOT
from pathlib import Path as _CIPath
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
import sys

from ci.build import verify_current_bindings as M


class CurrentBindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.harness = self.root / 'harness'
        self.sources = self.root / 'sources'
        (self.harness / 'ci/qualification/native').mkdir(parents=True)
        (self.harness / 'ci/qualification/multitask').mkdir()
        (self.sources / 'LiveContainer/Resources').mkdir(parents=True)
        self.file = self.sources / 'LiveContainer/Resources/Localizable.xcstrings'
        self.file.write_text('synthetic catalog')
        self.graph = {'owners': {'LiveContainer': ['NRG-Wardog/LiveContainer', 'a' * 40]}}
        self.multitask = {'schema': 'multitask-launch-source-v1', 'repository': 'NRG-Wardog/LiveContainer',
                          'commit': 'a' * 40, 'baseline_app_builder': 'b' * 40}
        self.manifest = {'candidate_inputs': {'LiveContainer/Resources/Localizable.xcstrings': hashlib.sha256(self.file.read_bytes()).hexdigest()}}
        self.write('ci/qualification/multitask/source.json', self.multitask)
        for name in M.HASH_MANIFESTS:
            self.write('ci/qualification/native/' + name, self.manifest)

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
        self.write('ci/qualification/multitask/source.json', self.multitask)
        with self.assertRaisesRegex(ValueError, 'source pin differs'):
            self.verify()

    def test_changed_catalog_and_cross_manifest_hash_fail(self):
        self.file.write_text('changed catalog')
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()
        self.manifest['candidate_inputs']['LiveContainer/Resources/Localizable.xcstrings'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.write('ci/qualification/native/' + M.HASH_MANIFESTS[0], self.manifest)
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()

    def test_unknown_owner_escape_and_invalid_digest_rejected(self):
        for path, digest in [('SideStore/secret', 'a'*64), ('../outside', 'a'*64),
                             ('LiveContainer/Resources/Localizable.xcstrings', 'main')]:
            self.write('ci/qualification/native/' + M.HASH_MANIFESTS[0], {'candidate_inputs': {path: digest}})
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
        module_root = (_CIPath(__file__).resolve().parents[3]/'ci/qualification/native')
        for name in ('verify_current_bindings.py', 'verify_candidate_graph.py', 'candidate-graph.json', 'direct_host_projection.py', 'direct-host-source-delta.json', 'direct_refresh_projection.py'):
            destination = self.harness / native_file(name).relative_to(REPO_ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(native_file(name), destination)
        shutil.copy2(REPO_ROOT/'ci/native_paths.py', self.harness/'ci/native_paths.py')
        for package in ('ci', 'ci/build', 'ci/qualification', 'ci/qualification/native'):
            (self.harness/package/'__init__.py').touch()
        actual_graph = json.loads(((module_root.parents[1]/'build/candidate-graph.json')).read_text())
        self.multitask['repository'], self.multitask['commit'] = actual_graph['owners']['LiveContainer']
        self.write('ci/qualification/multitask/source.json', self.multitask)
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        output = unrelated / 'bindings.json'
        subprocess.run([sys.executable, '-B', str(self.harness / 'ci/build/verify_current_bindings.py'),
                        '--sources', str(self.sources), '--output', str(output)],
                       cwd=unrelated, check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(output.read_text())['status'], 'PASS')

    def test_only_exact_reviewed_host_delta_can_normalize(self):
        from unittest.mock import patch
        path = 'LiveContainer/SideStoreSupport/SideStore.swift'
        target = self.sources / path
        target.parent.mkdir(parents=True)
        target.write_text('reviewed new host bytes')
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        historical = 'd' * 64
        self.write('ci/qualification/native/' + M.HASH_MANIFESTS[-1],
                   {'candidate_inputs': {path: historical}})
        with patch.object(M.direct_host, 'specification', return_value={
                'before_sha256': historical, 'after_sha256': actual}), \
             patch.object(M.direct_host, 'validate_bytes', return_value=True) as inverse:
            result = self.verify()
            inverse.assert_called_once_with(target.read_bytes())
            self.assertEqual(result['current_source_sha256'][path], actual)
            self.assertEqual(result['reviewed_successor_bindings'][path]['historical_sha256'], historical)
            self.assertFalse(result['historical_contracts_rewritten'])

    def test_unknown_host_bytes_cannot_normalize(self):
        path = 'LiveContainer/SideStoreSupport/SideStore.swift'
        target = self.sources / path
        target.parent.mkdir(parents=True)
        target.write_text('unreviewed host bytes')
        self.write('ci/qualification/native/' + M.HASH_MANIFESTS[-1],
                   {'candidate_inputs': {path: 'd' * 64}})
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()

    def test_inverse_failure_is_not_converted_to_binding_success(self):
        from unittest.mock import patch
        path = 'LiveContainer/SideStoreSupport/SideStore.swift'
        target = self.sources / path
        target.parent.mkdir(parents=True)
        target.write_text('reviewed new host bytes')
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        historical = 'd' * 64
        self.write('ci/qualification/native/' + M.HASH_MANIFESTS[-1],
                   {'candidate_inputs': {path: historical}})
        with patch.object(M.direct_host, 'specification', return_value={
                'before_sha256': historical, 'after_sha256': actual}), \
             patch.object(M.direct_host, 'validate_bytes', side_effect=ValueError('inverse failed')):
            with self.assertRaisesRegex(ValueError, 'inverse failed'):
                self.verify()

    def test_exact_vpn_successor_requires_inverse_and_rejects_unknown_bytes(self):
        from unittest.mock import patch
        path = M.direct_refresh.PATH
        target = self.sources / path
        target.parent.mkdir(parents=True)
        target.write_text('reviewed new generator')
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        self.write('ci/qualification/native/' + M.HASH_MANIFESTS[-1],
                   {'candidate_inputs': {path: M.direct_refresh.BEFORE}})
        with patch.object(M.direct_refresh, 'AFTER', actual), \
             patch.object(M.direct_refresh, 'validate_source', return_value=True) as inverse:
            result = self.verify()
            inverse.assert_called_once_with(self.sources)
            self.assertEqual(result['current_source_sha256'][path], actual)
            inverse.side_effect = ValueError('VPN inverse failed')
            with self.assertRaisesRegex(ValueError, 'VPN inverse failed'):
                self.verify()
        with self.assertRaisesRegex(ValueError, 'stale current-source'):
            self.verify()

    def test_duplicate_manifest_keys_rejected(self):
        (self.harness / 'ci/qualification/multitask/source.json').write_text('{"commit":"a","commit":"b"}')
        with self.assertRaisesRegex(ValueError, 'duplicate manifest key'):
            self.verify()


if __name__ == '__main__':
    unittest.main(verbosity=2)
