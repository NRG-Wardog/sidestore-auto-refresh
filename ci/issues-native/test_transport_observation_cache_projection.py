import copy,json,unittest
from pathlib import Path
import transport_observation_cache_projection as p
import cold_vpn_cache_projection as old
class ProjectionTests(unittest.TestCase):
 def setUp(self):
  self.base=json.loads(Path(__file__).with_name('setup-state-recovery-contract.json').read_text())
  self.base['input_sha256'].update({old.PATH:old.SHA,old.TRUTH_PATH:old.TRUTH_SHA,p.PATH:p.SHA})
  self.proof={'host_pipeline_byte_identical':False,'host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical':True,'reviewed_refresh_truth_delta_sha256':old.TRUTH_SHA,'reviewed_cold_vpn_delta_sha256':old.SHA,'reviewed_transport_observation_delta_sha256':p.SHA}
 def test_exact_layer_preserves_old_proofs_and_case_closure(self):
  original=copy.deepcopy(self.base);result=p.add(self.base)
  self.assertEqual(self.base,original);self.assertEqual(result['case_names'],original['case_names']);self.assertEqual(result['delivered_baseline'],original['delivered_baseline'])
  self.assertEqual(p.inputs(result,original['additive_suite_inputs']['cache-compatibility']),result['additive_suite_inputs']['cache-compatibility'])
  p.verify_proof(self.proof,result)
 def test_old_variant_unchanged_and_unexpected_observation_rejected(self):
  value=copy.deepcopy(self.base);value['input_sha256'].pop(p.PATH);result=p.add(value)
  self.assertEqual(result,old.add(value))
  proof=dict(self.proof);proof.pop('reviewed_transport_observation_delta_sha256');p.verify_proof(proof,result)
  with self.assertRaises(ValueError):p.verify_proof(self.proof,result)
 def test_missing_changed_old_or_new_proof_rejected(self):
  result=p.add(self.base)
  for key in self.proof:
   proof=dict(self.proof);proof.pop(key)
   with self.subTest(key=key),self.assertRaises(ValueError):p.verify_proof(proof,result)
  for action in ('hash','extra','oldhash','missing'):
   value=copy.deepcopy(result)
   if action=='hash':value['input_sha256'][p.PATH]='0'*64
   elif action=='extra':value[p.KEY]['extra']=True
   elif action=='oldhash':value['input_sha256'][old.TRUTH_PATH]='0'*64
   else:value['input_sha256'].pop(p.PATH)
   with self.subTest(action=action),self.assertRaises(ValueError):p.inputs(value,self.base['additive_suite_inputs']['cache-compatibility'])
if __name__=='__main__':unittest.main()
