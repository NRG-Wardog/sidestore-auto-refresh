import copy,json,unittest
from unittest.mock import patch
import refresh_observation_setup_projection as r
import refresh_truth_setup_projection as truth
import russian_setup_projection as russian
import verify_setup_state_recovery as gate
class ExactObservationProjectionTests(unittest.TestCase):
 def setUp(self):
  self.ext,self.digest=r.extension()
  ru,original,ru_digest=russian.extension();tr,tr_digest=truth.extension()
  # Synthetic source rebinding only. Real prior case validators remain active.
  hashes={**ru['candidate_inputs'],**tr['candidate_inputs'],**self.ext['candidate_inputs']}
  ru['candidate_inputs']={p:hashes[p] for p in ru['candidate_inputs']};tr['candidate_inputs']={p:hashes[p] for p in tr['candidate_inputs']}
  self.patches=[patch.object(russian,'extension',return_value=(ru,original,ru_digest)),patch.object(truth,'extension',return_value=(tr,tr_digest))]
  for p in self.patches:p.start()
  base=json.loads((r.HERE/'setup-state-recovery-contract.json').read_text());base['input_sha256'].update(hashes)
  self.prior=truth.add(russian.add(base))
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
 def test_exact_replacement_normalizes_through_real_prior_validator(self):
  before=copy.deepcopy(self.prior);after=r.add(before)
  self.assertEqual(before,self.prior);self.assertEqual(r.variant(after),r.CURRENT)
  self.assertEqual(len(r.REPLACEMENTS),20);self.assertEqual(len(r.EXTRA),200)
  inverse={n:o for o,n in r.REPLACEMENTS.items()}
  self.assertEqual(sorted(inverse.get(c,c) for c in after['case_names']['setup-localization'] if c not in r.EXTRA),before['case_names']['setup-localization'])
  self.assertEqual(after['delivered_baseline'],before['delivered_baseline'])
  self.assertEqual(gate.markers(after)['localization-run'],'SETUP_LOCALIZATION_RENDERER_PASS cases=1979')
 def test_prior_variant_stays_unchanged(self):self.assertEqual(r.variant(self.prior),r.PRIOR)
 def test_missing_unreviewed_old_case_cannot_be_hidden_by_new_case(self):
  for change in ('old','new','replace','duplicate','order','obsolete'):
   value=r.add(self.prior);cases=value['case_names']['setup-localization']
   if change=='old':cases.remove(next(c for c in cases if c not in r.EXTRA and c not in r.REPLACEMENTS.values()));cases.append('unreviewed-new-case');cases.sort()
   elif change=='new':cases.remove(r.EXTRA[0])
   elif change=='replace':cases.remove(next(iter(r.REPLACEMENTS.values())))
   elif change=='duplicate':cases[-1]=cases[0];cases.sort()
   elif change=='order':cases.reverse()
   else:cases[0]=next(iter(r.REPLACEMENTS));cases.sort()
   with self.subTest(change=change),self.assertRaises(ValueError):r.variant(value)
 def test_source_hash_identity_type_or_manifest_drift_rejected(self):
  for path in self.ext['candidate_inputs']:
   value=r.add(self.prior);value['input_sha256'][path]='0'*64
   with self.subTest(path=path),self.assertRaises(ValueError):r.variant(value)
  value=r.add(self.prior);value[r.TAG]['manifest_sha256']='0'*64
  with self.assertRaises(ValueError):r.variant(value)
  for key,data in [('additional_cases',r.EXTRA+['new']),('replacements',{}),('key_count',206.0),('expected_case_count',True),('candidate_inputs',{**self.ext['candidate_inputs'],'unreviewed':'0'*64})]:
   value=copy.deepcopy(self.ext);value[key]=data
   with patch.object(r.json,'loads',return_value=value),self.subTest(key=key),self.assertRaises(ValueError):r.extension()
 def test_duplicate_json_keys_rejected(self):
  with self.assertRaises(ValueError):r.pairs([('a',1),('a',2)])
if __name__=='__main__':unittest.main()
