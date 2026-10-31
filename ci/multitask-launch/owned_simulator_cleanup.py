"""Bounded cleanup of one successful create result; no retry or global cleanup."""
import hashlib,json,os,signal,subprocess,time
from pathlib import Path
from attachment_contract import CLEANUP_SCHEMA,_uuid,owned_absent

def sha_bytes(data):return hashlib.sha256(data).hexdigest()
def creation_ownership(stdout,argv):
    if not (len(argv)==6 and argv[:3]==['xcrun','simctl','create']):raise ValueError('Invalid creation command')
    if type(stdout) is not str or len(stdout)>128:raise ValueError('Invalid creation output')
    owner=_uuid(stdout.strip())
    return {'udid':owner,'create_argv':argv,'create_exit_code':0,'create_stdout':stdout,'create_stdout_sha256':sha_bytes(stdout.encode())}

def bounded_command(argv,timeout,log):
    started=time.monotonic();code=None;kind='spawn_error';reaped=True
    # Regular-file output prevents inherited stdout pipes from keeping communicate
    # alive. Only a newly created private process group is killed on timeout.
    with log.open('xb') as stream:
        try:
            child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
        except OSError:pass
        else:
            try:
                code=child.wait(timeout=timeout);kind='success' if code==0 else 'nonzero'
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                try:code=child.wait(timeout=5);kind='timeout'
                except subprocess.TimeoutExpired:kind='unreaped';reaped=False
    return {'argv':argv,'timeout_seconds':timeout,'elapsed_ms':int((time.monotonic()-started)*1000),
            'outcome':kind,'returncode':code,'reaped':reaped,'log':log.name,'log_sha256':sha_bytes(log.read_bytes())}

def cleanup_owned(ownership,work,runner=bounded_command):
    owner=_uuid(ownership['udid'])
    if ownership!=creation_ownership(ownership['create_stdout'],ownership['create_argv']):raise ValueError('Cleanup lacks owned create result')
    actions=[runner(['xcrun','simctl',action,owner],45,work/(action+'-cleanup.log')) for action in ('shutdown','delete')]
    result={'schema':CLEANUP_SCHEMA,'ownership':ownership,'actions':actions,'readback':None,'status':'failed'}
    if all(row['outcome']=='success' and row['reaped'] for row in actions):result['status']='completeByCommand';return result
    # Exactly one bounded, complete, UNFILTERED readonly lookup after errors.
    row=runner(['xcrun','simctl','list','devices','--json'],10,work/'cleanup-final-inventory.json')
    text=''
    if row['outcome']=='success':
        try:
            path=work/row['log']
            if path.stat().st_size<=8*1024*1024:text=path.read_bytes().decode('utf-8',errors='strict')
        except (UnicodeError,OSError):pass
    result['readback']={'command':row,'owned_uuid':owner,'inventory_json':text,'inventory_sha256':sha_bytes(text.encode())}
    try:
        if row['outcome']=='success' and row['reaped'] and all(x['reaped'] for x in actions) and owned_absent(text,owner,ownership['create_argv'][5]):result['status']='completeByAbsence'
    except (ValueError,TypeError,KeyError):pass
    return result
