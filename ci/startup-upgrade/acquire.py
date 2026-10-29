#!/usr/bin/env python3
"""Acquire and seal exactly the LC and SideStore owners needed by focused tests."""
import argparse, hashlib, json, os, re, stat, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'issues-native'))
from verify_candidate_graph import load_candidate_graph, unique_pairs
OWNERS={'LiveContainer':'NRG-Wardog/LiveContainer','SideStore':'NRG-Wardog/SideStore'}
GRAPH=ROOT.parent/'issues-native/candidate-graph.json'
def require(ok,message):
    if not ok:raise ValueError(message)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def identity():
    result={k:os.environ.get(v,'') for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
    require(re.fullmatch('[0-9a-f]{40}',result['workflow_commit']) is not None,'invalid workflow commit')
    require(all(re.fullmatch('[1-9][0-9]*',result[k]) for k in ('run_id','run_attempt')),'invalid run identity')
    return result
def git(root,*args,timeout=180):
    command=['git','--no-replace-objects','--literal-pathspecs','-C',str(root),'-c','core.hooksPath=/dev/null','-c','core.autocrlf=false','-c','core.quotePath=false',*map(str,args)]
    return subprocess.check_output(command,stderr=subprocess.STDOUT,timeout=timeout)
def selected_graph():
    graph=load_candidate_graph(GRAPH)
    result={}
    for owner,repository in OWNERS.items():
        repo,commit=graph['owners'][owner]
        require(repo==repository,'owner repository differs')
        delta=graph['reviewed_deltas'].get(owner)
        require(delta is not None,'focused owner lacks reviewed tree')
        result[owner]={'repository':repo,'commit':commit,'tree':delta['tree']}
    return result
def fresh(path):
    require(not path.exists() and not path.is_symlink(),'fresh path required: '+path.name)
    require(path.parent.is_dir() and not path.parent.is_symlink(),'owned parent required')
def inventory(root,expected):
    require(root.is_dir() and not root.is_symlink() and (root/'.git').is_dir() and not (root/'.git').is_symlink(),'real source checkout required')
    require(git(root,'rev-parse','HEAD').decode().strip()==expected['commit'],'source HEAD differs')
    require(git(root,'rev-parse','HEAD^{tree}').decode().strip()==expected['tree'],'source tree differs')
    require(not git(root,'status','--porcelain=v1','--untracked-files=all').strip(),'source worktree not pristine')
    require(not git(root,'diff','--cached','--name-only','HEAD').strip(),'source index differs')
    require(not git(root,'ls-files','--others').strip(),'unexpected ignored or untracked source output')
    rows={}
    for row in git(root,'ls-tree','-rz','HEAD').split(b'\0'):
        if not row:continue
        metadata,name=row.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=name.decode()
        path=root/name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts,'unsafe tracked path')
        if kind=='commit':
            require(mode=='160000','invalid gitlink mode')
            require(not path.is_symlink() and (not path.exists() or (path.is_dir() and not any(path.iterdir()))),'submodule unexpectedly populated')
            # A gitlink is identity metadata only. No submodule source is acquired.
            rows[name]={'mode':mode,'gitlink':oid};continue
        require(kind=='blob' and mode in ('100644','100755','120000'),'unexpected tree entry')
        require(not any((root/ancestor).is_symlink() for ancestor in Path(name).parents),'linked tracked ancestor')
        if mode=='120000':
            require(path.is_symlink(),'tracked symlink differs');data=os.fsencode(os.readlink(path))
        else:
            require(path.is_file() and not path.is_symlink(),'tracked file missing/linked')
            require(bool(path.stat().st_mode&0o111)==(mode=='100755'),'tracked executable mode differs')
            data=path.read_bytes()
        require(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==oid,'tracked bytes differ: '+name)
        rows[name]={'mode':mode,'blob':oid,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    return rows
def write_json(path,value):
    fresh(path);path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
def harness_hashes():
    paths=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py','.swift','.md')]
    paths += [GRAPH,ROOT.parent/'issues-native/verify_candidate_graph.py',
              ROOT.parent/'issue56-startup/run_startup_tests.py',
              ROOT.parents[1]/'.github/workflows/startup-upgrade-preflight.yml',
              ROOT.parents[1]/'.github/workflows/cold-vpn-native.yml']
    return {str(p.relative_to(ROOT.parents[1])):sha(p) for p in sorted(paths)}
def acquire(sources,evidence):
    require(sources.is_dir() and not sources.is_symlink(),'source parent required')
    expected=selected_graph();rows={}
    require(os.environ.get('GIT_ALLOW_PROTOCOL')=='https','HTTPS-only acquisition required')
    for owner,value in expected.items():
        root=sources/owner;fresh(root);root.mkdir()
        git(root,'init')
        git(root,'fetch','--depth=1','--no-tags','https://github.com/'+value['repository']+'.git',value['commit'])
        git(root,'checkout','--detach','FETCH_HEAD')
        rows[owner]=inventory(root,value)
    write_json(evidence,{'schema':'startup-upgrade-acquisition-v1','identity':identity(),'acquired_owners':expected,
        'candidate_graph_sha256':sha(GRAPH),'harness_sha256':harness_hashes(),'source_inventory':rows,
        'submodules_acquired':False,'other_graph_owners_acquired':False})
def verify(sources,evidence,output):
    report=json.loads(evidence.read_text(),object_pairs_hook=unique_pairs)
    require(set(report)=={'schema','identity','acquired_owners','candidate_graph_sha256','harness_sha256','source_inventory','submodules_acquired','other_graph_owners_acquired'},'acquisition closure differs')
    require(report['schema']=='startup-upgrade-acquisition-v1' and report['identity']==identity(),'acquisition identity differs')
    require(report['acquired_owners']==selected_graph() and report['candidate_graph_sha256']==sha(GRAPH),'candidate graph differs')
    require(report['harness_sha256']==harness_hashes(),'focused harness changed')
    require(report['submodules_acquired'] is False and report['other_graph_owners_acquired'] is False,'acquisition scope differs')
    after={owner:inventory(sources/owner,value) for owner,value in report['acquired_owners'].items()}
    require(after==report['source_inventory'],'source inventory changed during tests')
    write_json(output,{'schema':'startup-upgrade-source-immutability-v1','status':'PASS_EXACT_ACQUIRED_OWNERS_UNCHANGED',
        'identity':identity(),'acquisition_sha256':sha(evidence),'acquired_owners':report['acquired_owners'],
        'source_inventory_sha256':{owner:hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest() for owner,rows in after.items()},
        'submodules_acquired':False,'other_graph_owners_acquired':False})
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['acquire','verify']);parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.mode=='acquire':acquire(args.sources.resolve(strict=True),args.evidence)
    else:
        require(args.output is not None,'verification output required');verify(args.sources.resolve(strict=True),args.evidence,args.output)
