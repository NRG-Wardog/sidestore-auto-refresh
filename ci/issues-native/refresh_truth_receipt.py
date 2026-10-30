"""Closed same-run refresh evidence contract. Synthetic native fixtures, not device proof."""
import hashlib, json, re, subprocess, stat
from pathlib import Path
SCHEMA='refresh-truth-native-v1'
STATUS='PASS_SOURCE_OWNED_NATIVE_FIXTURES_NOT_DEVICE'
SUITES={
 'LiveContainer':('.ci/refresh-truth/test_refresh_truth.py',5),
 'LiveContainer-observations':('.ci/refresh-observations/test_refresh_observations.py',3),
 'SideStore':('tests/refresh_truth/test_contract.py',8),
 'SideStore-direct':('tests/direct_refresh_evidence/test_contract.py',4),
 'SideStore/Dependencies/minimuxer':('.ci/test-profile-readback.py',6),
}
INPUTS={
 'LiveContainer':('SideStoreSupport/SideStore.swift','LiveContainerSwiftUI/App/AppDelegate.swift','LiveContainerSwiftUI/Views/V3UnifiedShell.swift','LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift','Resources/Localizable.xcstrings','.ci/refresh-truth/test_refresh_truth.py','.ci/refresh-truth/runtime_cases.swift','.ci/refresh-truth/notification_cases.swift','.ci/refresh-truth/ticket_module_cases.swift','.ci/refresh-truth/localization_keys.json','SideStoreSupport/SideStoreHooks.m','.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py','.ci/comprehensive-diagnostics/test_setup_localization_contract.py','.ci/comprehensive-diagnostics/generate_wire_fixture.py','.ci/refresh-observations/test_refresh_observations.py','.ci/refresh-observations/cases.swift','.ci/refresh-observations/localization_keys.json'),
 'SideStore':('SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift','SideStore/Core/Operations/PipelineOperations/VerifyCertificateOperation.swift','SideStore/Core/Certificates/OCSPValidator.swift','SideStore/Core/Certificates/CertificateManager.swift','SideStore/Core/Operations/PipelineRunner.swift','SideStore/Core/Operations/OperationContexts.swift','SideStore/Core/Operations/RefreshGroup.swift','SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift','tests/refresh_truth/test_contract.py','tests/refresh_truth/runtime.swift','tests/refresh_truth/cases.swift','tests/refresh_truth/operation_doubles.swift','tests/refresh_truth/operation_cases.swift','AltStore/AppDelegate.swift','SideStore/Core/Operations/PipelineExecutor.swift','SideStore/Core/Operations/PipelineOperations/InstallAppOperation.swift','tests/direct_refresh_evidence/test_contract.py','tests/direct_refresh_evidence/cases.swift','tests/direct_refresh_evidence/registration.swift','SideStore/Core/DeviceApi/MinimuxerWrapper.swift','tests/cold_vpn_readiness/test_contract.py','tests/cold_vpn_readiness/boundary_doubles.swift','tests/cold_vpn_readiness/runtime_cases.swift','tests/cold_vpn_readiness/test_transport_observation.py','tests/cold_vpn_readiness/transport_observation_cases.swift'),
 'SideStore/Dependencies/minimuxer':('Sources/MinimuxerApi.swift','Sources/MinimuxerImpl.swift','DeviceGateway/DeviceGatewayAPI.swift','DeviceGateway/DeviceGatewayError.swift','DeviceGateway/InstalledProfileReadback.swift','DeviceGateway/idevice/IdeviceGateway.swift','DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift','.ci/test-profile-readback.py','.ci/profile-readback/policy-fixture.swift','.ci/profile-readback/ffi-fixture.swift'),
 'idevice':('Cargo.toml','Cargo.lock','idevice/Cargo.toml','idevice/src/services/misagent.rs','ffi/Cargo.toml','ffi/src/misagent.rs'),
}
SUITE_OWNERS={key:({'SideStore-direct':'SideStore','LiveContainer-observations':'LiveContainer'}.get(key,key)) for key in SUITES}
OBSERVATION_CASES=('typed_guard_preserved', 'specific_safe_guard_copy', 'unknown_guard_dropped', 'orphan_scope_dropped', 'bad_scope_primary_failure_retained', 'wrong_source_guard_dropped', 'wrong_stage_guard_dropped', 'wrong_kind_guard_dropped', 'malformed_optional_primary_retained', 'nonstring_guard_dropped', 'finite_transport_observation', 'boolean_version_rejected', 'extra_transport_fields_rejected', 'unowned_transport_rejected', 'wire_preserves_owned_observation', 'wrong_run_observation_rejected', 'mixed_target_ack_not_aggregated', 'target_guard_scope_preserved', 'target_rows_no_identifiers', 'copy_preserves_individual_outcomes', 'copy_no_raw_values', 'transport_observed_even_renewal_failed', 'renewal_stays_unverified_independent', 'no_inferred_coredevice_transport', 'historical_ready_not_current_tunnel', 'active_generation_suppresses_history', 'no_transport_proof_no_transport_verified', 'malformed_history_no_transport_verified', 'wrong_run_history_no_transport_verified', 'floating_summary_version_rejected', 'boolean_summary_version_rejected', 'nonterminal_observation_not_promoted', 'mixed_aggregate_does_not_hide_target_guard', 'selection_guard_not_vpn_advice', 'stored_diagnostics_reject_raw_values', 'renewal_verified_without_transport_observation_not_inferred', 'overflow_summary_version_rejected', 'noncanonical_run_rejected', 'actual_summary_preserves_complete_observation', 'actual_summary_rejects_partial_observation', 'partial_manifest_no_target_rows', 'target_diagnostics_bounded_32', 'target_33_truncation_explicit', 'unknown_failure_kind_rejected', 'target_summary_float_version_rejected', 'nonterminal_target_summary_rejected', 'wrong_operation_guard_dropped')

RUST_CASES=tuple('services::misagent::profile_readback_tests::'+name for name in ('bounds_fail_closed_without_truncation','payload_must_be_complete_data_array','status_is_mandatory_integer_zero','success_and_empty_store_are_observations'))

FFI_CASES=tuple('misagent::bounded_profile_readback_tests::'+name for name in ('exported_buffers_roundtrip_and_free','exported_deadline_drops_query_before_return_not_provider','exported_failure_keeps_provider_and_clears_outputs','invalid_provider_zeros_outputs','successful_query_drops_owner_and_preserves_bytes','timeout_drops_owned_query_before_return'))

def require(ok, why):
 if not ok: raise ValueError(why)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def pairs(rows):
 out={}
 for key,value in rows:
  require(key not in out,'duplicate receipt key');out[key]=value
 return out
def read(p):
 require(p.is_file() and not p.is_symlink(),'missing/linked evidence')
 return json.loads(p.read_text(),object_pairs_hook=pairs)
def identity(value):
 require(type(value) is dict and set(value)=={'workflow_commit','run_id','run_attempt'},'run identity closure differs')
 require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}',value['workflow_commit']),'invalid workflow commit')
 require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*',value[k]) for k in ('run_id','run_attempt')),'invalid run identity')
 return value
def git(root,*args):
 return subprocess.check_output(['git','--no-replace-objects','--literal-pathspecs','-C',str(root),'-c','core.hooksPath=/dev/null',*args],text=True,timeout=30).strip()
GENERATED_STAGES={
 'LiveContainer':('generated-openssl','GeneratedDependencies/OpenSSL35'),
 'SideStore/Dependencies/minimuxer':('generated-embedded-openssl','GeneratedDependencies/RemotePairingKit'),
}
def generated_stage_files(owner,inventory):
 if owner not in GENERATED_STAGES:return {}
 key,relative=GENERATED_STAGES[owner];stage=inventory.get(key,{})
 if stage.get('present') is not True:return {}
 require(stage.get('relative_path')==owner+'/'+relative,'unexpected generated owner path')
 rows=stage.get('files');require(type(rows) is dict and rows and '.' in rows,'missing generated inventory')
 result={}
 for name,row in rows.items():
  require(type(name) is str and (name=='.' or (not name.startswith('/') and all(p not in ('','.', '..') for p in name.split('/')))),'unsafe generated relative path')
  require(type(row) is dict and row.get('kind') in ('file','directory') and type(row.get('mode')) is int and 0<=row['mode']<=0o777,'invalid generated inventory row')
  require(set(row)==({'kind','mode','sha256'} if row['kind']=='file' else {'kind','mode'}),'generated inventory row closure differs')
  if row['kind']=='file':
   require(re.fullmatch('[0-9a-f]{64}',row['sha256']) is not None,'invalid generated digest')
   result[relative+'/'+name]=row['sha256']
 require(rows['.']['kind']=='directory','generated root is not directory')
 return result

def verify_generated_stage(root,owner,inventory):
 allowed=generated_stage_files(owner,inventory)
 if not allowed:return allowed
 key,relative=GENERATED_STAGES[owner];base=root/relative;cursor=root
 for part in relative.split('/'):
  cursor=cursor/part;require(cursor.is_dir() and not cursor.is_symlink(),'missing/linked generated ancestor')
 actual={}
 for path in [base,*sorted(base.rglob('*'))]:
  mode=path.lstat().st_mode
  require(stat.S_ISDIR(mode) or stat.S_ISREG(mode),'linked/nonregular generated compiler input')
  row={'kind':'directory' if stat.S_ISDIR(mode) else 'file','mode':stat.S_IMODE(mode)}
  if stat.S_ISREG(mode):row['sha256']=sha(path)
  actual[path.relative_to(base).as_posix()]=row
 require(actual==inventory[key]['files'],'generated source inventory differs from compiler seal')
 return allowed

def generated_failure_context(owner,name):
 relative=name if name.startswith(('GeneratedDependencies/','DeviceGateway/LocalBinary/')) and re.fullmatch(r'[A-Za-z0-9_./+\-]{1,180}',name) else 'path_sha256='+hashlib.sha256(name.encode()).hexdigest()
 return owner+':'+relative

def collect(sources,graph,compiler_inventory=None):
 sources=Path(sources).resolve(strict=True);observed={};hashes={}
 for owner,paths in INPUTS.items():
  root=sources/owner
  require(root.is_dir() and not root.is_symlink(),'missing/linked source owner')
  require(Path(git(root,'rev-parse','--show-toplevel')).resolve(strict=True)==root.resolve(strict=True),'source owner is inherited parent Git root: '+owner)
  require(git(root,'rev-parse','HEAD')==graph['owners'][owner][1],'source HEAD differs: '+owner)
  tree=git(root,'rev-parse','HEAD^{tree}')
  require(tree==graph['reviewed_deltas'][owner]['tree'],'source tree differs: '+owner)
  if compiler_inventory is None:
   require(not git(root,'status','--porcelain=v1','--untracked-files=all'),'source worktree/index dirty: '+owner)
  else:
   # The caller supplies the existing validated before/after compiler snapshot.
   # Only the already sealed resolver/generated-input lifecycle is admitted;
   # every tracked byte is re-read here, and tested blobs remain Git-exact.
   sealed=compiler_inventory.get(owner)
   require(type(sealed) is dict and sealed.get('commit')==graph['owners'][owner][1] and sealed.get('tree')==tree,'sealed compiler owner differs')
   index_rows={}
   for row in git(root,'ls-files','--stage').splitlines():
    metadata,name=row.split('\t',1);mode,blob,stage=metadata.split();require(stage=='0','unmerged compiler source')
    index_rows[name]=(mode,blob)
   head_rows={}
   for row in git(root,'ls-tree','-r','HEAD').splitlines():
    metadata,name=row.split('\t',1);mode,kind,blob=metadata.split();head_rows[name]=(mode,blob)
   require(index_rows==head_rows,'compiler index differs from committed tree')
   tracked={}
   for row in git(root,'ls-files','--stage').splitlines():
    metadata,name=row.split('\t',1);mode,blob,stage=metadata.split();require(stage=='0','unmerged compiler source')
    path=root/name
    if mode=='160000':tracked[name]={'gitlink':blob}
    elif path.is_symlink():
     import os
     tracked[name]={'mode':mode,'symlink':os.readlink(path)}
    else:
     require(path.is_file(),'missing tracked compiler input')
     require(bool(path.stat().st_mode & 0o111)==(mode=='100755'),'tracked executable mode differs')
     tracked[name]={'mode':mode,'sha256':sha(path)}
   require(tracked==sealed.get('files'),'tracked compiler source differs from sealed inventory')
   changes=set(git(root,'diff','--name-only','--ignore-submodules=dirty','HEAD').splitlines())
   permitted={'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'} if owner=='SideStore' else set()
   require(changes<=permitted,'unexpected tracked compiler modification')
   allowed_generated=verify_generated_stage(root,owner,compiler_inventory)
   if owner=='SideStore/Dependencies/minimuxer':
    allowed_generated.update({'DeviceGateway/LocalBinary/IDevice.xcframework/'+name:digest for name,digest in compiler_inventory.get('local-framework',{}).items()})
   for name in git(root,'ls-files','--others','--exclude-standard').splitlines():
    require(name in allowed_generated,'unsealed generated source file '+generated_failure_context(owner,name))
    path=root/name;require(path.is_file() and not path.is_symlink() and sha(path)==allowed_generated[name],'generated source file differs from compiler seal')
  observed[owner]={'repository':graph['owners'][owner][0],'commit':graph['owners'][owner][1],'tree':tree}
  for name in paths:
   p=root/name;cursor=sources
   for part in (owner+'/'+name).split('/'):
    cursor=cursor/part;require(not cursor.is_symlink(),'linked compiler input')
   require(p.is_file(),'missing compiler input: '+owner+'/'+name)
   expected=subprocess.check_output(['git','--no-replace-objects','-C',str(root),'cat-file','blob','HEAD:'+name],timeout=30)
   require(p.read_bytes()==expected,'compiler input differs from committed blob')
   hashes[owner+'/'+name]=sha(p)
 return {'owners':observed,'source_sha256':hashes}
def completed_tests(log,count):
 require(type(log) is str and len(log.encode())<=4*1024*1024,'invalid/big suite log')
 counts=re.findall(r'^Ran ([1-9][0-9]*) tests? in [0-9.]+s$',log,re.M)
 require(counts==[str(count)] and re.search(r'^OK\s*$',log,re.M) is not None and re.search(r'^(?:FAILED|OK \()',log,re.M) is None,'source suite failed/skipped or test count differs')
def observation_tests(log):
 completed_tests(log,3)
 require(tuple(re.findall(r'^PASS ([a-z0-9_]+)$',log,re.M))==OBSERVATION_CASES,'observation native cases differ')
 require(len(re.findall(r'^REFRESH_OBSERVATIONS_PASS$',log,re.M))==1,'observation native completion missing/duplicated')
def rust_tests(log,ffi=False):
 require(type(log) is str and len(log.encode())<=4*1024*1024,'invalid/big Rust log')
 cases=re.findall(r'^test ((?:services::misagent::profile_readback_tests|misagent::bounded_profile_readback_tests)::\w+) \.\.\. ok$',log,re.M)
 expected=FFI_CASES if ffi else RUST_CASES
 require(sorted(cases)==list(expected),'Rust parser/FFI cases differ')
 require(re.search(r'test result: ok\. '+str(len(expected))+r' passed; 0 failed; 0 ignored; 0 measured;',log),'Rust parser/FFI tests failed/skipped')
def validate_part(part,kind,graph,run):
 require(type(part) is dict and set(part)=={'schema','kind','status','identity','source_graph','source','logs','compiler','physical_device_execution'},'part receipt closure differs')
 require(part['schema']==SCHEMA+'-part' and part['kind']==kind and part['status']=='PASS','part not passed')
 require(identity(part['identity'])==identity(run) and part['source_graph']==graph,'part run/source graph differs')
 source=part['source'];require(type(source) is dict and set(source)=={'owners','source_sha256'},'source receipt closure differs')
 require(set(source['owners'])==set(INPUTS),'source owner closure differs')
 for owner,row in source['owners'].items():
  require(type(row) is dict and set(row)=={'repository','commit','tree'} and [row['repository'],row['commit']]==list(graph[owner]) and re.fullmatch('[0-9a-f]{40}',row['tree']),'source owner pin differs')
 require(set(source['source_sha256'])=={o+'/'+p for o,paths in INPUTS.items() for p in paths},'source input closure differs')
 require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in source['source_sha256'].values()),'invalid source digest')
 require(part['physical_device_execution'] is False,'device claim forbidden')
 compiler=part['compiler'];require(type(compiler) is dict and set(compiler)=={'path_sha256','version'} and re.fullmatch('[0-9a-f]{64}',compiler['path_sha256']) and type(compiler['version']) is str and compiler['version'],'compiler identity missing')
 logs=part['logs'];require(type(logs) is dict and set(logs)==(set(SUITES) if kind=='swift' else {'idevice','idevice-ffi'}),'suite log closure differs')
 if kind=='swift':
  for owner,(_,count) in SUITES.items():completed_tests(logs[owner],count)
  observation_tests(logs['LiveContainer-observations'])
 else:
  rust_tests(logs['idevice']);rust_tests(logs['idevice-ffi'],ffi=True)
 return part
def verify_portable(value,graph,run):
 require(type(value) is dict and set(value)=={'schema','status','identity','source_graph','parts','part_sha256','physical_device_execution','os_profile_selection_proven','future_certificate_trust_proven'},'refresh receipt closure differs')
 require(value['schema']==SCHEMA and value['status']==STATUS,'refresh native qualification not passed')
 require(identity(value['identity'])==identity(run) and value['source_graph']==graph,'refresh native source/run differs')
 require(value['physical_device_execution'] is False and value['os_profile_selection_proven'] is False and value['future_certificate_trust_proven'] is False,'unsupported device/trust claim')
 require(set(value['parts'])=={'swift','rust'} and set(value['part_sha256'])=={'swift','rust'},'refresh part closure differs')
 for kind,part in value['parts'].items():
  require(value['part_sha256'][kind]==digest(part),'part digest mismatch');validate_part(part,kind,graph,run)
 require(value['parts']['swift']['source']==value['parts']['rust']['source'],'source changed between native suites')
 return value

def sealed_compiler_inventory(compiler_evidence,graph,run):
 before=read(Path(compiler_evidence)/'compiler-inputs.json');after=read(Path(compiler_evidence)/'after-build.json')
 require(before==after,'compiler input seal changed after build')
 for owner,(key,_) in GENERATED_STAGES.items():
  if owner not in graph['owners']:continue
  sealed=after.get(key,{})
  require(sealed.get('present') is True,'required generated stage missing')
  stage_path=Path(compiler_evidence)/(key+'-stage.json');stage=read(stage_path)
  require(sha(stage_path)==sealed.get('stage_receipt_sha256'),'generated stage receipt hash differs')
  expected={k:v for k,v in sealed.items() if k!='stage_receipt_sha256'}
  require(stage.get('schema')=='same-run-'+key+'-input-v1' and stage.get('consumer_commit')==graph['owners'][owner][1] and stage.get('generated')==expected,'generated stage identity differs')
  require({k:str(stage.get(k,'')) for k in run}==run,'generated stage run differs')
  if key=='generated-embedded-openssl' and 'RemotePairingKit' in graph['owners']:
   require(after.get('RemotePairingKit',{}).get('commit')==graph['owners']['RemotePairingKit'][1] and sealed.get('source_commit')==graph['owners']['RemotePairingKit'][1] and sealed.get('source_tree')==after['RemotePairingKit'].get('tree'),'embedded generated upstream pin differs')
  generated_stage_files(owner,after)
 return after

def verify_local(path,sources,graph,run,compiler_evidence=None):
 value=verify_portable(read(path),graph['owners'],run)
 inventory=sealed_compiler_inventory(compiler_evidence,graph,run) if compiler_evidence is not None else None
 require(collect(sources,graph,inventory)==value['parts']['swift']['source'],'current compiler source differs from refresh fixture source')
 return value
