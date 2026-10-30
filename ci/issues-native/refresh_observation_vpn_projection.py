"""One exact existing operation source used by the reviewed transport fixture.
The prior Wi-Fi dependency projection and all behavioral cases are unchanged.
"""
import copy
from local_wifi_fixture_projection import inputs as prior_inputs
PATH='SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift'
SHA='f15da0a64815bd181fa80b1f1c2988cc6e4623c1a649f1aa0c107697ec0d89fc'
KEY='reviewed_refresh_observation_vpn_dependency'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:return False
 require(value=={'path':PATH,'sha256':SHA},'Unknown refresh observation VPN dependency')
 require(contract['input_sha256'].get(PATH)==SHA,'Unbound refresh observation VPN dependency')
 return True
def inputs(contract,original):
 result=prior_inputs(contract,original)
 if binding(contract):
  require(PATH not in result['transport_fixture'],'Duplicate refresh observation VPN dependency')
  result['transport_fixture'].append(PATH)
 return result
def add(contract):
 result=copy.deepcopy(contract)
 require(KEY not in result,'Refresh observation VPN dependency already declared')
 result[KEY]={'path':PATH,'sha256':SHA};binding(result)
 # The earlier projection has already added its own Wi-Fi dependency. Do not
 # invoke it twice or alter its metadata/case expectations.
 fixture=result['additive_suite_inputs']['vpn-refresh-recovery']['transport_fixture']
 require(PATH not in fixture,'Duplicate refresh observation VPN dependency')
 fixture.append(PATH)
 return result
