import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from project_native_evidence import project
class ProjectionTests(unittest.TestCase):
    def fixture(self,root,body=b'bound evidence',digest=None,receipt_override=None):
        digest=digest or hashlib.sha256(body).hexdigest()
        receipt=json.dumps({'evidence_sha256':{'probe.log':digest}}).encode()
        a=root/'input.zip'
        with zipfile.ZipFile(a,'w') as z:
            for name, data in [('native-receipt.json',receipt_override or receipt),('probe.log',body)]:
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
if __name__=='__main__':unittest.main()
