import importlib.util,json,tempfile,unittest,subprocess,hashlib
from pathlib import Path
from unittest import mock
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('cold_runner',HERE/'run_cold_vpn_tests.py');R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
class Controls(unittest.TestCase):
 def test_native_skips_failclosed(self):
  R.admit_log(0,'Ran 3 tests\n\nOK\n')
  for code,text in [(1,'OK\n'),(0,'OK (skipped=1)\n'),(0,'OK (expected failures=1)\n'),(0,'FAILED\n'),(0,'')]:
   with self.assertRaises(ValueError):R.admit_log(code,text)
 def test_three_owned_entrypoints_and_mandatory_envs(self):
  self.assertEqual(set(R.SUITES),{'host','backend','transport'})
  self.assertTrue(all(row[2].startswith('REQUIRE_SWIFT_') for row in R.SUITES.values()))
  source=(HERE/'run_cold_vpn_tests.py').read_text()
  self.assertIn("env:'1'",source);self.assertIn('source_hashes(sources,graph)==before',source)
 def test_receipt_rejects_wrong_identity_source_log_and_skips(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();(root/'cold-vpn').mkdir();p=root/'cold-vpn-tests.json'
   for name in R.SUITES:(root/'cold-vpn'/(name+'.log')).write_text('Ran 2 tests\nOK\n')
   for name in ['historical-vpn.log','historical-vpn-sources.json','historical-vpn-cases.json']:(root/'cold-vpn'/name).write_text('VPN_REFRESH_RECOVERY_PASS cases=97\n' if name.endswith('.log') else '{}')
   ident={'workflow_commit':'a'*40,'run_id':'1','run_attempt':'1'}
   data={'schema':'cold-vpn-source-regressions-v1','status':'PASS_SOURCE_EXECUTION_NOT_DEVICE_VPN','run_identity':ident,'source_graph':{'owner':['repo','a'*40]},'source_owner_inventory_sha256':{'owner':'b'*64},'physical_device_execution':False,'real_vpn_execution':False,'historical_vpn_sha256':{n:R.sha(root/'cold-vpn'/n) for n in ['historical-vpn.log','historical-vpn-sources.json','historical-vpn-cases.json']},'suite_log_sha256':{n:R.sha(root/'cold-vpn'/(n+'.log')) for n in R.SUITES}}
   with mock.patch.object(R,'load_candidate_graph',return_value={'owners':data['source_graph']}),mock.patch.object(R,'source_hashes',return_value=data['source_owner_inventory_sha256']):
    p.write_text(json.dumps(data));R.verify_receipt(p,root,ident)
    for key,value in [('status','FAILED'),('run_identity',{}),('source_graph',{}),('source_owner_inventory_sha256',{}),('suite_log_sha256',{}),('real_vpn_execution',True)]:
     p.write_text(json.dumps({**data,key:value}))
     with self.assertRaises(ValueError):R.verify_receipt(p,root,ident)
    p.write_text(json.dumps(data));(root/'cold-vpn/host.log').write_text('OK (skipped=1)\n')
    with self.assertRaises(ValueError):R.verify_receipt(p,root,ident)
    data['suite_log_sha256']['host']=R.sha(root/'cold-vpn/host.log');p.write_text(json.dumps(data))
    with self.assertRaises(ValueError):R.verify_receipt(p,root,ident)
 def test_exact_reviewed_lock_transition_only(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();repo=root/'repo';repo.mkdir();evidence=root/'evidence';evidence.mkdir()
   def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
   git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
   before={'version':3,'originHash':'1'*64,'pins':[{'identity':'anisettekit','state':{'revision':'a'*40}}]}
   lock=repo/R.APP_LOCK;lock.parent.mkdir(parents=True);lock.write_text(json.dumps(before))
   (repo/'helper.swift').write_text('original source')
   git('add','.');git('commit','-qm','owned fixture');commit=git('rev-parse','HEAD')
   graph={'owners':{'AnisetteKit':['repo','a'*40],'SideStore':['repo',commit]}}
   raw=lock.read_bytes();baseline=R.lock_hash(repo,raw,graph,None)
   after={**before,'originHash':'2'*64};changed=json.dumps(after).encode();lock.write_bytes(changed)
   transition={'status':'OBSERVED_RESOLVER_METADATA_ONLY','source_commit':commit,'working_checkout_has_uncommitted_app_lock':True,'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(changed).hexdigest(),'before_originHash':'1'*64,'after_originHash':'2'*64,'pins_changed':[],'only_allowed_metadata_change':'originHash','new_source_commit_published':False}
   (evidence/'SideStore.Package.resolved.before').write_bytes(raw);(evidence/'SideStore.Package.resolved.after').write_bytes(changed);(evidence/'app-lock-transition.json').write_text(json.dumps(transition))
   ident={'workflow_commit':'b'*40,'run_id':'1','run_attempt':'1'}
   (evidence/'cold-vpn-tests.json').write_text(json.dumps({'run_identity':ident}))
   native={**ident,'evidence_sha256':{p.name:R.sha(p) for p in evidence.iterdir()}}
   (evidence/'receipt.json').write_text(json.dumps(native))
   self.assertEqual(R.lock_hash(repo,changed,graph,evidence),baseline)
   with mock.patch.object(R,'SUITES',{'backend':('repo','unused','REQUIRE_SWIFT_FIXTURE')}):
    owner_graph={'owners':{'repo':['repo',commit]}}
    inventory=R.source_hashes(root,owner_graph)
    (repo/'helper.swift').write_text('unrelated mutation')
    self.assertNotEqual(R.source_hashes(root,owner_graph),inventory)
    (repo/'helper.swift').write_text('original source')
   with self.assertRaises(ValueError):R.lock_hash(repo,changed,graph,None)
   for altered in [{**after,'version':4},{**after,'pins':[{'identity':'anisettekit','state':{'revision':'c'*40}}]},{**after,'originHash':True}]:
    with self.assertRaises(ValueError):R.admit_lock_transition(raw,json.dumps(altered).encode(),transition,graph)
   (evidence/'SideStore.Package.resolved.after').write_bytes(raw)
   with self.assertRaises(ValueError):R.lock_hash(repo,changed,graph,evidence)
 def test_historical_fixture_executes_not_just_generates(self):
  source=(HERE/'run_cold_vpn_tests.py').read_text()
  self.assertIn("['xcrun','swiftc'",source)
  self.assertIn("[root/'fixture',generated/'cases.json']",source)
  self.assertIn("report['historical_vpn_sha256']=historical_vpn(sources,work)",source)
  self.assertIn("cases['cases']==definition['expected_case_names']",source)
 def test_workflow_retains_prior_gates_and_new_failure_barrier(self):
  root=HERE.parents[1];native=(root/'.github/workflows/cold-vpn-native.yml').read_text()
  for value in ['run_language_switch_tests.py','run_startup_tests.py','russian_tests','minimuxer_tests','--same-run-qualified-size --language-switch --cold-vpn',"steps.cold_vpn_tests.outcome == 'failure'"]:self.assertIn(value,native)
  focused=(root/'.github/workflows/cold-vpn-preflight.yml').read_text();self.assertIn('verify_candidate_graph.py',focused);self.assertIn('run_cold_vpn_tests.py',focused);self.assertNotIn('package_candidate.py',focused)
 def test_packaging_requires_receipt_and_original_gates(self):
  root=HERE.parents[1];source=(root/'ci/issues-native/package_candidate.py').read_text()
  self.assertIn('if not (same_run_qualified and language_switch):',source)
  self.assertEqual(source.count('verify_cold_vpn_receipt(cold_vpn_receipt'),2)
  self.assertIn("provenance['cold_vpn_regression_sha256']",source)
  for name in ['acquire.py','independent_verify.py']:
   text=(root/'ci/cold-vpn-zopfli'/name).read_text();self.assertIn('cold_vpn_regression_sha256',text);self.assertIn('language_switch_regression_sha256',text)
  manifest=json.loads((root/'ci/cold-vpn-zopfli/input-manifest.json').read_text());self.assertEqual(manifest['schema'],'cold-vpn-green-native-input-v1')
if __name__=='__main__':unittest.main(verbosity=2)
