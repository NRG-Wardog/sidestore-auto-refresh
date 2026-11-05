import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import native_transfer_seal as seal


class TransferSeal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = {'workflow_commit': 'a' * 40, 'run_id': '123', 'run_attempt': '1'}
        self.receipt = self.root / 'receipt.json'
        self.receipt.write_text(json.dumps(dict(self.identity, schema_version=1,
            status='COMPILE_ONLY_PASS_REQUIRES_REVIEW')))
        self.roots = {owner: self.root / owner for owner in seal.OWNERS}
        for root in self.roots.values():
            root.mkdir()
            (root / 'retained-files.json').write_text('{"symbols":{"test":"original"}}')
        self.output = self.root / 'seal.json'
        self.digest = seal.create(self.receipt, self.roots, self.output, self.identity)

    def verify(self, **kwargs):
        return seal.verify(self.output, self.receipt, self.roots, self.identity, **kwargs)

    def mutate(self, change):
        value = json.loads(self.output.read_text())
        change(value)
        self.output.write_text(json.dumps(value))

    def test_roundtrip_immutable(self):
        result = self.verify(expected_seal_sha=self.digest)
        self.assertEqual(set(result), seal.OWNERS)
        with self.assertRaises(TypeError): result['LiveContainer'] = '0' * 64

    def test_receipt_drift(self):
        self.receipt.write_text(self.receipt.read_text() + ' ')
        with self.assertRaises(ValueError): self.verify()

    def test_manifest_drift_self_consistent(self):
        # Even a plausible rewritten inventory has different bytes from retention.
        path = self.roots['LiveContainer'] / 'retained-files.json'
        path.write_text(json.dumps({'symbols': {'rewritten': hashlib.sha256(b'changed').hexdigest()}}))
        with self.assertRaises(ValueError): self.verify()

    def test_rewritten_seal_with_manifest_detected_by_external_hash(self):
        path = self.roots['LiveContainer'] / 'retained-files.json'
        path.write_text('{}')
        self.mutate(lambda v: v['retained_manifest_sha256'].update(LiveContainer=hashlib.sha256(path.read_bytes()).hexdigest()))
        with self.assertRaises(ValueError): self.verify(expected_seal_sha=self.digest)

    def test_wrong_run(self):
        self.identity['run_attempt'] = '2'
        with self.assertRaises(ValueError): self.verify()

    def test_receipt_not_compile_pass(self):
        value = json.loads(self.receipt.read_text()); value['status'] = 'FAIL'
        self.receipt.write_text(json.dumps(value))
        with self.assertRaises(ValueError): seal.create(self.receipt, self.roots, self.root/'new.json', self.identity)

    def test_extraneous_owner(self):
        self.roots['Extra'] = self.root
        with self.assertRaises(ValueError): self.verify()

    def test_malformed_hashes(self):
        original = self.output.read_bytes()
        for value in ['A'*64, '0'*63, '', None, 12]:
            self.output.write_bytes(original)
            self.mutate(lambda v: v['retained_manifest_sha256'].update(LiveContainer=value))
            with self.assertRaises(ValueError): self.verify()

    def test_linked_manifest(self):
        path = self.roots['LiveContainer']/'retained-files.json'
        real = self.root/'real.json'; path.rename(real); path.symlink_to(real)
        with self.assertRaises(ValueError): self.verify()

    def test_linked_root(self):
        real = self.roots['LiveContainer']; alias = self.root/'alias'; alias.symlink_to(real, target_is_directory=True)
        self.roots['LiveContainer'] = alias
        with self.assertRaises(ValueError): self.verify()

    def test_linked_receipt_and_seal(self):
        for path in (self.receipt, self.output):
            real = path.with_suffix('.original'); path.rename(real); path.symlink_to(real)
            with self.assertRaises(ValueError): self.verify()
            path.unlink(); real.rename(path)

    def test_fresh_output(self):
        original = self.output.read_bytes()
        with self.assertRaises(ValueError): seal.create(self.receipt, self.roots, self.output, self.identity)
        self.assertEqual(self.output.read_bytes(), original)
        self.output.unlink(); self.output.symlink_to(self.root/'absent')
        with self.assertRaises(ValueError): seal.create(self.receipt, self.roots, self.output, self.identity)
        self.assertFalse((self.root/'absent').exists())

    def test_duplicate_json_key(self):
        self.output.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError): self.verify()

    def test_directory_instead_of_manifest(self):
        path = self.roots['LiveContainer']/'retained-files.json'
        path.unlink(); path.mkdir()
        with self.assertRaises(ValueError): self.verify()

    def test_same_root_for_both_owners(self):
        self.roots['SideStore'] = self.roots['LiveContainer']
        with self.assertRaises(ValueError): self.verify()

    def test_unexpected_seal_owner(self):
        self.mutate(lambda v: v['retained_manifest_sha256'].update(Extra='0'*64))
        with self.assertRaises(ValueError): self.verify()

    def test_native_hash_malformed(self):
        self.mutate(lambda v: v.update(native_receipt_sha256='A'*64))
        with self.assertRaises(ValueError): self.verify()

    def test_alternate_cwd(self):
        code = ('import sys,json;sys.path.insert(0,sys.argv[1]);import native_transfer_seal as s;'
                's.verify(sys.argv[2],sys.argv[3],json.loads(sys.argv[4]),json.loads(sys.argv[5]),sys.argv[6])')
        subprocess.run([sys.executable, '-B', '-c', code, str(Path(__file__).resolve().parent),
                        str(self.output), str(self.receipt), json.dumps({k:str(v) for k,v in self.roots.items()}),
                        json.dumps(self.identity), self.digest], cwd=self.root, check=True)

if __name__ == '__main__': unittest.main()
