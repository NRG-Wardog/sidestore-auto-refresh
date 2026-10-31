import hashlib,json,tempfile,unittest,zipfile,urllib.error,traceback
from unittest.mock import patch
from pathlib import Path
from project_native_evidence import project, validate_read_only_archive
import recover_native_artifacts as gate
import project_native_evidence as projection
from contract import manifest
class ProjectionTests(unittest.TestCase):
    def test_current_manifest_reaches_metadata_boundary(self):
        current = manifest(Path(projection.__file__).with_name('input-manifest.json'))
        with patch.object(projection, 'manifest', return_value=current), patch.object(projection.sys, 'argv', ['project_native_evidence.py']), patch.dict(projection.os.environ, {'GH_TOKEN': 'synthetic'}), patch.object(projection, 'API', side_effect=RuntimeError('metadata boundary')) as api:
            with self.assertRaisesRegex(RuntimeError, 'metadata boundary'):
                projection.main()
            api.assert_called_once_with('synthetic')

    def test_every_changed_manifest_field_rejected_before_network(self):
        current = manifest(Path(projection.__file__).with_name('input-manifest.json'))
        for key, value in current.items():
            changed = dict(current)
            changed[key] = value + 1 if type(value) is int else value + 'changed'
            with self.subTest(key=key), patch.object(projection, 'manifest', side_effect=[changed, current]), patch.object(projection.sys, 'argv', ['project_native_evidence.py']), patch.object(projection, 'API') as api:
                with self.assertRaisesRegex(ValueError, 'projection candidate differs'):
                    projection.main()
                api.assert_not_called()

    def test_old_native_candidate_rejected_before_network(self):
        current = manifest(Path(projection.__file__).with_name('input-manifest.json'))
        old = dict(current, run_id=41220384567, commit='dd1b08d0124e43caa8a1c7b490612382d46f1735', run_attempt=2)
        with patch.object(projection, 'manifest', side_effect=[old, current]), patch.object(projection.sys, 'argv', ['project_native_evidence.py']), patch.object(projection, 'API') as api:
            with self.assertRaisesRegex(ValueError, 'projection candidate differs'):
                projection.main()
            api.assert_not_called()

    def fixture(self,root,body=b'bound evidence',digest=None,receipt_override=None):
        digest=digest or hashlib.sha256(body).hexdigest()
        receipt=json.dumps({'evidence_sha256':{'probe.log':digest}}).encode()
        a=root/'input.zip'
        with zipfile.ZipFile(a,'w') as z:
            for name, data in [('receipt.json',receipt_override or receipt),('probe.log',body)]:
                entry=zipfile.ZipInfo(name);entry.create_system=3;entry.external_attr=0o100644<<16;z.writestr(entry,data)
        return a,receipt
    def check(self,a,r,o,**kw):
        return project(a,r,o,expected_sha=kw.get('sha',hashlib.sha256(a.read_bytes()).hexdigest()),expected_bytes=a.stat().st_size,expected_count=kw.get('count',1))
    def test_exact_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a,r=self.fixture(p);report=self.check(a,r,p/'out.zip')
            self.assertEqual(report['verified_reference_count'],1)
            with zipfile.ZipFile(p/'out.zip') as z:self.assertEqual(z.read('probe.log'),b'bound evidence')
    def test_bad_member_hash_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a,r=self.fixture(p,digest='0'*64)
            with self.assertRaisesRegex(ValueError,'hash differs'):self.check(a,r,p/'out.zip')
    def test_bad_archive_hash_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a,r=self.fixture(p)
            with self.assertRaisesRegex(ValueError,'archive bytes'):self.check(a,r,p/'out.zip',sha='0'*64)
    def test_reference_count_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a,r=self.fixture(p)
            with self.assertRaisesRegex(ValueError,'reference count'):self.check(a,r,p/'out.zip',count=2)
    def test_receipt_substitution_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a,r=self.fixture(p,receipt_override=b'{}')
            with self.assertRaisesRegex(ValueError,'receipt differs'):self.check(a,r,p/'out.zip')
    def test_storage_http_code_without_url_or_token(self):
        location='https://fixture.blob.core.windows.net/a?signature=private-fixture'
        redirect=urllib.error.HTTPError('https://api.github.com/fixed',302,'redirect',{'Location':location},None)
        failure=urllib.error.HTTPError(location,403,'signed '+location,{},None)
        with tempfile.TemporaryDirectory() as d, patch.object(gate.API,'request',side_effect=redirect), patch.object(gate.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=failure
            with self.assertRaises(ValueError) as found:gate.API('private-token').download(1,Path(d)/'output')
            text=''.join(traceback.format_exception(found.exception))
            self.assertIn('HTTP 403',text)
            for secret in ['private-fixture','private-token',location]:self.assertNotIn(secret,text)
    def test_storage_network_type_without_reason(self):
        location='https://fixture.blob.core.windows.net/a?signature=private-fixture'
        redirect=urllib.error.HTTPError('https://api.github.com/fixed',302,'redirect',{'Location':location},None)
        with tempfile.TemporaryDirectory() as d, patch.object(gate.API,'request',side_effect=redirect), patch.object(gate.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=urllib.error.URLError(OSError('private-fixture'))
            with self.assertRaises(ValueError) as found:gate.API('private-token').download(1,Path(d)/'output')
            self.assertIn('network_type=OSError',str(found.exception));self.assertNotIn('private-fixture',str(found.exception))
    def test_read_only_evidence_accepts_ordinary_modes_without_restoring(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'modes.zip'
            with zipfile.ZipFile(p,'w') as z:
                for mode in [0o444,0o664,0o700]:
                    e=zipfile.ZipInfo(str(mode));e.create_system=3;e.external_attr=(0o100000|mode)<<16;z.writestr(e,b'data')
            self.assertEqual(validate_read_only_archive(p),{'0o444':1,'0o664':1,'0o700':1})
    def test_read_only_evidence_rejects_privileged_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'modes.zip'
            with zipfile.ZipFile(p,'w') as z:
                e=zipfile.ZipInfo('setuid');e.create_system=3;e.external_attr=0o104755<<16;z.writestr(e,b'data')
            with self.assertRaisesRegex(ValueError,'special permission'):validate_read_only_archive(p)
    def test_actual_0700_evidence_mode_rejected_by_restore_but_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'actual-mode.zip'
            with zipfile.ZipFile(p,'w') as z:
                e=zipfile.ZipInfo('evidence.log');e.create_system=3;e.external_attr=0o100700<<16;z.writestr(e,b'data')
            with self.assertRaisesRegex(ValueError,'Unexpected archive mode'):gate.archive_plan(p)
            self.assertEqual(validate_read_only_archive(p),{'0o700':1})
if __name__=='__main__':unittest.main()
