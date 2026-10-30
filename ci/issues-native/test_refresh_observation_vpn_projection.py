import copy,json,unittest
from pathlib import Path
import refresh_observation_vpn_projection as p
import local_wifi_fixture_projection as old
class ProjectionTests(unittest.TestCase):
 def setUp(self):
  self.base=json.loads(Path(__file__).with_name('setup-state-recovery-contract.json').read_text())
  self.base['input_sha256'].update({old.PATH:old.SHA,p.PATH:p.SHA})
  self.prior=old.add(self.base)
 def test_one_exact_dependency_preserves_prior_and_cases(self):
  before=copy.deepcopy(self.prior);result=p.add(before)
  self.assertEqual(before,self.prior)
  self.assertEqual(result['case_names'],before['case_names'])
  self.assertEqual(result['delivered_baseline'],before['delivered_baseline'])
  self.assertEqual(result[old.KEY],before[old.KEY])
  raw=self.base['additive_suite_inputs']['vpn-refresh-recovery']
  expected=p.inputs(result,raw)
  self.assertEqual(expected,result['additive_suite_inputs']['vpn-refresh-recovery'])
  self.assertEqual(expected['transport_fixture'][:-1],before['additive_suite_inputs']['vpn-refresh-recovery']['transport_fixture'])
  self.assertEqual(expected['transport_fixture'][-1],p.PATH)
 def test_old_variant_unchanged(self):
  raw=self.base['additive_suite_inputs']['vpn-refresh-recovery']
  self.assertEqual(p.inputs(self.prior,raw),old.inputs(self.prior,raw))
 def test_hash_path_metadata_duplicate_rejected(self):
  raw=self.base['additive_suite_inputs']['vpn-refresh-recovery']
  for action in ('hash','path','extra','missing','priorhash'):
   value=p.add(self.prior)
   if action=='hash':value['input_sha256'][p.PATH]='0'*64
   elif action=='path':value[p.KEY]['path']='unreviewed'
   elif action=='extra':value[p.KEY]['extra']=True
   elif action=='missing':value['input_sha256'].pop(p.PATH)
   else:value['input_sha256'][old.PATH]='0'*64
   with self.subTest(action=action),self.assertRaises(ValueError):p.inputs(value,raw)
  value=p.add(self.prior)
  with self.assertRaises(ValueError):p.add(value)
  duplicated=copy.deepcopy(raw);duplicated['transport_fixture'].append(p.PATH)
  with self.assertRaises(ValueError):p.inputs(value,duplicated)
if __name__=='__main__':unittest.main()
