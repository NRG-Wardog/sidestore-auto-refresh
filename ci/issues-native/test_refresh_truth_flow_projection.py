import copy,json,unittest
from unittest.mock import patch
import refresh_truth_flow_projection as r
import grace_recovery_projection as g
import verify_setup_state_recovery as gate
class FlowTruthProjection(unittest.TestCase):
 def setUp(self):
  self.ext,self.digest=r.extension()
  original=json.loads((r.HERE/'setup-state-recovery-contract.json').read_text());original['input_sha256'].update(g.FILES)
  self.prior=g.add(original)
  self.prior['input_sha256'].update(self.ext['candidate_inputs'])
  files=dict(g.FILES);files.update({p:h for p,h in self.ext['candidate_inputs'].items() if p in files})
  self.patch=patch.object(g,'FILES',files);self.patch.start();self.prior[g.KEY]['source_sha256']=files
 def tearDown(self):self.patch.stop()
 def test_exact_two_cases_preserve_all_prefixes(self):
  old=copy.deepcopy(self.prior);new=r.add(old);self.assertEqual(old,self.prior)
  for suite in r.ADDED:self.assertEqual(new['case_names'][suite][:-1],old['case_names'][suite])
  self.assertEqual(r.vpn_count(new),106);self.assertEqual(r.connection_count(new),26)
  self.assertEqual(gate.markers(new)['vpn-recovery-run'],'VPN_REFRESH_RECOVERY_PASS cases=106')
  self.assertEqual(gate.markers(new)['connection-run'],'SETUP_CONNECTION_EVIDENCE_PASS cases=26')
 def test_previous_counts_unchanged(self):
  self.assertEqual(r.vpn_count(self.prior),105);self.assertEqual(r.connection_count(self.prior),25)
 def test_drop_reorder_or_unreviewed_case_rejected(self):
  for suite in r.ADDED:
   for action in ('drop','reorder','unexpected'):
    v=r.add(self.prior);rows=v['case_names'][suite]
    if action=='drop':rows.pop(0)
    elif action=='reorder':rows.reverse()
    else:rows[-1]='not-reviewed'
    with self.subTest(suite=suite,action=action),self.assertRaises(ValueError):r.normalize(v)
 def test_source_and_identity_tamper_rejected(self):
  for p in self.ext['candidate_inputs']:
   v=r.add(self.prior);v['input_sha256'][p]='0'*64
   with self.assertRaises(ValueError):r.normalize(v)
  v=r.add(self.prior);v[r.KEY]['manifest_sha256']='0'*64
  with self.assertRaises(ValueError):r.normalize(v)
 def test_omitting_prior_guard_cannot_qualify(self):
  v=r.add(self.prior);v.pop(g.KEY)
  with self.assertRaises(ValueError):r.normalize(v)
if __name__=='__main__':unittest.main()
