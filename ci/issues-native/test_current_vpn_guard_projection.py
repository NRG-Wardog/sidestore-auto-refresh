import unittest
import current_vpn_guard_projection as P
class GuardProofTests(unittest.TestCase):
 def setUp(self):
  self.contract={'input_sha256':{P.PATH:P.EXPECTED['source_receipt_sha256'],P.GENERATOR:P.EXPECTED['generator_sha256'],P.BASELINE:P.EXPECTED['baseline_classifier_sha256']}}
  self.current=P.add(self.contract);self.proof={'cold_guard_provenance':P.CURRENT,'baseline_classifier_sha256':P.EXPECTED['baseline_classifier_sha256']}
 def test_current_exact_source_bound_proof(self):
  P.verify(self.proof,self.current)
  self.assertNotIn(P.KEY,self.contract)
  with self.assertRaises(ValueError):P.verify(self.proof,self.contract)
  with self.assertRaises(ValueError):P.add(self.current)
 def test_old_claim_or_baseline_mutation_rejects(self):
  for key,value in [('cold_guard_provenance',P.LEGACY),('cold_guard_provenance','pinned_source_receipt_sparse_generation'),('baseline_classifier_sha256','0'*64)]:
   with self.assertRaises(ValueError):P.verify({**self.proof,key:value},self.current)
  for path in self.contract['input_sha256']:
   with self.assertRaises(ValueError):P.verify(self.proof,{**self.current,'input_sha256':{**self.current['input_sha256'],path:'0'*64}})
  for key in P.EXPECTED:
   with self.assertRaises(ValueError):P.verify(self.proof,{**self.current,P.KEY:{**P.EXPECTED,key:'0'*64}})
 def test_historical_variant_preserves_exact_classifier(self):
  old={'input_sha256':{P.PATH:'1'*64,P.BASELINE:'2'*64}}
  P.verify({'cold_guard_provenance':P.LEGACY,'baseline_classifier_sha256':'2'*64},old)
  with self.assertRaises(ValueError):P.verify({'cold_guard_provenance':P.CURRENT,'baseline_classifier_sha256':'2'*64},old)
if __name__=='__main__':unittest.main(verbosity=2)
