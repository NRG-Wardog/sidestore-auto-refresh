import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy,unittest
from pathlib import Path
from ci.qualification.native import progress_owner_cache_projection as p
from tests.ci.native import test_preservation_successor_projection as previous

class ProgressOwner(unittest.TestCase):
 def setUp(self):
  self.old=previous.PreservationSuccessors();self.old.setUp()
 def base(self):
  value=self.old.cache_base();value['input_sha256'].update({p.PATH:p.SHA,p.RUNNER:p.RUNNER_SHA,p.prior.PATH:p.prior.SHA});return value
 def test_exact_successor_normalizes_to_prior_source_and_proofs(self):
  base=self.base();before=copy.deepcopy(base);value=p.add(base)
  self.assertEqual(base,before);self.assertEqual(value['case_names'],base['case_names']);self.assertEqual(value['delivered_baseline'],base['delivered_baseline'])
  old=copy.deepcopy(base);old['input_sha256'][p.RUNNER]=p.prior.RUNNER_SHA
  self.assertEqual(p.normalized(value),p.prior.add(old))
  self.assertEqual(p.inputs(value,before['additive_suite_inputs']['cache-compatibility']),value['additive_suite_inputs']['cache-compatibility'])
  proof=self.old.proof();proof[p.prior.PROOF]=p.prior.SHA;proof[p.PROOF]=p.SHA;p.verify_proof(proof,value)
 def test_missing_changed_duplicate_extra_and_unbound_successor_rejected(self):
  value=p.add(self.base())
  for path in (p.PATH,p.RUNNER):
   for missing in (False,True):
    changed=copy.deepcopy(value)
    if missing:changed['input_sha256'].pop(path)
    else:changed['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):p.normalized(changed)
  for mutate in (lambda c:c[p.KEY].update(extra=True),lambda c:c.pop(p.prior.KEY),lambda c:c['additive_suite_inputs']['cache-compatibility']['fixture'].append(p.PATH),lambda c:c['additive_suite_inputs']['cache-compatibility']['fixture'].remove(p.PATH)):
   changed=copy.deepcopy(value);mutate(changed)
   with self.assertRaises(ValueError):p.normalized(changed)
  with self.assertRaises(ValueError):p.add(value)
 def test_proof_required_and_historical_variant_unmodified(self):
  value=p.add(self.base());proof=self.old.proof();proof[p.prior.PROOF]=p.prior.SHA
  with self.assertRaises(ValueError):p.verify_proof(proof,value)
  proof[p.PROOF]='0'*64
  with self.assertRaises(ValueError):p.verify_proof(proof,value)
  old=self.old.cache_base();old['input_sha256'].update({p.prior.PATH:p.prior.SHA,p.RUNNER:p.prior.RUNNER_SHA});self.assertEqual(p.add(old),p.prior.add(old))
  with self.assertRaises(ValueError):p.verify_proof(proof,p.add(old))
  oldproof=self.old.proof();oldproof[p.prior.PROOF]=p.prior.SHA;p.verify_proof(oldproof,p.add(old))
 def test_registered_current_routes_and_controls(self):
  here=(_CIPath(__file__).resolve().parents[3]/'ci/qualification/native')
  self.assertIn('from ci.qualification.native.progress_owner_cache_projection import PATH as PROGRESS_CACHE_INPUT, add as add_cold_vpn_cache',((here.parents[1]/'build/project_candidate_contracts.py')).read_text())
  self.assertIn('from ci.qualification.native.progress_owner_cache_projection import inputs as cold_cache_inputs',((here.parents[1]/'diagnostics/verify_setup_state_recovery.py')).read_text())
  self.assertEqual(p.prior.RUNNER_SHA,'c9a92748bad7f96eeb49607a72b32dd1d199b247ec6e58f4631c135523dd6ada')
  for name, job in (('focused-diagnostics.yml', 'refresh_truth'), ('focused-diagnostics.yml', 'cold_vpn'), ('native-build.yml', None)):
   self.assertEqual(workflow_job((here.parents[2]/'.github/workflows')/name, job).count('python3 -B harness/tests/ci/native/test_progress_owner_cache_projection.py'),1)

if __name__=='__main__':unittest.main()
