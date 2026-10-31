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


# One measured closest-result acceptance, not a raised general size ceiling.
# Exact native acquisition and exact recompressed bytes are both mandatory.
CLOSEST_NATIVE_INPUT={
    "schema": "cold-vpn-green-native-input-v1",
    "run_id": 41160811783,
    "run_attempt": 1,
    "commit": "fea02c69117bdfbe87fcda3c013852571b2fb067",
    "artifact_id": 13028560658,
    "artifact_bytes": 26594574,
    "artifact_sha256": "0794412fed32b26e894809662ad9c19481081dbe4726e9937c94d4e4511adf41",
    "ipa_bytes": 24907828,
    "ipa_sha256": "660e2b8eb17cbf615cd068b66d4ca554f5c8bf98d885b976439247f31e0db236",
    "entries": 274
}
CLOSEST_OUTPUT_BYTES=24230651
CLOSEST_OUTPUT_SHA256='ca000b547a4ced3c3737cedd6abf77e1c8c68c19cddfbf6deeb88a114c075f86'
def verify_size_acceptance(input_manifest,final_bytes,final_sha256,target_met):
    require(type(final_bytes) is int and final_bytes>0 and final_bytes<=input_manifest['ipa_bytes'],'invalid or growing final size')
    require(type(target_met) is bool and target_met==(final_bytes<=TARGET_BYTES),'size target claim differs')
    if target_met:
        return {'policy':'standard_24mb_target','target_met':True,'target_bytes':TARGET_BYTES,'excess_bytes':0}
    require(input_manifest==CLOSEST_NATIVE_INPUT and all(type(input_manifest[key]) is type(value) for key,value in CLOSEST_NATIVE_INPUT.items()),'closest-result native input differs')
    require(final_bytes==CLOSEST_OUTPUT_BYTES and final_sha256==CLOSEST_OUTPUT_SHA256,'closest-result output differs')
    return {'policy':'exact_reviewed_closest_candidate_only','target_met':False,'target_bytes':TARGET_BYTES,
            'excess_bytes':final_bytes-TARGET_BYTES,'accepted_output_sha256':CLOSEST_OUTPUT_SHA256,
            'native_run_id':CLOSEST_NATIVE_INPUT['run_id'],'native_commit':CLOSEST_NATIVE_INPUT['commit']}
