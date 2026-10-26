"""Exact additive cache preservation proof for the reviewed cold VPN delta."""
import copy
PATH='SideStore/tests/cache_compatibility/reviewed-cold-vpn-host-delta.json'
SHA='7c10bae55feec2ebaf8f70eddc1c0a470804a5e5d0bb6cf3442c91ec890ff7e9'
KEY='reviewed_cold_vpn_cache_host_change'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:return None
 require(value=={'delta_path':PATH,'delta_sha256':SHA},'Unknown cold VPN cache preservation delta')
 require(contract['input_sha256'].get(PATH)==SHA,'Unbound cold VPN cache preservation delta')
 return value
def inputs(contract,original):
 result=copy.deepcopy(original)
 if binding(contract):
  require(PATH not in result['fixture'],'Duplicate cold VPN cache fixture')
  result['fixture'].append(PATH)
 return result
def add(contract):
 result=copy.deepcopy(contract);require(KEY not in result,'Cold VPN cache proof already declared')
 result[KEY]={'delta_path':PATH,'delta_sha256':SHA}
 binding(result)
 result['additive_suite_inputs']['cache-compatibility']=inputs(result,result['additive_suite_inputs']['cache-compatibility'])
 return result

def verify_proof(value,contract):
 cold=binding(contract)
 if cold:
  require(value.get('host_pipeline_byte_identical') is False and
          value.get('host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical') is True and
          value.get('reviewed_cold_vpn_delta_sha256')==cold['delta_sha256'] and
          value.get('host_pipeline_except_reviewed_reuse_main_byte_identical') is not True,
          'Cache cold VPN preservation proof differs')
 return cold
