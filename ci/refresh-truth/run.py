#!/usr/bin/env python3
"""Execute exact source-owned Swift and Rust refresh fixtures, no app/device operations."""
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'issues-native'))
import refresh_truth_receipt as C
from verify_candidate_graph import load_candidate_graph

def run_identity():
 return C.identity({k:os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]})
def fresh(p):C.require(not p.exists() and not p.is_symlink(),'fresh output required: '+p.name)
def compiler(name):
 path=shutil.which(name);C.require(path is not None,'required native compiler absent: '+name)
 path=Path(path).resolve(strict=True)
 return {'path_sha256':C.sha(path),'version':subprocess.check_output([str(path),'--version'],text=True,timeout=30).strip()}
def command(args,cwd,log,env,timeout):
 fresh(log)
 with log.open('x') as stream:
  result=subprocess.run(args,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
 C.require(result.returncode==0,'source-owned fixture command failed: '+log.name)
 return log.read_text()
def run(kind,sources,output):
 sources=sources.resolve(strict=True);output=output.resolve(strict=True)
 graph=load_candidate_graph();run=run_identity();before=C.collect(sources,graph)
 C.require(not output.is_relative_to(sources),'fixture output must be outside source closure')
 target=output/('refresh-truth-'+kind+'.json');fresh(target)
 if kind=='seal':
  parts={name:C.read(output/('refresh-truth-'+name+'.json')) for name in ('swift','rust')}
  value={'schema':C.SCHEMA,'status':C.STATUS,'identity':run,'source_graph':graph['owners'],
   'parts':parts,'part_sha256':{name:C.digest(part) for name,part in parts.items()},
   'physical_device_execution':False,'os_profile_selection_proven':False,'future_certificate_trust_proven':False}
  C.verify_portable(value,graph['owners'],run)
  C.require(before==parts['swift']['source'],'source changed before seal')
  target=output/'refresh-truth-tests.json';fresh(target)
  target.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
  C.verify_local(target,sources,graph,run);return
 report={'schema':C.SCHEMA+'-part','kind':kind,'status':'FAILED','identity':run,'source_graph':graph['owners'],
  'source':before,'logs':{},'compiler':compiler('swiftc' if kind=='swift' else 'rustc'),
  'physical_device_execution':False}
 try:
  if kind=='swift':
   env={**os.environ,'V3_REQUIRE_SWIFT':'1','REQUIRE_NATIVE_LEGACY_REPLACEMENT':'1','REQUIRE_NATIVE_PROFILE_READBACK':'1','PYTHONDONTWRITEBYTECODE':'1','V3_HOST_SOURCE_ROOT':str(sources/'LiveContainer')}
   failures=[]
   for owner,(script,count) in C.SUITES.items():
    label=owner.replace('/','-');log=output/('refresh-truth-'+label+'.log')
    try:
     text=command([sys.executable,'-B',str(sources/C.SUITE_OWNERS[owner]/script),'-v'],output,log,env,600)
     C.completed_tests(text,count);report['logs'][owner]=text
    except Exception as error:
     # Independent source-owned fixtures run once each; a failure never
     # becomes a passing part, and missing logs remain a hard failure.
     if log.is_file() and not log.is_symlink():report['logs'][owner]=log.read_text()
     else:failures.append(owner+': required suite log missing')
     failures.append(owner+': '+str(error))
    # A failed fixture may be independent; changed source is not. Never run
    # another owner after the pinned graph has been modified.
    C.require(C.collect(sources,graph)==before,'source changed after native refresh suite: '+owner)
   C.require(C.collect(sources,graph)==before,'source changed during native refresh qualification')
   C.require(not failures,'independent Swift qualification failed: '+'; '.join(failures))
  else:
   env={**os.environ,'CARGO_TARGET_DIR':str(output/'rust-target')}
   # Cargo resolves only the exact checked-in lock; no package replacement or
   # untracked lock edits are accepted. Build products remain outside sources.
   for package,features,filter_name in [('idevice','misagent,openssl','profile_readback_tests'),('idevice-ffi','misagent,openssl,rsd,core_device_proxy,tunnel_tcp_stack,heartbeat','bounded_profile_readback_tests')]:
    args=['cargo','test','--locked','--manifest-path',str(sources/'idevice/Cargo.toml'),
          '-p',package,'--lib','--no-default-features','--features',features,filter_name,'--','--nocapture']
    text=command(args,output,output/('refresh-truth-'+package+'-rust.log'),env,900)
    C.rust_tests(text,ffi=package=='idevice-ffi');report['logs'][package]=text
  C.require(C.collect(sources,graph)==before,'source changed during native refresh qualification')
  report['status']='PASS'
 finally:target.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 C.validate_part(report,kind,graph['owners'],run)
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['swift','rust','seal']);parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();run(args.mode,args.sources,args.output)
