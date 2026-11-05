import copy,unittest
import cold_vpn_cache_projection as P
class ColdCacheTests(unittest.TestCase):
 def setUp(self):
  self.original={'input_sha256':{P.PATH:P.SHA},'additive_suite_inputs':{'cache-compatibility':{'fixture':['old.py'],'production':['host.swift']}}}
  self.current=P.add(self.original)
 def test_exact_additive_fixture_preserves_original(self):
  self.assertEqual(self.original['additive_suite_inputs']['cache-compatibility']['fixture'],['old.py'])
  self.assertEqual(self.current['additive_suite_inputs']['cache-compatibility']['fixture'],['old.py',P.PATH])
  self.assertEqual(P.inputs(self.current,self.original['additive_suite_inputs']['cache-compatibility']),self.current['additive_suite_inputs']['cache-compatibility'])
  for key,value in [(P.KEY,{'delta_path':P.PATH,'delta_sha256':'0'*64}),('input_sha256',{P.PATH:'0'*64})]:
   with self.assertRaises(ValueError):P.binding({**self.current,key:value})
  with self.assertRaises(ValueError):P.add(self.current)
  with self.assertRaises(ValueError):P.inputs(self.current,self.current['additive_suite_inputs']['cache-compatibility'])
 def test_honest_new_proof_and_stale_or_tampered_proof_rejected(self):
  proof={'host_pipeline_byte_identical':False,'host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical':True,'reviewed_cold_vpn_delta_sha256':P.SHA}
  self.assertIsNotNone(P.verify_proof(proof,self.current))
  for key,value in [('host_pipeline_byte_identical',True),('host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical',False),('host_pipeline_except_reviewed_reuse_main_byte_identical',True),('reviewed_cold_vpn_delta_sha256','0'*64)]:
   with self.assertRaises(ValueError):P.verify_proof({**proof,key:value},self.current)
  self.assertIsNone(P.verify_proof({},self.original))
class RefreshTruthCacheTests(unittest.TestCase):
 def setUp(self):
  self.original={'input_sha256':{P.PATH:P.SHA,P.TRUTH_PATH:P.TRUTH_SHA},'additive_suite_inputs':{'cache-compatibility':{'fixture':['old.py'],'production':['host.swift']}}}
  self.current=P.add(self.original)
  self.proof={'host_pipeline_byte_identical':False,'host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical':True,'reviewed_cold_vpn_delta_sha256':P.SHA,'reviewed_refresh_truth_delta_sha256':P.TRUTH_SHA}
 def test_exact_new_manifest_bound_without_rewriting_prior(self):
  self.assertEqual(self.original['additive_suite_inputs']['cache-compatibility']['fixture'],['old.py'])
  self.assertEqual(self.current['additive_suite_inputs']['cache-compatibility']['fixture'],['old.py',P.PATH,P.TRUTH_PATH])
  self.assertIsNotNone(P.verify_proof(self.proof,self.current))
 def test_missing_stale_or_broader_proof_rejected(self):
  for key,value in [('host_pipeline_byte_identical',True),('host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical',False),('host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical',True),('reviewed_refresh_truth_delta_sha256','0'*64),('reviewed_cold_vpn_delta_sha256','0'*64)]:
   with self.subTest(key=key),self.assertRaises(ValueError):P.verify_proof({**self.proof,key:value},self.current)
 def test_wrong_manifest_hash_or_identity_rejected(self):
  value=copy.deepcopy(self.current);value['input_sha256'][P.TRUTH_PATH]='0'*64
  with self.assertRaises(ValueError):P.truth_binding(value)
  value=copy.deepcopy(self.current);value[P.TRUTH_KEY]['delta_path']='elsewhere'
  with self.assertRaises(ValueError):P.truth_binding(value)
  value=copy.deepcopy(self.current);value.pop(P.KEY)
  with self.assertRaises(ValueError):P.truth_binding(value)
if __name__=='__main__':unittest.main(verbosity=2)
