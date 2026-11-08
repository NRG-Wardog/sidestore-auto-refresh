"""Retired experimental inputs fail closed; baseline product fixtures remain."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import maintained_sources as sources
import maintained_package_evidence as packages
import verify_candidate_ipa as ipa

RETIRED = (
    'p0-constructor-diagnostics-v1', 'p0-cxx-allocation-v1',
    'p0-elf-metadata-bounds-v1', 'p0-full-login-diagnostics-v1',
    'p0-abi-tls-certificate-lifecycle-v1', 'p1-ui-feedback-v1',
    'p2-composed-host-v1', 'maintained-adi-consumption-v1',
    'maintained-adi-consumption-v2', 'unknown-new-experiment',
)
FIXTURE = ROOT/'migration/tests/fixtures/accepted-maintained-sources-a939e4c.json'


class RetiredSourceBasisTests(unittest.TestCase):
    def test_baseline_identity_and_evidence_remain_exact(self):
        pins = sources.load_pins(FIXTURE)
        self.assertIsNone(pins.get('source_basis'))
        directory, registry = sources.contract_basis(pins)
        self.assertEqual(registry, sources.REGISTRY)
        self.assertEqual(directory, ROOT/'migration/contracts')
        self.assertIn('compatibility-registry.json', packages.contract_files(pins))
        self.assertTrue(sources.anisette_source_hashes(sources.expected_anisette_evidence(pins)))

    def test_retired_pins_rejected_before_network_source_access_or_writes(self):
        baseline = json.loads(FIXTURE.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for basis in RETIRED:
                with self.subTest(basis=basis):
                    pins = {**baseline, 'source_basis': basis}
                    path = root/'pins.json';path.write_text(json.dumps(pins))
                    with patch.object(sources, 'git') as git, patch.object(sources.subprocess, 'run') as run:
                        operations = (
                            lambda: sources.load_pins(path),
                            lambda: sources.contract_basis(pins),
                            lambda: sources.acquire(root/'must-not-exist', pins),
                            lambda: sources.verify_all(root/'must-not-exist', pins),
                            lambda: sources.expected_anisette_evidence(pins),
                            lambda: sources.anisette_evidence(root, pins),
                            lambda: sources.verify_source_transition(root, "SideStore", {}, pins),
                            lambda: sources.verify_native_readiness({}, pins),
                            lambda: packages.contract_files(pins),
                        )
                        for operation in operations:
                            with self.assertRaises(ValueError): operation()
                        git.assert_not_called();run.assert_not_called()
                    self.assertFalse((root/'must-not-exist').exists())

    def test_binary_and_manifest_helpers_cannot_admit_retired_basis(self):
        for basis in RETIRED:
            with self.subTest(basis=basis):
                with self.assertRaisesRegex(ValueError, 'retired'):
                    sources.anisette_source_hashes({'source_basis': basis, 'files': []})
                with self.assertRaisesRegex(ValueError, 'retired'):
                    ipa.verify_constructor_diagnostic_binaries(b'', b'', b'', {'source_basis': basis})
                with self.assertRaisesRegex(ValueError, 'retired'):
                    ipa.verify_isolated_anisette_binary(ipa.ANISETTE_COMPILED_MARKER + ipa.ANISETTE_COMPILED_LITERAL,
                                                        {'source_basis': basis})

    def test_no_implicit_retired_default_pins(self):
        with patch.object(sys, 'argv', ['maintained_sources.py', 'env']):
            with self.assertRaises(SystemExit) as stopped: sources.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertFalse((ROOT/'migration/maintained-sources.json').exists())


if __name__ == '__main__':
    unittest.main()
