import copy,json,unittest
from unittest.mock import patch
import refresh_truth_setup_projection as r
import russian_setup_projection as old
import verify_setup_state_recovery as gate
class ExactSetupExtensionTests(unittest.TestCase):
 def setUp(self):
  self.ext,self.digest=r.extension();previous,_,_=old.extension()
  self.contract=json.loads((r.HERE/'setup-state-recovery-contract.json').read_text())
  self.contract['input_sha256'].update(previous['candidate_inputs'])
  self.prior=old.add(self.contract)
  # Final publication rebinds the Russian and truth extension to identical source bytes.
  self.previous=previous;self.previous['candidate_inputs'].update(self.ext['candidate_inputs'])
  self.prior['input_sha256'].update(self.ext['candidate_inputs'])
  self.patch=patch.object(old,'extension',return_value=(self.previous, self.contract['case_names']['setup-localization'],self.prior['russian_setup_extension']['manifest_sha256']));self.patch.start()
 def tearDown(self):self.patch.stop()
 def test_exact_twenty_additions_keep_all_prior_cases_and_baseline(self):
  before=copy.deepcopy(self.prior);after=r.add(before)
  self.assertEqual(before,self.prior);self.assertEqual(after['delivered_baseline'],before['delivered_baseline'])
  self.assertEqual([c for c in after['case_names']['setup-localization'] if c not in r.EXTRA],before['case_names']['setup-localization'])
  self.assertEqual(r.variant(after),{'cases':1779,'values':930,'baseline_missing':354,'keys':186})
  self.assertEqual(gate.markers(after)['localization-run'],'SETUP_LOCALIZATION_RENDERER_PASS cases=1779')
 def test_prior_variant_unchanged(self):self.assertEqual(r.variant(self.prior),{'cases':1759,'values':920,'baseline_missing':344})
 def test_drop_reorder_duplicate_extra_or_old_case_rejected(self):
  for action in ('drop','reorder','duplicate','old'):
   value=r.add(self.prior);rows=value['case_names']['setup-localization']
   if action=='drop':rows.remove(r.EXTRA[0])
   if action=='reorder':rows.reverse()
   if action=='duplicate':rows.append(rows[0])
   if action=='old':rows.remove(next(c for c in rows if c not in r.EXTRA))
   with self.subTest(action=action),self.assertRaises(ValueError):r.variant(value)
 def test_source_and_receipt_tamper_rejected(self):
  for key in self.ext['candidate_inputs']:
   value=r.add(self.prior);value['input_sha256'][key]='0'*64
   with self.subTest(key=key),self.assertRaisesRegex(ValueError,'source hashes'):r.variant(value)
  value=r.add(self.prior);value['refresh_truth_setup_extension']['manifest_sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'identity'):r.variant(value)
 def test_manifest_finite_additions_cannot_widen(self):
  for key,new in [('additional_cases',r.EXTRA+['arbitrary']),('expected_case_count',1780)]:
   value=copy.deepcopy(self.ext);value[key]=new
   with patch.object(r.json,'loads',return_value=value),self.assertRaises(ValueError):r.extension()
if __name__=='__main__':unittest.main()
