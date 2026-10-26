"""Reviewed current guard/ordinary-forwarding proof, without changing baseline cases."""
import copy
PATH='LiveContainer/.ci/vpn-refresh-recovery/cold-peer-source.json'
GENERATOR='LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py'
KEY='reviewed_current_vpn_guard'
EXPECTED={
 'source_receipt_sha256':'eb5a2849d2d0d131e72b75d0395595d7c545f2dff2294da858ab8433a61f420e',
 'generator_sha256':'437ab2050bc61571bcfbfddb38e5f5248c8c72775098c8b8e8170f29015d2fda',
 'guard_sha256':'5760310dd524f8109519f692dae0dae8b0b1a68b5f13bc44c6b6ef052d2018f9',
 'forwarding_sha256':'295ff7a4c7651ca457fc033f2d8cd87c595b35c31c1030ef8e98c7809afd35b8',
 'baseline_classifier_sha256':'11317ac8513b261a85ab7b61f38d543f87aa2e4b9fab014e163e0e0303bc5f81'}
BASELINE= 'LiveContainer/.ci/vpn-refresh-recovery/OriginalRefreshReadinessFacts.swift'
LEGACY='actual_unchanged_guard_blocks_and_historical_baseline'
CURRENT='actual_current_guard_and_forwarding_with_historical_baseline'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:
  require(contract['input_sha256'].get(PATH)!=EXPECTED['source_receipt_sha256'],'Current VPN proof needs explicit reviewed binding')
  return None
 require(value==EXPECTED,'Unknown current VPN guard proof')
 for path,key in [(PATH,'source_receipt_sha256'),(GENERATOR,'generator_sha256'),(BASELINE,'baseline_classifier_sha256')]:
  require(contract['input_sha256'].get(path)==EXPECTED[key],'Unbound current VPN proof input')
 return value
def add(contract):
 result=copy.deepcopy(contract);require(KEY not in result,'Current VPN proof already declared');result[KEY]=dict(EXPECTED);binding(result);return result
def verify(value,contract):
 current=binding(contract)
 require(value.get('cold_guard_provenance')==(CURRENT if current else LEGACY),'VPN actual dependency or original classifier proof differs')
 require(value.get('baseline_classifier_sha256')==contract['input_sha256'][BASELINE],'VPN actual dependency or original classifier proof differs')
