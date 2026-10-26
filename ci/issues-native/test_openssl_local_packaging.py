"""Caller integration controls. Host/Apple native outcomes are not simulated proof."""
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import package_candidate as caller
import optimize_openssl_local_candidate as adapter
from experiment_strip_stabs import write,digest

class OpenSSLCallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(strict=True)
        self.native={'workflow_commit':'1'*40,'run_id':'1','run_attempt':'1'}
        self.packaging={'workflow_commit':'2'*40,'run_id':'2','run_attempt':'1'}
    def test_default_disabled_never_loads_qualification(self):
        with patch.object(adapter,'verify_host_qualification') as verifier:
            self.assertIsNone(caller.admit_openssl_local_qualification(False,self.native,self.packaging))
            verifier.assert_not_called()
        self.assertFalse(inspect.signature(caller.main).parameters['strip_openssl_true_locals'].default)
    def test_missing_or_malformed_trusted_hash_rejected(self):
        with patch.dict(os.environ,{},clear=True),patch.object(adapter,'verify_host_qualification') as verifier:
            with self.assertRaisesRegex(ValueError,'trusted SHA256'):caller.admit_openssl_local_qualification(True,self.native,self.packaging)
            verifier.assert_not_called()
    def test_explicit_identity_path_sha_forwarded_and_failure_propagated(self):
        receipt=self.root/'host.json';receipt.write_text('{}');expected=digest(receipt)
        env={'OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT':str(receipt),'OPENSSL_LOCAL_HOST_QUALIFICATION_SHA256':expected}
        with patch.dict(os.environ,env),patch.object(adapter,'verify_host_qualification',return_value={'fixture_only':True}) as verifier:
            result=caller.admit_openssl_local_qualification(True,self.native,self.packaging)
            verifier.assert_called_once_with(receipt,expected,self.packaging,self.native)
            self.assertEqual(result,(receipt,expected,{'fixture_only':True}))
        with patch.dict(os.environ,env),patch.object(adapter,'verify_host_qualification',side_effect=ValueError('hash mismatch')):
            with self.assertRaisesRegex(ValueError,'hash mismatch'):caller.admit_openssl_local_qualification(True,self.native,self.packaging)
    def test_linked_receipt_rejected(self):
        real=self.root/'real';real.write_text('{}');link=self.root/'link';link.symlink_to(real)
        with patch.dict(os.environ,{'OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT':str(link),'OPENSSL_LOCAL_HOST_QUALIFICATION_SHA256':digest(real)}):
            with self.assertRaisesRegex(ValueError,'regular receipt'):caller.admit_openssl_local_qualification(True,self.native,self.packaging)
    def test_context_free_optin_rejected_before_any_io(self):
        with patch.object(caller.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'explicit current'):caller.main(strip_openssl_true_locals=True)
            run.assert_not_called()
    def test_fourth_stage_consumes_exact_persisted_third_stage_receipt(self):
        cwd=Path.cwd()
        try:
            os.chdir(self.root);path=Path('size-diagnostics/sidebackup/cumulative-receipt.json');path.parent.mkdir(parents=True)
            prior={'fixture_only':True};write(path,prior)
            qualification=(self.root/'host.json','a'*64,{'fixture_only':True})
            with patch.object(adapter,'optimize_openssl_local_candidate',return_value=('result','report')) as optimize:
                result=caller.append_openssl_local_stage(Path('input.ipa'),prior,qualification,
                    transfer_roots={'fixture_only':True},receipt_path=Path('receipt'),seal_path=Path('seal'),seal_sha='b'*64,
                    native_identity=self.native,packaging_identity=self.packaging)
                self.assertEqual(result,('result','report'));kw=optimize.call_args.kwargs
                self.assertEqual(kw['expected_prior_receipt_sha'],digest(path));self.assertEqual(kw['prior_receipt_path'],path)
                self.assertEqual(kw['native_identity'],self.native);self.assertEqual(kw['packaging_identity'],self.packaging)
                self.assertEqual(kw['original_sidecar'],Path('size-diagnostics/retained-debug-diagnostics.zip'))
                self.assertEqual(kw['expected_host_qualification_sha'],'a'*64)
                write(path,{'changed':True})
                with self.assertRaises(ValueError):caller.append_openssl_local_stage(Path('input.ipa'),prior,qualification,
                    transfer_roots={},receipt_path=Path('receipt'),seal_path=Path('seal'),seal_sha='b'*64,
                    native_identity=self.native,packaging_identity=self.packaging)
                self.assertEqual(optimize.call_count,1)
        finally:os.chdir(cwd)
    def test_early_qualification_late_russian_widget_and_receipt_gates_preserved(self):
        source=Path(caller.__file__).read_text()
        self.assertLess(source.index('openssl_qualification = admit_openssl'),source.index("root = Path('packaging-inputs')"))
        self.assertLess(source.index('optimized, size_report = optimize_sidebackup_same_run'),source.index('optimized, size_report = append_openssl_local_stage'))
        self.assertLess(source.index('optimized, size_report = append_openssl_local_stage'),source.index('russian = verify_russian_ipa'))
        self.assertIn('verify_final_widget(',source)
        self.assertIn("sha(stage_receipt) != expected_stage_sha",source)
        self.assertIn("Path('package-evidence/openssl-local-reports')",source)
        self.assertNotIn('export GITHUB_',source)
        workflow=(Path(caller.__file__).resolve().parents[2]/'.github/workflows/recover-native-package.yml').read_text()
        self.assertEqual(workflow.count('package_candidate.py --recover-fixed-native --strip-unsigned-widget --strip-widget-true-locals --strip-openssl-true-locals'),1)
        self.assertLess(workflow.index('acquire_openssl_host_qualification.py 2>&1'),workflow.index('package_candidate.py --recover-fixed-native'))
        self.assertNotIn('continue-on-error:',workflow)

if __name__=='__main__':unittest.main()
