"""Two exact negative renewal-evidence cases; all prior flow cases stay ordered."""
import copy,hashlib,json
from pathlib import Path
import grace_recovery_projection as grace
HERE=Path(__file__).resolve().parent
KEY='reviewed_refresh_truth_flow_cases'
ADDED={'setup-connection-evidence':'legacy_success_without_renewal_proof_not_green','vpn-refresh-recovery':'network_ready_without_renewal_proof_rejected'}
PATHS=tuple('LiveContainer/.ci/'+folder+'/'+name for folder in ('setup-connection-evidence','vpn-refresh-recovery') for name in ('generate_fixture.py','cases.swift'))
def require(ok,why):
 if not ok:raise ValueError(why)
def extension():
 raw=(HERE/'refresh-truth-flow-extension.json').read_bytes();v=json.loads(raw)
 require(set(v)=={'schema','additional_cases','candidate_inputs'} and v['schema']=='reviewed-refresh-truth-flow-extension-v1','Refresh flow extension closure differs')
 require(v['additional_cases']==ADDED and set(v['candidate_inputs'])==set(PATHS),'Refresh flow exact additions/inputs differ')
 return v,hashlib.sha256(raw).hexdigest()
def names():
 frozen=json.loads((HERE/'setup-state-recovery-contract.json').read_text())['case_names']['setup-connection-evidence']
 require(len(frozen)==25,'Frozen connection case count differs')
 return {'setup-connection-evidence':frozen,'vpn-refresh-recovery':grace.expected_names()}
def normalize(contract):
 if KEY not in contract:return contract
 v,digest=extension();require(contract[KEY]=={'manifest_sha256':digest},'Refresh flow extension identity differs')
 require(all(contract['input_sha256'].get(p)==h for p,h in v['candidate_inputs'].items()),'Refresh flow source hashes differ')
 result=copy.deepcopy(contract);result.pop(KEY)
 for suite,prior in names().items():
  require(contract['case_names'][suite]==prior+[ADDED[suite]],'Refresh flow old order or exact additional case differs')
  result['case_names'][suite]=list(prior)
 require(grace.count(result)==105,'Refresh flow requires exact grace predecessor')
 return result
def add(contract):
 require(KEY not in contract and grace.count(contract)==105,'Refresh flow cannot replace prior grace cases')
 for suite,prior in names().items():require(contract['case_names'][suite]==prior,'Refresh flow prior case closure differs')
 v,digest=extension();result=copy.deepcopy(contract);result[KEY]={'manifest_sha256':digest}
 for suite in ADDED:result['case_names'][suite].append(ADDED[suite])
 normalize(result);return result
def vpn_count(contract):
 prior=normalize(contract);return grace.count(prior)+(1 if KEY in contract else 0)
def connection_count(contract):
 normalize(contract);return 26 if KEY in contract else 25
