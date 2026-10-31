"""Synthetic contract controls only; never claims native CMS execution."""
import copy, json, pathlib, tempfile, unittest
from unittest.mock import patch
import preservation_cms_receipt as c
HERE=pathlib.Path(__file__).resolve().parent
class CMSControls(unittest.TestCase):
 def cms(self):return {'schema':'profile-cms-native-v1','openssl':'3.5.9','qualified':True,'time_policy':'inner-current-system-time;legacy-no-check-time','tested_at_utc':'2026-10-31T18:00:00+00:00','checks':list(c.CMS_CASES),'count':28,'status':'passed'}
 def public(self):return {'schema':'preservation-der-public-v1','status':'PASS','cases':list(c.PUBLIC_CASES),'cms_verified':True,'device_executed':False}
 def test_exact_actual_case_contracts(self):
  c.validate_cms_report(self.cms());c.validate_public_log(json.dumps(self.public()))
  self.assertEqual(len(c.CMS_CASES),28);self.assertEqual(len(c.PUBLIC_CASES),16)
 def test_development_or_other_openssl_rejected(self):
  for k,v in [('openssl','3.5.7'),('qualified',False),('qualified',1),('status','skipped'),('time_policy','ignore-all-time'),('tested_at_utc','raw-profile')]:
   r=self.cms();r[k]=v
   with self.subTest(k=k,v=v),self.assertRaises(ValueError):c.validate_cms_report(r)
 def test_cms_count_type_or_incomplete_cases_rejected(self):
  for v in [27,28.0,True,'28']:
   r=self.cms();r['count']=v
   with self.subTest(v=v),self.assertRaises(ValueError):c.validate_cms_report(r)
  for rows in [list(c.CMS_CASES)[:-1],list(reversed(c.CMS_CASES)),list(c.CMS_CASES)+[c.CMS_CASES[0]],['fake']*28]:
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
  host={'source':c.read(HERE.parent/'issue53-lts/source.json'),'static_archives_sha256':{'/exact/install/lib/libcrypto.a':'b'*64},'header_inventory_sha256':'c'*64}
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
if __name__=='__main__':unittest.main()
