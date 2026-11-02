"""Immutable source policy and a new, explicitly populated green native input manifest."""
import hashlib,json,re
from pathlib import Path
PIN='df1517dd8518113a245e4d203a04b8a0e02ab493'
TREE='b635c1f017ccce001399fcc3ff9c687cbf00f5b2'
SOURCE_SHA='ae5c3afb20156a52f2086277fd102a08d902c36d84db7eff74cc7e5e19b020c6'
REPO='NRG-Wardog/sidestore-auto-refresh'
WORKFLOW='.github/workflows/cold-vpn-native.yml'
IPA='LiveContainer-SideStore-issues-experimental.ipa'
BUDGET=900
ITERATIONS=(5,10,20,30)
TARGET_BYTES=24000000
SHA=lambda value:hashlib.sha256(value).hexdigest()
def require(value,message):
    if not value: raise ValueError(message)
def strict_pairs(pairs):
    result={}
    for key,value in pairs:
        require(key not in result,'duplicate JSON key')
        result[key]=value
    return result
def read_json(path): return json.loads(Path(path).read_text(),object_pairs_hook=strict_pairs)
def manifest(path):
    value=read_json(path)
    require(set(value)=={'schema','run_id','run_attempt','commit','artifact_id','artifact_bytes','artifact_sha256','ipa_bytes','ipa_sha256','entries'},'input manifest fields')
    require(value['schema']=='cold-vpn-green-native-input-v1','input manifest schema')
    for key in ('run_id','run_attempt','artifact_id','artifact_bytes','ipa_bytes','entries'):
        require(type(value[key]) is int and value[key]>0,'input manifest integer: '+key)
    require(value['artifact_bytes']<=128*1024**2 and value['ipa_bytes']<=64*1024**2 and value['entries']<=512,'input manifest size bounds')
    for key,length in [('commit',40),('artifact_sha256',64),('ipa_sha256',64)]:
        require(isinstance(value[key],str) and re.fullmatch('[0-9a-f]{'+str(length)+'}',value[key]) is not None,'input manifest digest: '+key)
    return value


def verify_round_schedule(result,original_bytes,final_bytes):
    """Audit complete-round stopping independently from compressor execution."""
    rounds=result['rounds'];attempts=result['results'];stop=result['stop_reason']
    require(type(rounds) is list and 1<=len(rounds)<=len(ITERATIONS),'round count')
    require([r['iterations'] for r in rounds]==list(ITERATIONS)[:len(rounds)],'round schedule prefix')
    size=original_bytes
    for index,row in enumerate(rounds):
        iteration=row['iterations']
        require(type(iteration) is int and type(row['attempts']) is int and row['attempts']>=0,'round types')
        selected=[a for a in attempts if a['iterations']==iteration]
        require(row['attempts']==len(selected) and type(row['elapsed_seconds']) in (int,float) and 0<=row['elapsed_seconds']<=BUDGET+5,'round accounting')
        size-=sum(a['saved_vs_previous'] for a in selected)
        if size<=TARGET_BYTES:
            require(index==len(rounds)-1 and stop=='size_target_met','must stop after first target-reaching round')
    require(size==final_bytes,'round savings differ from final archive')
    require(stop in ('size_target_met','iteration_schedule_exhausted','aggregate_budget_reached','remaining_candidates_plateau_or_estimated_cost_exceeds_bounds'),'round stop reason')
    if stop=='size_target_met':require(final_bytes<=TARGET_BYTES,'target stop without target')
    if stop=='aggregate_budget_reached':require(result['elapsed_seconds']>=BUDGET-3,'early budget stop')
    if stop=='iteration_schedule_exhausted':require(len(rounds)==len(ITERATIONS),'incomplete exhausted schedule')
    if stop=='remaining_candidates_plateau_or_estimated_cost_exceeds_bounds':require(rounds[-1]['attempts']==0,'plateau has attempts')


# Candidate-specific measured upper bound; never a general ceiling increase.
# This SIZE check does not qualify bytes: independent_verify additionally requires
# complete payload/signature/raw ZIP metadata equality and source/evidence binding.
# Delivery must pin the independently audited actual CI output SHA256.
CLOSEST_NATIVE_INPUT={
    "schema": "cold-vpn-green-native-input-v1",
    "run_id": 41521418679,
    "run_attempt": 2,
    "commit": "ea7d7fed1a5a5751dbaa71a919e688a322d694c9",
    "artifact_id": 13183589571,
    "artifact_bytes": 26771780,
    "artifact_sha256": "5f3d606175328a0f063475074a8e85d03f3bc28b85ce0273de1a625ed20d4185",
    "ipa_bytes": 25028830,
    "ipa_sha256": "65658db28919966a98566dca26bc5063d7944db83670897fdd6251eaaf39c44b",
    "entries": 274
}
CLOSEST_OUTPUT_BYTES=24348757
# Reference measurement, not an equality requirement for the same bounded CI schedule.
CLOSEST_OUTPUT_SHA256='76e92bd9a5d7edd39d3fa6c81e9489d39f38d7d08311f9a16c04f4624aaab225'
def verify_size_acceptance(input_manifest,final_bytes,final_sha256,target_met):
    require(type(final_bytes) is int and final_bytes>0 and final_bytes<=input_manifest['ipa_bytes'],'invalid or growing final size')
    require(isinstance(final_sha256,str) and re.fullmatch('[0-9a-f]{64}',final_sha256) is not None,'invalid actual output digest')
    require(type(target_met) is bool and target_met==(final_bytes<=TARGET_BYTES),'size target claim differs')
    if target_met:
        return {'policy':'standard_24mb_target','target_met':True,'target_bytes':TARGET_BYTES,'excess_bytes':0}
    require(input_manifest==CLOSEST_NATIVE_INPUT and all(type(input_manifest[key]) is type(value) for key,value in CLOSEST_NATIVE_INPUT.items()),'closest-result native input differs')
    require(final_bytes<=CLOSEST_OUTPUT_BYTES,'closest-result measured upper bound exceeded')
    return {'policy':'exact_reviewed_candidate_measured_upper_bound','target_met':False,'target_bytes':TARGET_BYTES,
            'excess_bytes':final_bytes-TARGET_BYTES,'accepted_output_sha256':final_sha256,
            'maximum_output_bytes':CLOSEST_OUTPUT_BYTES,'reference_measurement_sha256':CLOSEST_OUTPUT_SHA256,
            'native_run_id':CLOSEST_NATIVE_INPUT['run_id'],'native_commit':CLOSEST_NATIVE_INPUT['commit']}
