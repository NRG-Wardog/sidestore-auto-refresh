"""Exact preservation successor; normalize only reviewed inputs through frozen host validators."""
import copy
import legacy_replacement_host_projection as legacy
import refresh_truth_host_bundle_projection as truth
KEY='reviewed_preservation_host_fixture'
PINS={'SideStore/tests/host_refresh_bundle/generate_fixture.py': 'a602aeb1b06b0497bdf73c01771c28e959705e302d697b0aa289dbfeef254a9f', 'SideStore/AltStore/AppDelegate.swift': '97f897d7c9709ef86d2920fbb9b06d7652b7357ab99441962f3da392b4085d67', 'SideStore/tests/host_refresh_bundle/test_contract.py': '2fc0ce8407d08cbd3d93f9c2b19b1394cfd7f4a7f4d22f57ec1b0a4b34e74c8e', 'SideStore/tests/host_refresh_bundle/Cases.swift': '636c75337496b0dafbb0ae2a4c1432f819d65687607377ddae6526b80ebdba5b', 'SideStore/tests/host_refresh_bundle/BoundaryDoubles.swift': 'fa3bafd28803a3686e80ed5cc515aaf81f8fd6ce5e93298ab59a6b7fb19ce867'}
CASE_RENAMES={
 'integration_unregistered_payload_nonce_does_not_opt_in':'integration_old_registry_does_not_migrate_implicitly',
 'integration_registered_payload_restores_validated_strategy':'integration_ack_history_recovered_from_nonce_free_source',
 'integration_opt_out_bool_overrides_matching_registry':'integration_opt_out_bool_excludes_ack_history',
 'integration_known_association_nonce_mismatch_fails_before_pipeline':'integration_changed_source_is_unknown_not_replacement',
}
def require(ok,why):
 if not ok:raise ValueError(why)
def matches(contract):
 return contract['input_sha256'].get(legacy.PATH)==PINS[legacy.PATH]
def binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]=={'inputs':PINS,'case_renames':CASE_RENAMES},'Unknown preservation host successor')
 require(all(contract['input_sha256'].get(p)==h for p,h in PINS.items()),'Unbound preservation host input')
 require(legacy.KEY in contract and truth.KEY in contract,'Missing historical host proof layers')
 return True
def normalized(contract):
 result=copy.deepcopy(contract)
 if binding(result):
  result.pop(KEY)
  result['input_sha256'].update({legacy.PATH:legacy.SHA,legacy.SERVICE:legacy.SERVICE_SHA,truth.PATH:truth.SHA})
  names=result['case_names']['host-refresh-bundle']
  reverse={v:k for k,v in CASE_RENAMES.items()}
  require(all(names.count(v)==1 for v in reverse),'Missing/duplicate preservation host cases')
  result['case_names']['host-refresh-bundle']=[reverse.get(n,n) for n in names]
 return result
def add(contract):
 require(KEY not in contract,'Preservation host already projected')
 require(all(contract['input_sha256'].get(p)==h for p,h in PINS.items()),'Unreviewed preservation host source')
 before=copy.deepcopy(contract)
 before['input_sha256'].update({legacy.PATH:legacy.SHA,legacy.SERVICE:legacy.SERVICE_SHA,truth.PATH:truth.SHA})
 result=legacy.add(truth.add(before))
 result['input_sha256']=copy.deepcopy(contract['input_sha256'])
 names=result['case_names']['host-refresh-bundle']
 require(all(names.count(n)==1 for n in CASE_RENAMES),'Missing/duplicate historical registry cases')
 result['case_names']['host-refresh-bundle']=[CASE_RENAMES.get(n,n) for n in names]
 result[KEY]={'inputs':dict(PINS),'case_renames':dict(CASE_RENAMES)}
 normalized(result)
 return result
def inputs(contract,previous):
 return legacy.inputs(normalized(contract),previous)
def assertions(contract,previous):
 return truth.assertions(normalized(contract),previous)
def expected_cases(contract,old_cases):
 if binding(contract):return [CASE_RENAMES.get(n,n) for n in old_cases]
 return old_cases
