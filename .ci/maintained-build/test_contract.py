from pathlib import Path
import hashlib,json,os,unittest
ROOT=Path(__file__).resolve().parents[2]
CONTRACT=json.loads((ROOT/'.ci/maintained-build/contract.json').read_text())
class EntryContract(unittest.TestCase):
 def test_both_entries_use_one_pinned_readonly_pipeline(self):
  for name in ('livecontainer-build.yml','build-current.yml'):
   text=(ROOT/'.github/workflows'/name).read_text()
   self.assertIn('uses: NRG-Wardog/LiveContainer/.github/workflows/native-app-compile.yml@'+CONTRACT['implementation_commit'],text)
   self.assertIn('permissions:\n  contents: read',text)
   self.assertNotIn('secrets:',text)
   self.assertNotIn('push:',text)
 def test_immutable_implementation_matches_reviewed_bytes(self):
  base=Path(os.environ['MAINTAINED_IMPLEMENTATION_ROOT'])/'.github/workflows'
  for name,digest in CONTRACT['workflow_sha256'].items():
   self.assertEqual(hashlib.sha256((base/name).read_bytes()).hexdigest(),digest,name)
 def test_native_harness_and_caller_provenance_are_distinct(self):
  text=(Path(os.environ['MAINTAINED_IMPLEMENTATION_ROOT'])/'.github/workflows/native-app-compile.yml').read_text()
  self.assertIn('ref: '+CONTRACT['harness_commit'],text)
  self.assertIn('invocation-provenance.json',text)
  self.assertIn('CALLER_COMMIT:',text)
  self.assertIn('harness_commit=commit',text)
 def test_package_is_reviewed_exact_run_not_latest(self):
  text=(Path(os.environ['MAINTAINED_IMPLEMENTATION_ROOT'])/'.github/workflows/package-diagnostic.yml').read_text()
  self.assertIn("COMPILE_RUN: '"+str(CONTRACT['reviewed_native_run'])+"'",text)
  self.assertIn('REVIEWED_NATIVE_RECEIPT_SHA256:',text)
  self.assertIn(CONTRACT['packager_commit'],text)
  self.assertNotIn('contents: write',text)
  self.assertNotIn('gh release create',text)
if __name__=='__main__':unittest.main()
