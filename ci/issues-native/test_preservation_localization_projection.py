import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import preservation_localization_projection as p
import russian_setup_projection as russian
import refresh_truth_setup_projection as truth
import refresh_observation_setup_projection as observation
HERE=Path(__file__).resolve().parent
class LocalizationSuccessor(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  names=['setup-state-recovery-contract.json','russian-setup-extension.json','refresh-truth-setup-extension.json','refresh-observation-setup-extension.json']
  for name in names:
   data=(HERE/name).read_bytes()
   if name!='setup-state-recovery-contract.json':
    obj=json.loads(data)
    # Model the existing CURRENT candidate-input rebinding, never modify any
    # frozen case, count, key, historical contract byte or prior validator.
    obj['candidate_inputs']={k:p.PINS.get(k,v) for k,v in obj['candidate_inputs'].items()}
    data=(json.dumps(obj,indent=2)+'\n').encode()
   (root/name).write_bytes(data)
  for module in (russian,truth,observation):
   patcher=patch.object(module,'HERE',root);patcher.start();self.addCleanup(patcher.stop)
  value=json.loads((root/'setup-state-recovery-contract.json').read_text())
  for name in names[1:]:value['input_sha256'].update(json.loads((root/name).read_text())['candidate_inputs'])
  value['input_sha256'].update(p.PINS)
  self.base=observation.add(truth.add(russian.add(value)))
 def test_exact_successor_retains_all_unreviewed_prior_cases(self):
  old=copy.deepcopy(self.base);result=p.add(self.base)
  self.assertEqual(old,self.base);self.assertEqual(result['delivered_baseline'],old['delivered_baseline'])
  self.assertEqual(p.variant(result),p.CURRENT);self.assertEqual(p.variant(old),p.PRIOR)
  expected=sorted([p.REPLACEMENTS.get(c,c) for c in old['case_names']['setup-localization']]+p.EXTRA)
  self.assertEqual(result['case_names']['setup-localization'],expected)
  self.assertEqual(len(p.REPLACEMENTS),10);self.assertEqual(len(p.EXTRA),100)
 def test_missing_modified_duplicate_cases_and_sources_rejected(self):
  result=p.add(self.base)
  for path in p.PINS:
   for missing in (True,False):
    changed=copy.deepcopy(result)
    if missing:changed['input_sha256'].pop(path)
    else:changed['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):p.variant(changed)
  for change in [lambda c:c[p.TAG].update(extra=True),lambda c:c['case_names']['setup-localization'].pop(),lambda c:c['case_names']['setup-localization'].append(p.EXTRA[0]),lambda c:c['case_names']['setup-localization'].__setitem__(0,'unknown'),lambda c:c.pop(observation.TAG)]:
   bad=copy.deepcopy(result);change(bad)
   with self.assertRaises(ValueError):p.variant(bad)
  with self.assertRaises(ValueError):p.add(result)
 def test_harness_correction_is_exact_and_does_not_change_case_or_baseline_counts(self):
  self.assertEqual(p.HARNESS,'LiveContainer/.ci/comprehensive-diagnostics/setup_localization_harness.swift')
  self.assertEqual(p.PINS[p.HARNESS],'809ea23d9e95020ebf2b903cc228a7455ccacca17aad80247bb924d08edd7879')
  self.assertEqual(p.PINS[p.INVENTORY_TEST],'9bd7a25653bf5964e5aabb657a2129c22aca033be70a3ba3044ab4dacc0a6ba3')
  self.assertEqual(p.CURRENT,{'cases':2079,'values':1080,'baseline_missing':504,'keys':216})
  self.assertEqual(p.PRIOR,{'cases':1979,'values':1030,'baseline_missing':454,'keys':206})
  result=p.add(self.base)
  for value in (None,'0'*64):
   changed=copy.deepcopy(result)
   if value is None:changed['input_sha256'].pop(p.HARNESS)
   else:changed['input_sha256'][p.HARNESS]=value
   with self.assertRaisesRegex(ValueError,'Unbound preservation localization source'):p.variant(changed)

 def test_source_gate_requires_inventory_test_and_current_exact_helpers(self):
  for workflow in ('refresh-truth-preflight.yml','cold-vpn-native.yml','cold-vpn-preflight.yml'):
   source=(HERE.parents[1]/'.github/workflows'/workflow).read_text()
   self.assertEqual(source.count('python3 -B sources/'+p.INVENTORY_TEST),1)
   self.assertEqual(source.count('python3 -B harness/ci/issues-native/test_preservation_localization_projection.py'),1)
  source=(HERE/'project_candidate_contracts.py').read_text()
  self.assertIn('preservation_localization.INVENTORY_TEST',source)
  self.assertIn('projected = preservation_localization.add(projected)',source)
  self.assertIn('from preservation_localization_projection import variant as russian_setup_variant',(HERE/'verify_setup_state_recovery.py').read_text())
if __name__=='__main__':unittest.main()
