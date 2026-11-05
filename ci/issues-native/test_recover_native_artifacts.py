"""Owned synthetic acquisition/archive controls; never downloads or executes products."""
import io
import json
from pathlib import Path
import stat
import tempfile
import traceback
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
import recover_native_artifacts as gate

class RecoveryArtifactTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def archive(self,rows):
  target=self.root/'fixture.zip'
  with zipfile.ZipFile(target,'w') as z:
   for name,mode,data in rows:
    item=zipfile.ZipInfo(name);item.create_system=3;item.external_attr=mode<<16;z.writestr(item,data)
  return target
 def test_regular_files_and_modes_are_preserved(self):
  path=self.archive([('root/',stat.S_IFDIR|0o755,b''),('root/program',stat.S_IFREG|0o755,b'not executed'),('root/private.json',stat.S_IFREG|0o600,b'{}')])
  gate.extract(path,self.root/'output',prefix='root')
  self.assertEqual((self.root/'output/root/program').stat().st_mode&0o777,0o755)
  self.assertEqual((self.root/'output/root/private.json').stat().st_mode&0o777,0o600)
 def test_traversal_and_ambiguous_paths_rejected_before_destination_exists(self):
  for name in ['../escape','/absolute','a/../escape','a//b','a/./b','C:drive','a\\b','a//']:
   with self.subTest(name=name):
    path=self.archive([(name,stat.S_IFREG|0o644,b'x')])
    with self.assertRaises(ValueError):gate.extract(path,self.root/'output')
    self.assertFalse((self.root/'output').exists())
 def test_symlink_special_and_unexpected_modes_rejected(self):
  for mode in [stat.S_IFLNK|0o777,stat.S_IFIFO|0o644,stat.S_IFREG|0o4755,0,stat.S_IFREG|0o777]:
   with self.subTest(mode=mode):
    path=self.archive([('file',mode,b'target')])
    with self.assertRaises(ValueError):gate.archive_plan(path)
 def test_duplicate_casefold_and_file_parent_rejected(self):
  for names in [('a','A'),('a','a/b')]:
   path=self.archive([(name,stat.S_IFREG|0o644,b'x') for name in names])
   with self.assertRaises(ValueError):gate.archive_plan(path)
 def test_prefix_and_existing_destination_rejected(self):
  path=self.archive([('root/file',stat.S_IFREG|0o644,b'x')])
  with self.assertRaises(ValueError):gate.archive_plan(path,prefix='other')
  output=self.root/'output';output.mkdir()
  with self.assertRaises(ValueError):gate.extract(path,output)
 def test_fixed_outer_hash_precedes_restore(self):
  downloads=self.root/'downloads';downloads.mkdir();workspace=self.root/'workspace';workspace.mkdir();temp=self.root/'temp';temp.mkdir()
  for owner in gate.ARTIFACTS:(downloads/(owner+'.zip')).write_bytes(b'wrong')
  with self.assertRaisesRegex(ValueError,'outer artifact'):gate.restore(downloads,workspace,temp)
  self.assertFalse((workspace/'evidence').exists())
 def test_exact_run_provenance_and_failure_boundary(self):
  value={'id':gate.RUN,'head_sha':gate.HEAD,'run_attempt':1,'repository':{'full_name':gate.REPOSITORY},'head_repository':{'full_name':gate.REPOSITORY},'path':'.github/workflows/issues-native.yml','status':'completed','conclusion':'failure'}
  gate.validate_run(value)
  for key,bad in [('id',1),('head_sha','0'*40),('run_attempt',2),('status','in_progress'),('conclusion','success')]:
   with self.subTest(key=key),self.assertRaises(ValueError):gate.validate_run({**value,key:bad})
 def test_exact_artifact_identity_hash_and_expiry(self):
  for owner,expected in gate.ARTIFACTS.items():
   value={'id':expected['id'],'name':gate.artifact_name(owner),'expired':False,'size_in_bytes':1024,'digest':'sha256:'+expected['sha256'],'workflow_run':{'id':gate.RUN,'head_sha':gate.HEAD}}
   gate.validate_artifact(value,owner)
   for key,bad in [('id',1),('name','wrong'),('expired',True),('size_in_bytes',True),('digest','sha256:'+'0'*64)]:
    with self.subTest(owner=owner,key=key),self.assertRaises(ValueError):gate.validate_artifact({**value,key:bad},owner)
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
 def test_ldid_acquisition_policy_matches_existing_native_workflow_exactly(self):
  root=Path(__file__).resolve().parents[2]
  original=(root/'.github/workflows/issues-native.yml').read_text()
  recovery=(root/'.github/workflows/recover-native-package.yml').read_text()
  begin='          # Formula metadata is checked'
  expected=original[original.index(begin):original.index('      - name: Upload experimental candidate')]
  actual=recovery[recovery.index(begin):recovery.index('      - name: Upload verified experimental IPA')]
  recovery_cli='package_candidate.py --recover-fixed-native --strip-unsigned-widget --strip-widget-true-locals --strip-openssl-true-locals'
  self.assertEqual(actual.count(recovery_cli),1)
  self.assertEqual(actual.replace(recovery_cli,'package_candidate.py'),expected)
 def test_checkout_trust_and_diagnostics_are_scoped_to_attempted_owned_steps(self):
  root=Path(__file__).resolve().parents[2];text=(root/'.github/workflows/recover-native-package.yml').read_text()
  self.assertEqual(text.count('uses: actions/checkout@'),2)
  self.assertEqual(text.count('set-safe-directory: false'),2)
  block=text.split('      - name: Retain recovery diagnostics without changing original evidence\n',1)[1]
  expected="        if: ${{ !cancelled() && ("+' || '.join("steps."+step+".outcome == '"+outcome+"'" for step in ('controls','host_qualification','acquisition','size_controls','packaging') for outcome in ('success','failure'))+") }}"
  self.assertIn(expected,block)
  self.assertNotIn('always()',text)
  for step in ('controls','host_qualification','acquisition','size_controls','packaging'):
   self.assertEqual(text.count('        id: '+step+'\n'),1)
 def test_workflow_exact_boundaries_no_rebuild_no_identity_spoof(self):
  root=Path(__file__).resolve().parents[2];text=(root/'.github/workflows/recover-native-package.yml').read_text()
  for expected in ('persist-credentials: false','--recover-fixed-native','93634e6dd7bae045a9d63f4a4df7ab5f9dd82f49','Xcode 26.4.1','--force-bottle','contents: read'):self.assertIn(expected,text)
  for forbidden in ('GITHUB_SHA:','GITHUB_RUN_ID:','GITHUB_RUN_ATTEMPT:','CLANG_COVERAGE_MAPPING=','build_ios.py','-scheme LiveContainer','--location-trusted','actions: read','actions: write','contents: write'):self.assertNotIn(forbidden,text)
  self.assertNotIn('> evidence/',text)
  self.assertNotIn('mkdir -p recovery-evidence package-evidence diagnostic size-diagnostics',text)
  self.assertIn('test_recover_native.py',text)
  self.assertIn('if: success()',text)

if __name__=='__main__':unittest.main(verbosity=2)
