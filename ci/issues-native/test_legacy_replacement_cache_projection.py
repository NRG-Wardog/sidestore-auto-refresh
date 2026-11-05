import copy,json,unittest
from pathlib import Path
import legacy_replacement_cache_projection as p
import transport_observation_cache_projection as observation
import cold_vpn_cache_projection as old
class ProjectionTests(unittest.TestCase):
 def setUp(self):
  self.base=json.loads(Path(__file__).with_name('setup-state-recovery-contract.json').read_text())
  self.base['input_sha256'].update({old.PATH:old.SHA,old.TRUTH_PATH:old.TRUTH_SHA,observation.PATH:observation.SHA,p.PATH:p.SHA,p.STAGE:p.STAGE_SHA})
  self.proof={'host_pipeline_byte_identical':False,'host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical':True,'reviewed_refresh_truth_delta_sha256':old.TRUTH_SHA,'reviewed_cold_vpn_delta_sha256':old.SHA,'reviewed_transport_observation_delta_sha256':observation.SHA,'reviewed_legacy_replacement_delta_sha256':p.SHA}
 def test_exact_layer_keeps_prior_cases_and_baseline(self):
  original=copy.deepcopy(self.base);result=p.add(self.base)
  self.assertEqual(original,self.base);self.assertEqual(original['case_names'],result['case_names']);self.assertEqual(original['delivered_baseline'],result['delivered_baseline'])
  self.assertEqual(p.inputs(result,original['additive_suite_inputs']['cache-compatibility']),result['additive_suite_inputs']['cache-compatibility'])
  p.verify_proof(self.proof,result)
 def test_prior_variant_unchanged(self):
  base=copy.deepcopy(self.base);base['input_sha256'].pop(p.PATH);base['input_sha256'].pop(p.STAGE)
  result=p.add(base);self.assertEqual(result,observation.add(base))
  proof=dict(self.proof);proof.pop('reviewed_legacy_replacement_delta_sha256');p.verify_proof(proof,result)
  with self.assertRaises(ValueError):p.verify_proof(self.proof,result)
 def test_changed_missing_or_duplicate_boundaries_fail(self):
  result=p.add(self.base)
  for path in (p.PATH,p.STAGE,old.TRUTH_PATH,observation.PATH):
   for missing in (False,True):
    value=copy.deepcopy(result)
    if missing:value['input_sha256'].pop(path)
    else:value['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):p.inputs(value,self.base['additive_suite_inputs']['cache-compatibility'])
  for key in self.proof:
   proof=dict(self.proof);proof.pop(key)
   with self.assertRaises(ValueError):p.verify_proof(proof,result)
  value=copy.deepcopy(result);value[p.KEY]['extra']=True
  with self.assertRaises(ValueError):p.legacy_binding(value)
if __name__=='__main__':unittest.main()
