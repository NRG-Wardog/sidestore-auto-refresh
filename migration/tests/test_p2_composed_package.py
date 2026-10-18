"""Real source packaging policy once admitted; never substitutes for native capture."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import p2_composed_host as p2
import p0_cxx_allocation as cxx
import maintained_sources as gate
import maintained_package_evidence as package
import combined_build_evidence as collector
import verify_candidate_ipa as verifier

class ComposedPackageEvidenceTests(unittest.TestCase):
 def admitted(self):
  if p2.REVIEW_SHA256 is None:
   with self.assertRaisesRegex(ValueError,'pending'):p2.approval(ROOT)
   return False
  self.pin_path=ROOT/'migration/maintained-sources.json';self.pins=gate.load_pins(self.pin_path)
  self.assertEqual(self.pins['source_basis'],p2.BASIS)
  configured=os.environ.get('P0_CXX_ANISETTE_TEST_SOURCE');self.assertTrue(configured,'explicit unchanged C++ AK source required')
  self.source=Path(configured)
  self.assertEqual(gate.git(self.source,'rev-parse','HEAD').decode().strip(),cxx.AK_COMMIT)
  self.assertEqual(gate.git(self.source,'rev-parse','HEAD^{tree}').decode().strip(),cxx.AK_TREE)
  self.expected=gate.expected_anisette_evidence(self.pins);self.assertEqual(self.expected['source_basis'],cxx.BASIS)
  self.root,_=gate.contract_basis(self.pins);self.literals=[r['value'].encode()for r in self.expected['required_compiled_evidence']['literals']]
  self.binary=b'\0'.join(self.literals);self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.output=Path(self.tmp.name).resolve()/'evidence'
  return True
 def collect(self):
  return collector.collect_isolated_anisette_evidence(self.source,self.root/'anisette-generated/maintained-source-manifest.json',self.output,self.binary,self.pin_path)
 def test_actual_source_package_roundtrip_or_pending(self):
  if not self.admitted():return
  binding=self.collect();verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
  hashes=package.collect_contract_evidence(self.output,self.pins);package.verify_contract_evidence(self.output,hashes,self.pins)
  self.assertIn('native-capture.json',hashes);self.assertIn('historical-p0/allocation-helper.json',hashes)
 def test_every_inherited_compiled_literal_required_or_pending(self):
  if not self.admitted():return
  for missing in self.literals:
   with self.subTest(missing=missing),self.assertRaises(ValueError):verifier.verify_isolated_anisette_binary(b'\0'.join(v for v in self.literals if v!=missing),self.expected)
 def test_rehashed_actual_source_cannot_self_authorize_or_pending(self):
  if not self.admitted():return
  binding=self.collect();name='Native/Loader/cxx_allocation_runtime.h';path=self.output/verifier.ANISETTE_EVIDENCE_DIRECTORY/name
  path.write_bytes(path.read_bytes()+b'\n// test mutation\n');binding['source_sha256'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
  with self.assertRaises(ValueError):verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
 def test_rehashed_capture_swap_cannot_self_authorize_or_pending(self):
  if not self.admitted():return
  hashes=package.collect_contract_evidence(self.output,self.pins);path=self.output/package.CONTRACT_DIRECTORY/'native-capture.json'
  path.write_bytes((self.output/package.CONTRACT_DIRECTORY/'historical-p0/native-receipt.json').read_bytes());hashes['native-capture.json']=hashlib.sha256(path.read_bytes()).hexdigest()
  with self.assertRaises(ValueError):package.verify_contract_evidence(self.output,hashes,self.pins)

if __name__=='__main__':unittest.main()
