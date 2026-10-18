"""Actual bounds-source packaging checks; pending admission remains fail-closed."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import p0_elf_metadata_bounds as bounds
import maintained_sources as gate
import maintained_package_evidence as package
import combined_build_evidence as collector
import verify_candidate_ipa as verifier

class BoundsPackageEvidenceTests(unittest.TestCase):
    def admitted(self):
        if bounds.REVIEW_SHA256 is None:
            with self.assertRaisesRegex(ValueError,'pending'):bounds.approval(ROOT)
            return False
        self.pins=gate.load_pins(ROOT/'migration/maintained-sources.json')
        self.assertEqual(self.pins['source_basis'],bounds.BASIS)
        configured=os.environ.get('P0_BOUNDS_ANISETTE_TEST_SOURCE')
        self.assertTrue(configured,'explicit final bounds AK source required')
        self.source=Path(configured);self.assertTrue(self.source.is_dir())
        self.assertEqual(gate.git(self.source,'rev-parse','HEAD').decode().strip(),bounds.AK_COMMIT)
        self.assertEqual(gate.git(self.source,'rev-parse','HEAD^{tree}').decode().strip(),bounds.AK_TREE)
        self.expected=gate.expected_anisette_evidence(self.pins)
        self.root,_=gate.contract_basis(self.pins)
        self.literals=[row['value'].encode() for row in self.expected['required_compiled_evidence']['literals']]
        self.binary=b'\0'.join(self.literals)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.output=Path(self.tmp.name).resolve()/'evidence'
        return True
    def collect(self):
        return collector.collect_isolated_anisette_evidence(self.source,self.root/'anisette-generated/maintained-source-manifest.json',self.output,self.binary,ROOT/'migration/maintained-sources.json')
    def test_actual_final_source_and_contract_roundtrip_or_pending(self):
        if not self.admitted():return
        binding=self.collect();self.assertIn('Native/Loader/elf_metadata_bounds.h',binding['source_sha256'])
        self.assertIn(bounds.FIXTURE_FILE['path'],binding['source_sha256'])
        verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
        hashes=package.collect_contract_evidence(self.output,self.pins);package.verify_contract_evidence(self.output,hashes,self.pins)
        self.assertIn('reviewed-host-fixture-manifest.json',hashes)
        self.assertIn('historical-evidence.json',hashes)
    def test_each_inherited_compiled_literal_required_or_pending(self):
        if not self.admitted():return
        for missing in self.literals:
            with self.subTest(missing=missing),self.assertRaises(ValueError):
                verifier.verify_isolated_anisette_binary(b'\0'.join(x for x in self.literals if x!=missing),self.expected)
    def test_rehashed_helper_source_cannot_self_authorize_or_pending(self):
        if not self.admitted():return
        binding=self.collect();name='Native/Loader/elf_metadata_bounds.h';path=self.output/verifier.ANISETTE_EVIDENCE_DIRECTORY/name
        path.write_bytes(path.read_bytes()+b'\n// mutation\n');binding['source_sha256'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
    def test_historical_and_fresh_receipts_cannot_be_swapped_or_pending(self):
        if not self.admitted():return
        hashes=package.collect_contract_evidence(self.output,self.pins);path=self.output/package.CONTRACT_DIRECTORY/'metadata-helper.json'
        path.write_bytes((self.output/package.CONTRACT_DIRECTORY/'historical-evidence.json').read_bytes());hashes['metadata-helper.json']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):package.verify_contract_evidence(self.output,hashes,self.pins)

if __name__=='__main__':unittest.main()
