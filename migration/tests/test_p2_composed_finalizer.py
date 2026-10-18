"""Pure file-finalization checks; real capture parsing has separate mutation tests."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('p2_candidate_finalizer',ROOT/'migration/finalize_p2_composed_candidate.py')
finalizer=importlib.util.module_from_spec(spec);spec.loader.exec_module(finalizer)

class CandidateWriteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.files={'sample.json':b'{"synthetic_only":true}\n'}
  self.review={'files':{name:hashlib.sha256(raw).hexdigest()for name,raw in self.files.items()}}
  self.summary={'candidate_review_sha256':hashlib.sha256(finalizer.dump(self.review)).hexdigest(),'activated':False}
 def test_write_fresh_preserves_exact_bytes(self):
  out=self.root/'candidate';finalizer.write_candidate(out,self.files,self.review,self.summary)
  self.assertEqual((out/'metadata/sample.json').read_bytes(),self.files['sample.json'])
  self.assertEqual((out/'candidate-review.json').read_bytes(),finalizer.dump(self.review))
 def test_existing_output_rejected(self):
  out=self.root/'candidate';out.mkdir()
  with self.assertRaises(ValueError):finalizer.write_candidate(out,self.files,self.review,self.summary)
 def test_symlinked_parent_rejected(self):
  link=self.root/'link';link.symlink_to(self.root,target_is_directory=True)
  with self.assertRaises(ValueError):finalizer.write_candidate(link/'candidate',self.files,self.review,self.summary)
 def test_escaped_file_rejected(self):
  self.files={'../escape':b'x'};self.review={'files':{'../escape':hashlib.sha256(b'x').hexdigest()}}
  self.summary['candidate_review_sha256']=hashlib.sha256(finalizer.dump(self.review)).hexdigest()
  with self.assertRaises(ValueError):finalizer.write_candidate(self.root/'candidate',self.files,self.review,self.summary)
  self.assertFalse((self.root/'escape').exists())
 def test_mismatched_file_digest_rejected(self):
  self.files['sample.json']+=b'\n'
  with self.assertRaises(ValueError):finalizer.write_candidate(self.root/'candidate',self.files,self.review,self.summary)
 def test_summary_cannot_grant_acceptance(self):
  self.summary['activated']=True
  with self.assertRaises(ValueError):finalizer.write_candidate(self.root/'candidate',self.files,self.review,self.summary)
if __name__=='__main__':unittest.main()
