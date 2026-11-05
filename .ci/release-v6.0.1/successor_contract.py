"""One reviewed CI-only main successor; no generic source-equivalence exception."""
import hashlib,json,re
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parent
BASE_COMMIT='fefb69346a4d3bb79ebbb4294b3b99bf81fcd045'
BASE_TREE='2883e33a3d0328b905c90f543df08a0ac3d024cb'
REPLACES_TARGET='bc06ca11d2ee7f881ed7f277073c603f43f76ee1'
POLICY_SHA256='942cf4d7efe7242808eb8ac23f58d62d0ab632eb7f2b11ce0ffdfc3338132689'
PATHS={'.ci/validation-history/reviewed-successor.json','.ci/validation-history/source_contract.py','.ci/validation-history/test_source_contract.py','.ci/validation-history/verify.py','.github/workflows/maintained-packaging-contract.yml','.github/workflows/validation-history-contract.yml','tests/test_repository.py'}
def require(ok,message):
    if not ok:raise ValueError(message)
def strict_pairs(pairs):
    result={}
    for k,v in pairs:
        require(k not in result,'Duplicate successor policy key');result[k]=v
    return result
def is_sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{40}',value) is not None
def validate_policy(p):
    require(type(p) is dict and set(p)=={'schema','base_commit','base_tree','replaces_target','target_commit','target_tree','replacements'},'Successor policy closure differs')
    require(p['schema']=='exact-seven-file-ci-successor-v1' and p['base_commit']==BASE_COMMIT and p['base_tree']==BASE_TREE and p['replaces_target']==REPLACES_TARGET,'Successor baseline differs')
    require(is_sha(p['target_commit']) and is_sha(p['target_tree']) and p['target_commit'] not in (BASE_COMMIT,REPLACES_TARGET) and p['target_tree']!=BASE_TREE,'Unbound or invalid successor identity')
    rows=p['replacements'];require(type(rows) is list and len(rows)==7,'Successor must contain exactly seven reviewed replacements')
    require(all(type(r) is dict and set(r)=={'path','mode','type','sha'} for r in rows),'Successor replacement closure differs')
    require({r['path'] for r in rows}==PATHS,'Successor replacement paths differ')
    require(all(r['mode']=='100644' and r['type']=='blob' and is_sha(r['sha']) for r in rows),'Successor replacement blob identity differs')
    return p
def load_policy():
    path=ROOT/'main-successor.json'
    require(path.is_file() and not path.is_symlink(),'Missing or linked successor policy')
    raw=path.read_bytes()
    require(re.fullmatch('[0-9a-f]{64}',POLICY_SHA256) is not None and hashlib.sha256(raw).hexdigest()==POLICY_SHA256,'Unbound or altered reviewed successor policy')
    return validate_policy(json.loads(raw,object_pairs_hook=strict_pairs))
def index_tree(value,root):
    require(type(value) is dict and value.get('sha')==root and value.get('truncated') is False and type(value.get('tree')) is list,'Unbound or truncated Git tree')
    leaves={};directories=set();seen=set()
    for row in value['tree']:
        path=row.get('path');require(type(path) is str and path and PurePosixPath(path).as_posix()==path and not path.startswith('/') and '\\' not in path and all(x not in ('','.','..') for x in path.split('/')),'Unsafe tree path')
        require(path not in seen,'Duplicate tree path');seen.add(path)
        kind=row.get('type');mode=row.get('mode');require(is_sha(row.get('sha')),'Invalid tree object identity')
        if kind=='tree':require(mode=='040000','Tree mode differs');directories.add(path)
        else:
            require((kind=='blob' and mode in ('100644','100755','120000')) or (kind=='commit' and mode=='160000'),'Unsupported Git entry')
            leaves[path]={'mode':mode,'type':kind,'sha':row['sha']}
    for path in seen:
        for parent in PurePosixPath(path).parents:
            if str(parent)!='.':require(str(parent) in directories,'Missing parent tree')
    return leaves,directories
def verify_delta(base,target,policy):
    validate_policy(policy)
    before,base_dirs=index_tree(base,BASE_TREE);after,target_dirs=index_tree(target,policy['target_tree'])
    require(base_dirs==target_dirs,'Successor added or removed a directory')
    changes={name for name in set(before)|set(after) if before.get(name)!=after.get(name)}
    require(changes==PATHS,'Successor has missing or extra changes/deletions')
    expected=dict(before)
    for row in policy['replacements']:
        require(row['path'] in after,'Successor removed a reviewed path')
        expected[row['path']]={key:row[key] for key in ('mode','type','sha')}
    require(after==expected,'Successor Git blob/mode/type differs')
    return {'base_tree':BASE_TREE,'target_tree':policy['target_tree'],'exact_reviewed_changes':7,'production_tree_preserved':True}
def admit_rebind(current,new_target,policy):
    validate_policy(policy)
    require(new_target==policy['target_commit'],'Rebind target is not the reviewed successor')
    require(current in (REPLACES_TARGET,new_target),'Current binding cannot be replaced by this policy')
