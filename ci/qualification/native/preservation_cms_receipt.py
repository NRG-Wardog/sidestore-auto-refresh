"""Exact same-run CMS core and real public-profile decoder qualification.
Only finite receipts/logs survive; downloaded public profiles stay in runner temp.
"""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import argparse, datetime, hashlib, importlib.util, json, os, platform, re, shutil, subprocess, sys, tempfile, selectors, signal, tarfile, time
from pathlib import Path
SCHEMA = 'preservation-cms-native-v1'
STATUS = 'PASS_EXACT_CRYPTO_AND_SWIFT_PUBLIC_BOUNDARY_NOT_DEVICE'
CMS_CASES = ('production_has_no_injected_trust_exports', 'test_legacy_chain_and_math', 'test_inner_exact_pin_chain_and_math', 'production_rejects_synthetic_outer', 'production_rejects_unknown_inner_pin', 'test_rejects_wrong_inner_pin', 'inspection_unknown_pin_untrusted_not_authority', 'inspection_known_pin_still_distinct_result', 'inspection_production_rejects_synthetic_root', 'inspection_signature_tamper_empty', 'inspection_content_tamper_empty', 'inspection_trailing_data_empty', 'inspection_truncated_data_empty', 'inspection_detached_empty', 'inspection_attributes_required', 'inspection_strong_algorithm_required', 'inspection_sha1_not_authority', 'inspection_empty_input_empty', 'inspection_oversized_input_empty', 'diagnostic_unknown_pin_verified_hash_not_authority', 'diagnostic_verified_finite_role_observations', 'diagnostic_wrong_root_no_hash_unchecked_time_signature', 'diagnostic_bad_signature_no_hash', 'diagnostic_bad_content_digest_no_hash', 'diagnostic_malformed_resets_evidence', 'diagnostic_unsupported_algorithm_no_hash', 'diagnostic_profile_markers_are_not_authority', 'diagnostic_app_developer_distinct_not_authority', 'diagnostic_untrusted_role_markers_not_disclosed', 'signature_tamper_rejected', 'content_tamper_rejected', 'trailing_data_rejected', 'truncated_data_rejected', 'detached_rejected', 'no_signed_attributes_rejected', 'unsupported_digest_rejected', 'sha1_not_inner_authority', 'sha1_only_legacy_envelope', 'diagnostic_expired_time_no_hash', 'inspection_expired_time_empty', 'expired_inner_time_rejected', 'expired_legacy_documented_no_time', 'diagnostic_future_time_no_hash', 'inspection_future_time_empty', 'future_inner_time_rejected', 'future_legacy_documented_no_time', 'reordered_certificates_same_authority', 'duplicate_matching_sid_rejected', 'inspection_duplicate_sid_empty', 'conflicting_same_sid_order_0', 'conflicting_same_sid_order_1', 'signed_content_type_tamper_rejected', 'multiple_signers_rejected', 'inspection_multiple_signers_empty', 'oversized_rsa_candidate_rejected_before_math', 'inspection_oversized_key_empty', 'empty_input_rejected', 'oversized_input_rejected')
PUBLIC_CASES = ('public-0-outer-role-authenticated', 'public-0-inner-pinned-role-authenticated', 'public-0-signer-diagnostic-facade', 'public-0-cryptographic-inspection-facade', 'public-0-inspection-tamper-rejected', 'public-0-inspection-malformed-rejected', 'public-0-complete-authority-bound', 'public-0-outer-tamper-rejected', 'public-0-inner-tamper-rejected', 'public-0-inner-cannot-be-outer-role', 'public-0-outer-cannot-be-inner-role', 'public-1-outer-role-authenticated', 'public-1-inner-pinned-role-authenticated', 'public-1-signer-diagnostic-facade', 'public-1-cryptographic-inspection-facade', 'public-1-inspection-tamper-rejected', 'public-1-inspection-malformed-rejected', 'public-1-complete-authority-bound', 'public-1-outer-tamper-rejected', 'public-1-inner-tamper-rejected', 'public-1-inner-cannot-be-outer-role', 'public-1-outer-cannot-be-inner-role', 'public-0-cross-profile-binding-rejected', 'public-1-cross-profile-binding-rejected')
INPUTS = {
 'SideStore': ('SideStore/Core/Operations/PipelineOperations/PreservedProfileDecoder.swift',
  'tests/preservation_der/run_public.sh','tests/preservation_der/PublicCases.swift',
  'tests/preservation_der/public-fixtures.json','tests/preservation_der/fetch_public_fixtures.py',
  'tests/preservation_der/expected-cases.json','tests/preservation_der/test_contract.py'),
 'SideStore/Dependencies/minimuxer': ('Package.swift','DeviceGateway/Package.swift','DeviceGateway/ProvisioningCMSCore/profile_cms.c',
  'DeviceGateway/ProvisioningCMSCore/include/profile_cms.h',
  'DeviceGateway/ProvisioningCMSCore/production_roots.h','DeviceGateway/ProvisioningCMSCore/approved_inner.h',
  'DeviceGateway/libimobiledevice/ProvisioningCMSVerifier.swift','Sources/ProfileCMSVerifier.swift',
  'TestsCMS/run_native.py','TestsCMS/diagnostic_support.py','TestsCMS/approved-inner-provenance.json','TestsCMS/expected-cases.json','TestsCMS/verify_profile_cms.c'),
}
LOGS = ('preservation-cms-native.json', 'preservation-cms-public.log')
def require(ok, why):
 if not ok: raise ValueError(why)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pairs(rows):
 d={}
 for k,v in rows:
  require(k not in d,'duplicate JSON field');d[k]=v
 return d
def read(path):
 p=Path(path);require(p.is_file() and not p.is_symlink(),'missing/linked receipt input')
 return json.loads(p.read_text(),object_pairs_hook=pairs)
def identity(value):
 require(type(value) is dict and set(value)=={'workflow_commit','run_id','run_attempt'},'run identity fields')
 require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}',value['workflow_commit']),'builder identity')
 require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*',value[k]) for k in ('run_id','run_attempt')),'run integer identity')
 return value
def git(root,*args):
 return subprocess.check_output(['git','--no-replace-objects','--literal-pathspecs','-C',str(root),'-c','core.hooksPath=/dev/null',*args],text=True,timeout=30).strip()
def sources(root,graph):
 result={}
 for owner,paths in INPUTS.items():
  base=Path(root)/owner;require(base.is_dir() and not base.is_symlink(),'source owner missing/linked')
  require(Path(git(base,'rev-parse','--show-toplevel')).resolve()==base.resolve(),'inherited owner Git root')
  require(git(base,'rev-parse','HEAD')==graph['owners'][owner][1],'source owner HEAD differs')
  require(git(base,'rev-parse','HEAD^{tree}')==graph['reviewed_deltas'][owner]['tree'],'source owner tree differs')
  for name in paths:
   path=base/name;require(path.is_file() and not path.is_symlink(),'missing/linked CMS source')
   require(all(not parent.is_symlink() for parent in path.parents if parent!=base.parent),'linked CMS source ancestor')
   data=path.read_bytes();blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
   require(git(base,'rev-parse','HEAD:'+name)==blob,'CMS source bytes differ from graph')
   result[owner+'/'+name]=hashlib.sha256(data).hexdigest()
 return result
def validate_cms_report(r):
 require(type(r) is dict and set(r)=={'schema','openssl','qualified','time_policy','tested_at_utc','checks','count','status'},'CMS result closure')
 require(r['schema']=='profile-cms-native-v1' and r['status']=='passed','CMS result status')
 require(r['openssl']=='3.5.9' and r['qualified'] is True,'CMS exact qualified version')
 require(r['time_policy']=='inner-current-system-time;legacy-no-check-time','CMS temporal policy')
 require(type(r['tested_at_utc']) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+\+00:00',r['tested_at_utc']),'CMS test timestamp')
 require(type(r['count']) is int and r['count']==len(CMS_CASES),'CMS count differs')
 require(type(r['checks']) is list and r['checks']==list(CMS_CASES),'exact CMS cases differ')
 return r
def validate_public_log(text):
 require(type(text) is str and len(text)<100_000,'public log bound')
 lines=text.splitlines();require(len(lines)==1,'public result must be one finite JSON record')
 r=json.loads(lines[0],object_pairs_hook=pairs)
 require(set(r)=={'schema','status','cases','cms_verified','device_executed'},'public result closure')
 require(r['schema']=='preservation-der-public-v1' and r['status']=='PASS','public qualification status')
 require(r['cms_verified'] is True and r['device_executed'] is False,'public qualification execution scope')
 require(type(r['cases']) is list and r['cases']==list(PUBLIC_CASES),'public exact case identities')
 return r
def host_build(built,evidence):
 built=Path(built).resolve(strict=True);receipt=read(built/'build-receipt.json')
 pins=read(((_CIPath(__file__).resolve().parents[3]/'ci/build/openssl/source.json')))
 require(receipt['source']==pins and pins['version']=='3.5.9','unqualified OpenSSL source')
 require(receipt['schema']=='issue53-lts-generated-dependency-v1' and receipt['status']=='BUILT_NOT_QUALIFIED_NOT_APP_READY','host build status')
 require(receipt['recipe_sha256']==sha(((_CIPath(__file__).resolve().parents[3]/'ci/build/openssl/build.py'))),'host recipe drift')
 require((built/'build-receipt.json').read_bytes()==(Path(evidence)/'openssl-host-build-receipt.json').read_bytes(),'same-run host receipt differs')
 require(sha(built/'openssl-3.5.9.tar.gz')==pins['sha256'],'same-run archive changed')
 prefix=built/'install';headers=read(built/'headers.json')
 require(sha(built/'headers.json')==receipt['header_inventory_sha256'],'same-run header manifest changed')
 header_root=prefix/'include/openssl';require(header_root.is_dir() and not header_root.is_symlink(),'missing/linked qualified headers')
 header_paths=sorted(header_root.rglob('*'));require(all(not p.is_symlink() for p in header_paths),'linked qualified header')
 actual={p.relative_to(header_root).as_posix():sha(p) for p in header_paths if p.is_file()}
 require(actual==headers,'qualified installed headers differ')
 libs=[prefix/'lib/libcrypto.a',prefix/'lib64/libcrypto.a'];present=[p for p in libs if p.is_file()]
 require(len(present)==1 and not present[0].is_symlink(),'ambiguous/missing qualified archive')
 crypto=present[0];require(receipt['static_archives_sha256'].get(str(crypto))==sha(crypto),'crypto archive differs from same-run build')
 return prefix, {'version':'3.5.9','build_receipt_sha256':sha(built/'build-receipt.json'),
  'source_archive_sha256':pins['sha256'],'libcrypto_sha256':sha(crypto),
  'headers_inventory_sha256':sha(built/'headers.json')}
class QualificationCommandFailure(ValueError):
 def __init__(self,returncode,stdout,stderr):
  super().__init__('CMS qualification command failed (raw temporary data not retained)')
  self.returncode=returncode;self.stdout=stdout;self.stderr=stderr

def execute(argv,cwd,env,timeout=600):
 result=subprocess.run([str(x) for x in argv],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 if result.returncode!=0:raise QualificationCommandFailure(result.returncode,result.stdout,result.stderr)
 return result.stdout,result.stderr

FAILURE_DIAGNOSTIC = 'preservation-cms-failure-diagnostic.json'
COMPILER_BASENAMES = ('profile_cms.c','profile_cms.h','ProvisioningCMSVerifier.swift',
 'ProfileCMSVerifier.swift','PreservedProfileDecoder.swift','main.swift','version.c')

def finite_stage_failure(phase,status,returncode,stderr):
 require(phase in ('syntheticC','publicSwift'),'unknown qualification diagnostic phase')
 require(status in ('failed','timedOut'),'diagnostic cannot represent success')
 require(returncode is None if status=='timedOut' else type(returncode) is int and returncode!=0,'invalid failed exit status')
 if isinstance(stderr,bytes):stderr=stderr.decode('utf-8',errors='replace')
 if not isinstance(stderr,str):stderr=''
 # Bound parsing work; raw input is never copied to the report.
 if len(stderr)>131072:
  head=stderr[:65536];tail=stderr[-65536:]
  # Never manufacture a complete case marker by cutting an original line.
  head=head.rsplit('\n',1)[0] if '\n' in head else ''
  tail=tail.split('\n',1)[1] if '\n' in tail else ''
  bounded=head+'\n'+tail
 else:bounded=stderr
 failed=None;last=None;locations=[]
 for line in bounded.splitlines():
  if phase=='syntheticC':
   match=re.fullmatch(r'AssertionError: ([A-Za-z0-9_-]+)',line)
   if match and match[1] in CMS_CASES:failed=match[1]
  else:
   match=re.search(r'(?:^|: )Fatal error: unexpected acceptance: ([A-Za-z0-9_-]+)$',line)
   if match and match[1] in PUBLIC_CASES:failed=match[1]
   match=re.fullmatch(r'V3_PUBLIC_CASE_BEGIN ([A-Za-z0-9_-]+)',line)
   if match and match[1] in PUBLIC_CASES:last=match[1]
  match=re.match(r'^(?:[^\n:]*/)?('+'|'.join(map(re.escape,COMPILER_BASENAMES))+r'):([0-9]{1,7}):([0-9]{1,7}): (?:error|warning|note):',line)
  if match and 1<=int(match[2])<=1000000 and 1<=int(match[3])<=100000:
   location={'file':match[1],'line':int(match[2]),'column':int(match[3])}
   if location not in locations and len(locations)<8:locations.append(location)
 return {'schema':'preservation-cms-failure-diagnostic-v1','phase':phase,'status':status,
  'returncode':returncode,'confirmed_failed_case':failed,'last_case':last,
  'compiler_locations':locations,'raw_output_retained':False,'device_executed':False}

def execute_stage(phase,argv,cwd,env,evidence,timeout=600):
 require(phase in ('syntheticC','publicSwift'),'unknown qualification diagnostic phase')
 try:return execute(argv,cwd,env,timeout)
 except (QualificationCommandFailure,subprocess.TimeoutExpired) as error:
  timed_out=isinstance(error,subprocess.TimeoutExpired)
  try:
   report=finite_stage_failure(phase,'timedOut' if timed_out else 'failed',None if timed_out else error.returncode,error.stderr)
   destination=Path(evidence)/FAILURE_DIAGNOSTIC
   require(not destination.exists() and not destination.is_symlink(),'failure diagnostic already exists/linked')
   data=json.dumps(report,sort_keys=True)+'\n';require(len(data.encode())<4096,'failure diagnostic bound exceeded')
   with destination.open('x') as output:output.write(data)
  except (OSError,ValueError):
   pass # Diagnostic persistence must never replace or rescue the original failure.
  raise

CLI_DIAGNOSTIC = 'preservation-cms-build-diagnostic.json'
CLI_OUTPUT_LIMIT = 32768

def cli_source(built):
 """Same exact nested path returned by issue53-lts/build.py unpack()."""
 built=Path(built).resolve(strict=True);source=built/'source'/'openssl-3.5.9'
 require(source.is_dir() and not source.is_symlink() and not source.parent.is_symlink(),'missing/linked configured CLI source')
 for name in ('Makefile','configdata.pm','Configure'):
  require((source/name).is_file() and not (source/name).is_symlink(),'missing/linked configured CLI input')
 require((source/'apps').is_dir() and not (source/'apps').is_symlink(),'missing/linked CLI apps directory')
 require(not (source/'apps/openssl').is_symlink(),'linked CLI output')
 require((built/'configdata.txt').is_file() and not (built/'configdata.txt').is_symlink(),'missing/linked configuration dump')
 receipt=read(built/'build-receipt.json')
 require(sha(built/'configdata.txt')==receipt['configuration_sha256'],'configured CLI receipt drift')
 require(len(receipt['configure_command'])>=2 and receipt['configure_command'][1]==str(source/'Configure'),'configured CLI source differs from host recipe')
 with tarfile.open(built/'openssl-3.5.9.tar.gz','r:gz') as archive:
  entries=[m for m in archive.getmembers() if m.name=='openssl-3.5.9/Configure']
  require(len(entries)==1 and entries[0].isfile() and entries[0].size<=1024*1024,'original Configure member invalid')
  original=archive.extractfile(entries[0]).read()
 require(hashlib.sha256(original).hexdigest()==sha(source/'Configure'),'original Configure source changed')
 return source

def cli_environment(built):
 """Recreate the already-qualified host recipe's environment, not ambient flags."""
 receipt=read(Path(built)/'build-receipt.json')
 recipe=((_CIPath(__file__).resolve().parents[3]/'ci/build/openssl/build.py'))
 require(receipt['recipe_sha256']==sha(recipe),'CLI environment recipe changed')
 spec=importlib.util.spec_from_file_location('cms_qualified_host_recipe',recipe)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 env=module.sanitized_environment()
 def selected(*args):
  value=subprocess.check_output(['/usr/bin/xcrun',*args],env=env,text=True,timeout=30).strip()
  require(value and '\n' not in value,'invalid selected host SDK/tool')
  return value
 sdk=Path(receipt['sdk_path'])
 require(sdk.is_absolute() and sdk.is_dir(),'qualified SDK directory missing')
 require(Path(selected('--sdk','macosx','--show-sdk-path')).resolve(strict=True)==sdk.resolve(strict=True),'selected SDK differs from qualified host')
 require(selected('--sdk','macosx','--show-sdk-version')==receipt['sdk_version'],'selected SDK version differs from qualified host')
 tools=receipt['tools_sha256'];resolved=receipt['tool_resolved_paths']
 require(type(tools) is dict and type(resolved) is dict and set(tools)==set(resolved),'qualified tool receipt fields')
 expected={'clang','perl','make','ar','ranlib','ld','dsymutil','dwarfdump','vtool'}
 require(len(tools)==len(expected) and {Path(p).name for p in tools}==expected,'qualified tool inventory')
 invocations={}
 for name,digest in tools.items():
  require(Path(name).is_absolute(),'qualified tool path must be absolute')
  invocation=module.tool_invocation(name);require(invocation.is_file(),'qualified tool missing')
  require(str(invocation.resolve(strict=True))==resolved[name] and sha(invocation)==digest,'qualified tool identity changed')
  basename=invocation.name
  if basename in ('perl','make'):require(name=='/usr/bin/'+basename,'unexpected host tool invocation')
  else:require(str(module.tool_invocation(selected('--find',basename)))==name,'selected tool differs from qualified host')
  # Preserve invocation name, notably ranlib -> libtool multicall dispatch.
  invocations[basename]=name
 recorded=receipt['build_environment']
 require(type(recorded) is dict and set(recorded)=={'CFLAGS','LDFLAGS','ZERO_AR_DATE','SOURCE_DATE_EPOCH'},'qualified build environment fields')
 arch=receipt['architecture'];require(arch in ('arm64','x86_64') and arch==platform.machine(),'qualified host architecture differs')
 minimum='11.0' if arch=='arm64' else '10.15'
 require(receipt['minimum_os']==minimum,'qualified host deployment target differs')
 optimization=module.optimization_flag(receipt['optimization'])
 expected_environment={'CFLAGS':optimization+' -g -fPIC -mmacosx-version-min='+minimum,
  'LDFLAGS':'-mmacosx-version-min='+minimum,'ZERO_AR_DATE':'1',
  'SOURCE_DATE_EPOCH':str(int(datetime.datetime(2026,9,29,tzinfo=datetime.timezone.utc).timestamp()))}
 require(recorded==expected_environment,'qualified build environment differs from recipe')
 env.update(expected_environment)
 env.update({'CC':invocations['clang'],'AR':invocations['ar'],'RANLIB':invocations['ranlib'],
  'SDKROOT':str(sdk),'PATH':str(Path(invocations['clang']).parent)+':/usr/bin:/bin:/usr/sbin:/sbin'})
 return env

def build_cli(built,evidence,env,timeout=300):
 """Only the known source-only CLI make can retain compiler diagnostics.
 CMS/public-test subprocesses continue through execute(), which retains no raw output.
 """
 source=cli_source(built);destination=Path(evidence)/CLI_DIAGNOSTIC
 require(not destination.exists() and not destination.is_symlink(),'CLI diagnostic already exists/linked')
 argv=['/usr/bin/make','-C',str(source),'-j3','apps/openssl']
 chunks={'stdout':bytearray(),'stderr':bytearray()};totals={'stdout':0,'stderr':0}
 status='launchFailed';returncode=None;process=None
 try:
  process=subprocess.Popen(argv,cwd=Path(built).resolve(),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
  with selectors.DefaultSelector() as selector:
   for name,stream in [('stdout',process.stdout),('stderr',process.stderr)]:
    os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,name)
   deadline=time.monotonic()+timeout;timed_out=False;drain_deadline=None
   while selector.get_map() or process.poll() is None:
    now=time.monotonic()
    if not timed_out and now>=deadline:
     timed_out=True;drain_deadline=now+2
     try:os.killpg(process.pid,signal.SIGKILL)
     except ProcessLookupError:pass
    if drain_deadline is not None and now>=drain_deadline:break
    for key,_ in selector.select(0.05):
     data=os.read(key.fileobj.fileno(),8192)
     if not data:selector.unregister(key.fileobj);key.fileobj.close();continue
     name=key.data;totals[name]+=len(data)
     chunks[name].extend(data[:max(0,CLI_OUTPUT_LIMIT-len(chunks[name]))])
   for key in list(selector.get_map().values()):
    selector.unregister(key.fileobj);key.fileobj.close()
   try:returncode=process.wait(timeout=2)
   except subprocess.TimeoutExpired:timed_out=True;returncode=process.poll()
   status='timedOut' if timed_out else ('passed' if returncode==0 else 'failed')
 except OSError:
  status='launchFailed'
  raise
 finally:
  if process is not None and process.poll() is None:
   try:os.killpg(process.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   try:process.wait(timeout=2)
   except subprocess.TimeoutExpired:pass
  # JSON escaping keeps terminal control bytes inert; all captured bytes come
  # exclusively from this verified source-only make, before any profile input.
  report={'schema':'preservation-cms-cli-build-diagnostic-v1','phase':'opensslSourceCLICompilation',
   'status':status,'returncode':returncode,'target':'apps/openssl','source_directory':'source/openssl-3.5.9',
   'stream_limit_bytes':CLI_OUTPUT_LIMIT,'public_inputs_used':False,
   'streams':{name:{'text':bytes(chunks[name]).decode('utf-8',errors='replace'),
    'bytes_observed':totals[name],'truncated':totals[name]>CLI_OUTPUT_LIMIT} for name in chunks}}
  with destination.open('x') as f:json.dump(report,f,sort_keys=True);f.write('\n')
 require(status=='passed','CMS CLI source build '+status+' (original exit '+str(returncode)+'; see bounded source diagnostic)')
 return source/'apps/openssl'

def qualification_python():
 # Preserve the running harness invocation, not a PATH lookup or resolved alias.
 interpreter=Path(sys.executable)
 require(interpreter.is_absolute() and interpreter.is_file() and os.access(interpreter,os.X_OK),'missing/invalid harness interpreter')
 return str(interpreter)

def run(root,built,evidence,graph,run_identity):
 root=Path(root).resolve(strict=True);evidence=Path(evidence).resolve(strict=True)
 identity(run_identity);before=sources(root,graph);prefix,crypto=host_build(built,evidence)
 env=cli_environment(built)
 env.update({'PYTHONDONTWRITEBYTECODE':'1','CMS_REQUIRE_QUALIFIED':'1','CMS_OPENSSL_ROOT':str(prefix)})
 python=qualification_python()
 mini=root/'SideStore/Dependencies/minimuxer'
 # Build only the CLI using the existing verified source/configuration. This is
 # test tooling, never a replacement product crypto library or new acquisition.
 cli=build_cli(built,evidence,env,timeout=300)
 require(host_build(built,evidence)[1]==crypto,'CLI build changed qualified crypto inputs')
 require(cli.is_file() and not cli.is_symlink() and not cli.parent.is_symlink() and cli.resolve().parent==cli_source(built)/'apps','missing/linked qualified CLI')
 with tempfile.TemporaryDirectory(prefix='cms-qualification-') as temporary:
  report_path=Path(temporary)/'cms.json'
  stdout,stderr=execute_stage('syntheticC',[python,'-B',mini/'TestsCMS/run_native.py','--openssl-root',prefix,'--openssl-cli',cli,'--output',report_path],mini,env,evidence)
  cms=validate_cms_report(read(report_path))
 stdout,stderr=execute_stage('publicSwift',['/bin/bash','-e',root/'SideStore/tests/preservation_der/run_public.sh','--openssl-root',prefix,'--python',python],root/'SideStore',env,evidence)
 public=validate_public_log(stdout)
 # Compiler diagnostics are not retained from the public-input lane. The actual
 # command must succeed and stdout must contain only the validated finite receipt.
 require(sources(root,graph)==before,'source changed during CMS qualification')
 require(host_build(built,evidence)[1]==crypto,'qualified crypto changed during tests')
 for name,data in zip(LOGS,(json.dumps(cms,sort_keys=True)+'\n',stdout)):
  with (evidence/name).open('x') as f:f.write(data)
 receipt={'schema':SCHEMA,'status':STATUS,'run':run_identity,'source_sha256':before,
  'crypto':crypto,'tool_sha256':{str(Path(shutil.which(name,path=env['PATH'])).resolve(strict=True)):sha(Path(shutil.which(name,path=env['PATH'])).resolve(strict=True)) for name in ('clang','swiftc')},'openssl_cli_sha256':sha(cli),'cms_cases':list(CMS_CASES),'public_cases':list(PUBLIC_CASES),
  'log_sha256':{name:sha(evidence/name) for name in LOGS},'cms_verified':True,
  'device_executed':False,'public_profile_bytes_retained':False}
 with (evidence/'preservation-cms-tests.json').open('x') as f:json.dump(receipt,f,sort_keys=True,indent=2);f.write('\n')
 verify(evidence/'preservation-cms-tests.json',root,graph,run_identity)
 print('PASS exact OpenSSL 3.5.9 CMS tests and authenticated Swift decoder/public-wrapper linkage; no device execution')
 return receipt

def verify(path,root,graph,run_identity):
 path=Path(path);r=read(path);identity(run_identity)
 require(set(r)=={'schema','status','run','source_sha256','crypto','tool_sha256','openssl_cli_sha256','cms_cases','public_cases','log_sha256','cms_verified','device_executed','public_profile_bytes_retained'},'CMS receipt fields')
 require(r['schema']==SCHEMA and r['status']==STATUS and r['run']==run_identity,'CMS receipt lineage/status')
 require(r['cms_verified'] is True and r['device_executed'] is False and r['public_profile_bytes_retained'] is False,'CMS receipt scope')
 require(r['source_sha256']==sources(root,graph),'CMS receipt source closure')
 require(r['cms_cases']==list(CMS_CASES) and r['public_cases']==list(PUBLIC_CASES),'CMS receipt case closure')
 crypto=r['crypto'];require(set(crypto)=={'version','build_receipt_sha256','source_archive_sha256','libcrypto_sha256','headers_inventory_sha256'} and crypto['version']=='3.5.9','CMS crypto receipt fields')
 require(all(type(crypto[k]) is str and re.fullmatch('[0-9a-f]{64}',crypto[k]) for k in crypto if k!='version'),'CMS crypto hashes')
 host=read(path.parent/'openssl-host-build-receipt.json')
 require(host['recipe_sha256']==sha(((_CIPath(__file__).resolve().parents[3]/'ci/build/openssl/build.py'))),'CMS host recipe differs')
 pins=read(((_CIPath(__file__).resolve().parents[3]/'ci/build/openssl/source.json')))
 require(crypto['build_receipt_sha256']==sha(path.parent/'openssl-host-build-receipt.json') and host['source']==pins and crypto['source_archive_sha256']==pins['sha256'],'CMS qualified host lineage')
 crypto_archives=[v for k,v in host['static_archives_sha256'].items() if Path(k).name=='libcrypto.a']
 require(crypto_archives==[crypto['libcrypto_sha256']],'CMS crypto archive not same-run')
 require(crypto['headers_inventory_sha256']==host['header_inventory_sha256'],'CMS host headers lineage')
 require(type(r['openssl_cli_sha256']) is str and re.fullmatch('[0-9a-f]{64}',r['openssl_cli_sha256']),'CMS CLI digest')
 require(type(r['tool_sha256']) is dict and 1<=len(r['tool_sha256'])<=2,'CMS compiler identities')
 for name,digest in r['tool_sha256'].items():require(type(name) is str and Path(name).is_absolute() and type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest),'CMS compiler identity format')
 require(set(r['log_sha256'])==set(LOGS),'CMS log closure')
 for name,digest in r['log_sha256'].items():require(digest==sha(path.parent/name),'CMS log bytes differ')
 validate_cms_report(read(path.parent/LOGS[0]));validate_public_log((path.parent/LOGS[1]).read_text())
 return r
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--sources',type=Path,required=True);p.add_argument('--built',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args()
 from ci.build.verify_candidate_graph import load_candidate_graph
 run(a.sources,a.built,a.evidence,load_candidate_graph(),{'workflow_commit':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT']})
