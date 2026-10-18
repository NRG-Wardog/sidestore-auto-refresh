"""Constructor compiled-marker gates plus actual source collection when active."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import p0_constructor_diagnostics as d
import maintained_sources as gate
import combined_build_evidence as collector
import verify_candidate_ipa as verifier
class ConstructorPackageTests(unittest.TestCase):
    def test_constructor_source_hashes_use_exact_maintained_bytes(self):
        row={'path':'Native/example.cpp','sha256':'a'*64,'prepared_sha256':'b'*64}
        for basis in (gate.DIAGNOSTIC_BASIS,gate.P0_BASIS,d.BASIS):
            self.assertEqual(gate.anisette_source_hashes({'source_basis':basis,'files':[row]}),{row['path']:'a'*64})
        self.assertEqual(gate.anisette_source_hashes({'files':[row]}),{row['path']:'b'*64})
    def test_each_binary_must_keep_all_five_constructor_keys(self):
        pins={'source_basis':d.BASIS};keys=[b'native_init_'+part for part in (b'kind',b'reason',b'engine',b'library',b'initializer')]
        full=b'\0'.join(keys+[b'setupConstructors',d.REQUIRED_DIAGNOSTIC_LITERALS[0].encode()])
        verifier.verify_constructor_diagnostic_binaries(full,full,full,pins)
        for i in range(3):
            for key in keys:
                binaries=[full]*3;binaries[i]=full.replace(key,b'absent')
                with self.subTest(binary=i,key=key),self.assertRaises(ValueError):verifier.verify_constructor_diagnostic_binaries(*binaries,pins)
    def test_phase_and_unique_native_prefix_required_only_for_new_basis(self):
        full=b' '.join([b'native_init_'+v for v in (b'kind',b'reason',b'engine',b'library',b'initializer')]+[b'setupConstructors',d.REQUIRED_DIAGNOSTIC_LITERALS[0].encode()])
        for position,marker in ((1,b'setupConstructors'),(2,b'setupConstructors'),(1,d.REQUIRED_DIAGNOSTIC_LITERALS[0].encode())):
            parts=[full]*3;parts[position]=full.replace(marker,b'absent')
            with self.assertRaises(ValueError):verifier.verify_constructor_diagnostic_binaries(*parts,{'source_basis':d.BASIS})
        verifier.verify_constructor_diagnostic_binaries(b'',b'',b'',{'source_basis':'p0-abi-tls-certificate-lifecycle-v1'})
    def test_actual_active_source_collection_or_pending_scaffold(self):
        pin_path=ROOT/'migration/maintained-sources.json'
        pins=gate.load_pins(pin_path)
        if pins['source_basis'] in {'p0-elf-metadata-bounds-v1','p0-cxx-allocation-v1','p2-composed-host-v1'}:
            pin_path=ROOT/'migration/p0-elf-metadata-bounds-v1/inherited/constructor-pins.json'
            pins=gate.load_pins(pin_path)
        if pins['source_basis']!=d.BASIS:
            self.assertIsNone(d.DIAGNOSTIC_REVIEW_SHA256)
            with self.assertRaises(ValueError):d.approval(ROOT)
            return
        source=os.environ.get('P0_DIAGNOSTIC_ANISETTE_TEST_SOURCE');self.assertTrue(source,'explicit diagnostic Anisette source required')
        expected=gate.expected_anisette_evidence(pins);self.assertEqual(gate.git(Path(source),'rev-parse','HEAD').decode().strip(),expected['anisettekit_revision'])
        binary=b'\0'.join(row['value'].encode()for row in expected['required_compiled_evidence']['literals'])
        root,_=gate.contract_basis(pins)
        with tempfile.TemporaryDirectory()as tmp:
            binding=collector.collect_isolated_anisette_evidence(Path(source),root/'anisette-generated/maintained-source-manifest.json',Path(tmp),binary,pin_path)
            verifier.verify_isolated_anisette_evidence(Path(tmp),binding,binary,pins)
if __name__=='__main__':unittest.main()
