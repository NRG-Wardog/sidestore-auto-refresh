import copy,json,unittest,hashlib,tempfile
from unittest.mock import patch
import preservation_selection_projection as selection
import upstream_candidate_projection as upstream
from pathlib import Path
import preservation_host_projection as host
import preservation_cache_projection as cache
import legacy_replacement_cache_projection as legacy
import transport_observation_cache_projection as observation
import cold_vpn_cache_projection as cold
from project_candidate_contracts import add_reviewed_host_coverage, HOST_CONTEXT_INPUT, IDENTITY_CANDIDATE_INPUT
from verify_setup_state_recovery import host_bundle_variant
HERE=Path(__file__).resolve().parent
class PreservationSuccessors(unittest.TestCase):
 def setUp(self):
  self.frozen=json.loads((HERE/'setup-state-recovery-contract.json').read_text())
 def host_base(self):
  value=copy.deepcopy(self.frozen)
  value['input_sha256'].update(host.PINS)
  value['input_sha256'].update({HOST_CONTEXT_INPUT:'1'*64,IDENTITY_CANDIDATE_INPUT:'2'*64})
  return add_reviewed_host_coverage(value)
 def cache_base(self):
  value=copy.deepcopy(self.frozen)
  value['input_sha256'].update({cold.PATH:cold.SHA,cold.TRUTH_PATH:cold.TRUTH_SHA,observation.PATH:observation.SHA,legacy.PATH:legacy.SHA,legacy.STAGE:legacy.STAGE_SHA,cache.PATH:cache.SHA,cache.INSTALL:cache.INSTALL_SHA,cache.RUNNER:cache.RUNNER_SHA})
  return value
 def proof(self):
  return {'host_pipeline_byte_identical':False,'host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical':True,'reviewed_refresh_truth_delta_sha256':cold.TRUTH_SHA,'reviewed_cold_vpn_delta_sha256':cold.SHA,'reviewed_transport_observation_delta_sha256':observation.SHA,'reviewed_legacy_replacement_delta_sha256':legacy.SHA,'reviewed_preservation_registry_delta_sha256':cache.SHA}
 def test_host_exact_successor_keeps_original_thirty(self):
  base=self.host_base();before=copy.deepcopy(base);result=host.add(base)
  self.assertEqual(base,before)
  self.assertEqual(result['delivered_baseline'],self.frozen['delivered_baseline'])
  self.assertEqual(host.normalized(result)['case_names'],before['case_names'])
  self.assertEqual(host_bundle_variant(result)[1:],(35,41))
  self.assertEqual(result['case_names']['host-refresh-bundle'][:-6],self.frozen['case_names']['host-refresh-bundle'][:-1])
 def test_host_changed_missing_extra_or_duplicate_fails(self):
  result=host.add(self.host_base())
  for path in host.PINS:
   for missing in [True,False]:
    changed=copy.deepcopy(result)
    if missing:changed['input_sha256'].pop(path)
    else:changed['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):host_bundle_variant(changed)
  for mutate in [lambda c:c[host.KEY].update(extra=True),lambda c:c['case_names']['host-refresh-bundle'].append(next(iter(host.CASE_RENAMES.values()))),lambda c:c.pop(host.legacy.KEY)]:
   changed=copy.deepcopy(result);mutate(changed)
   with self.assertRaises(ValueError):host_bundle_variant(changed)
  with self.assertRaises(ValueError):host.add(result)
 def test_cache_exact_successor_adds_closed_inputs(self):
  base=self.cache_base();before=copy.deepcopy(base);result=cache.add(base)
  self.assertEqual(base,before);self.assertEqual(result['case_names'],before['case_names']);self.assertEqual(result['delivered_baseline'],before['delivered_baseline'])
  self.assertEqual(cache.inputs(result,before['additive_suite_inputs']['cache-compatibility']),result['additive_suite_inputs']['cache-compatibility'])
  cache.verify_proof(self.proof(),result)
 def test_cache_old_variant_unmodified(self):
  base=self.cache_base();base['input_sha256'].pop(cache.PATH);base['input_sha256'].pop(cache.INSTALL)
  result=cache.add(base);self.assertEqual(result,legacy.add(base))
  with self.assertRaises(ValueError):cache.verify_proof(self.proof(),result)
  proof=self.proof();proof.pop('reviewed_preservation_registry_delta_sha256');cache.verify_proof(proof,result)
 def test_cache_changed_missing_or_extra_fails(self):
  result=cache.add(self.cache_base())
  for path in [cache.PATH,cache.INSTALL,cache.RUNNER]:
   for missing in [True,False]:
    changed=copy.deepcopy(result)
    if missing:changed['input_sha256'].pop(path)
    else:changed['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):cache.verify_proof(self.proof(),changed)
  for key in self.proof():
   proof=self.proof();proof.pop(key)
   with self.assertRaises(ValueError):cache.verify_proof(proof,result)
  changed=copy.deepcopy(result);changed[cache.KEY]['extra']=True
  with self.assertRaises(ValueError):cache.preservation_binding(changed)
class PreservationSelectionSuccessor(unittest.TestCase):
 def base(self, current=True):
  return {'input_sha256':{selection.PATH:selection.SHA if current else selection.prior.SHA},
          'additive_suite_inputs':{'vpn-refresh-recovery':{'transport_fixture':['frozen']}},
          'case_names':['unchanged'],'delivered_baseline':{'source':'frozen'}}
 def test_exact_selection_normalizes_through_prior_and_retains_closure(self):
  base=self.base();before=copy.deepcopy(base);result=selection.add(base)
  self.assertEqual(base,before)
  self.assertEqual(result['case_names'],base['case_names']);self.assertEqual(result['delivered_baseline'],base['delivered_baseline'])
  self.assertEqual(selection.normalized(result),selection.prior.add(self.base(False)))
  self.assertEqual(result['additive_suite_inputs']['vpn-refresh-recovery']['transport_fixture'],['frozen',selection.PATH])
  self.assertEqual(selection.add(self.base(False)),selection.prior.add(self.base(False)))
 def test_missing_changed_duplicate_selection_fails(self):
  result=selection.add(self.base())
  for mutate in [lambda c:c[selection.KEY].update(extra=True),lambda c:c['input_sha256'].__setitem__(selection.PATH,'0'*64),lambda c:c['input_sha256'].pop(selection.PATH),lambda c:c.pop(selection.prior.KEY),lambda c:c[selection.prior.KEY].update(sha256='0'*64)]:
   changed=copy.deepcopy(result);mutate(changed)
   with self.assertRaises(ValueError):selection.normalized(changed)
  with self.assertRaises(ValueError):selection.add(result)
  changed=self.base();changed['input_sha256'][selection.PATH]='0'*64
  with self.assertRaises(ValueError):selection.add(changed)
 def test_upstream_exact_selection_and_unchanged_mapper_only(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);selected=root/selection.PATH;mapper=root/upstream.OBSERVATION_MAPPER
   selected.parent.mkdir(parents=True);mapper.parent.mkdir(parents=True)
   selected.write_bytes(b'exact successor');mapper.write_bytes(b'unchanged mapper')
   with patch.object(selection,'SHA',hashlib.sha256(selected.read_bytes()).hexdigest()),patch.object(upstream,'OBSERVATION_MAPPER_SHA',hashlib.sha256(mapper.read_bytes()).hexdigest()):
    self.assertEqual(selection.validate_upstream_dependencies(root),selection.descriptor())
    for path in (selected,mapper):
     original=path.read_bytes();path.write_bytes(original+b' changed')
     with self.assertRaises(ValueError):selection.validate_upstream_dependencies(root)
     path.write_bytes(original)
   with patch.object(selection.prior,'SHA',hashlib.sha256(selected.read_bytes()).hexdigest()),patch.object(upstream,'SELECTION_SHA',hashlib.sha256(selected.read_bytes()).hexdigest()),patch.object(upstream,'OBSERVATION_MAPPER_SHA',hashlib.sha256(mapper.read_bytes()).hexdigest()):
    self.assertIsNone(selection.validate_upstream_dependencies(root))
 def test_all_current_routes_use_successor_without_changing_historical_pin(self):
  self.assertEqual(selection.prior.SHA,upstream.SELECTION_SHA)
  self.assertNotEqual(selection.SHA,upstream.SELECTION_SHA)
  self.assertIn('selection_successor=validate_upstream_dependencies(sources)',Path(upstream.__file__).read_text())
  self.assertIn('from preservation_selection_projection import add as add_refresh_observation_vpn',(HERE/'project_candidate_contracts.py').read_text())
  self.assertIn('from preservation_selection_projection import inputs as local_wifi_fixture_inputs',(HERE/'verify_setup_state_recovery.py').read_text())
  for path in ('record.py','verify_upstream_sync.py'):
   self.assertIn('from upstream_candidate_projection import current_spec',(HERE/path).read_text())

if __name__=='__main__':unittest.main()
