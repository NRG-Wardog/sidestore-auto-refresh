"""Exercise approved P0 packaging inputs; never substitute synthetic native receipts."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import maintained_sources as gate
import maintained_package_evidence as package
import p0_maintained_sources as p0
import combined_build_evidence as collector
import verify_candidate_ipa as verifier

class P0PackageEvidenceTests(unittest.TestCase):
    def setUp(self):
        # A final integration branch is required to have reviewed P0 pins.
        # Missing pins, disabled admission, or an absent explicit source must fail.
        active = gate.load_pins(ROOT/'migration/maintained-sources.json')
        self.pin_path = ROOT/'migration/maintained-sources.json'
        if active['source_basis'] == 'p0-constructor-diagnostics-v1':
            self.pin_path = ROOT/'migration/p0-constructor-diagnostics-v1/inherited/p0-pins.json'
        self.pins = gate.load_pins(self.pin_path)
        self.assertEqual(self.pins['source_basis'], p0.BASIS)
        configured = os.environ.get('P0_ANISETTE_TEST_SOURCE')
        self.assertTrue(configured, 'P0_ANISETTE_TEST_SOURCE is required')
        self.source = Path(configured)
        self.assertTrue(self.source.is_dir(), 'Explicit P0 Anisette source is missing')
        self.expected = gate.expected_anisette_evidence(self.pins)
        self.assertEqual(gate.git(self.source, 'rev-parse', 'HEAD').decode().strip(), self.pins['owners']['AnisetteKit']['commit'])
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.output = self.root/'evidence'
        self.manifest = self.root/'manifest.json'
        selected, _ = gate.contract_basis(self.pins)
        self.manifest.write_bytes((selected/'anisette-generated/maintained-source-manifest.json').read_bytes())
        self.literals = [row['value'].encode() for row in self.expected['required_compiled_evidence']['literals']]
        self.binary = b'\0'.join(self.literals)
    def collect(self):
        # Runs the actual source verification subprocess. The binary is only a
        # literal-check fixture; genuine native/build evidence is admitted separately.
        return collector.collect_isolated_anisette_evidence(self.source, self.manifest, self.output,
                  self.binary, self.pin_path)
    def test_actual_p0_source_collection_and_contract_roundtrip(self):
        binding = self.collect()
        self.assertEqual(set(binding['source_sha256']), {row['path'] for row in self.expected['files']})
        self.assertIn('Native/Loader/android_tls_runtime.h', binding['source_sha256'])
        verifier.verify_isolated_anisette_evidence(self.output, binding, self.binary, self.pins)
        hashes = package.collect_contract_evidence(self.output, self.pins)
        package.verify_contract_evidence(self.output, hashes, self.pins)
        self.assertIn('reviewed-approval.json', hashes)
    def test_every_inherited_literal_is_required(self):
        for missing in self.literals:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                verifier.verify_isolated_anisette_binary(b'\0'.join(x for x in self.literals if x != missing), self.expected)
    def test_rehashed_p0_source_cannot_self_authorize(self):
        binding = self.collect()
        path = 'Native/Loader/android_tls_runtime.h'
        source = self.output/verifier.ANISETTE_EVIDENCE_DIRECTORY/path
        source.write_bytes(source.read_bytes()+b'\n// mutation\n')
        binding['source_sha256'][path] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.binary, self.pins)
    def test_rehashed_p0_contract_cannot_self_authorize(self):
        hashes = package.collect_contract_evidence(self.output, self.pins)
        path = self.output/package.CONTRACT_DIRECTORY/'native-receipt.json'
        path.write_bytes(b'{}\n'); hashes['native-receipt.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): package.verify_contract_evidence(self.output, hashes, self.pins)

if __name__ == '__main__': unittest.main()
