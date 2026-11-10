"""Read-only Anisette admission; no generator, templates or upstream fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import anisette_evidence as evidence


class EvidenceContractTests(unittest.TestCase):
    def test_accepted_manifest_is_exact_and_independent(self):
        manifest = evidence.expected_evidence()
        self.assertEqual(hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                         'b0d339e24505e5658e45f03041f1d058e110f6e73f7eba9be96a721417b210ff')
        manifest['files'].clear()
        self.assertEqual(len(evidence.expected_evidence()['files']), 5)

    def test_evidence_boundary_does_not_import_replay(self):
        root = Path(evidence.__file__).parent
        for name in ('anisette_evidence.py', 'maintained_sources.py', 'combined_build_evidence.py'):
            source = (root / name).read_text()
            self.assertNotIn('patch_anisette_isolated_otp', source)
        self.assertNotIn('templates/', (root / 'anisette_evidence.py').read_text())


class PreparedCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Evidence Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.manifest = evidence.expected_evidence()
        for entry in self.manifest['files']:
            target = self.root / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            original = ('original:' + entry['path']).encode()
            target.write_bytes(original)
            entry['original_sha256'] = hashlib.sha256(original).hexdigest()
            entry['prepared_sha256'] = hashlib.sha256(b'prepared:' + original).hexdigest()
        self.git('add', '.')
        self.git('commit', '-qm', 'Original inputs')
        self.manifest['anisettekit_revision'] = self.git('rev-parse', 'HEAD').decode().strip()
        for entry in self.manifest['files']:
            target = self.root / entry['path']
            target.write_bytes(b'prepared:' + target.read_bytes())
        patcher = mock.patch.object(evidence, 'expected_evidence', side_effect=lambda: copy.deepcopy(self.manifest))
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args])

    def test_valid_read_only_checkout_preserves_bytes_modes_index(self):
        target = self.root / self.manifest['files'][0]['path']
        target.chmod(0o444)
        before = {p: (p.read_bytes(), p.stat().st_mode) for p in
                  (self.root / entry['path'] for entry in self.manifest['files'])}
        index = self.git('diff', '--cached', '--binary')
        self.assertEqual(evidence.verify_prepared_checkout(self.root), 5)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mode) for p in before})
        self.assertEqual(index, self.git('diff', '--cached', '--binary'))

    def test_wrong_revision_and_original_contract_fail_closed(self):
        self.manifest['anisettekit_revision'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'exact pinned revision'):
            evidence.verify_prepared_checkout(self.root)
        self.manifest['anisettekit_revision'] = self.git('rev-parse', 'HEAD').decode().strip()
        self.manifest['files'][0]['original_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source hash drift'):
            evidence.verify_prepared_checkout(self.root)

    def test_source_change_and_symlink_fail_closed(self):
        target = self.root / self.manifest['files'][0]['path']
        data = target.read_bytes()
        target.write_bytes(data + b'drift')
        with self.assertRaisesRegex(ValueError, 'prepared-source hash mismatch'):
            evidence.verify_prepared_checkout(self.root)
        target.unlink()
        other = self.root.parent / 'other.cpp'
        other.write_bytes(data)
        target.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'substituted'):
            evidence.verify_prepared_checkout(self.root)

    def test_directory_symlink_and_wrong_owner_fail_closed(self):
        original = self.root / 'Native'
        moved = self.root.parent / 'Native'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Substituted'):
            evidence.verify_prepared_checkout(self.root)
        original.unlink()
        moved.rename(original)
        if hasattr(os, 'geteuid'):
            with mock.patch.object(evidence.os, 'geteuid', return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(ValueError, 'not owned'):
                    evidence.verify_prepared_checkout(self.root)


if __name__ == '__main__':
    unittest.main()
