import ast, base64, copy, hashlib, importlib.util, json, os, pathlib, signal, stat, subprocess, sys, tempfile, time, types, unittest, zipfile
from unittest.mock import patch, Mock
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import run, runtime

class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host=pathlib.Path(os.environ['P2_FORM_SOURCE']).resolve()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve()
    def tearDown(self): self.tmp.cleanup()
    def test_exact_frozen_candidate_reconstructed(self):
        self.assertEqual(hashlib.sha256(run.verify_hypothesis(self.host).encode()).hexdigest(),'73d1247c4ed4d6290fa8beaed2757a37d037d31afcb0dea21276f1b700d4ea75')
    def test_exact_sources_and_project_prepared(self):
        p=run.prepare(self.host,self.root/'fixture','b5f0154152a7ee2d19e457c9e3c967835921f082')
        app=(p.parent/'Sources/App.swift').read_text()
        for n in ['V3Localization.swift','V3LanguageSettings.swift']:
            self.assertIn((self.host/'LiveContainerSwiftUI/Utilities'/n).read_text(),app)
        self.assertEqual((p/'project.pbxproj').read_bytes(),(HERE.parent/'settings/project/project.pbxproj').read_bytes())
        self.assertEqual((p.parent/'Sources/Localizable.xcstrings').read_bytes(),(self.host/'Resources/Localizable.xcstrings').read_bytes())
        identity=json.loads((p.parent/'source-identity.json').read_text())
        for name,digest in identity['generatedFiles'].items():self.assertEqual(run.sha(p.parent/name),digest)
        self.assertFalse(identity['candidateCompiledAsWholeView'])
    def test_all_extracted_helpers_bound(self):
        p=run.prepare(self.host,self.root/'fixture','b5f0154152a7ee2d19e457c9e3c967835921f082')
        identity=json.loads((p.parent/'source-identity.json').read_text())
        self.assertEqual(set(identity['extractedDeclarations']),set(run.ANCHORS)|{'class SceneDelegate','View.textFieldAlert'})
        self.assertEqual(len(identity['sourceFiles']),7)
    def test_no_production_branch_or_build_operation(self):
        source=(HERE/'run.py').read_text()
        self.assertNotIn('LiveContainer.xcodeproj',source)
        self.assertNotIn('git push',source)
        self.assertEqual(source.count("'build-for-testing'"),1)
        self.assertEqual(source.count("'test-without-building'"),1)
    def test_binding_verifies_every_imported_input(self):
        data=run.verify_inputs(self.host,run.APPROVED_INPUTS)
        for p in ['ci/p2/form_direction/runtime.py','ci/p2/form_direction/verify.py','scripts/run_p0_signin_rendering.py','scripts/p2_localization_contract.py','tests/fixtures/p2_localization_contracts/compiled-translations.json','ci/p2/preflight.py']:
            self.assertIn(p,data['builderFiles'])
    def test_hypothesis_manifest_unchanged(self):
        self.assertEqual(run.sha(HERE/'hypothesis/manifest.json'),'d4da90a28dc1203202bc83a1096e150df040e14f6832282ded2f7cb786af69aa')
    def test_workflow_has_isolated_budget_and_filters(self):
        s=(run.ROOT/'.github/workflows/p2-form-direction.yml').read_text()
        self.assertIn('ci/p2/form_direction/**',s);self.assertIn('cancel-in-progress: false',s)
        self.assertIn('timeout-minutes: 35',s);self.assertNotIn('workflow_dispatch',s)
        self.assertNotIn('release.yml',s)
    def test_generated_input_recheck_retains_mismatch(self):
        fixture=self.root/'fixture';fixture.mkdir();(fixture/'App.swift').write_text('before')
        expected={'App.swift':run.sha(fixture/'App.swift')}
        self.assertTrue(run.verify_generated(fixture,expected,self.root/'pass.json')['stable'])
        (fixture/'App.swift').write_text('after')
        with self.assertRaises(ValueError):run.verify_generated(fixture,expected,self.root/'fail.json')
        receipt=json.loads((self.root/'fail.json').read_text())
        self.assertFalse(receipt['stable']);self.assertEqual(receipt['changed']['App.swift']['before'],expected['App.swift'])
        self.assertEqual(receipt['changed']['App.swift']['after'],run.sha(fixture/'App.swift'))

    def test_strict_seal_normalization_only(self):
        raw=(HERE/'run.py').read_bytes();norm=run.normalized(raw,'ci/p2/form_direction/run.py')
        self.assertEqual(sum(x == b"APPROVED_INPUTS = 'TO_BE_SEALED'" for x in norm.splitlines()),1)
        with self.assertRaises(ValueError):run.normalized(raw+raw,'ci/p2/form_direction/run.py')

class ResultsTests(unittest.TestCase):
    def sample(self):
        m=run.METHODS['control'];return {'totalTestCount':1,'passedTests':1,'failedTests':0,'skippedTests':0,'result':'Passed'}, {'children':[{'nodeType':'Test Case','name':m+'()','nodeIdentifier':'FormDirectionTests/'+m+'()','result':'Passed'}]}
    def test_one_exact_native_case(self):
        s,t=self.sample();self.assertEqual(run.verify_result(s,t,run.METHODS['control'])['passed'],1)
    def test_wrong_count_skip_boolean_and_foreign_reject(self):
        for k,v in [('passedTests',True),('skippedTests',1),('totalTestCount',2),('result','Failed')]:
            s,t=self.sample();s[k]=v
            with self.assertRaises(ValueError):run.verify_result(s,t,run.METHODS['control'])
        s,t=self.sample();t['children'][0]['nodeIdentifier']='Foreign/testControlRoundTrip()'
        with self.assertRaises(ValueError):run.verify_result(s,t,run.METHODS['control'])
    def test_duplicate_method_reject(self):
        s,t=self.sample();t['children']*=2
        with self.assertRaises(ValueError):run.verify_result(s,t,run.METHODS['control'])

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.source=self.root/'source';self.source.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def test_archive_preserves_files_modes_and_links(self):
        (self.source/'payload').write_bytes(b'exact\x00bytes');(self.source/'payload').chmod(0o755)
        (self.source/'alias').symlink_to('payload');(self.source/'empty').mkdir()
        r=run.archive(self.source,self.root/'retained.zip');self.assertEqual(r['entries'],3)
        with zipfile.ZipFile(self.root/'retained.zip') as z:
            self.assertEqual(z.read('payload'),b'exact\x00bytes');self.assertEqual(z.read('alias'),b'payload')
            self.assertEqual(stat.S_IMODE(z.getinfo('payload').external_attr>>16),0o755)
            self.assertTrue(stat.S_ISLNK(z.getinfo('alias').external_attr>>16))
    def test_escape_broken_absolute_links_reject(self):
        for target in ['../outside','/etc/hosts','missing']:
            link=self.source/'alias';link.symlink_to(target)
            with self.assertRaises(ValueError):run.archive(self.source,self.root/'bad.zip')
            link.unlink()
    def test_special_file_reject(self):
        os.mkfifo(self.source/'pipe')
        with self.assertRaises(ValueError):run.inventory(self.source)
    def test_changed_source_retention_fails(self):
        (self.source/'a').write_text('a');orig=run.inventory;calls=0
        def changed(root,deadline=None):
            nonlocal calls
            calls+=1
            if calls==2:(root/'a').write_text('changed')
            return orig(root,deadline)
        with patch.object(run,'inventory',side_effect=changed),self.assertRaises(ValueError):run.archive(self.source,self.root/'bad.zip')
    def test_retention_deadline_rejects_before_read(self):
        (self.source/'a').write_text('a')
        with self.assertRaises(ValueError):run.archive(self.source,self.root/'late.zip',deadline=0)
    def test_entry_and_byte_limits_reject(self):
        (self.source/'a').write_text('abcd')
        for name,value in [('MAX_ARCHIVE_ENTRIES',0),('MAX_ARCHIVE_BYTES',2),('MAX_ARCHIVE_FILE_BYTES',2)]:
            with patch.object(run,name,value),self.assertRaises(ValueError):run.archive(self.source,self.root/'big.zip')
    def test_failed_readback_retains_both_inventories(self):
        (self.source/'a').write_text('a')
        original=zipfile.ZipFile.open
        def bad(z, name, mode='r', *args, **kwargs):
            if mode=='r':raise ValueError('Injected readback failure')
            return original(z,name,mode,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'open',bad),self.assertRaises(ValueError):run.archive(self.source,self.root/'bad.zip')
        self.assertTrue((self.root/'bad.before-inventory.json').exists())
        self.assertTrue((self.root/'bad.after-inventory.json').exists())

class OwnershipTests(unittest.TestCase):
    def fake(self,reaped=False):
        p=Mock(pid=100123,returncode=0 if reaped else None)
        p.poll.return_value=p.returncode
        def comm(timeout):p.returncode=-15 if p.returncode is None else p.returncode;return ('out','err')
        p.communicate.side_effect=comm
        return p
    def test_reaped_child_gets_no_signal(self):
        p=self.fake(True)
        with patch.object(runtime.os,'killpg',side_effect=ProcessLookupError) as kill:
            r=runtime.finish_owned_child(p)
        self.assertTrue(r['ownedProcessGroup']['quiescent']);self.assertTrue(all(c.args[1]==0 for c in kill.call_args_list))
    def test_live_owned_child_signals_before_reap_only(self):
        p=self.fake();events=[]
        def kill(pid,sig):
            events.append((sig,p.returncode))
            if sig==0:raise ProcessLookupError
        with patch.object(runtime.os,'getpgid',return_value=p.pid),patch.object(runtime.os,'killpg',side_effect=kill),patch.object(runtime.time,'sleep'):
            r=runtime.finish_owned_child(p)
        self.assertEqual([e[0] for e in events],[signal.SIGTERM,signal.SIGKILL,0])
        self.assertTrue(all(code is None for sig,code in events if sig));self.assertTrue(r['ownedProcessGroup']['quiescent'])
    def test_poll_reap_removes_signal_authority(self):
        p=self.fake()
        p.poll.side_effect=lambda:setattr(p,'returncode',0)
        with patch.object(runtime.os,'killpg',side_effect=ProcessLookupError) as kill:
            runtime.finish_owned_child(p)
        self.assertTrue(all(c.args[1]==0 for c in kill.call_args_list))
    def test_wrong_group_never_signalled(self):
        p=self.fake()
        with patch.object(runtime.os,'getpgid',return_value=p.pid+1),patch.object(runtime.os,'killpg',side_effect=ProcessLookupError) as kill:
            r=runtime.finish_owned_child(p)
        self.assertIn('quiescenceError',r);self.assertTrue(all(c.args[1]==0 for c in kill.call_args_list))
    def test_surviving_reaped_group_failure_readonly(self):
        p=self.fake(True)
        with patch.object(runtime.os,'killpg') as kill,patch.object(runtime.time,'monotonic',side_effect=[0,2]):
            r=runtime.finish_owned_child(p)
        self.assertIn('quiescenceError',r);self.assertTrue(all(c.args[1]==0 for c in kill.call_args_list))
    def test_nonstandard_sigchld_fails_closed(self):
        p=self.fake(True)
        with patch.object(runtime.signal,'getsignal',return_value=signal.SIG_IGN),patch.object(runtime.os,'killpg',side_effect=ProcessLookupError):r=runtime.finish_owned_child(p)
        self.assertIn('quiescenceError',r)
    def test_real_command_receipt_and_settlement(self):
        with tempfile.TemporaryDirectory() as t:
            c=runtime.Commands(pathlib.Path(t).resolve());out,r=c.run([sys.executable,'-c','print("fixture")'],5)
            self.assertEqual(out.strip(),'fixture');self.assertTrue(r['ownedProcessGroup']['quiescent']);self.assertTrue(c.all_settled)
    def test_timeout_retains_original_failure(self):
        with tempfile.TemporaryDirectory() as t:
            c=runtime.Commands(pathlib.Path(t).resolve());out,r=c.run([sys.executable,'-c','import time;time.sleep(30)'],.05,checked=False)
            self.assertEqual(r['primaryError'],'Command deadline exceeded');self.assertTrue(r['ownedProcessGroup']['leaderReaped'])
            self.assertTrue((pathlib.Path(t)/'001.json').exists())
            self.assertEqual(c.failed_receipts[0]['primaryError'],'Command deadline exceeded')
            self.assertTrue(c.failed_receipts[0]['ownedProcessGroup']['leaderReaped'])
    def test_shared_deadline_fails_before_spawn(self):
        with tempfile.TemporaryDirectory() as t:
            c=runtime.Commands(pathlib.Path(t).resolve(),seconds=-1)
            with patch.object(runtime.subprocess,'Popen') as pop,self.assertRaises(ValueError):c.run(['must-not-run'])
            pop.assert_not_called()
    def test_command_budget_never_exceeds_240(self):
        with tempfile.TemporaryDirectory() as t:
            c=runtime.Commands(pathlib.Path(t).resolve());_,r=c.run([sys.executable,'-c','pass'],999)
            self.assertEqual(r['budgetSeconds'],240)
    def test_unexpected_collection_error_still_finishes_owned_child(self):
        with tempfile.TemporaryDirectory() as t:
            process=self.fake(True)
            process.communicate.side_effect=[RuntimeError('collector'),('retained','')]
            with patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime.os,'killpg',side_effect=ProcessLookupError):
                c=runtime.Commands(pathlib.Path(t).resolve());out,r=c.run(['synthetic'],checked=False)
            self.assertEqual(out,'retained');self.assertIn('collection failed',r['primaryError'])
            self.assertTrue(r['ownedProcessGroup']['pipesJoined'])
    def test_slow_spawn_does_not_restart_command_deadline(self):
        with tempfile.TemporaryDirectory() as t:
            process=self.fake(True)
            c=runtime.Commands(pathlib.Path(t).resolve());c.deadline=100
            with patch.object(runtime.time,'monotonic',side_effect=[0,0,4,4]),patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime,'finish_owned_child',return_value={'stdout':'','stderr':'','ownedProcessGroup':{'quiescent':True}}):
                _,r=c.run(['synthetic'],timeout=3,checked=False)
            process.communicate.assert_not_called()
            self.assertEqual(r['postSpawnRemainingSeconds'],0)
            self.assertEqual(r['primaryError'],'Command deadline exceeded')

class OutputSnapshotTests(unittest.TestCase):
    def decode(self,row):
        return base64.b64decode(row['headBase64'],validate=True)+base64.b64decode(row['tailBase64'],validate=True)
    def test_short_bytes_and_utf8_are_exact(self):
        for value in [b'raw\x00\xff', 'עברית Unicode 🧪', '', b'']:
            row=runtime.output_snapshot(value)
            raw=value.encode('utf-8') if isinstance(value,str) else value
            self.assertEqual(self.decode(row),raw)
            self.assertEqual(row['capturedBytes'],len(raw))
            self.assertEqual(row['capturedSHA256'],hashlib.sha256(raw).hexdigest())
            self.assertFalse(row['truncated']);self.assertFalse(row['completeProcessOutputProven'])
            self.assertEqual(row['representation'],'utf8-of-decoded-popen-text' if isinstance(value,str) else 'popen-partial-bytes')
    def test_long_multibyte_output_has_byte_bounds(self):
        raw=('🧪עברית'*4000).encode('utf-8');row=runtime.output_snapshot(raw)
        self.assertEqual(base64.b64decode(row['headBase64']),raw[:1024])
        self.assertEqual(base64.b64decode(row['tailBase64']),raw[-3072:])
        self.assertEqual(len(self.decode(row)),4096);self.assertTrue(row['truncated'])
        self.assertEqual(row['capturedSHA256'],hashlib.sha256(raw).hexdigest())
    def test_partial_timeout_streams_survive_failed_join(self):
        with tempfile.TemporaryDirectory() as t:
            process=Mock(pid=123,returncode=None)
            process.communicate.side_effect=subprocess.TimeoutExpired(['fixture'],1,output=b'partial\xff',stderr=b'err')
            cleanup={'ownedProcessGroup':{'quiescent':False,'pipesJoined':False},'quiescenceError':'unsettled'}
            with patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime,'finish_owned_child',return_value=cleanup):
                c=runtime.Commands(pathlib.Path(t).resolve());_,record=c.run(['fixture'],1,checked=False)
            row=c.failed_output_receipts[0]
            self.assertEqual(self.decode(row['stdout']),b'partial\xff');self.assertEqual(self.decode(row['stderr']),b'err')
            self.assertEqual(row['stdout']['captureOrigin'],'timeout-partial')
            self.assertFalse(c.all_settled);self.assertEqual(record['primaryError'],'Command deadline exceeded')
    def test_cumulative_join_replaces_partial_without_duplication(self):
        with tempfile.TemporaryDirectory() as t:
            process=Mock(pid=123,returncode=-15)
            process.communicate.side_effect=subprocess.TimeoutExpired(['fixture'],1,output=b'prefix',stderr=b'error-prefix')
            cleanup={'stdout':'prefix plus final','stderr':None,'ownedProcessGroup':{'quiescent':True,'pipesJoined':True}}
            with patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime,'finish_owned_child',return_value=cleanup):
                c=runtime.Commands(pathlib.Path(t).resolve());c.run(['fixture'],1,checked=False)
            row=c.failed_output_receipts[0]
            self.assertEqual(self.decode(row['stdout']),b'prefix plus final')
            self.assertEqual(self.decode(row['stderr']),b'error-prefix')
            self.assertEqual(row['stdout']['captureOrigin'],'cleanup-communicate-return')
            self.assertEqual(row['stderr']['captureOrigin'],'timeout-partial')
    def test_first_failure_survives_more_than_eight_failures(self):
        with tempfile.TemporaryDirectory() as t:
            c=runtime.Commands(pathlib.Path(t).resolve())
            process=Mock(pid=123,returncode=1);process.communicate.return_value=('failed','')
            cleanup={'ownedProcessGroup':{'quiescent':True,'pipesJoined':True}}
            with patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime,'finish_owned_child',return_value=cleanup):
                for i in range(12):c.run(['fixture',str(i)],1,checked=False)
            for rows in [c.failed_receipts,c.failed_output_receipts]:
                self.assertEqual([row['number'] for row in rows],[1,6,7,8,9,10,11,12])

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve()
        self.evidence=self.root/'evidence';self.evidence.mkdir();(self.evidence/'result.json').write_text('{}')
        self.publish=self.root/'publish'
    def tearDown(self):self.tmp.cleanup()
    def test_unsettled_export_never_indexed_or_transported(self):
        attachments=self.evidence/'control/attachments';attachments.mkdir(parents=True);(attachments/'mutable.txt').write_text('still writing')
        with patch.object(run,'archive') as archive,patch.object(run,'inventory') as inventory:
            self.assertFalse(run.publish_evidence(self.evidence,self.publish,False,context={'status':'failed','errors':['native timeout']}))
        archive.assert_not_called();inventory.assert_not_called()
        self.assertFalse((self.evidence/'evidence-index.json').exists())
        self.assertEqual([p.name for p in self.publish.iterdir()],['failure.json'])
        self.assertEqual(json.loads((self.publish/'failure.json').read_text())['primaryOutcome']['errors'],['native timeout'])
    def test_unsettled_output_uses_only_parent_memory(self):
        outputs=[{'number':1,'stdout':runtime.output_snapshot('native-start'),'stderr':runtime.output_snapshot(b'error\xff')}]
        with patch.object(run,'archive') as archive,patch.object(run,'inventory') as inventory,patch.object(pathlib.Path,'read_bytes',side_effect=AssertionError('mutable read')),patch.object(pathlib.Path,'read_text',side_effect=AssertionError('mutable read')):
            self.assertFalse(run.publish_evidence(self.evidence,self.publish,False,outputs=outputs))
        archive.assert_not_called();inventory.assert_not_called()
        self.assertEqual(set(p.name for p in self.publish.iterdir()),{'failure.json','failure-command-output.json'})
        self.assertEqual(json.loads((self.publish/'failure-command-output.json').read_text())['commands'],outputs)
    def test_output_bounds_preserve_primary_failure(self):
        for outputs in [[{}]*9,[{'oversized':'x'*(129*1024)}]]:
            publish=self.root/('limit-'+str(len(outputs)))
            self.assertFalse(run.publish_evidence(self.evidence,publish,False,context={'errors':['primary']},outputs=outputs))
            self.assertEqual(set(p.name for p in publish.iterdir()),{'failure.json'})
            receipt=json.loads((publish/'failure.json').read_text())
            self.assertEqual(receipt['primaryOutcome']['errors'],['primary']);self.assertIn('outputRetentionError',receipt)
    def test_eight_maximum_output_receipts_fit_bound(self):
        outputs=[{'number':i,'phase':'p'*256,'commandIdentity':'c'*1024,'primaryError':'e'*1024,'stdout':runtime.output_snapshot(b'x'*100000),'stderr':runtime.output_snapshot(b'y'*100000)} for i in range(8)]
        self.assertFalse(run.publish_evidence(self.evidence,self.publish,False,outputs=outputs))
        path=self.publish/'failure-command-output.json'
        self.assertTrue(path.is_file());self.assertLessEqual(path.stat().st_size,128*1024)
    def test_failed_archive_keeps_output_without_partial_transport(self):
        outputs=[{'number':1,'stdout':runtime.output_snapshot('captured before archive'),'stderr':runtime.output_snapshot('')}]
        def partial(root,target,deadline):target.write_bytes(b'partial');raise ValueError('injected')
        with patch.object(run,'archive',side_effect=partial):self.assertFalse(run.publish_evidence(self.evidence,self.publish,True,outputs=outputs))
        self.assertEqual(set(p.name for p in self.publish.iterdir()),{'failure.json','failure-command-output.json'})
        self.assertEqual(json.loads((self.publish/'failure-command-output.json').read_text())['commands'],outputs)
    def test_full_transport_is_verified_archive_only(self):
        self.assertTrue(run.publish_evidence(self.evidence,self.publish,True))
        self.assertEqual(set(p.name for p in self.publish.iterdir()),{'transport.json','evidence.zip'})
        with zipfile.ZipFile(self.publish/'evidence.zip') as z:self.assertIn('evidence-index.json',z.namelist())
    def test_failed_archive_does_not_transport_partial(self):
        def partial(root,target,deadline):target.write_bytes(b'partial');raise ValueError('injected')
        with patch.object(run,'archive',side_effect=partial):self.assertFalse(run.publish_evidence(self.evidence,self.publish,True))
        self.assertEqual([p.name for p in self.publish.iterdir()],['failure.json'])
    def test_workflow_upload_scope_excludes_mutable_evidence(self):
        text=(run.ROOT/'.github/workflows/p2-form-direction.yml').read_text()
        self.assertIn('p2-form-publish/**',text);self.assertNotIn('p2-form-evidence/**',text)
    def main_failure_case(self, export_failure=True, shutdown_failure=False):
        host=self.root/'host';host.mkdir();work=self.root/'work';evidence=self.root/'main-evidence';publish=self.root/'main-publish'
        class FakeCommands:
            def __init__(self,*args):self.deadline=time.monotonic()+360;self.all_settled=True;self.failed_receipts=[];self.failed_output_receipts=[]
            def begin_finalization(self):pass
            def run(self,argv,timeout=60,checked=True):
                a=list(map(str,argv));rec={'primaryError':None,'returncode':0}
                out=''
                if a[0]=='git':
                    if 'status' in a:out=''
                    elif a[-1]=='HEAD^{tree}':out=run.TREE
                    elif a[2]==str(host):out=run.SOURCE
                    else:out='b'*40
                elif a==['xcodebuild','-version']:out='Xcode 26.4.1\nBuild version fixture'
                elif a[:4]==['xcrun','simctl','list','runtimes']:out=json.dumps({'runtimes':[{'identifier':run.RUNTIME,'isAvailable':True,'buildversion':'23F77'}]})
                elif 'build-for-testing' in a:
                    products=work/'DerivedData/Build/Products';products.mkdir(parents=True);(products/'fixture.xctestrun').write_bytes(b'exact')
                elif a[:3]==['xcrun','simctl','create']:out='11111111-1111-4111-8111-111111111111'
                elif 'test-without-building' in a:
                    pathlib.Path(a[a.index('-resultBundlePath')+1]).mkdir()
                elif a[:4]==['xcrun','xcresulttool','get','test-results']:out='{}'
                elif a[:3]==['xcrun','xcresulttool','export']:
                    path=pathlib.Path(a[a.index('--output-path')+1]);path.mkdir(parents=True);(path/'still-writing.txt').write_text('mutable')
                    if export_failure:
                        self.all_settled=False
                        self.failed_output_receipts=[{'number':14,'stdout':runtime.output_snapshot('export phase reached'),'stderr':runtime.output_snapshot('owned writer unresolved')}]
                        self.failed_receipts=[{'phase':'xcresulttool export','primaryError':'timeout','ownedProcessGroup':{'quiescent':False}}]
                        raise ValueError('Injected unresolved export writer')
                elif a[:3]==['xcrun','simctl','shutdown'] and shutdown_failure:
                    rec={'primaryError':'Injected shutdown failure','returncode':1}
                    self.failed_receipts=[{'phase':'simctl shutdown','primaryError':'nonzero','ownedProcessGroup':{'quiescent':True}}]
                return out,rec
        fakeverifier=types.SimpleNamespace(verify_arm=lambda *a: {},verify_pair=lambda *a:{})
        def loader(name,path):
            if name=='p2_form_runtime':return types.SimpleNamespace(Commands=FakeCommands)
            if name=='p2_form_plural':return types.SimpleNamespace(verify_exact_plural_values=lambda *a:{'status':'passed'})
            return fakeverifier
        (host/'Resources').mkdir();(host/'Resources/Localizable.xcstrings').write_text('{}')
        data={'builderFiles':{'.github/workflows/p2-form-direction.yml':'synthetic'}}
        def prepare(host,output,builder):
            output.mkdir(parents=True);(output/'source-identity.json').write_text('{"generatedFiles":{}}');return output/'project'
        with patch.object(run,'verify_inputs',return_value=data),patch.object(run,'load',side_effect=loader),patch.object(run,'prepare',side_effect=prepare),patch.object(run.platform,'system',return_value='Darwin'),patch.object(run.platform,'machine',return_value='arm64'),patch.dict(os.environ,{'GITHUB_SHA':'b'*40}),patch.dict(sys.modules,{'p2_localization_contract':types.SimpleNamespace(verify_app=lambda *a:{'status':'passed'})}):
            result=run.Probe(host,work,evidence,publish,'fixture').run()
        self.assertEqual(result['status'],'failed')
        self.assertTrue((evidence/'control/attachments/still-writing.txt').exists())
        self.assertFalse((evidence/'evidence-index.json').exists())
        self.assertEqual(set(p.name for p in publish.iterdir()),{'failure.json','failure-command-output.json'} if export_failure else {'failure.json'})
        if export_failure:
            output=json.loads((publish/'failure-command-output.json').read_text())
            self.assertEqual(base64.b64decode(output['commands'][0]['stdout']['headBase64']),b'export phase reached')
            self.assertFalse(output['completeProcessOutputProven'])
        if export_failure:self.assertNotIn('candidate',result['arms'])
        else:self.assertIn('candidate',result['arms'])
        receipt=json.loads((publish/'failure.json').read_text())
        self.assertEqual(receipt['primaryOutcome']['failedCommands'][0]['phase'], 'xcresulttool export' if export_failure else 'simctl shutdown')

    def test_actual_main_export_cleanup_failure_blocks_transport(self):
        self.main_failure_case()

    def test_actual_main_settled_groups_shutdown_failure_blocks_transport(self):
        self.main_failure_case(export_failure=False,shutdown_failure=True)

if __name__=='__main__':unittest.main()

class ProbeIsolationContracts(unittest.TestCase):
    def test_only_telemetry_observes_probe_store(self):
        source=(HERE/'App.swift.in').read_text()
        model=source.split('final class FormDirectionModel:',1)[1].split('// Probe notifications',1)[0]
        self.assertIn('let probeStore = FormDirectionProbeStore()',model)
        self.assertNotIn('probeRevision',model)
        self.assertNotIn('func record(',model)
        self.assertNotIn('objectWillChange',model)
        telemetry=source.split('struct FormDirectionTelemetry:',1)[1].split('struct FormDirectionScreen:',1)[0]
        self.assertIn('@ObservedObject var probeStore:',telemetry)
        self.assertEqual(source.count('@ObservedObject var probeStore:'),1)
    def test_probe_callback_does_not_invalidate_screen_model(self):
        source=(HERE/'App.swift.in').read_text()
        probe=source.split('struct FormEnvironmentProbe:',1)[1].split('struct FormDirectionTelemetry:',1)[0]
        self.assertNotIn('model',probe)
        self.assertIn('[store, name]',probe)
        self.assertIn('store.record(name, identity: identity, locale: locale, direction: direction)',probe)
    def test_identity_and_environment_changes_remain_observable(self):
        source=(HERE/'App.swift.in').read_text()
        record=source.split('func record(',1)[1].split('// Passive SwiftUI',1)[0]
        for key in ['identity','locale','direction']:
            self.assertIn('previous?["'+key+'"] as? String != '+key,record)
        self.assertIn('"uptime": ProcessInfo.processInfo.systemUptime',record)
        self.assertIn('"revision": (previous?["revision"] as? Int ?? 0) + 1',record)
        self.assertIn('probeRevision += 1',record)
