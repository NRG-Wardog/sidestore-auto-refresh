"""Exact additional binding dependency for the unchanged 35 host fixture cases."""
import copy
PATH='SideStore/tests/host_refresh_bundle/generate_fixture.py'
SHA='a63a1801bd5b7f74ba2959601630e305ff2c8e9f52f139afb7c41a801a15201c'
SERVICE='SideStore/AltStore/AppDelegate.swift'
SERVICE_SHA='9af632417ccecfc885e29a01a40b519bf9706a5cc8b9a32bc631dc031fbc684b'
KEY='reviewed_legacy_replacement_host_fixture'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]=={'generator_sha256':SHA,'service_sha256':SERVICE_SHA},'Legacy host dependency identity differs')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(SERVICE)==SERVICE_SHA,'Legacy host fixture dependency unbound')
 return True
def inputs(contract,previous):
 result=copy.deepcopy(previous)
 if binding(contract):
  require(SERVICE not in result['production'],'Duplicate legacy host fixture dependency')
  result['production'].append(SERVICE)
 return result
def add(contract):
 result=copy.deepcopy(contract)
 require(KEY not in result,'Legacy host fixture already projected')
 result[KEY]={'generator_sha256':SHA,'service_sha256':SERVICE_SHA}
 result['additive_suite_inputs']['host-refresh-bundle']=inputs(result,result['additive_suite_inputs']['host-refresh-bundle'])
 return result
