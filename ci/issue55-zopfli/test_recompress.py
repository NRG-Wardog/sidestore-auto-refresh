import importlib.util,io,pathlib,struct,subprocess,sys,unittest,zipfile
ROOT=pathlib.Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('bounded_recompress',ROOT/'recompress.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def archive(extra=b'',comment=b''):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for n in ('Payload/Test.app/Info.plist','Payload/Test.app/code'):
   i=zipfile.ZipInfo(n);i.compress_type=8;i.extra=extra;i.external_attr=0o100644<<16;z.writestr(i,b'unchanged payload '*80)
  z.comment=comment
 return b.getvalue()
class Format(unittest.TestCase):
 def test_exact_zip_geometry(self):
  data=archive();rows,end=m.parse(data);self.assertEqual(len(rows),2);self.assertTrue(all(r['raw']==b'unchanged payload '*80 for r in rows));self.assertEqual(end,data[-22:])
 def test_no_trailing_or_truncated_archive(self):
  for data in (archive()[:-1],archive()+b'x',archive(comment=b'comment')):
   with self.assertRaises((ValueError,struct.error)):m.parse(bytes(data))
 def test_zip64_extra_rejected(self):
  with self.assertRaises(ValueError):m.parse(archive(struct.pack('<HHQ',1,8,100)))
 def test_local_crc_mutation_rejected(self):
  data=bytearray(archive());data[14]^=1
  with self.assertRaises(ValueError):m.parse(bytes(data))
 def test_duplicate_local_offset_rejected(self):
  data=bytearray(archive());pos=data.index(b'PK\x01\x02');pos=data.index(b'PK\x01\x02',pos+4);struct.pack_into('<I',data,pos+42,0)
  with self.assertRaises(ValueError):m.parse(bytes(data))
 def test_limiter_and_aggregate_budget(self):
  source=(ROOT/'recompress.py').read_text();limit=(ROOT/'limited_exec.py').read_text()
  self.assertIn('max_workers=2',source);self.assertIn('deadline=start+1600',source);self.assertIn('timeout=min(600,remaining)',source);self.assertNotIn('preexec_fn',source)
  for x in ('RLIMIT_AS,(2*1024**3,2*1024**3)','RLIMIT_CPU,(600,600)','RLIMIT_FSIZE,(64*1024**2,64*1024**2)','RLIMIT_CORE,(0,0)'):self.assertIn(x,limit)
  self.assertIn(".open('xb')",source);self.assertIn("not (ROOT/'recompressed.ipa').is_symlink()",source)
 def test_optimized_python_rejected_before_inputs(self):
  for name in ('verify_inputs.py','independent_verify.py','limited_exec.py'):
   p=subprocess.run([sys.executable,'-O',str(ROOT/name)],capture_output=True,text=True)
   self.assertNotEqual(p.returncode,0);self.assertIn('Python optimization is not permitted',p.stderr)
 def test_source_identity_before_compile(self):
  source=(ROOT/'verify_inputs.py').read_text();self.assertIn('ae5c3afb20156a52f2086277fd102a08d902c36d84db7eff74cc7e5e19b020c6',source);self.assertIn("assert treehash('')==commit['tree']['sha']",source)
if __name__=='__main__':unittest.main()
