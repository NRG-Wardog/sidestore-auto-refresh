"""Immutable source policy and a new, explicitly populated green native input manifest."""
import hashlib,json,re
from pathlib import Path
PIN='df1517dd8518113a245e4d203a04b8a0e02ab493'
TREE='b635c1f017ccce001399fcc3ff9c687cbf00f5b2'
SOURCE_SHA='ae5c3afb20156a52f2086277fd102a08d902c36d84db7eff74cc7e5e19b020c6'
REPO='NRG-Wardog/sidestore-auto-refresh'
WORKFLOW='.github/workflows/cold-vpn-native.yml'
IPA='LiveContainer-SideStore-issues-experimental.ipa'
BUDGET=1500
ITERATIONS=(5,10)
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
