"""Pinned producer admission: strict identity, bytes and isolated imports."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('pinned_native_admission', ROOT / 'ci/packaging/zopfli/pinned_native_admission.py')
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


class PinnedNativeAdmissionTests(unittest.TestCase):
    def test_reviewed_closure_and_producer_hashes(self):
        modules = admission.trusted(dict(admission.IDENTITY))
        expected = {name: digest for name, digest in admission.HASHES.items() if name != 'startup_upgrade_receipt.py'}
        self.assertEqual(modules['multitask_receipt'].verifier_sources(), expected)
        startup = modules['startup_upgrade_receipt']
        self.assertEqual(startup.sha(Path(startup.__file__)), admission.HASHES['startup_upgrade_receipt.py'])

    def test_each_identity_field_rejected(self):
        for key in admission.IDENTITY:
            identity = dict(admission.IDENTITY, **{key: 'wrong'})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity'):
                admission.trusted(identity)
        with self.assertRaises(ValueError):
            admission.trusted(dict(admission.IDENTITY, extra='wrong'))

    def test_no_global_module_contamination(self):
        poison = types.ModuleType('poison')
        with patch.dict(sys.modules, attachment_contract=poison, diagnostics_contract=poison):
            modules = admission.trusted(admission.IDENTITY)
            self.assertEqual(len(modules['multitask_receipt'].verifier_sources()), 6)
            self.assertIs(sys.modules['attachment_contract'], poison)
            self.assertIs(sys.modules['diagnostics_contract'], poison)

    def test_snapshot_tampering_missing_extra_and_symlink_rejected(self):
        for mode in ('tamper', 'missing', 'extra', 'symlink'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / 'snapshot'
                shutil.copytree(admission.SNAPSHOT, folder)
                path = folder / 'multitask_receipt.py'
                if mode == 'tamper':
                    path.write_bytes(path.read_bytes() + b'\n')
                elif mode == 'missing':
                    path.unlink()
                elif mode == 'extra':
                    (folder / 'unexpected.py').write_text('')
                else:
                    path.unlink()
                    path.symlink_to(admission.SNAPSHOT / path.name)
                with patch.object(admission, 'SNAPSHOT', folder), self.assertRaises(ValueError):
                    admission.trusted(admission.IDENTITY)

    def test_both_callers_use_pinned_boundary(self):
        for name in ('acquire.py', 'independent_verify.py'):
            source = (ROOT / 'ci/packaging/zopfli' / name).read_text()
            self.assertIn('from pinned_native_admission import verify_multitask', source)
            self.assertIn('from pinned_native_admission import verify_startup_upgrade', source)
            self.assertNotIn('from ci.qualification.native.multitask_receipt import', source)
            self.assertNotIn('from ci.qualification.native.startup_upgrade_receipt import', source)


if __name__ == '__main__':
    unittest.main()
