"""Synthetic contract controls only; never claims native CMS execution."""
import copy, json, pathlib, tempfile, unittest
from unittest.mock import patch
import preservation_cms_receipt as c
HERE=pathlib.Path(__file__).resolve().parent
class CMSControls(unittest.TestCase):
 def cms(self):return {'schema':'profile-cms-native-v1','openssl':'3.5.9','qualified':True,'time_policy':'inner-current-system-time;legacy-no-check-time','tested_at_utc':'2026-10-31T18:00:00+00:00','checks':list(c.CMS_CASES),'count':40,'status':'passed'}
 def public(self):return {'schema':'preservation-der-public-v1','status':'PASS','cases':list(c.PUBLIC_CASES),'cms_verified':True,'device_executed':False}
 def test_exact_actual_case_contracts(self):
  c.validate_cms_report(self.cms());c.validate_public_log(json.dumps(self.public()))
  self.assertEqual(len(c.CMS_CASES),40);self.assertEqual(len(c.PUBLIC_CASES),18)
 def test_diagnostic_helper_and_every_new_case_mandatory(self):
  self.assertIn('TestsCMS/diagnostic_support.py',c.INPUTS['SideStore/Dependencies/minimuxer'])
  self.assertIn('TestsCMS/approved-inner-provenance.json',c.INPUTS['SideStore/Dependencies/minimuxer'])
  for case in [name for name in c.PUBLIC_CASES if name.endswith('signer-diagnostic-facade')]:
   r=self.public();r['cases'].remove(case)
   with self.subTest(case=case),self.assertRaises(ValueError):c.validate_public_log(json.dumps(r))
  for case in [name for name in c.CMS_CASES if name.startswith('diagnostic_')]:
   r=self.cms();r['checks'].remove(case)
   with self.subTest(case=case),self.assertRaises(ValueError):c.validate_cms_report(r)
 def test_development_or_other_openssl_rejected(self):
  for k,v in [('openssl','3.5.7'),('qualified',False),('qualified',1),('status','skipped'),('time_policy','ignore-all-time'),('tested_at_utc','raw-profile')]:
   r=self.cms();r[k]=v
   with self.subTest(k=k,v=v),self.assertRaises(ValueError):c.validate_cms_report(r)
 def test_cms_count_type_or_incomplete_cases_rejected(self):
  for v in [28,39,40.0,True,'40']:
   r=self.cms();r['count']=v
   with self.subTest(v=v),self.assertRaises(ValueError):c.validate_cms_report(r)
  for rows in [list(c.CMS_CASES)[:-1],list(reversed(c.CMS_CASES)),list(c.CMS_CASES)+[c.CMS_CASES[0]],['fake']*40]:
   r=self.cms();r['checks']=rows
   with self.assertRaises(ValueError):c.validate_cms_report(r)
 def test_cms_extra_raw_fields_rejected(self):
  r=self.cms();r['profile_bytes']='sensitive'
  with self.assertRaises(ValueError):c.validate_cms_report(r)
 def test_public_incomplete_false_claim_or_additional_data_rejected(self):
  for k,v in [('cms_verified',False),('cms_verified',1),('device_executed',True),('schema','synthetic'),('status','SKIP'),('cases',list(c.PUBLIC_CASES)[:-1]),('cases',list(reversed(c.PUBLIC_CASES))),('raw_profile','secret')]:
   r=self.public();r[k]=v
   with self.subTest(k=k,v=v),self.assertRaises(ValueError):c.validate_public_log(json.dumps(r))
 def test_public_duplicate_fields_or_extra_lines_rejected(self):
  for s in [json.dumps(self.public())+'\nraw bytes',json.dumps(self.public())[:-1]+',"status":"PASS"}', 'x'*100001]:
   with self.assertRaises(ValueError):c.validate_public_log(s)
 def fixture(self,d):
  root=pathlib.Path(d).resolve(strict=True);run={'workflow_commit':'a'*40,'run_id':'1','run_attempt':'2'}
  host={'recipe_sha256':c.sha(HERE.parent/'issue53-lts/build.py'),'source':c.read(HERE.parent/'issue53-lts/source.json'),'static_archives_sha256':{'/exact/install/lib/libcrypto.a':'b'*64},'header_inventory_sha256':'c'*64}
  (root/'openssl-host-build-receipt.json').write_text(json.dumps(host))
  (root/c.LOGS[0]).write_text(json.dumps(self.cms()));(root/c.LOGS[1]).write_text(json.dumps(self.public()))
  r={'schema':c.SCHEMA,'status':c.STATUS,'run':run,'source_sha256':{'source':'d'*64},'crypto':{'version':'3.5.9','build_receipt_sha256':c.sha(root/'openssl-host-build-receipt.json'),'source_archive_sha256':host['source']['sha256'],'libcrypto_sha256':'b'*64,'headers_inventory_sha256':'c'*64},'tool_sha256':{'/usr/bin/clang':'e'*64},'openssl_cli_sha256':'f'*64,'cms_cases':list(c.CMS_CASES),'public_cases':list(c.PUBLIC_CASES),'log_sha256':{name:c.sha(root/name) for name in c.LOGS},'cms_verified':True,'device_executed':False,'public_profile_bytes_retained':False}
  path=root/'preservation-cms-tests.json';path.write_text(json.dumps(r));return path,r,run
 def test_complete_synthetic_receipt_contract(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'sources',return_value={'source':'d'*64}):
   p,r,run=self.fixture(d);self.assertEqual(c.verify(p,d,{},run),r)
 def test_missing_linked_receipt_and_tampered_logs_rejected(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'sources',return_value={'source':'d'*64}):
   p,r,run=self.fixture(d);(pathlib.Path(d)/c.LOGS[0]).write_text('{}')
   with self.assertRaises(ValueError):c.verify(p,d,{},run)
   p.unlink();p.symlink_to(pathlib.Path(d)/c.LOGS[0])
   with self.assertRaises(ValueError):c.verify(p,d,{},run)
 def test_source_crypto_lineage_and_privacy_claim_drift_rejected(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'sources',return_value={'source':'d'*64}):
   p,r,run=self.fixture(d)
   bad=[]
   for key,value in [('run',dict(run,run_attempt='1')),('source_sha256',{}),('cms_cases',[]),('public_cases',[]),('device_executed',True),('public_profile_bytes_retained',True),('cms_verified',1),('extra','raw')]:
    changed=copy.deepcopy(r);changed[key]=value;bad.append(changed)
   for key,value in [('version','3.5.7'),('build_receipt_sha256','a'*64),('source_archive_sha256','a'*64),('libcrypto_sha256','a'*64),('headers_inventory_sha256','a'*64)]:
    changed=copy.deepcopy(r);changed['crypto'][key]=value;bad.append(changed)
   for changed in bad:
    p.write_text(json.dumps(changed))
    with self.assertRaises(ValueError):c.verify(p,d,{},run)
 def test_failed_command_never_becomes_success(self):
  import subprocess
  with patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout='raw-profile',stderr='secret')):
   with self.assertRaisesRegex(ValueError,'raw temporary data not retained'):c.execute(['owned'],'.',{})
 def test_source_failure_prevents_any_execution(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'sources',side_effect=ValueError('source drift')),patch.object(c,'execute') as execute:
   with self.assertRaises(ValueError):c.run(d,d,d,{}, {'workflow_commit':'a'*40,'run_id':'1','run_attempt':'2'})
   execute.assert_not_called()
 def test_exact_existing_build_placement_and_mandatory_receipt(self):
  w=(HERE.parents[1]/'.github/workflows/cold-vpn-native.yml').read_text()
  command='python3 -B harness/ci/issues-native/preservation_cms_receipt.py --sources sources --built lts-host-build --evidence evidence'
  self.assertEqual(w.count(command),1)
  self.assertLess(w.index('cp lts-host-build/build-receipt.json evidence/openssl-host-build-receipt.json'),w.index(command))
  self.assertLess(w.index(command),w.index('python3 -B harness/ci/issue53-lts/build_ios.py'))
  record=(HERE/'record.py').read_text();self.assertIn("verify_preservation_cms(E / 'preservation-cms-tests.json'",record)
  early='python3 -B harness/ci/issues-native/test_preservation_cms_receipt.py'
  focused=(HERE.parents[1]/'.github/workflows/refresh-truth-preflight.yml').read_text()
  self.assertEqual(focused.count(early),1)
  self.assertLess(focused.index(early),focused.index('id: swift_tests'))
  self.assertEqual(w.count(early),2)
  self.assertLess(w.index(early),w.index('id: openssl_inputs'))
 def test_native_crypto_runs_early_and_reuses_exact_host_inputs(self):
  w=(HERE.parents[1]/'.github/workflows/cold-vpn-native.yml').read_text()
  early=w.index('id: preservation_cms');fixtures=w.index('id: refresh_truth_swift')
  late=w.index('id: openssl_inputs');next_step=w.index('      - name:',late)
  self.assertLess(early,fixtures);self.assertLess(early,w.index('id: multitask_tests'))
  build='python3 -B harness/ci/issue53-lts/build.py --output lts-host-build --optimization Os'
  driver='python3 -B harness/ci/issues-native/preservation_cms_receipt.py --sources sources --built lts-host-build --evidence evidence'
  self.assertEqual(w.count(build),1);self.assertTrue(early<w.index(build)<fixtures)
  self.assertEqual(w.count(driver),1);self.assertTrue(early<w.index(driver)<fixtures)
  self.assertNotIn('stage-openssl',w[early:fixtures])
  self.assertNotIn('stage-embedded-openssl',w[early:fixtures])
  recheck=w[late:next_step]
  self.assertIn("steps.preservation_cms.outcome == 'success'",recheck)
  self.assertIn("cms.host_build(pathlib.Path('lts-host-build'), pathlib.Path('evidence'))",recheck)
  self.assertIn("cms.verify(pathlib.Path('evidence/preservation-cms-tests.json')",recheck)
  self.assertLess(recheck.index('cms.verify('),recheck.index('harness/ci/issue53-lts/build_ios.py'))
  self.assertIn('--archive lts-host-build/openssl-3.5.9.tar.gz',recheck)
 def test_only_finite_logs_and_temporary_public_input_lane(self):
  self.assertEqual(c.LOGS,('preservation-cms-native.json','preservation-cms-public.log'))
  source=(HERE/'preservation_cms_receipt.py').read_text()
  self.assertIn("tempfile.TemporaryDirectory(prefix='cms-qualification-')",source)
  self.assertIn("'--openssl-root',prefix,'--openssl-cli',cli",source)
  self.assertIn("'apps/openssl'",source);self.assertIn('timeout=300',source)
  self.assertNotIn('pip install',source)
class SourceBindings(unittest.TestCase):
 def setup_sources(self,base):
  import subprocess
  graph={'owners':{},'reviewed_deltas':{}}
  for owner,paths in c.INPUTS.items():
   root=base/owner;root.mkdir(parents=True,exist_ok=True)
   def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()
   git('init','-q');git('config','user.email','synthetic@example.invalid');git('config','user.name','Synthetic contract fixture')
   for path in paths:
    p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic source '+path+'\n')
   git('add','--',*paths);git('-c','core.hooksPath=/dev/null','commit','-qm','synthetic inputs only')
   graph['owners'][owner]=['NRG-Wardog/fixture',git('rev-parse','HEAD')];graph['reviewed_deltas'][owner]={'tree':git('rev-parse','HEAD^{tree}')}
  return graph
 def test_actual_git_tree_binding_and_changed_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);graph=self.setup_sources(root);bound=c.sources(root,graph)
   self.assertEqual(len(bound),sum(map(len,c.INPUTS.values())))
   p=root/'SideStore'/c.INPUTS['SideStore'][0];p.write_text('changed')
   with self.assertRaisesRegex(ValueError,'source bytes differ'):c.sources(root,graph)
 def test_temporary_root_alias_requires_canonical_source_path(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);real=root/'real';real.mkdir()
   graph=self.setup_sources(real);alias=root/'alias';alias.symlink_to(real,target_is_directory=True)
   with self.assertRaisesRegex(ValueError,'linked CMS source ancestor'):c.sources(alias,graph)
   self.assertEqual(c.sources(alias.resolve(strict=True),graph),c.sources(real,graph))
 def test_wrong_head_or_tree_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);graph=self.setup_sources(root)
   for kind in ('owners','reviewed_deltas'):
    changed=copy.deepcopy(graph)
    if kind=='owners':changed[kind]['SideStore'][1]='0'*40
    else:changed[kind]['SideStore']['tree']='0'*40
    with self.assertRaises(ValueError):c.sources(root,changed)
 def test_linked_source_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);graph=self.setup_sources(root);p=root/'SideStore'/c.INPUTS['SideStore'][0]
   target=root/'copy';target.write_bytes(p.read_bytes());p.unlink();p.symlink_to(target)
   with self.assertRaisesRegex(ValueError,'linked CMS source'):c.sources(root,graph)
 def test_host_receipt_header_archive_binding(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);built=root/'built';evidence=root/'evidence';evidence.mkdir();include=built/'install/include/openssl';include.mkdir(parents=True);(include/'a.h').write_text('synthetic header')
   lib=built/'install/lib/libcrypto.a';lib.parent.mkdir();lib.write_text('synthetic not executable archive')
   (built/'openssl-3.5.9.tar.gz').write_text('synthetic not real archive')
   headers={'a.h':c.sha(include/'a.h')};(built/'headers.json').write_text(json.dumps(headers))
   pins=c.read(HERE.parent/'issue53-lts/source.json')
   receipt={'schema':'issue53-lts-generated-dependency-v1','status':'BUILT_NOT_QUALIFIED_NOT_APP_READY','source':pins,'recipe_sha256':c.sha(HERE.parent/'issue53-lts/build.py'),'header_inventory_sha256':c.sha(built/'headers.json'),'static_archives_sha256':{str(lib):c.sha(lib)}}
   for path in [built/'build-receipt.json',evidence/'openssl-host-build-receipt.json']:path.write_text(json.dumps(receipt))
   actual_sha=c.sha
   with patch.object(c,'sha',side_effect=lambda p:pins['sha256'] if pathlib.Path(p).name=='openssl-3.5.9.tar.gz' else actual_sha(p)):
    canonical=c.host_build(built,evidence)
    alias=root/'built-alias';alias.symlink_to(built,target_is_directory=True)
    self.assertEqual(c.host_build(alias,evidence),canonical)
    (include/'a.h').write_text('changed header')
    with self.assertRaisesRegex(ValueError,'installed headers differ'):c.host_build(built,evidence)
    (include/'a.h').write_text('synthetic header');lib.write_text('changed archive')
    with self.assertRaisesRegex(ValueError,'archive differs'):c.host_build(built,evidence)
class CLIBuildDiagnostics(unittest.TestCase):
 def fixture(self,root,recipe="\t@mkdir -p apps; touch apps/openssl\n"):
  import io,tarfile,hashlib,importlib.util
  root=pathlib.Path(root).resolve(strict=True)
  built=root/'built';built.mkdir();evidence=root/'evidence';evidence.mkdir()
  archive=built/'openssl-3.5.9.tar.gz';configure=b'original synthetic Configure only\n'
  with tarfile.open(archive,'w:gz') as tar:
   info=tarfile.TarInfo('openssl-3.5.9/Configure');info.size=len(configure);tar.addfile(info,io.BytesIO(configure))
  spec=importlib.util.spec_from_file_location('actual_lts_build',HERE.parent/'issue53-lts/build.py');build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
  source=build.unpack(archive,built/'source',hashlib.sha256(archive.read_bytes()).hexdigest())
  (source/'apps').mkdir()
  (source/'Makefile').write_text('apps/openssl:\n'+recipe)
  (source/'configdata.pm').write_text('synthetic generated config')
  (built/'configdata.txt').write_text('synthetic config dump')
  (built/'build-receipt.json').write_text(json.dumps({'configuration_sha256':c.sha(built/'configdata.txt'),'configure_command':['/usr/bin/perl',str(source/'Configure')]}))
  return built,evidence,source
 def test_actual_unpack_old_path_fails_new_exact_path_succeeds(self):
  import subprocess,os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d)
   old=subprocess.run(['/usr/bin/make','-C',str(built/'source'),'-j3','apps/openssl'],capture_output=True)
   self.assertNotEqual(old.returncode,0)
   self.assertEqual(c.cli_source(built),source)
   cli=c.build_cli(built,evidence,dict(os.environ));self.assertEqual(cli,source/'apps/openssl');self.assertTrue(cli.is_file())
   report=c.read(evidence/c.CLI_DIAGNOSTIC);self.assertEqual(report['status'],'passed');self.assertEqual(report['returncode'],0)
   self.assertFalse(report['public_inputs_used']);self.assertEqual(report['phase'],'opensslSourceCLICompilation')
 def test_original_failure_and_both_streams_retained(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d,"\t@echo source-stdout; echo source-compiler-error >&2; exit 7\n")
   with self.assertRaisesRegex(ValueError,'original exit 2'):c.build_cli(built,evidence,dict(os.environ))
   report=c.read(evidence/c.CLI_DIAGNOSTIC);self.assertEqual(report['status'],'failed');self.assertEqual(report['returncode'],2)
   self.assertIn('source-stdout',report['streams']['stdout']['text']);self.assertIn('source-compiler-error',report['streams']['stderr']['text'])
 def test_output_is_bounded_without_hiding_failure(self):
  import os,sys
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d, "\t@"+sys.executable+" -c 'import sys;print(chr(120)*100000);sys.stderr.write(chr(121)*100000);sys.exit(1)'\n")
   with self.assertRaises(ValueError):c.build_cli(built,evidence,dict(os.environ))
   report=c.read(evidence/c.CLI_DIAGNOSTIC)
   for stream in report['streams'].values():self.assertTrue(stream['truncated']);self.assertLessEqual(len(stream['text'].encode()),c.CLI_OUTPUT_LIMIT);self.assertGreater(stream['bytes_observed'],c.CLI_OUTPUT_LIMIT)
   self.assertLess((evidence/c.CLI_DIAGNOSTIC).stat().st_size,2*c.CLI_OUTPUT_LIMIT+2048)
 def test_timeout_retains_bounded_source_diagnostic_and_fails(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d,"\t@echo source-start; sleep 10\n")
   with self.assertRaisesRegex(ValueError,'timedOut'):c.build_cli(built,evidence,dict(os.environ),timeout=0.1)
   report=c.read(evidence/c.CLI_DIAGNOSTIC);self.assertEqual(report['status'],'timedOut');self.assertNotEqual(report['returncode'],0);self.assertIn('source-start',report['streams']['stdout']['text'])
 def test_timeout_still_applies_after_both_pipes_close(self):
  import os,sys,time,subprocess
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);real_popen=subprocess.Popen
   def closed_pipes(*args,**kwargs):
    return real_popen([sys.executable,'-c','import os,time;os.close(1);os.close(2);time.sleep(30)'],**kwargs)
   start=time.monotonic()
   with patch.object(c.subprocess,'Popen',side_effect=closed_pipes),self.assertRaisesRegex(ValueError,'timedOut'):
    c.build_cli(built,evidence,dict(os.environ),timeout=0.1)
   self.assertLess(time.monotonic()-start,5)
   self.assertEqual(c.read(evidence/c.CLI_DIAGNOSTIC)['status'],'timedOut')
 def test_postkill_drain_bounded_when_child_retains_pipe(self):
  import os,sys,time,subprocess
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);real_popen=subprocess.Popen;owned=[]
   def held_pipe(*args,**kwargs):
    reader,writer=os.pipe()
    try:
     # Both processes are direct test children: explicit handles permit reaping.
     holder=real_popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=writer,stderr=subprocess.DEVNULL,start_new_session=True);owned.append(holder)
     kwargs['stdout']=writer
     process=real_popen([sys.executable,'-c','import time;time.sleep(30)'],**kwargs);process.stdout=os.fdopen(reader,'rb');reader=None
     return process
    finally:
     os.close(writer)
     if reader is not None:os.close(reader)
   started=time.monotonic()
   try:
    with patch.object(c.subprocess,'Popen',side_effect=held_pipe),self.assertRaisesRegex(ValueError,'timedOut'):
     c.build_cli(built,evidence,dict(os.environ),timeout=0.2)
    self.assertLess(time.monotonic()-started,5)
    self.assertEqual(c.read(evidence/c.CLI_DIAGNOSTIC)['status'],'timedOut')
   finally:
    for child in owned:
     if child.poll() is None:child.kill()
     child.wait(timeout=2)
 def test_launch_failure_recorded_without_exception_text(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d)
   with patch.object(c.subprocess,'Popen',side_effect=OSError('untrusted exception detail')),self.assertRaises(OSError):c.build_cli(built,evidence,dict(os.environ))
   report=c.read(evidence/c.CLI_DIAGNOSTIC);self.assertEqual(report['status'],'launchFailed');self.assertNotIn('untrusted exception detail',(evidence/c.CLI_DIAGNOSTIC).read_text())
 def test_invalid_source_provenance_prevents_execution(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);(source/'Configure').write_text('changed')
   with patch.object(c.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'Configure source changed'):c.build_cli(built,evidence,dict(os.environ))
   popen.assert_not_called();self.assertFalse((evidence/c.CLI_DIAGNOSTIC).exists())
 def test_missing_linked_or_wrong_configured_directory_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);receipt=c.read(built/'build-receipt.json');receipt['configure_command'][1]=str(built/'source/Configure');(built/'build-receipt.json').write_text(json.dumps(receipt))
   with self.assertRaisesRegex(ValueError,'source differs'):c.cli_source(built)
   receipt['configure_command'][1]=str(source/'Configure');(built/'build-receipt.json').write_text(json.dumps(receipt));(source/'Makefile').unlink()
   with self.assertRaisesRegex(ValueError,'configured CLI input'):c.cli_source(built)
   (source/'Makefile').symlink_to(source/'Configure')
   with self.assertRaisesRegex(ValueError,'configured CLI input'):c.cli_source(built)
 def test_linked_apps_or_configuration_dump_rejected_before_make(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);(source/'apps').rmdir();outside=pathlib.Path(d)/'outside';outside.mkdir();(source/'apps').symlink_to(outside,target_is_directory=True)
   with patch.object(c.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'CLI apps directory'):c.build_cli(built,evidence,dict(os.environ))
   popen.assert_not_called();(source/'apps').unlink();(source/'apps').mkdir()
   dump=built/'configdata.txt';copy=built/'copy';copy.write_bytes(dump.read_bytes());dump.unlink();dump.symlink_to(copy)
   with patch.object(c.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'configuration dump'):c.build_cli(built,evidence,dict(os.environ))
   popen.assert_not_called()
 def test_no_general_log_opt_in_and_public_output_still_suppressed(self):
  import inspect,subprocess
  self.assertEqual(list(inspect.signature(c.execute).parameters),['argv','cwd','env','timeout'])
  with tempfile.TemporaryDirectory() as d,patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],2,stdout='raw-public-profile',stderr='private-key')):
   with self.assertRaisesRegex(ValueError,'raw temporary data not retained'):c.execute(['arbitrary-public-command'],d,{})
   self.assertEqual(list(pathlib.Path(d).iterdir()),[])
  source=inspect.getsource(c.build_cli);self.assertIn("argv=['/usr/bin/make','-C',str(source),'-j3','apps/openssl']",source)
 def test_existing_or_linked_diagnostic_never_overwritten(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,evidence,source=self.fixture(d);destination=evidence/c.CLI_DIAGNOSTIC;destination.write_text('existing')
   with patch.object(c.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'already exists'):c.build_cli(built,evidence,dict(os.environ))
   popen.assert_not_called();self.assertEqual(destination.read_text(),'existing')
 def test_early_failure_artifact_exact_source_only_allowlist(self):
  workflow=(HERE.parents[1]/'.github/workflows/cold-vpn-native.yml').read_text()
  start=workflow.index('name: preservation-cms-failure-');end=workflow.index('      - name:',start)
  block=workflow[start:end]
  paths=block.split('          path: |\n',1)[1].split('          if-no-files-found:',1)[0]
  actual=[line.strip() for line in paths.splitlines() if line.strip()]
  self.assertEqual(actual,['evidence/preservation-cms-driver.log','evidence/preservation-cms-build-diagnostic.json','evidence/preservation-cms-failure-diagnostic.json','evidence/openssl-host-build.log','evidence/openssl-host-config.log','evidence/openssl-host-build-receipt.json','evidence/lts-build-driver.log','evidence/openssl-lts-controls.log','evidence/openssl-size-controls.log','evidence/openssl-admission-controls.log'])
  self.assertNotIn('*',paths);self.assertNotIn('public',paths);self.assertNotIn('cms-qualification',paths)
  self.assertIn("steps.preservation_cms.outcome == 'failure'",workflow[workflow.rfind('      - name:',0,start):start])
  driver=(HERE/'preservation_cms_receipt.py').read_text()
  self.assertIn("require(host_build(built,evidence)[1]==crypto,'CLI build changed qualified crypto inputs')",driver)
  self.assertIn("require(host_build(built,evidence)[1]==crypto,'qualified crypto changed during tests')",driver)
  build=(HERE.parent/'issue53-lts/build.py').read_text()
  self.assertIn("'no-shared','no-module'",build);self.assertIn("'enable-legacy','no-asm'",build);self.assertNotIn("'no-apps'",build)
class CLIQualifiedEnvironment(unittest.TestCase):
 def fixture(self,d):
  root=pathlib.Path(d).resolve(strict=True);built=root/'built';built.mkdir();sdk=root/'MacOSX.sdk';sdk.mkdir();bindir=root/'toolchain/bin';bindir.mkdir(parents=True)
  tools={};resolved={};selected={}
  for name in ['clang','ar','ranlib','ld','dsymutil','dwarfdump','vtool']:
   p=bindir/name
   if name=='ranlib':
    (bindir/'libtool').write_text('synthetic multicall tool');p.symlink_to(bindir/'libtool')
   else:p.write_text('synthetic tool '+name)
   tools[str(p)]=c.sha(p);resolved[str(p)]=str(p.resolve(strict=True));selected[name]=str(p)
  for name in ['perl','make']:
   p=pathlib.Path('/usr/bin')/name;tools[str(p)]=c.sha(p);resolved[str(p)]=str(p.resolve(strict=True))
  architecture=c.platform.machine();minimum='11.0' if architecture=='arm64' else '10.15'
  receipt={'architecture':architecture,'minimum_os':minimum,'optimization':'Os','recipe_sha256':c.sha(HERE.parent/'issue53-lts/build.py'),'sdk_path':str(sdk),'sdk_version':'26.4','tools_sha256':tools,'tool_resolved_paths':resolved,'build_environment':{'CFLAGS':'-Os -g -fPIC -mmacosx-version-min='+minimum,'LDFLAGS':'-mmacosx-version-min='+minimum,'ZERO_AR_DATE':'1','SOURCE_DATE_EPOCH':'1790640000'}}
  (built/'build-receipt.json').write_text(json.dumps(receipt))
  def query(argv,**kwargs):
   self.assertEqual(argv[0],'/usr/bin/xcrun')
   if argv[1:]==['--sdk','macosx','--show-sdk-path']:return str(sdk)+'\n'
   if argv[1:]==['--sdk','macosx','--show-sdk-version']:return '26.4\n'
   self.assertEqual(argv[1],'--find');return selected[argv[2]]+'\n'
  return built,receipt,query,bindir
 def test_reuses_actual_producer_sanitizer_and_qualified_environment(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   built,r,query,bindir=self.fixture(d)
   hostile={'SDKROOT':'/wrong/sdk','CC':'untrusted-compiler','AR':'wrong-ar','RANLIB':'wrong-ranlib','CFLAGS':'-wrong','LDFLAGS':'-wrong','CPATH':'/wrong/include','LIBRARY_PATH':'/wrong/lib','MAKEFLAGS':'-f hostile','PERL5OPT':'hostile','OPENSSL_CONF':'/wrong/config','DYLD_INSERT_LIBRARIES':'wrong','PATH':'/wrong/bin'}
   with patch.dict(os.environ,hostile),patch.object(c.subprocess,'check_output',side_effect=query):env=c.cli_environment(built)
   self.assertEqual(env['SDKROOT'],r['sdk_path']);self.assertEqual(env['CC'],str(bindir/'clang'));self.assertEqual(env['AR'],str(bindir/'ar'));self.assertEqual(env['RANLIB'],str(bindir/'ranlib'))
   self.assertNotEqual(env['RANLIB'],str((bindir/'ranlib').resolve()))
   for key,value in r['build_environment'].items():self.assertEqual(env[key],value)
   for key in ['CPATH','LIBRARY_PATH','MAKEFLAGS','PERL5OPT','OPENSSL_CONF','DYLD_INSERT_LIBRARIES']:self.assertNotIn(key,env)
   self.assertEqual(env['PATH'],str(bindir)+':/usr/bin:/bin:/usr/sbin:/sbin')
 def test_selected_sdk_and_version_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   built,r,query,bindir=self.fixture(d);other=pathlib.Path(d)/'other-sdk';other.mkdir()
   for flag,value in [('--show-sdk-path',str(other)),('--show-sdk-version','99.0')]:
    def changed(argv,**kwargs):return value if argv[-1]==flag else query(argv,**kwargs)
    with patch.object(c.subprocess,'check_output',side_effect=changed),self.assertRaisesRegex(ValueError,'selected SDK'):c.cli_environment(built)
 def test_changed_tool_bytes_alias_or_selection_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   built,r,query,bindir=self.fixture(d);clang=bindir/'clang';original=clang.read_bytes();clang.write_text('changed executable')
   with patch.object(c.subprocess,'check_output',side_effect=query),self.assertRaisesRegex(ValueError,'tool identity'):c.cli_environment(built)
   clang.write_bytes(original)
   (bindir/'wrong-clang').write_bytes(original)
   def changed(argv,**kwargs):return str(bindir/'wrong-clang') if argv[-2:]==['--find','clang'] else query(argv,**kwargs)
   with patch.object(c.subprocess,'check_output',side_effect=changed),self.assertRaisesRegex(ValueError,'selected tool'):c.cli_environment(built)
   (bindir/'samebytes').write_bytes((bindir/'libtool').read_bytes());(bindir/'ranlib').unlink();(bindir/'ranlib').symlink_to(bindir/'samebytes')
   with patch.object(c.subprocess,'check_output',side_effect=query),self.assertRaisesRegex(ValueError,'tool identity'):c.cli_environment(built)
 def test_recipe_or_unknown_environment_fields_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   built,r,query,bindir=self.fixture(d)
   for field,value in [('recipe_sha256','0'*64),('build_environment',dict(r['build_environment'],SDKROOT='/arbitrary')),('build_environment',dict(r['build_environment'],CFLAGS='-include /arbitrary/path')),('build_environment',dict(r['build_environment'],LDFLAGS='-Wl,-load,/arbitrary/plugin')),('minimum_os','99.0'),('architecture','unsupported'),('optimization','injected')]:
    changed=copy.deepcopy(r);changed[field]=value;(built/'build-receipt.json').write_text(json.dumps(changed))
    with patch.object(c.subprocess,'check_output',side_effect=query),self.assertRaises(ValueError):c.cli_environment(built)
 def test_same_validated_environment_reaches_all_three_commands(self):
  import sys
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d).resolve(strict=True);mini=root/'SideStore/Dependencies/minimuxer';mini.mkdir(parents=True);evidence=root/'evidence';evidence.mkdir();cli=root/'openssl';cli.write_text('synthetic nonexecuted CLI')
   validated={'SDKROOT':'/qualified/sdk','CC':'/qualified/clang','PATH':'/qualified/bin','CFLAGS':'qualified flags'};seen=[]
   def make(built,ev,env,timeout):seen.append(dict(env));return cli
   def execute(argv,cwd,env,timeout=600):
    seen.append(dict(env))
    if '--output' in argv:
     self.assertEqual(argv[0],sys.executable)
     pathlib.Path(argv[argv.index('--output')+1]).write_text(json.dumps(CMSControls().cms()));return '', ''
    self.assertEqual(argv[-2:],['--python',sys.executable])
    return json.dumps(CMSControls().public()), ''
   with patch.object(c,'sources',return_value={}),patch.object(c,'host_build',return_value=(root,{})),patch.object(c,'cli_source',return_value=root),patch.object(c,'cli_environment',return_value=dict(validated)),patch.object(c,'build_cli',side_effect=make),patch.object(c,'execute',side_effect=execute),patch.object(c,'verify'),patch.object(c.shutil,'which',return_value=sys.executable),patch.object(c,'sha',return_value='1'*64),patch('builtins.print'):
    # Preserve final path check while keeping all execution mocked.
    apps=root/'apps';apps.mkdir();cli.rename(apps/'openssl');cli=apps/'openssl'
    c.run(root,root,evidence,{}, {'workflow_commit':'a'*40,'run_id':'1','run_attempt':'1'})
   self.assertEqual(len(seen),3)
   for env in seen:
    for key,value in validated.items():self.assertEqual(env[key],value)
    self.assertEqual(env['CMS_REQUIRE_QUALIFIED'],'1')
   self.assertEqual(seen[0],seen[1]);self.assertEqual(seen[1],seen[2])
 def test_harness_python_binding_rejects_missing_relative_nonexecutable(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'nonexecutable';p.write_text('not executable')
   for candidate in ['', 'python3', str(pathlib.Path(d)/'missing'), str(p),d]:
    with patch.object(c.sys,'executable',candidate),self.assertRaisesRegex(ValueError,'harness interpreter'):c.qualification_python()
 def test_harness_python_preserves_valid_invocation_symlink(self):
  import sys
  with tempfile.TemporaryDirectory() as d:
   alias=pathlib.Path(d).resolve(strict=True)/'python-invocation';alias.symlink_to(pathlib.Path(sys.executable).resolve(strict=True))
   with patch.object(c.sys,'executable',str(alias)),patch.dict(c.os.environ,{'PATH':'/arbitrary/unrelated/path'}):
    self.assertEqual(c.qualification_python(),str(alias))
 def test_portable_receipt_rejects_changed_host_recipe(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'sources',return_value={'source':'d'*64}):
   path,receipt,identity=CMSControls().fixture(d);hostpath=pathlib.Path(d)/'openssl-host-build-receipt.json';host=c.read(hostpath);host['recipe_sha256']='0'*64;hostpath.write_text(json.dumps(host))
   receipt['crypto']['build_receipt_sha256']=c.sha(hostpath);path.write_text(json.dumps(receipt))
   with self.assertRaisesRegex(ValueError,'host recipe differs'):c.verify(path,d,{},identity)
 def test_cli_only_consumes_environment_after_same_run_host_validation(self):
  source=(HERE/'preservation_cms_receipt.py').read_text()
  self.assertIn('env=cli_environment(built)',source)
  self.assertIn('cli=build_cli(built,evidence,env,timeout=300)',source)
  self.assertIn('],mini,env,evidence)',source)
  self.assertIn("],root/'SideStore',env,evidence)",source)
  self.assertIn("shutil.which(name,path=env['PATH'])",source)
  self.assertLess(source.index('prefix,crypto=host_build(built,evidence)'),source.index('cli=build_cli('))
  self.assertIn("require(host_build(built,evidence)[1]==crypto,'CLI build changed qualified crypto inputs')",source)
  self.assertIn('env=module.sanitized_environment()',source)
class FiniteFailureDiagnostics(unittest.TestCase):
 def test_known_assertion_and_last_case_are_distinct(self):
  cms=c.CMS_CASES[0];public=c.PUBLIC_CASES[0]
  r=c.finite_stage_failure('syntheticC','failed',1,'AssertionError: '+cms)
  self.assertEqual(r['confirmed_failed_case'],cms);self.assertIsNone(r['last_case'])
  r=c.finite_stage_failure('publicSwift','failed',-6,'V3_PUBLIC_CASE_BEGIN '+public+'\nmain/main.swift:10: Fatal error: unexpected acceptance: '+c.PUBLIC_CASES[3])
  self.assertEqual(r['last_case'],public);self.assertEqual(r['confirmed_failed_case'],c.PUBLIC_CASES[3])
 def test_marker_without_assertion_does_not_claim_failed_case(self):
  r=c.finite_stage_failure('publicSwift','failed',1,'V3_PUBLIC_CASE_BEGIN '+c.PUBLIC_CASES[1]+'\nError: secret profile message')
  self.assertEqual(r['last_case'],c.PUBLIC_CASES[1]);self.assertIsNone(r['confirmed_failed_case'])
 def test_hostile_data_and_unknown_ids_never_appear(self):
  hostile='-----BEGIN PRIVATE KEY-----\nsecret-token-abc\n<plist>personal-profile</plist>\n'
  stderr=hostile+'AssertionError: unknown-private-case\nV3_PUBLIC_CASE_BEGIN secret-token-abc\n/private/owner/profile/main.swift:12:4: error: '+hostile+'\n/private/token/unknown.swift:7:8: error: secret'
  for phase in ['syntheticC','publicSwift']:
   r=c.finite_stage_failure(phase,'failed',1,stderr);encoded=json.dumps(r)
   for secret in ['PRIVATE KEY','secret','personal-profile','/private','unknown.swift','owner']:self.assertNotIn(secret,encoded)
   self.assertIsNone(r['confirmed_failed_case']);self.assertIsNone(r['last_case'])
   self.assertEqual(r['compiler_locations'],[{'file':'main.swift','line':12,'column':4}])
 def test_cross_phase_or_nonexact_cases_ignored(self):
  text='AssertionError: '+c.CMS_CASES[0]+' extra-private-data\nV3_PUBLIC_CASE_BEGIN '+c.PUBLIC_CASES[0]+' extra-private-data\nAssertionError: '+c.PUBLIC_CASES[0]
  for phase in ['syntheticC','publicSwift']:
   r=c.finite_stage_failure(phase,'failed',1,text);self.assertIsNone(r['confirmed_failed_case']);self.assertIsNone(r['last_case'])
 def test_report_and_parsing_bounds(self):
  text='private'*100000+'\n'+''.join('/private/main.swift:'+str(i)+':2: error: secret\n' for i in range(1,100))+'main.swift:9999999:2: error: secret\n'
  r=c.finite_stage_failure('publicSwift','failed',1,text);self.assertEqual(len(r['compiler_locations']),8);self.assertLess(len(json.dumps(r)),4096)
  self.assertNotIn('private',json.dumps(r));self.assertNotIn('secret',json.dumps(r))
 def test_truncation_cannot_fabricate_exact_case_lines(self):
  for phase,marker in [('syntheticC','AssertionError: '+c.CMS_CASES[0]),('publicSwift','V3_PUBLIC_CASE_BEGIN '+c.PUBLIC_CASES[0])]:
   head='x'*(65536-len(marker)-1)+'\n'+marker+' extra-private-data'+'x'*140000
   tail='x'*140000+'private-prefix-'+marker+'\n'+'x'*(65536-len(marker)-1)
   for text in [head,tail]:
    r=c.finite_stage_failure(phase,'failed',1,text)
    self.assertIsNone(r['confirmed_failed_case']);self.assertIsNone(r['last_case'])
 def test_failure_and_timeout_rethrown_without_raw_output(self):
  import subprocess
  for error in [c.QualificationCommandFailure(7,'private stdout','AssertionError: '+c.CMS_CASES[2]),subprocess.TimeoutExpired(['synthetic'],2,output=b'private stdout',stderr=b'private stderr')]:
   with tempfile.TemporaryDirectory() as d,patch.object(c,'execute',side_effect=error):
    with self.assertRaises(type(error)) as caught:c.execute_stage('syntheticC',['synthetic'],d,{},d)
    self.assertIs(caught.exception,error);r=c.read(pathlib.Path(d)/c.FAILURE_DIAGNOSTIC)
    self.assertEqual(r['status'],'timedOut' if isinstance(error,subprocess.TimeoutExpired) else 'failed')
    self.assertFalse(r['raw_output_retained']);self.assertNotIn('private',json.dumps(r))
 def test_success_emits_no_failure_diagnostic(self):
  with tempfile.TemporaryDirectory() as d,patch.object(c,'execute',return_value=('finite-success','')):
   self.assertEqual(c.execute_stage('publicSwift',['synthetic'],d,{},d),('finite-success',''));self.assertEqual(list(pathlib.Path(d).iterdir()),[])
  for phase,status,code in [('unknown','failed',1),('publicSwift','passed',0),('publicSwift','failed',0),('publicSwift','failed',True)]:
   with self.assertRaises(ValueError):c.finite_stage_failure(phase,status,code,'')
 def test_existing_or_linked_diagnostic_cannot_mask_original_failure(self):
  for linked in [False,True]:
   with tempfile.TemporaryDirectory() as d:
    root=pathlib.Path(d);other=root/'other';other.write_text('untouched');destination=root/c.FAILURE_DIAGNOSTIC
    if linked:destination.symlink_to(other)
    else:destination.write_text('untouched')
    error=c.QualificationCommandFailure(2,'private','private')
    with patch.object(c,'execute',side_effect=error),self.assertRaises(c.QualificationCommandFailure) as caught:c.execute_stage('publicSwift',[],d,{},d)
    self.assertIs(caught.exception,error);self.assertEqual(destination.read_text(),'untouched');self.assertEqual(other.read_text(),'untouched')
if __name__=='__main__':unittest.main()
