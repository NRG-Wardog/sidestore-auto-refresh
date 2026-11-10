"""Shared artifact transport controls, without network or product execution."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
from pathlib import Path as _CIPath
import io, tempfile, traceback, unittest, urllib.error, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str((_CIPath(__file__).resolve().parents[3]/'ci/artifacts')))
import github_artifacts as gate

class ArtifactTransportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_redirect_never_forwards_api_authorization(self):
  api=gate.API('synthetic-private-token')
  error=urllib.error.HTTPError('https://api.github.com/fixed',302,'redirect',{'Location':'https://fixture.blob.core.windows.net/artifact?signature=synthetic'},None)
  class Response(io.BytesIO):pass
  with patch.object(api,'request',side_effect=error),patch.object(gate.urllib.request,'build_opener') as opener:
   opener.return_value.open.return_value=Response(b'fixed artifact')
   api.download(1,self.root/'download.zip')
   request=opener.return_value.open.call_args.args[0]
   self.assertEqual(request.header_items(),[])
   self.assertNotIn('synthetic-private-token',repr(request.headers))
 def test_signed_storage_exception_is_sanitized(self):
  location='https://fixture.blob.core.windows.net/artifact?signature=secret-test-signature'
  redirect=urllib.error.HTTPError('https://api.github.com/fixed',302,'redirect',{'Location':location},None)
  failure=urllib.error.HTTPError(location,403,'signed URL '+location,{},None)
  with patch.object(gate.API,'request',side_effect=redirect),patch.object(gate.urllib.request,'build_opener') as opener:
   opener.return_value.open.side_effect=failure
   try:gate.API('synthetic').download(1,self.root/'download')
   except ValueError as error:
    text=''.join(traceback.format_exception(error))
    self.assertNotIn('secret-test-signature',text);self.assertNotIn(location,text)
   else:self.fail('Storage failure must be rejected')
 def test_redirect_http_userinfo_and_unknown_host_rejected(self):
  for target in ['http://fixture.blob.core.windows.net/a','https://user:pass@fixture.blob.core.windows.net/a','https://evil.test/a','https://fixture.blob.core.windows.net:444/a']:
   error=urllib.error.HTTPError('https://api.github.com/fixed',302,'redirect',{'Location':target},None)
   with patch.object(gate.API,'request',side_effect=error),self.assertRaises(ValueError):gate.API('synthetic').download(1,self.root/'file')
 def test_foreign_api_path_rejected_before_open(self):
  with patch.object(gate.time,'sleep'),patch.object(gate.urllib.request,'build_opener') as opener:
   with self.assertRaisesRegex(ValueError,'Unexpected API path'):
    gate.API('synthetic').request('/repos/other/repository/actions/runs/1')
   opener.assert_not_called()
 def test_metadata_bound_is_enforced(self):
  with patch.object(gate.API,'request',return_value=io.BytesIO(b'x'*(2*1024*1024+1))):
   with self.assertRaisesRegex(ValueError,'Oversized API metadata'):gate.API('synthetic').json('/unused')
 def test_download_bound_is_enforced(self):
  with patch.object(gate.API,'request',return_value=io.BytesIO(b'oversized')),patch.object(gate,'LIMIT',3):
   with self.assertRaisesRegex(ValueError,'download exceeds bound'):gate.API('synthetic').download(1,self.root/'bounded')
 def test_download_never_overwrites_existing_file(self):
  path=self.root/'existing';path.write_bytes(b'preserve')
  with patch.object(gate.API,'request',return_value=io.BytesIO(b'replacement')):
   with self.assertRaisesRegex(ValueError,'transfer failed'):gate.API('synthetic').download(1,path)
  self.assertEqual(path.read_bytes(),b'preserve')
