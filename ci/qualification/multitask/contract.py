"""Strict source/evidence helpers for focused synthetic multitasking tests."""
import hashlib,json,os,re,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent

def require(ok,why):
    if not ok: raise ValueError(why)
def strict(rows):
    out={}
    for key,value in rows:
        require(key not in out,'Duplicate JSON key');out[key]=value
    return out
def read(path):return json.loads(path.read_text(),object_pairs_hook=strict)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def manifest():
    data=read(HERE/'source.json')
    require(set(data)=={'schema','repository','commit','baseline_app_builder'},'Source manifest fields differ')
    require(data['schema']=='multitask-launch-source-v1' and data['repository']=='NRG-Wardog/LiveContainer','Wrong source owner')
    for name in ('commit','baseline_app_builder'):require(type(data[name]) is str and re.fullmatch('[0-9a-f]{40}',data[name]),'Invalid immutable source identity')
    return data
def command(args,*,cwd=None,timeout=120):
    try:return subprocess.check_output([str(x) for x in args],cwd=cwd,text=True,stderr=subprocess.STDOUT,timeout=timeout)
    except subprocess.SubprocessError as error:
        output=getattr(error,'output','') or ''
        if isinstance(output,bytes):output=output.decode('utf-8','replace')
        if output:print(output,flush=True)
        raise
def git(root,*args):return command(['git','--no-replace-objects','-C',root,'-c','core.hooksPath=/dev/null',*args]).strip()
def inventory(root):
    root=root.resolve(strict=True)
    require(git(root,'rev-parse','HEAD')==manifest()['commit'],'Unexpected LC source commit')
    require(git(root,'status','--porcelain','--untracked-files=all')=='','Source worktree is not clean')
    result={}
    for row in git(root,'ls-files','--stage').splitlines():
        meta,name=row.split('\t',1);mode,blob,stage=meta.split();require(stage=='0','Unmerged source')
        path=root/name
        if mode=='160000':result[name]={'gitlink':blob}
        elif path.is_symlink():result[name]={'mode':mode,'symlink':os.readlink(path)}
        else:
            require(path.is_file(),'Missing tracked source');result[name]={'mode':mode,'sha256':sha(path)}
    return result
def identity():
    result={key:os.environ[value] for key,value in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
    require(re.fullmatch('[0-9a-f]{40}',result['workflow_commit']),'Invalid workflow identity')
    for key in ('run_id','run_attempt'):require(re.fullmatch('[1-9][0-9]*',result[key]),'Invalid run identity')
    return result
