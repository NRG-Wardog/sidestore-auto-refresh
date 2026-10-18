import copy, importlib.util, json, os, tempfile, unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('rebuild_run', HERE/'run.py'); run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)
spec=importlib.util.spec_from_file_location('rebuild_verify', HERE/'verify.py'); verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)
class CandidateTests(unittest.TestCase):
    def test_exact_inputs_and_prepare(self):
        host=Path(os.environ.get('P2_FORM_SOURCE','/tmp/review-p2-current-source')).resolve()
        run.verify_inputs(host,run.APPROVED_INPUTS)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()/'fixture'
            run.prepare(host,root,'0'*40)
            app=(root/'Sources/App.swift').read_text()
            self.assertIn('.id(direction == .rightToLeft)',app)
            self.assertNotIn('FormEnvironmentProbe',app)
            self.assertNotIn('environment(\\.layoutDirection, .leftToRight)',app)
            self.assertEqual(run.METHODS,{'candidate':'testCandidateRoundTrip'})
    def test_state_witness_uses_visible_label_and_actual_controls(self):
        app=(HERE/'App.swift.in').read_text(); tests=(HERE/'FormDirectionTests.swift.in').read_text()
        self.assertIn('Text(verbatim: language +',app)
        self.assertNotIn('.accessibilityValue(',app)
        self.assertIn('label == %@',tests)
        self.assertIn('XCTAssertEqual(app.textFields["retained-input"].value',tests)
        self.assertIn('XCTAssertEqual(app.switches["retained-toggle"].value',tests)
        self.assertIn('State mismatch: label=',tests)
        self.assertIn('frame.maxX-inset',tests)
        self.assertIn('window.contains(point)',tests)
        self.assertIn('XCTAssertEqual(matches.count,1)',tests)
        self.assertIn('Toggle setup failed:',tests)
        source=(HERE/'run.py').read_text()
        self.assertIn("'nativeState':self.report.get('nativeState'",source)
    def test_ui_budget_is_bounded_and_other_commands_unchanged(self):
        runtime=run.load('rebuild_runtime_test',HERE/'runtime.py')
        argv=runtime.UI_COMMAND_SHAPE.copy()
        argv[3]='/tmp/fixture.xctestrun';argv[5]='id=11111111-1111-1111-1111-111111111111';argv[7]='/tmp/candidate.xcresult'
        self.assertEqual(runtime.command_budget(argv,540,1000),540)
        self.assertEqual(runtime.command_budget(argv,999,1000),540)
        self.assertEqual(runtime.command_budget(argv,540,87),87)
        self.assertEqual(runtime.command_budget(argv,540,1000,True),240)
        variants=[['xcodebuild','test-without-building'],['xcodebuild','build-for-testing'],['xcrun','simctl'],['bash'],argv+['extra']]
        for index,value in [(3,'relative.xctestrun'),(5,'id=wrong'),(7,'/tmp/other'),(15,'999'),(18,'-only-testing:Other/test')]:
            variant=argv.copy();variant[index]=value;variants.append(variant)
        for variant in variants:
            self.assertEqual(runtime.command_budget(variant,540,1000),240)
        self.assertEqual(run.UI_COMMAND_BUDGET['nativeTestLimitSeconds'],150)
        self.assertEqual(sum(run.UI_COMMAND_BUDGET[k] for k in ['startupAllowanceSeconds','nativeTestLimitSeconds','resultFinalizationAllowanceSeconds']),540)
    def make_evidence(self,directory):
        rows=[]
        for stage,lang in [('candidate-en-initial','en'),('candidate-he','he'),('candidate-en-return','en')]:
            for ext in ['png','json']:
                file=stage+'.'+ext
                (directory/file).write_bytes(b'png' if ext=='png' else json.dumps({'stage':stage,'language':lang,'state':lang+'|Preserved user text 42|true','scope':'candidate-component-only'}).encode())
                rows.append({'suggestedHumanReadableName':stage+'_0_11111111-1111-1111-1111-111111111111.'+ext,'exportedFileName':file})
        manifest=[{'testIdentifier':'FormDirectionTests/testCandidateRoundTrip()','attachments':rows}]
        (directory/'manifest.json').write_text(json.dumps(manifest));return manifest
    def test_three_screens_remain_visually_unaccepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);self.make_evidence(path)
            result=verify.verify_arm(path,'candidate',lambda p:p.read_bytes()==b'png')
            self.assertEqual(len(result['observations']),3);self.assertFalse(result['visualAcceptance'])
    def test_bad_or_missing_evidence_rejected(self):
        for mutation in ['association','duplicate','missing','escape','state','png']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                path=Path(directory);manifest=self.make_evidence(path); rows=manifest[0]['attachments']
                if mutation=='association': manifest[0]['testIdentifier']='Wrong/test()'
                if mutation=='duplicate': rows.append(copy.deepcopy(rows[0]))
                if mutation=='missing': rows.pop()
                if mutation=='escape': rows[0]['exportedFileName']='../escape.png'
                if mutation=='state':(path/'candidate-he.json').write_text('{}')
                if mutation=='png':(path/'candidate-he.png').write_bytes(b'bad')
                (path/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):verify.verify_arm(path,'candidate',lambda p:p.read_bytes()==b'png')
    def test_duplicate_json_rejected(self):
        with self.assertRaises(ValueError):verify.strict_json('{"x":1,"x":2}')
    def test_native_harness_is_actual_candidate_extract(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'state.swift'
            subprocess.run([sys.executable,str(HERE/'make_native_state_harness.py'),str(output),str(HERE/'product/V3UnifiedShell.swift')],check=True,timeout=10)
            result=output.read_text()
            self.assertIn('generation == loadGeneration',result)
            self.assertIn('private func finishWrite',result)
            self.assertIn('old.cancel()',result)
            self.assertNotIn('SwiftUI',result)
if __name__=='__main__':unittest.main()
