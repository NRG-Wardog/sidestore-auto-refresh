import copy, unittest
from pathlib import Path
import observed_constraints_localization_projection as p
import test_preservation_localization_projection as baseline_tests

class ObservedConstraintCopy(unittest.TestCase):
 def setUp(self):
  baseline_tests.LocalizationSuccessor.setUp(self)
 def test_exact_twenty_replacements_reverse_through_unchanged_prior_validator(self):
  before=copy.deepcopy(self.base);old=p.prior.add(self.base);value=p.add(self.base)
  self.assertEqual(self.base,before)
  self.assertEqual(p.normalized(value),old)
  self.assertEqual(p.variant(value),{'cases':2079,'values':1080,'baseline_missing':504,'keys':216})
  self.assertEqual(value['delivered_baseline'],old['delivered_baseline'])
  self.assertEqual(len(p.COPY_REPLACEMENTS),3);self.assertEqual(len(p.CASE_REPLACEMENTS),20)
  a=set(old['case_names']['setup-localization']);b=set(value['case_names']['setup-localization'])
  self.assertEqual(a-b,set(p.CASE_REPLACEMENTS));self.assertEqual(b-a,set(p.CASE_REPLACEMENTS.values()))
  self.assertEqual(p.variant(old),p.prior.variant(old))
 def test_exact_copy_map_semantics_not_count_only(self):
  self.assertEqual(p.COPY_REPLACEMENTS,{
   'Refresh stopped because existing profile authorizations are ambiguous or conflicting. The installed app was preserved.':'Refresh stopped because observed profile constraints are ambiguous or conflicting. The installed app was preserved.',
   'Refresh stopped because the existing matching profile could not be verified on the device. The installed app was preserved.':'Refresh stopped because the profiles could not be read and matched safely. The installed app was preserved.',
   'Profile-store expiration advanced. Preservation-mode targets were not re-signed or replaced. Selection of these renewed profiles by the app and its extensions has not been independently verified.':'Profile-store expiration advanced while observed profile constraints were kept unchanged. Preservation-mode targets were not re-signed or replaced. This does not verify which profiles the app or its extensions use, future launch, or Keychain access.'})
  self.assertEqual(set(p.CASE_REPLACEMENTS)&set(p.prior.EXTRA),set(p.CASE_REPLACEMENTS))
 def test_changed_missing_extra_source_or_descriptor_rejected(self):
  value=p.add(self.base)
  for path in p.PINS:
   for absent in (False,True):
    changed=copy.deepcopy(value)
    if absent:changed['input_sha256'].pop(path)
    else:changed['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):p.variant(changed)
  for mutate in (lambda c:c[p.TAG].update(extra=True),lambda c:c.pop(p.prior.TAG),lambda c:c[p.TAG]['copy_replacements'].update(unknown='unknown'),lambda c:c[p.TAG]['case_replacements'].pop(next(iter(p.CASE_REPLACEMENTS)))):
   changed=copy.deepcopy(value);mutate(changed)
   with self.assertRaises(ValueError):p.variant(changed)
  with self.assertRaises(ValueError):p.add(value)
 def test_stale_unmapped_missing_duplicate_or_reordered_case_rejected(self):
  value=p.add(self.base)
  for mutate in (lambda names:names.pop(),lambda names:names.append(names[0]),lambda names:names.reverse(),lambda names:names.__setitem__(0,'unreviewed'),lambda names:names.__setitem__(names.index(next(iter(p.CASE_REPLACEMENTS.values()))),next(iter(p.CASE_REPLACEMENTS)))):
   changed=copy.deepcopy(value);mutate(changed['case_names']['setup-localization'])
   with self.assertRaises(ValueError):p.variant(changed)
  missing_tag=copy.deepcopy(value);missing_tag.pop(p.TAG)
  with self.assertRaises(ValueError):p.variant(missing_tag)
 def test_current_routes_and_mandatory_workflow_registration(self):
  here=Path(__file__).resolve().parent
  self.assertIn('import observed_constraints_localization_projection as preservation_localization',(here/'project_candidate_contracts.py').read_text())
  self.assertIn('from observed_constraints_localization_projection import variant as russian_setup_variant',(here/'verify_setup_state_recovery.py').read_text())
  for name in ('refresh-truth-preflight','cold-vpn-preflight','cold-vpn-native'):
   self.assertEqual((here.parents[1]/'.github/workflows'/f'{name}.yml').read_text().count('python3 -B harness/ci/issues-native/test_observed_constraints_localization_projection.py'),1)

if __name__=='__main__':unittest.main()
