"""Fail-closed source wiring, not actual artifact acquisition/native qualification."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
class HostWorkflowTests(unittest.TestCase):
 def test_acquisition_precedes_native_download_and_package_no_new_permissions(self):
  text=(ROOT/'.github/workflows/recover-native-package.yml').read_text()
  self.assertIn('permissions:\n  contents: read\n',text);self.assertNotIn('actions: read',text)
  for control in ('test_acquire_openssl_host_qualification.py','test_openssl_host_acquisition_workflow.py','test_openssl_local_packaging.py'):
   self.assertLess(text.index(control),text.index('      - name: Acquire exact successful OpenSSL host'))
  acquire=text.index('      - name: Acquire exact successful OpenSSL host')
  self.assertLess(acquire,text.index('      - name: Authenticate fixed original artifacts'))
  self.assertLess(acquire,text.index('package_candidate.py --recover-fixed-native'))
  self.assertIn('id: host_qualification',text)
  for name in ('test_openssl_local_symbol_policy.py','test_openssl_local_candidate.py','test_host_qualification.py'):
   self.assertLess(text.index(name),acquire)
  self.assertNotIn('continue-on-error:',text)
  self.assertEqual(text.count('package_candidate.py --recover-fixed-native --strip-unsigned-widget --strip-widget-true-locals --strip-openssl-true-locals'),1)
 def test_no_arbitrary_manifest_argument_or_acquired_app_upload(self):
  source=(ROOT/'ci/issues-native/acquire_openssl_host_qualification.py').read_text()
  text=(ROOT/'.github/workflows/recover-native-package.yml').read_text()
  self.assertIn("require(not sys.argv[1:]",source)
  self.assertIn("receipt,digest,report=acquire",source)
  self.assertLess(source.index('receipt,digest,report=acquire'),source.index("with open(os.environ['GITHUB_ENV']"))
  upload=text.split('Retain recovery diagnostics without changing original evidence',1)[1]
  self.assertIn("steps.host_qualification.outcome == 'failure'",upload)
  self.assertNotIn('openssl-host-inputs/',upload)
  self.assertIn('recovery-evidence/',upload)
if __name__=='__main__':unittest.main(verbosity=2)
