
import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/contracts/integrity'))
from pathlib import Path as _CIPath
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('current_integrity', (_CIPath(__file__).resolve().parents[3]/'ci/contracts/integrity/verify.py'))
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.files = {'scripts/package.py': b'guarded package', '.github/workflows/build.yml': b'guarded steps'}
        for p, raw in self.files.items():
            self.put(p, raw)
        self.manifest = {'schema': 'current-source-integrity-v1', 'basis': 'a'*40,
                         'files': {p: v.sha(raw) for p, raw in self.files.items()}}
        self.save()

    def put(self, name, raw):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def save(self):
        raw = json.dumps(self.manifest).encode()
        self.put('ci/contracts/integrity/manifest.json', raw)
        self.digest = v.sha(raw)

    def verify(self):
        return v.verify(self.root, expected_manifest_sha256=self.digest)

    def test_valid_current_scope_without_git_history(self):
        self.assertEqual(self.verify(), self.manifest)

    def test_changed_source_rejected(self):
        self.put('scripts/package.py', b'weakened guard')
        with self.assertRaisesRegex(ValueError, 'source bytes differ'): self.verify()

    def test_missing_source_rejected(self):
        (self.root/'scripts/package.py').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing source'): self.verify()

    def test_manifest_mutation_cannot_self_approve(self):
        self.put('ci/contracts/integrity/manifest.json', b'{}')
        with self.assertRaisesRegex(ValueError, 'manifest digest'): self.verify()

    def test_duplicate_keys_rejected_even_with_matching_digest(self):
        raw = b'{"schema": 1, "schema": 2}'
        self.put('ci/contracts/integrity/manifest.json', raw)
        self.digest = v.sha(raw)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.verify()

    def test_leaf_symlink_rejected(self):
        p = self.root/'scripts/package.py'
        p.unlink(); p.symlink_to('../.github/workflows/build.yml')
        with self.assertRaisesRegex(ValueError, 'Linked source'): self.verify()

    def test_parent_symlink_rejected(self):
        (self.root/'scripts').rename(self.root/'real-scripts')
        (self.root/'scripts').symlink_to('real-scripts', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked source'): self.verify()

    def test_workflow_inventory_expansion_rejected(self):
        for suffix in ('.yml', '.yaml'):
            with self.subTest(suffix=suffix):
                p = '.github/workflows/unreviewed'+suffix
                self.put(p, b'new action')
                with self.assertRaisesRegex(ValueError, 'workflow inventory'): self.verify()
                (self.root/p).unlink()

    def test_retired_history_not_reintroduced(self):
        self.put('ci/validation-history/old.py', b'old')
        with self.assertRaisesRegex(ValueError, 'Retired history'): self.verify()

    def test_invalid_paths_rejected(self):
        for name in ('../outside', '/tmp/outside', 'scripts//package.py'):
            with self.subTest(name=name):
                self.manifest['files'] = {name: 'a'*64}
                self.save()
                with self.assertRaisesRegex(ValueError, 'Invalid source path'): self.verify()

    def test_actual_repository(self):
        v.verify()


if __name__ == '__main__':
    unittest.main()
