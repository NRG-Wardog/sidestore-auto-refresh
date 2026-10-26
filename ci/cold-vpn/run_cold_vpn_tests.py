"""Execute pinned source-owned cold VPN fixtures. No device or real VPN claim."""
import argparse, hashlib, json, os, platform, re, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'issues-native'))
from verify_candidate_graph import load_candidate_graph
MM='SideStore/Dependencies/minimuxer'
SUITES={
 'host':('LiveContainer','.ci/cold-vpn-observation/test_contract.py','REQUIRE_SWIFT_COLD_VPN_TESTS'),
 'backend':('SideStore','tests/cold_vpn_readiness/test_contract.py','REQUIRE_SWIFT_COLD_VPN_TESTS'),
 'transport':(MM,'.ci/test-owned-readiness.py','REQUIRE_SWIFT_OWNED_READINESS_TESTS'),
}
# Each source owner is immutable and verified by the acquisition gate. Hash the
# complete owner tracked file closure as well, including source-owned fixtures.
def require(ok,why):
 if not ok: raise ValueError(why)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def strict(pairs):
 result={}
 for k,v in pairs:
  require(k not in result,'Duplicate JSON key');result[k]=v
 return result
def read(p):return json.loads(p.read_text(),object_pairs_hook=strict)
def git(root,*args):return subprocess.check_output(['git','--no-replace-objects','-C',str(root),'-c','core.hooksPath=/dev/null',*args],text=True).strip()
APP_LOCK='AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'
def admit_lock_transition(before_raw,after_raw,transition,graph):
 before=json.loads(before_raw,object_pairs_hook=strict);after=json.loads(after_raw,object_pairs_hook=strict)
 require(set(before)==set(after),'Resolver lock schema changed')
 require(all(before[k]==after[k] for k in set(before)-{'pins','originHash'}),'Resolver lock fields changed')
 def index(value):
  rows={p['identity']:p for p in value['pins']};require(len(rows)==len(value['pins']),'Duplicate lock identity');return rows
 old,new=index(before),index(after);require(new==old,'Resolver pins changed')
 require(old['anisettekit']['state']=={'revision':graph['owners']['AnisetteKit'][1]},'Wrong lock AK pin')
 if 'originHash' in after:require(type(after['originHash']) is str and re.fullmatch('[0-9a-f]{64}',after['originHash']),'Invalid resolver metadata')
 expected={'status':'OBSERVED_RESOLVER_METADATA_ONLY' if before_raw!=after_raw else 'UNCHANGED_PINNED_APP_LOCK','source_commit':graph['owners']['SideStore'][1],'working_checkout_has_uncommitted_app_lock':before_raw!=after_raw,'before_sha256':hashlib.sha256(before_raw).hexdigest(),'after_sha256':hashlib.sha256(after_raw).hexdigest(),'before_originHash':before.get('originHash'),'after_originHash':after.get('originHash'),'pins_changed':[],'only_allowed_metadata_change':'originHash','new_source_commit_published':False}
 require(transition==expected,'Reviewed resolver transition differs')
def lock_hash(root,current,graph,evidence):
 baseline=subprocess.check_output(['git','--no-replace-objects','-C',str(root),'show','HEAD:'+APP_LOCK])
 if current!=baseline:
  require(evidence is not None,'Lock changed before fixtures')
  paths=[evidence/n for n in ['SideStore.Package.resolved.before','SideStore.Package.resolved.after','app-lock-transition.json']]
  require(all(p.is_file() and not p.is_symlink() for p in paths),'Missing/linked resolver evidence')
  require(paths[0].read_bytes()==baseline and paths[1].read_bytes()==current,'Resolver before/after bytes differ')
  admit_lock_transition(baseline,current,read(paths[2]),graph)
  native=read(evidence/'receipt.json')
  expected_identity=read(evidence/'cold-vpn-tests.json')['run_identity']
  require({k:native[k] for k in expected_identity}==expected_identity,'Resolver native identity differs')
  require(all(native['evidence_sha256'].get(p.name)==sha(p) for p in paths),'Resolver transition not bound to native receipt')
 return hashlib.sha256(baseline).hexdigest()
def source_hashes(sources,graph,evidence=None):
 result={}
 for owner in sorted({v[0] for v in SUITES.values()}):
  root=(sources/owner).resolve(strict=True)
  require(root.is_relative_to(sources),'Source owner escapes root')
  require(git(root,'rev-parse','HEAD')==graph['owners'][owner][1],'Wrong cold VPN source owner '+owner)
  rows={}
  for row in git(root,'ls-files','--stage').splitlines():
   meta,name=row.split('\t',1);mode,blob,stage=meta.split();require(stage=='0','Unmerged source')
   p=root/name
   if mode=='160000':rows[name]={'gitlink':blob};continue
   if mode=='120000':
    require(p.is_symlink(),'Expected source link missing');rows[name]={'mode':mode,'symlink':os.readlink(p)};continue
   require(p.is_file() and not p.is_symlink() and p.resolve(strict=True).is_relative_to(root),'Missing/linked source '+name)
   digest=lock_hash(root,p.read_bytes(),graph,evidence) if owner=='SideStore' and name==APP_LOCK else sha(p)
   rows[name]={'mode':mode,'sha256':digest}
  require(rows,'Empty source owner');result[owner]=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 return result
def identity():
 d={k:os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
 require(re.fullmatch('[0-9a-f]{40}',d['workflow_commit']),'Bad workflow commit')
 require(all(re.fullmatch('[1-9][0-9]*',d[k]) for k in ('run_id','run_attempt')),'Bad run identity');return d
def admit_log(code,text):
 require(code==0 and re.search(r'^OK\s*$',text,re.M) is not None and re.search(r'^(?:FAILED|OK \()',text,re.M) is None,'Cold VPN fixture failed or skipped')
def historical_vpn(sources,work):
 log=work/'historical-vpn.log'
 generator=sources/'LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py'
 with tempfile.TemporaryDirectory(prefix='cold-vpn-history-') as tmp,log.open('x') as stream:
  root=Path(tmp).resolve();generated=root/'generated';generated.mkdir()
  commands=[([sys.executable,'-B',generator,'--candidate-root',sources,'--output',generated],120),
            (['xcrun','swiftc','-swift-version','5','-parse-as-library',generated/'vpn-refresh-recovery.swift','-o',root/'fixture'],180),
            ([root/'fixture',generated/'cases.json'],120)]
  for args,timeout in commands:
   result=subprocess.run([str(a) for a in args],stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
   stream.flush()
   if result.returncode:
    print(log.read_text()[-16000:],file=sys.stderr,flush=True)
    raise ValueError('Existing 89-case VPN fixture failed')
  cases=read(generated/'cases.json');definition=read(generated/'sources.json')
  require(cases['schema']=='vpn-refresh-recovery-cases-v1' and cases['status']=='pass','Historical VPN fixture failed')
  require(cases['case_count']==89 and len(cases['cases'])==89 and len(set(cases['cases']))==89 and cases['cases']==definition['expected_case_names'],'Historical VPN cases differ')
  require(cases['network_executed'] is False and cases['native_provider_executed'] is False,'Historical VPN scope differs')
  require(log.read_text().splitlines().count('VPN_REFRESH_RECOVERY_PASS cases=89')==1,'Historical VPN marker missing/duplicate')
  for name in ['sources.json','cases.json']:(work/('historical-vpn-'+name)).write_bytes((generated/name).read_bytes())
 return {name:sha(work/name) for name in ['historical-vpn.log','historical-vpn-sources.json','historical-vpn-cases.json']}
def run(sources,output):
 sources=sources.resolve(strict=True);output=output.resolve(strict=True)
 work=output/'cold-vpn';require(not work.exists(),'Fresh evidence required');work.mkdir()
 graph=load_candidate_graph();before=source_hashes(sources,graph)
 report={'schema':'cold-vpn-source-regressions-v1','status':'FAILED','run_identity':identity(),'source_graph':graph['owners'],'source_owner_inventory_sha256':before,'physical_device_execution':False,'real_vpn_execution':False,'suite_log_sha256':{}}
 receipt=output/'cold-vpn-tests.json'
 try:
  require(platform.system()=='Darwin','Mandatory Apple SDK and native Swift execution requires macOS')
  for name,(owner,entry,env) in SUITES.items():
   require(git(sources/owner,'ls-files','--error-unmatch',entry)==entry,'Untracked fixture entrypoint')
   entry_path=sources/owner/entry
   require(entry_path.is_file() and not entry_path.is_symlink() and entry_path.resolve(strict=True).is_relative_to((sources/owner).resolve(strict=True)),'Unsafe fixture entrypoint')
   log=work/(name+'.log')
   with log.open('x') as stream:
    r=subprocess.run([sys.executable,'-B',sources/owner/entry],cwd=work,env={**os.environ,env:'1','COLD_VPN_EVIDENCE_DIR':str(work)},stdout=stream,stderr=subprocess.STDOUT,timeout=300)
   text=log.read_text()
   try:admit_log(r.returncode,text)
   except ValueError:
    print('COLD_VPN_FIXTURE_FAILURE '+name+'\n'+text[-16000:],file=sys.stderr,flush=True);raise
   report['suite_log_sha256'][name]=sha(log)
  report['historical_vpn_sha256']=historical_vpn(sources,work)
  require(source_hashes(sources,graph)==before,'Source changed during fixtures')
  report['status']='PASS_SOURCE_EXECUTION_NOT_DEVICE_VPN'
 finally:receipt.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 verify_receipt(receipt,sources,report['run_identity'])
def verify_receipt(path,sources,native_identity):
 require(path.is_file() and not path.is_symlink(),'Missing/linked cold VPN receipt');d=read(path)
 require(d['schema']=='cold-vpn-source-regressions-v1' and d['status']=='PASS_SOURCE_EXECUTION_NOT_DEVICE_VPN','Cold VPN tests not passed')
 require(d['run_identity']==native_identity,'Cold VPN run differs')
 graph=load_candidate_graph();require(d['source_graph']==graph['owners'],'Cold VPN graph differs')
 require(d['source_owner_inventory_sha256']==source_hashes(sources.resolve(strict=True),graph,path.parent),'Cold VPN source differs')
 require(d['physical_device_execution'] is False and d['real_vpn_execution'] is False,'Incorrect cold VPN execution claim')
 require(set(d['suite_log_sha256'])==set(SUITES),'Missing cold VPN suite')
 for name,digest in d['suite_log_sha256'].items():
  p=path.parent/'cold-vpn'/(name+'.log');require(p.is_file() and not p.is_symlink() and sha(p)==digest,'Cold VPN log differs');admit_log(0,p.read_text())
 require(set(d['historical_vpn_sha256'])=={'historical-vpn.log','historical-vpn-sources.json','historical-vpn-cases.json'},'Historical VPN closure differs')
 for name,digest in d['historical_vpn_sha256'].items():
  p=path.parent/'cold-vpn'/name;require(p.is_file() and not p.is_symlink() and sha(p)==digest,'Historical VPN evidence differs')
 require((path.parent/'cold-vpn/historical-vpn.log').read_text().splitlines().count('VPN_REFRESH_RECOVERY_PASS cases=89')==1,'Historical VPN execution missing')
 return d
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--sources',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.sources,a.output)
