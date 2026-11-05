"""Independent read-only final archive audit; no driver parser or compressor execution."""
import argparse,hashlib,io,json,pathlib,struct,tarfile,zipfile,zlib
from contract import manifest,read_json,require,SHA,PIN,BUDGET,ITERATIONS,SOURCE_SHA
if not __debug__: raise RuntimeError('Python optimization forbidden')
parser=argparse.ArgumentParser();parser.add_argument('--root',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parent);args=parser.parse_args()
R=args.root.resolve(strict=True);input_manifest=manifest(R/'input-manifest.json');result=read_json(R/'result.json')
sha=SHA
old=(R/'original.ipa').read_bytes();new=(R/'recompressed.ipa').read_bytes()
assert (len(old),sha(old))==(input_manifest['ipa_bytes'],input_manifest['ipa_sha256'])
assert (len(new),sha(new))==(result['final_bytes'],result['final_sha256'])
assert len(new)<=min(len(old),24000000) and result['target_met'] is True
assert (len(old),sha(old))==(result['original_bytes'],result['original_sha256'])
assert result['saved_bytes']==len(old)-len(new)
assert result['schema']=='language-switch-zopfli-quick-v1' and result['source_commit']==PIN
assert result['workers']==2 and result['process_memory_limit']==2147483648
assert result['aggregate_timeout_seconds']==BUDGET and result['entry_timeout_seconds']==600
assert result['approved_total_budget_seconds']==1800 and result['iterations_schedule']==list(ITERATIONS)
assert 0<=result['elapsed_seconds']<=BUDGET+5 and result['device_executed'] is False
a=zipfile.ZipFile(io.BytesIO(old));b=zipfile.ZipFile(io.BytesIO(new));assert a.testzip() is None and b.testzip() is None;assert a.namelist()==b.namelist() and len(a.infolist())==input_manifest['entries'] and a.comment==b.comment
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
language_report=read_json(R/'language-switch-tests.json')
assert prov['language_switch_regression_sha256']==sha((R/'language-switch-tests.json').read_bytes())
assert language_report['schema']=='live-language-switch-v1' and language_report['status']=='PASS_SAME_SESSION_IOS_SIMULATOR_NOT_PHYSICAL_DEVICE'
assert language_report['run_identity']=={'workflow_commit':input_manifest['commit'],'run_id':str(input_manifest['run_id']),'run_attempt':str(input_manifest['run_attempt'])}
assert language_report['simulator_execution'] is True and language_report['physical_device_execution'] is False
assert type(language_report['transition_count']) is int and language_report['transition_count']==12
native_report=read_json(R/'native-receipt.json')
assert native_report['evidence_sha256']['language-switch-tests.json']==sha((R/'language-switch-tests.json').read_bytes())
nested='Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideBackup.ipa';assert a.read(nested)==b.read(nested)
with zipfile.ZipFile(io.BytesIO(b.read(nested))) as nestedzip:assert nestedzip.testzip() is None
assert sha((R/'source.tar.gz').read_bytes())==SOURCE_SHA
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

assert result['binary_sha256']==sha((R/'zopfli').read_bytes())
assert result['compiler_sha256']==sha(pathlib.Path('/usr/bin/cc').resolve().read_bytes())
assert result['entries']==len(a.infolist())
eligible={i for i,x in enumerate(a.infolist()) if x.file_size>=1024}
assert result['eligible_entries']==len(eligible)
seen_attempts=set();per_entry={i:[] for i in eligible};attempt_savings=0
for attempt in result['results']:
 i=attempt['index'];iteration=attempt['iterations'];status=attempt['status']
 assert type(i) is int and i in eligible and type(iteration) is int and iteration in ITERATIONS
 assert (i,iteration) not in seen_attempts;seen_attempts.add((i,iteration))
 prior=per_entry[i]
 assert not prior or (prior[-1]['iterations']<iteration and prior[-1]['status']=='verified')
 assert status in ('verified','bounded_timeout','budget_exhausted','compressor_exit')
 expected_keys={'index','iterations','status','elapsed_seconds','improved','saved_vs_previous'}
 if status=='verified':expected_keys|={'bytes','sha256'}
 if status=='compressor_exit':
  expected_keys.add('exit_code')
  assert type(attempt['exit_code']) is int and attempt['exit_code']!=0
 assert set(attempt)==expected_keys
 assert 0<=attempt['elapsed_seconds']<=605
 prior.append(attempt)
 if status=='verified':
  stream=(R/'streams'/f'{i}-{iteration}.deflate').read_bytes()
  assert len(stream)==attempt['bytes'] and sha(stream)==attempt['sha256']
  decoder=zlib.decompressobj(-15);decoded=decoder.decompress(stream,a.infolist()[i].file_size+1)
  assert decoded==a.read(a.infolist()[i]) and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
 else: assert not (R/'streams'/f'{i}-{iteration}.deflate').exists()
assert set(result['best'])=={str(i) for i in range(len(a.infolist()))}
for i,(x,y) in enumerate(zip(a.infolist(),b.infolist())):
 original_stream=lh(old,x)[1]
 expected={'bytes':len(original_stream),'sha256':sha(original_stream),'iterations':0}
 for attempt in per_entry.get(i,[]):
  before=expected['bytes']
  if attempt['status']=='verified' and attempt['bytes']<before:
   expected={'bytes':attempt['bytes'],'sha256':attempt['sha256'],'iterations':attempt['iterations']}
  assert attempt['saved_vs_previous']==before-expected['bytes']
  assert attempt['improved'] is (expected['bytes']<before)
 assert result['best'][str(i)]==expected
 selected=original_stream if expected['iterations']==0 else (R/'streams'/f"{i}-{expected['iterations']}.deflate").read_bytes()
 assert lh(new,y)[1]==selected
assert sum(x['saved_vs_previous'] for x in result['results'])==len(old)-len(new)
# Each round accounts for exactly the recorded attempts. No fictitious long run claim.
assert [x['iterations'] for x in result['rounds']]==list(ITERATIONS)[:len(result['rounds'])]
assert all(x['attempts']==sum(y['iterations']==x['iterations'] for y in result['results']) and 0<=x['elapsed_seconds']<=BUDGET+5 for x in result['rounds'])
assert result['stop_reason'] in ('iteration_schedule_exhausted','aggregate_budget_reached','remaining_candidates_plateau_or_estimated_cost_exceeds_bounds')
if result['stop_reason']=='aggregate_budget_reached':assert result['elapsed_seconds']>=BUDGET-3
if result['stop_reason']=='iteration_schedule_exhausted':assert len(result['rounds'])==len(ITERATIONS)
if result['stop_reason']=='remaining_candidates_plateau_or_estimated_cost_exceeds_bounds':assert result['rounds'][-1]['attempts']==0
report={'schema':'language-switch-independent-zopfli-audit-v1','status':'PASS_IDENTICAL_PAYLOAD_AND_ARCHIVE_METADATA','input':input_manifest,'final_bytes':len(new),'final_sha256':sha(new),'saved_bytes':len(old)-len(new),'target_met':len(new)<=24000000,'entries':len(inventory),'changed_compressed_streams':changed,'all_payload_bytes_identical':True,'all_binary_signature_bytes_identical':True,'all_archive_metadata_preserved_except_compressed_sizes_and_offsets':True,'original_unchanged':True,'russian_resource_hashes_match_receipt':True,'nested_sidebackup_crc_pass':True,'source_commit':pin,'source_tree':source_tree,'source_blobs':len(seen),'verified_attempts':sum(x['status']=='verified' for x in result['results']),'bounded_timeouts':sum(x['status']=='bounded_timeout' for x in result['results']),'nonzero_compressor_exits':sum(x['status']=='compressor_exit' for x in result['results']),'iterations_schedule':list(ITERATIONS),'stop_reason':result['stop_reason'],'compression_seconds':result['elapsed_seconds'],'resource_limits':{'workers':2,'per_process_memory_bytes':2147483648,'per_attempt_wall_seconds':600,'aggregate_compression_seconds':BUDGET,'approved_total_seconds':1800},'device_executed':False,'scope_limits':['Compression changes download size, not installed payload size.','Native qualification inherited only through byte-identical input payloads.','No native application execution by this audit.','Resource enforcement source-reviewed; no independent peak memory measurement.'],'members':inventory}
(R/'independent-audit.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='members'},indent=2))
