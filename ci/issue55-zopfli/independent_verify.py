"""Independent read-only ZIP/source audit. Never invokes compressor/app binaries."""
import argparse,hashlib,io,json,pathlib,struct,tarfile,zipfile,zlib
if not __debug__:raise RuntimeError('Python optimization is not permitted for qualification')
assert __debug__, "Audit must run without Python optimization"
parser=argparse.ArgumentParser();parser.add_argument("--root",type=pathlib.Path,default=pathlib.Path(__file__).resolve().parent);args=parser.parse_args()
R=args.root.resolve(strict=True)
result=json.loads((R/"result.json").read_text())
sha=lambda b:hashlib.sha256(b).hexdigest()
old=(R/'original.ipa').read_bytes();new=(R/'recompressed.ipa').read_bytes()
assert (len(old),sha(old))==(24597739,'e16afa534eb33e94c126b2b899240cb9894fd932e4cc36627efda033b08bb237')
assert (len(new),sha(new))==(result['final_bytes'],result['final_sha256'])
assert len(new)<=24000000 and result['target_met'] is True
assert (len(old),sha(old))==(result['original_bytes'],result['original_sha256'])
assert result['saved_bytes']==len(old)-len(new)
assert result['source_commit']=='df1517dd8518113a245e4d203a04b8a0e02ab493'
assert result['iterations']==5 and result['workers']==2 and result['process_memory_limit']==2147483648
assert result['aggregate_timeout_seconds']==1600 and result['entry_timeout_seconds']==600
assert 0<=result['elapsed_seconds']<=1600
assert result['all_uncompressed_bytes_identical'] is True and result['all_archive_metadata_preserved'] is True
assert result['original_unchanged'] is True and result['signatures_bytes_identical'] is True and result['device_executed'] is False
a=zipfile.ZipFile(io.BytesIO(old));b=zipfile.ZipFile(io.BytesIO(new));assert a.testzip() is None and b.testzip() is None;assert a.namelist()==b.namelist() and len(a.infolist())==274 and a.comment==b.comment
inventory=[];changed=0;ap=a.start_dir;bp=b.start_dir
for x,y in zip(a.infolist(),b.infolist()):
 raw=a.read(x);assert raw==b.read(y)
 for attr in ('filename','date_time','compress_type','comment','extra','create_system','create_version','extract_version','reserved','flag_bits','volume','internal_attr','external_attr','CRC','file_size'):assert getattr(x,attr)==getattr(y,attr),(x.filename,attr)
 def lh(data,i):
  off=i.header_offset;assert data[off:off+4]==b'PK\x03\x04';nl,el=struct.unpack_from('<HH',data,off+26);end=off+30+nl+el;h=bytearray(data[off:end]);h[18:22]=bytes(4);return h,data[end:end+i.compress_size]
 oh,os=lh(old,x);nh,ns=lh(new,y);assert oh==nh;changed+=os!=ns
 def ch(data,pos):
  assert data[pos:pos+4]==b'PK\x01\x02';nl,el,cl=struct.unpack_from('<HHH',data,pos+28);end=pos+46+nl+el+cl;v=bytearray(data[pos:end]);v[20:24]=bytes(4);v[42:46]=bytes(4);return v,end
 oc,ap=ch(old,ap);nc,bp=ch(new,bp);assert oc==nc
 inventory.append({'path':x.filename,'uncompressed_bytes':len(raw),'sha256':sha(raw),'old_compressed_bytes':x.compress_size,'new_compressed_bytes':y.compress_size,'compressed_stream_changed':os!=ns})
assert ap==len(old)-22 and bp==len(new)-22
end1=bytearray(old[ap:]);end2=bytearray(new[bp:]);end1[16:20]=end2[16:20]=bytes(4);assert end1==end2
ru=json.loads((R/'russian-localization.json').read_text())
for n,h in ru['resource_sha256'].items():assert sha(b.read(n))==h
widget='Payload/LiveContainer.app/PlugIns/LiveWidgetExtension.appex/LiveWidgetExtension';prov=json.loads((R/'provenance.json').read_text());assert sha(b.read(widget))==prov['unsigned_widget_prepackaging']['initial_and_final_signed_binary_sha256']
nested='Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideBackup.ipa';assert a.read(nested)==b.read(nested)
with zipfile.ZipFile(io.BytesIO(b.read(nested))) as nestedzip:assert nestedzip.testzip() is None
# Reconstruct every Git tree directly from validated archive blob bytes.
commit=json.loads((R/'official-commit.json').read_text());tree=json.loads((R/'official-tree.json').read_text());assert not tree['truncated'];pin='df1517dd8518113a245e4d203a04b8a0e02ab493';assert commit['sha']==pin
expected={v['path']:v for v in tree['tree'] if v['type']=='blob'};seen={};node={}
with tarfile.open(R/'source.tar.gz','r:gz') as tar:
 for m in tar.getmembers():
  if m.isdir():continue
  assert m.isfile() and not m.issym() and not m.islnk();prefix='zopfli-'+pin+'/';assert m.name.startswith(prefix);name=m.name[len(prefix):];assert name in expected and name not in seen;data=tar.extractfile(m).read();digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest();assert digest==expected[name]['sha'];seen[name]=sha(data);cur=node
  for part in name.split('/')[:-1]:cur=cur.setdefault(part,{})
  cur[name.split('/')[-1]]=(expected[name]['mode'],digest)
  material=R/'source'/name
  if material.exists():assert material.is_file() and not material.is_symlink() and material.read_bytes()==data
assert set(seen)==set(expected)
def treehash(n):
 rows=[]
 for name,v in n.items():
  if isinstance(v,dict):mode,d='40000',treehash(v);sort=name+'/'
  else:mode,d=v;sort=name
  rows.append((sort,mode.encode()+b' '+name.encode()+b'\0'+bytes.fromhex(d)))
 payload=b''.join(v for _,v in sorted(rows));return hashlib.sha1(b'tree '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
source_tree=treehash(node);assert source_tree==commit['tree']['sha']=='b635c1f017ccce001399fcc3ff9c687cbf00f5b2'
result=json.loads((R/'result.json').read_text());assert result['binary_sha256']==sha((R/'zopfli').read_bytes());assert result['compiler_sha256']==sha(pathlib.Path('/usr/bin/cc').resolve().read_bytes());assert len(result['results'])==110 and all(x['status']=='verified' for x in result['results']);assert changed==result['changed_compressed_streams']
eligible={i for i,x in enumerate(a.infolist()) if x.file_size>=1024}
assert len(eligible)==110 and {x['index'] for x in result['results']}==eligible
assert len({x['index'] for x in result['results']})==len(result['results'])
for row in result['results']:
 i=row['index'];x=a.infolist()[i];y=b.infolist()[i]
 assert row['old_bytes']==x.compress_size
 stream=(R/'streams'/str(i)).read_bytes()
 assert row['new_bytes']==len(stream) and row['stream_sha256']==sha(stream)
 decoder=zlib.decompressobj(-15);decoded=decoder.decompress(stream,x.file_size+1)
 assert decoded==a.read(x) and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
 assert row['saved_bytes']==max(0,x.compress_size-len(stream))
 assert 0<=row['elapsed_seconds']<=600
 assert y.compress_size==min(x.compress_size,len(stream))
 if row['saved_bytes']>0:assert lh(new,y)[1]==stream
 else:assert lh(new,y)[1]==lh(old,x)[1]
assert sum(x['saved_bytes'] for x in result['results'])==len(old)-len(new)
report={'schema':'independent-zopfli-copy-audit-v1','status':'PASS_IDENTICAL_UNCOMPRESSED_PAYLOAD_AND_ZIP_METADATA','original_bytes':len(old),'original_sha256':sha(old),'final_bytes':len(new),'final_sha256':sha(new),'saved_bytes':len(old)-len(new),'target_decimal_bytes':24000000,'target_met':True,'below_target_bytes':24000000-len(new),'entries':len(inventory),'changed_compressed_streams':changed,'all_payload_bytes_identical':True,'all_raw_local_and_central_metadata_identical_except_compressed_sizes_and_offsets':True,'archive_comment_and_eocd_fields_preserved_except_central_offset':True,'original_unchanged':True,'zip_and_nested_sidebackup_crc_pass':True,'all_binary_signature_bytes_unchanged':True,'russian_resources_match_original_2154_key_coverage_receipt':True,'widget_signed_sha256':sha(b.read(widget)),'source_commit':pin,'reconstructed_git_tree':source_tree,'all_git_blobs_verified':len(seen),'binary_sha256':result['binary_sha256'],'reported_compression_seconds':result['elapsed_seconds'],'limits_reviewed':{'workers':2,'per_process_memory_bytes':2147483648,'per_entry_seconds':600,'aggregate_timeout_seconds':1600,'clean_exec_environment':True,'exclusive_final_output':True},'scope_limits':['No compressor or application execution by independent auditor.','Original native CI evidence is inherited through identical payload bytes; no new native/device execution claimed.','Runtime resource-limit enforcement was code-reviewed; memory high-water marks were not independently measured.','Official commit identity is bound to retained official API metadata and reconstructed Git tree, not an independently fetched commit signature.'],'members':inventory}
(R/'independent-audit.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
(R/'independent-audit.md').write_text(f"""# Independent Zopfli audit

**PASS: {len(new):,} bytes**, {24000000-len(new):,} bytes below the 24,000,000-byte target. Savings: **{len(old)-len(new):,} bytes**.

SHA256: `{sha(new)}`

All {len(inventory)} members decompress to exactly the original bytes. Raw ZIP local/central metadata is identical except compressed sizes and required offsets; {changed} DEFLATE streams changed. ZIP and nested SideBackup CRC checks pass. Every binary/signature, Russian resource and widget payload is unchanged. Original IPA SHA remains intact.

All {len(seen)} official source blobs and reconstructed Git tree match the pinned Google commit `{pin}`. Compressor/compiler hashes match the experiment receipt. All 110 eligible streams have verified receipts. Two-worker resource controls enforce a 1,600-second aggregate compression bound. Reported compression time: {result['elapsed_seconds']:.2f} seconds.

This verifier executes no compressor or application. Original native CI evidence is inherited through exact payload equality; no new device-runtime verification is claimed. Resource controls were source-reviewed, not independently measured memory high-water marks. Commit identity is bound to retained official metadata and reconstructed Git tree, not a signed commit.
""")
print(json.dumps({k:v for k,v in report.items() if k!='members'},indent=2));print('AUDIT_SHA256',sha((R/'independent-audit.json').read_bytes()))
