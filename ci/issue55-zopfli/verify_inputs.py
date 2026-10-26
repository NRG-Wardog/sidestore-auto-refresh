import pathlib,json,tarfile,hashlib,zipfile
if not __debug__:raise RuntimeError('Python optimization is not permitted for qualification')
r=pathlib.Path(__file__).parent
sha=lambda b:hashlib.sha256(b).hexdigest()
a=r/'candidate-artifact.zip';assert a.stat().st_size==25471679 and sha(a.read_bytes())=='daacc4c0402cc34f662f940de7f99db8a3788fb5b890f242257db85a425a1eff'
with zipfile.ZipFile(a) as z:
 assert z.testzip() is None
 name='LiveContainer-SideStore-issues-experimental.ipa';b=z.read(name);assert len(b)==24597739 and sha(b)=='e16afa534eb33e94c126b2b899240cb9894fd932e4cc36627efda033b08bb237'
 (r/'original.ipa').write_bytes(b)
 for name in ('provenance.json','openssl-local-stage.json','size-optimization.json','russian-localization.json','unsigned-widget-receipt.json'):(r/name).write_bytes(z.read(name))
t=json.loads((r/'official-tree.json').read_text());assert not t['truncated'];expected={x['path']:x for x in t['tree'] if x['type']=='blob'}
commit=json.loads((r/'official-commit.json').read_text());assert commit['sha']=='df1517dd8518113a245e4d203a04b8a0e02ab493' and commit['tree']['sha']=='b635c1f017ccce001399fcc3ff9c687cbf00f5b2'
archive=r/'source.tar.gz';assert sha(archive.read_bytes())=='ae5c3afb20156a52f2086277fd102a08d902c36d84db7eff74cc7e5e19b020c6';seen={};prefix='zopfli-df1517dd8518113a245e4d203a04b8a0e02ab493/'
with tarfile.open(archive,'r:gz') as tar:
 for m in tar.getmembers():
  assert m.name==prefix[:-1] or m.name.startswith(prefix)
  if m.isdir():continue
  assert m.isfile() and m.size<10000000 and not m.issym() and not m.islnk()
  name=m.name[len(prefix):];assert name in expected and name not in seen and '..' not in pathlib.PurePosixPath(name).parts
  data=tar.extractfile(m).read();assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==expected[name]['sha'],name
  seen[name]=sha(data)
  # Materialize only approved compressor C/header files and license/readme; no hooks/build scripts.
  if (name.startswith('src/zopfli/') and pathlib.PurePosixPath(name).suffix in ('.c','.h')) or name in ('README','COPYING'):
   p=r/'source'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
assert set(seen)==set(expected),(set(expected)-set(seen))
(r/'input-verification.json').write_text(json.dumps({'candidate_sha256':sha(b),'source_archive_sha256':sha(archive.read_bytes()),'official_commit':'df1517dd8518113a245e4d203a04b8a0e02ab493','source_file_sha256':seen,'all_git_blob_hashes_match':True,'archive_entries':len(seen)},indent=2))
print('Original candidate exact SHA/size verified; all',len(seen),'official Git source blobs verified; only C/headers/license/readme materialized')

# Reconstruct the complete approved Git tree before any compiler execution.
def treehash(path):
 prefix=path+'/' if path else '';rows=[x for x in t['tree'] if x['path'].startswith(prefix) and '/' not in x['path'][len(prefix):]];payload=b''
 for row in sorted(rows,key=lambda x:(x['path'][len(prefix):]+('/' if x['type']=='tree' else '')).encode()):
  digest=treehash(row['path']) if row['type']=='tree' else row['sha'];assert digest==row['sha']
  payload+=str(int(row['mode'])).encode()+b' '+row['path'][len(prefix):].encode()+b'\0'+bytes.fromhex(digest)
 return hashlib.sha1(b'tree '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
assert treehash('')==commit['tree']['sha']
print('Complete approved source tree reconstructed before compilation')
