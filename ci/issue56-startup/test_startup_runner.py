"""Source-bound runner controls; synthetic subprocesses are not Swift evidence."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import run_startup_tests as runner

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(strict=True);self.sources=self.root/'sources';self.sources.mkdir()
        self.output=self.root/'evidence';self.output.mkdir()
        self.compiler=self.root/'swiftc';self.compiler.write_bytes(b'synthetic compiler not executed')
        self.identity={'workflow_commit':'a'*40,'run_id':'123','run_attempt':'1'}
        self.env={'GITHUB_SHA':self.identity['workflow_commit'],'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
        for owner,(_,paths) in runner.SUITES.items():
            for name in paths:
                p=self.sources/owner/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic source')
    def run_fixture(self, text='Ran 2 tests in 0.001s\n\nOK\n', code=0):
        def fake(command,**kwargs):
            self.assertEqual(kwargs['cwd'],self.output.resolve())
            self.assertEqual(kwargs['env']['REQUIRE_SWIFT_STARTUP_TESTS'],'1')
            self.assertEqual(kwargs['timeout'],300)
            self.assertEqual(command[:2],[runner.sys.executable,'-B'])
            self.assertTrue(Path(command[2]).is_absolute())
            kwargs['stdout'].write(text)
            return type('Result',(),{'returncode':code})()
        with patch.dict(os.environ,self.env), patch.object(runner.shutil,'which',return_value=str(self.compiler)),patch.object(runner.subprocess,'run',side_effect=fake):
            runner.run(self.sources,self.output)
        return self.output/'issue56-startup-tests.json'
    def test_both_suites_exact_source_closure_and_logs(self):
        receipt=self.run_fixture();report=runner.verify_receipt(receipt,self.sources,self.identity)
        self.assertEqual(set(report['suites']),set(runner.SUITES))
        self.assertEqual(report['source_sha256'],runner.hashes(self.sources))
        self.assertFalse(report['physical_device_execution'])
    def test_nonzero_exit_preserves_failed_receipt(self):
        with self.assertRaisesRegex(ValueError,'suite failed'):self.run_fixture(code=1)
        self.assertEqual(json.loads((self.output/'issue56-startup-tests.json').read_text())['status'],'FAILED')
    def test_skip_or_expected_failure_is_rejected(self):
        for text in ['Ran 0 tests in 0.001s\n\nOK\n','Ran 2 tests in 0.1s\n\nOK (skipped=1)\n','Ran 2 tests in 0.1s\n\nOK (expected failures=1)\n','Ran 2 tests in 0.1s\n\nFAILED (failures=1)\n']:
            with self.subTest(text=text),self.assertRaises(ValueError):runner.completed_tests(text)
    def test_changed_source_log_identity_rejected(self):
        receipt=self.run_fixture();before=receipt.read_bytes()
        report=json.loads(before);report['run_identity']['run_id']='456';receipt.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'identity'):runner.verify_receipt(receipt,self.sources,self.identity)
        receipt.write_bytes(before)
        source=next((self.sources/'LiveContainer').rglob('*.swift'));source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'source hashes'):runner.verify_receipt(receipt,self.sources,self.identity)
    def test_changed_log_rejected(self):
        receipt=self.run_fixture();(self.output/'issue56-startup-LiveContainer.log').write_text('changed')
        with self.assertRaisesRegex(ValueError,'log differs'):runner.verify_receipt(receipt,self.sources,self.identity)
    def test_boolean_counts_and_duplicate_json_rejected(self):
        receipt=self.run_fixture();before=receipt.read_bytes()
        for field in ('exit_code','skipped','tests'):
            report=json.loads(before);report['suites']['LiveContainer'][field]=False
            receipt.write_text(json.dumps(report))
            with self.subTest(field=field),self.assertRaises(ValueError):
                runner.verify_receipt(receipt,self.sources,self.identity)
        receipt.write_bytes(before[:-2]+b', "swift_required": true}\n')
        with self.assertRaisesRegex(ValueError,'duplicate JSON key'):
            runner.verify_receipt(receipt,self.sources,self.identity)

    def test_extra_fields_and_identity_types_rejected(self):
        receipt=self.run_fixture();before=receipt.read_bytes()
        for mutate in (lambda report:report.update(unknown=True),
                       lambda report:report['run_identity'].update(run_attempt=1),
                       lambda report:report['suites']['SideStore'].update(unknown=True),
                       lambda report:report.update(swiftc_sha256=False)):
            report=json.loads(before);mutate(report);receipt.write_text(json.dumps(report))
            with self.assertRaises(ValueError):runner.verify_receipt(receipt,self.sources,self.identity)

    def test_linked_source_rejected(self):
        path=self.sources/'SideStore'/runner.SUITES['SideStore'][0]
        path.unlink();path.symlink_to(self.compiler)
        with self.assertRaisesRegex(ValueError,'source file'):runner.hashes(self.sources)
    def test_compiler_absence_never_skips(self):
        with patch.object(runner.shutil,'which',return_value=None),self.assertRaisesRegex(ValueError,'Swift compiler required'):
            runner.run(self.sources,self.output)
        self.assertFalse((self.output/'issue56-startup-tests.json').exists())

if __name__=='__main__':unittest.main(verbosity=2)
