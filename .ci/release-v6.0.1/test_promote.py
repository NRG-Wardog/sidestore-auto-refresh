import copy, hashlib, io, json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import promote as p
SHA='a'*40
HASH='b'*64
class Gates(unittest.TestCase):
    def config(self): return json.loads((p.ROOT/'reviewed-inputs.json').read_text())
    def run(self, result=None):
        # Preserve unittest.TestCase.run; fixture is named successful_run below.
        return super().run(result)
    def successful_run(self):
        e=self.config()['package'];r=dict(e,repository={'full_name':p.REPO},head_repository={'full_name':p.REPO},status='completed',conclusion='success',head_commit={'tree_id':e['tree_id']});return e,r
    def fixture(self, changes=None):
        c=self.config();ipa=b'reviewed IPA fixture';c['ipa_size']=len(ipa);c['ipa_sha256']=p.digest(ipa)
        rows={n:b'{}' for n in p.SOURCE_FILES};rows['recompressed.ipa']=ipa
        rows['acquisition.json']=p.json_bytes({'packaging_identity':{'GITHUB_SHA':c['package']['head_sha'],'GITHUB_RUN_ID':str(c['package']['id']),'GITHUB_RUN_ATTEMPT':str(c['package']['run_attempt'])}})
        rows['input-manifest.json']=p.json_bytes({'commit':c['native']['head_sha'],'run_id':c['native']['id'],'run_attempt':c['native']['run_attempt'],'ipa_sha256':c['native_ipa_sha256'],'ipa_bytes':c['native_ipa_size'],'entries':274})
        rows['provenance.json']=p.json_bytes({'workflow_commit':c['native']['head_sha'],'native_run_id':str(c['native']['id']),'native_run_attempt':str(c['native']['run_attempt'])})
        rows['independent-audit.json']=p.json_bytes({'status':'PASS_IDENTICAL_PAYLOAD_AND_ARCHIVE_METADATA','all_payload_bytes_identical':True,'all_binary_signature_bytes_identical':True,'all_archive_metadata_preserved_except_compressed_sizes_and_offsets':True,'final_bytes':len(ipa),'final_sha256':p.digest(ipa)})
        rows['result.json']=p.json_bytes({'final_bytes':len(ipa),'final_sha256':p.digest(ipa)});rows['SHA256SUMS']=(p.digest(ipa)+'  recompressed.ipa\n').encode()
        if changes: changes(rows,c)
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for n,v in rows.items():z.writestr(n,v)
        raw=b.getvalue();c['artifact']['size_in_bytes']=len(raw);c['artifact']['digest']='sha256:'+p.digest(raw);return c,raw
    def extract(self,c,raw):
        with tempfile.TemporaryDirectory() as t:
            a=Path(t)/'archive.zip';o=Path(t)/'out.ipa';a.write_bytes(raw);p.extract_ipa(a,o,c);return o.read_bytes()
    def release_fixture(self):
        inv={'release_target':SHA,'files':{n:{'size':3,'sha256':HASH} for n in p.PUBLIC_ASSETS}}
        r={'id':123,'tag_name':p.TAG,'target_commitish':SHA,'name':p.TITLE,'body':'reviewed notes','draft':True,'prerelease':False,'assets':[{'name':n,'size':3,'digest':'sha256:'+HASH,'state':'uploaded'} for n in p.PUBLIC_ASSETS]};return inv,r
    def test_exact_run_passes(self):e,r=self.successful_run();p.verify_run(r,e)
    def test_failed_or_running_ci_rejected(self):
        e,r=self.successful_run()
        for k,v in [('status','in_progress'),('conclusion','failure'),('conclusion','cancelled')]:
            x=copy.deepcopy(r);x[k]=v
            with self.subTest(k=k,v=v),self.assertRaises(ValueError):p.verify_run(x,e)
    def test_all_run_identity_fields_pinned(self):
        e,r=self.successful_run()
        for k in ('id','head_sha','head_branch','workflow_id','path','run_attempt'):
            x=copy.deepcopy(r);x[k]='wrong'
            with self.subTest(k=k),self.assertRaises(ValueError):p.verify_run(x,e)
    def test_run_tree_and_repositories_pinned(self):
        e,r=self.successful_run()
        for k in ('repository','head_repository','head_commit'):
            x=copy.deepcopy(r);x[k]={}
            with self.subTest(k=k),self.assertRaises(ValueError):p.verify_run(x,e)
    def test_valid_artifact(self):
        c=self.config();a=dict(c['artifact'],expired=False,workflow_run={'id':c['package']['id'],'head_sha':c['package']['head_sha'],'head_branch':c['package']['head_branch'],'repository_id':p.REPOSITORY_ID,'head_repository_id':p.REPOSITORY_ID});p.verify_artifact(a,c)
    def test_artifact_drift(self):
        c=self.config();a=dict(c['artifact'],expired=False,workflow_run={'id':c['package']['id'],'head_sha':c['package']['head_sha'],'head_branch':c['package']['head_branch'],'repository_id':p.REPOSITORY_ID,'head_repository_id':p.REPOSITORY_ID})
        for k in ('id','name','size_in_bytes','digest','expired','workflow_run'):
            x=copy.deepcopy(a);x[k]=None
            with self.subTest(k=k),self.assertRaises(ValueError):p.verify_artifact(x,c)
    def test_exact_fixture_extracts_without_execution(self):
        c,raw=self.fixture()
        with patch('subprocess.run',side_effect=AssertionError('artifact execution forbidden')):self.assertEqual(self.extract(c,raw),b'reviewed IPA fixture')
    def test_archive_hash_tamper_rejected(self):
        c,raw=self.fixture();c['artifact']['digest']='sha256:'+'0'*64
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_archive_size_tamper_rejected(self):
        c,raw=self.fixture();c['artifact']['size_in_bytes']+=1
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_extra_and_missing_files_rejected(self):
        for change in (lambda r,c:r.update({'extra.py':b'raise RuntimeError()'}),lambda r,c:r.pop('result.json')):
            c,raw=self.fixture(change)
            with self.assertRaises(ValueError):self.extract(c,raw)
    def test_ipa_payload_tamper_rejected(self):
        c,raw=self.fixture(lambda r,c:r.update({'recompressed.ipa':b'wrong'}))
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_wrong_package_receipt_rejected(self):
        c,raw=self.fixture(lambda r,c:r.update({'acquisition.json':b'{"packaging_identity":{}}'}))
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_wrong_native_receipt_rejected(self):
        def change(r,c):
            v=json.loads(r['input-manifest.json']);v['commit']='0'*40;r['input-manifest.json']=p.json_bytes(v)
        c,raw=self.fixture(change)
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_wrong_native_provenance_rejected(self):
        def change(r,c):
            v=json.loads(r['provenance.json']);v['native_run_attempt']='2';r['provenance.json']=p.json_bytes(v)
        c,raw=self.fixture(change)
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_failed_audit_rejected(self):
        def change(r,c):
            v=json.loads(r['independent-audit.json']);v['status']='FAILED';r['independent-audit.json']=p.json_bytes(v)
        c,raw=self.fixture(change)
        with self.assertRaises(ValueError):self.extract(c,raw)
    def test_false_preservation_rejected(self):
        for key in ('all_payload_bytes_identical','all_binary_signature_bytes_identical','all_archive_metadata_preserved_except_compressed_sizes_and_offsets'):
            def change(r,c):
                v=json.loads(r['independent-audit.json']);v[key]=False;r['independent-audit.json']=p.json_bytes(v)
            c,raw=self.fixture(change)
            with self.subTest(key=key),self.assertRaises(ValueError):self.extract(c,raw)
    def test_result_and_checksum_tamper_rejected(self):
        for name in ('result.json','SHA256SUMS'):
            def change(r,c):
                if name=='SHA256SUMS':r[name]=b'wrong wrong'
                else:r[name]=p.json_bytes({'final_bytes':0,'final_sha256':'0'*64})
            c,raw=self.fixture(change)
            with self.subTest(name=name),self.assertRaises(ValueError):self.extract(c,raw)
    def test_duplicate_json_rejected(self):
        with self.assertRaises(ValueError):p.read_json('{"a":1,"a":2}')
    def test_exact_release_passes(self):
        inv,r=self.release_fixture();self.assertEqual(p.validate_release(r,inv,'reviewed notes',True,True),set(p.PUBLIC_ASSETS))
    def test_release_metadata_drift_rejected(self):
        inv,r=self.release_fixture()
        for key in ('tag_name','target_commitish','name','body','draft','prerelease'):
            x=copy.deepcopy(r);x[key]=None
            with self.subTest(key=key),self.assertRaises(ValueError):p.validate_release(x,inv,'reviewed notes',True,True)
    def test_release_asset_drift_rejected(self):
        inv,r=self.release_fixture()
        for key in ('size','digest','state','name'):
            x=copy.deepcopy(r);x['assets'][0][key]=None
            with self.subTest(key=key),self.assertRaises(ValueError):p.validate_release(x,inv,'reviewed notes',True,True)
    def test_duplicate_asset_rejected(self):
        inv,r=self.release_fixture();r['assets'].append(r['assets'][0])
        with self.assertRaises(ValueError):p.validate_release(r,inv,'reviewed notes',True,False)
    def test_incomplete_release_never_published(self):
        inv,r=self.release_fixture();r['assets']=[];self.assertEqual(p.validate_release(r,inv,'reviewed notes',True,False),set())
        with self.assertRaises(ValueError):p.validate_release(r,inv,'reviewed notes',True,True)
    def test_release_lookup_ambiguous_rejected(self):
        with patch.object(p,'gh_json',return_value=[{'tag_name':p.TAG},{'tag_name':p.TAG}]),self.assertRaises(ValueError):p.lookup_release()
    def test_release_lookup_ignores_prior_version(self):
        with patch.object(p,'gh_json',return_value=[{'tag_name':'v6.0.0'}]):self.assertIsNone(p.lookup_release())
    def test_tag_resolves_only_commit_or_bounded_annotations(self):
        with patch.object(p,'gh_json',return_value={'object':{'type':'commit','sha':SHA}}):self.assertEqual(p.tag_commit(),SHA)
        with patch.object(p,'gh_json',return_value={'object':{'type':'blob','sha':SHA}}),self.assertRaises(ValueError):p.tag_commit()
    def test_context_restricts_branch_repo_event(self):
        env={'GITHUB_REPOSITORY':p.REPO,'GITHUB_REF':p.BRANCH,'GITHUB_EVENT_NAME':'push','GITHUB_SHA':SHA}
        with patch.dict(os.environ,env,clear=True):self.assertEqual(p.context(),SHA)
        for key in env:
            with patch.dict(os.environ,dict(env,**{key:'wrong'}),clear=True),self.subTest(key=key),self.assertRaises(ValueError):p.context()
    def test_main_and_candidate_ancestry_required(self):
        c=self.config();c['publication_target']=SHA;c['publication_tree']='e'*40
        policy={'target_commit':SHA,'target_tree':'e'*40}
        good=[{'object':{'type':'commit','sha':SHA}},{'sha':SHA,'tree':{'sha':'e'*40}},{},{},{'status':'ahead','base_commit':{'sha':SHA},'merge_base_commit':{'sha':SHA}},{'status':'ahead','merge_base_commit':{'sha':c['package']['head_sha']}}]
        with patch.object(p,'load_policy',return_value=policy),patch.object(p,'verify_delta') as delta,patch.object(p,'gh_json',side_effect=good):p.verify_target(c,'c'*40);delta.assert_called_once()
        for index in (0,1,4,5):
            bad=copy.deepcopy(good)
            if index==0:bad[index]['object']['sha']='d'*40
            elif index==1:bad[index]['tree']['sha']='d'*40
            else:bad[index]['status']='diverged'
            with patch.object(p,'load_policy',return_value=policy),patch.object(p,'verify_delta'),patch.object(p,'gh_json',side_effect=bad),self.subTest(index=index),self.assertRaises(ValueError):p.verify_target(c,'c'*40)
    def test_workflow_permissions_separated_and_pinned(self):
        w=(p.ROOT.parents[1]/'.github/workflows/publish-v6.0.1.yml').read_text()
        self.assertIn('permissions: {}',w);self.assertEqual(w.count('contents: write'),1);self.assertIn('needs: verify',w);self.assertNotIn('pull_request_target',w);self.assertNotIn('release/**',w)
        for uses in [line.split('uses:',1)[1].strip() for line in w.splitlines() if 'uses:' in line]:self.assertRegex(uses,r'@[0-9a-f]{40}$')
    def test_settings_reject_unbound_target_or_verify_bound(self):
        import successor_contract
        if self.config()['publication_target'].startswith('REPLACE_') or successor_contract.POLICY_SHA256.startswith('UNBOUND'):
            with self.assertRaisesRegex(ValueError,'Unbound'):p.settings()
        else:
            p.settings()
            for field,value in [('id',0),('head_sha','0'*40),('tree_id','0'*40),('head_branch','other')]:
                with tempfile.TemporaryDirectory() as t:
                    import shutil
                    root=Path(t)/'metadata';shutil.copytree(p.ROOT,root,ignore=shutil.ignore_patterns('__pycache__'))
                    c=self.config();c['main_qualification'][0][field]=value;(root/'reviewed-inputs.json').write_text(json.dumps(c))
                    with patch.object(p,'ROOT',root),self.subTest(field=field),self.assertRaises(ValueError):p.settings()
class PublicationTransactions(unittest.TestCase):
    config = Gates.config
    release_fixture = Gates.release_fixture
    def test_published_exact_release_is_read_only(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture();r['draft']=False;r['html_url']='https://github.com/'+p.REPO+'/releases/tag/'+p.TAG
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',return_value=SHA),patch.object(p,'lookup_release',return_value=r),patch.object(p,'gh_json',return_value={'id':123}) as api,patch('subprocess.run') as command:
                p.publish(t,HASH);self.assertEqual(api.call_count,1);command.assert_not_called()
    def test_uncertain_missing_upload_stops_without_retry(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture();r['assets']=[]
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',return_value=None),patch.object(p,'lookup_release',return_value=r),patch.object(p,'gh_json',return_value=r) as api,patch('subprocess.run') as command:
                with self.assertRaisesRegex(ValueError,'Upload not verified'):p.publish(t,HASH)
                self.assertEqual(command.call_count,1);self.assertEqual(api.call_count,1)
    def test_uncertain_create_reconciles_without_second_create(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture()
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',return_value=None),patch.object(p,'lookup_release',side_effect=[None,None]) as lookup,patch.object(p,'gh_json',side_effect=ValueError('uncertain')) as api:
                with self.assertRaises(ValueError):p.publish(t,HASH)
                self.assertEqual(api.call_count,1);self.assertEqual(lookup.call_count,2)
    def test_uncertain_publish_observed_once_no_retry(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture();published=dict(r,draft=False,html_url='https://github.com/'+p.REPO+'/releases/tag/'+p.TAG)
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',side_effect=[None,None,SHA]),patch.object(p,'lookup_release',return_value=r),patch.object(p,'gh_json',side_effect=[r,ValueError('uncertain PATCH'),published,published,{'id':123}]) as api:
                p.publish(t,HASH)
                self.assertEqual(sum(call.args[1:2]==('PATCH',) for call in api.call_args_list),1)
    def test_uncertain_create_success_reconciles_same_draft(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture();published=dict(r,draft=False,html_url='https://github.com/'+p.REPO+'/releases/tag/'+p.TAG)
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',side_effect=[None,None,SHA]),patch.object(p,'lookup_release',side_effect=[None,r]) as lookup,patch.object(p,'gh_json',side_effect=[ValueError('uncertain POST'),r,published,published,{'id':123}]) as api:
                p.publish(t,HASH)
                self.assertEqual(sum(call.args[1:2]==('POST',) for call in api.call_args_list),1);self.assertEqual(lookup.call_count,2)
    def test_uncertain_upload_observed_success_no_duplicate(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture();initial=copy.deepcopy(r);initial['assets']=[];published=dict(r,draft=False,html_url='https://github.com/'+p.REPO+'/releases/tag/'+p.TAG)
        observations=[]
        for count in (1,2,3):
            item=copy.deepcopy(r);item['assets']=item['assets'][:count];observations.append(item)
        observations += [r,published,published,{'id':123}]
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',side_effect=[None,None,SHA]),patch.object(p,'lookup_release',return_value=initial),patch.object(p,'gh_json',side_effect=observations),patch('subprocess.run',return_value=type('Result',(),{'returncode':1})()) as uploads:
                p.publish(t,HASH);self.assertEqual(uploads.call_count,3)
                self.assertEqual(len({call.args[0][-1] for call in uploads.call_args_list}),3)
    def test_tag_race_blocks_patch_after_uploads(self):
        c=self.config();c['publication_target']=SHA;inv,r=self.release_fixture()
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/p.NOTES).write_text('reviewed notes')
            with patch.object(p,'context',return_value='c'*40),patch.object(p,'settings',return_value=c),patch.object(p,'verify_target'),patch.object(p,'verify_bundle',return_value=inv),patch.object(p,'tag_commit',side_effect=[None,'d'*40]),patch.object(p,'lookup_release',return_value=r),patch.object(p,'gh_json',return_value=r) as api:
                with self.assertRaisesRegex(ValueError,'Tag changed'):p.publish(t,HASH)
                self.assertFalse(any(call.args[1:2]==('PATCH',) for call in api.call_args_list))
if __name__=='__main__':unittest.main()
