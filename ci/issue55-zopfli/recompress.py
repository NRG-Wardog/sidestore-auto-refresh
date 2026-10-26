"""Copy-only bounded exact ZIP32 DEFLATE experiment; no app payload execution."""
import concurrent.futures as cf
import datetime,hashlib,json,os,pathlib,resource,struct,subprocess,sys,time,zipfile,zlib
ROOT=pathlib.Path(__file__).resolve().parent
ORIGINAL=ROOT/'original.ipa'
EXPECTED='e16afa534eb33e94c126b2b899240cb9894fd932e4cc36627efda033b08bb237'
SHA=lambda b:hashlib.sha256(b).hexdigest()
def require(x,m):
 if not x:raise ValueError(m)
def parse(data):
 require(len(data)>=22,'short ZIP')
 end=data[-22:];v=struct.unpack('<4s4H2IH',end)
 require(v[0]==b'PK\x05\x06' and v[1]==v[2]==v[7]==0 and v[3]==v[4] and 0<v[4]<=512,'unsupported end record')
 require(v[6]+v[5]==len(data)-22,'central directory geometry')
 pos=v[6];rows=[];local_end=0;names=set();total=0
 for i in range(v[4]):
  require(data[pos:pos+4]==b'PK\x01\x02','central signature')
  h=data[pos:pos+46];flags,method=struct.unpack_from('<HH',h,8);crc,compressed,size=struct.unpack_from('<III',h,16);nl,el,cl,disk=struct.unpack_from('<4H',h,28);off=struct.unpack_from('<I',h,42)[0]
  require(flags==0 and method==8 and disk==0 and size<=20*1024**2 and off==local_end,'unsupported entry layout')
  name=data[pos+46:pos+46+nl];central=data[pos:pos+46+nl+el+cl]
  require(name not in names and name and b'\0' not in name,'duplicate/empty name');names.add(name)
  pn=pathlib.PurePosixPath(name.decode('ascii'));require(not pn.is_absolute() and '..' not in pn.parts,'unsafe name')
  for extra in (central[46+nl:46+nl+el],):
   ep=0
   while ep<len(extra):
    require(ep+4<=len(extra),'extra header');kind,length=struct.unpack_from('<HH',extra,ep);require(kind!=1 and ep+4+length<=len(extra),'ZIP64 or truncated extra');ep+=4+length
  require(data[off:off+4]==b'PK\x03\x04','local signature');lh=data[off:off+30];ln,le=struct.unpack_from('<HH',lh,26)
  require(struct.unpack_from('<HH',lh,6)==(flags,method) and struct.unpack_from('<III',lh,14)==(crc,compressed,size),'local metadata mismatch')
  require(data[off+30:off+30+ln]==name,'local name mismatch');local=data[off:off+30+ln+le];start=off+len(local);stream=data[start:start+compressed]
  require(start+compressed<=v[6],'payload beyond central directory');dec=zlib.decompressobj(-15);raw=dec.decompress(stream)+dec.flush()
  require(dec.eof and not dec.unused_data and not dec.unconsumed_tail and len(raw)==size and zlib.crc32(raw)==crc,'original DEFLATE integrity')
  total+=size;require(total<=64*1024**2,'aggregate input bound')
  rows.append({'index':i,'name':name.decode('ascii'),'local':local,'central':central,'stream':stream,'raw':raw,'sha256':SHA(raw),'compressed':compressed})
  pos+=len(central);local_end=start+compressed
 require(pos==len(data)-22 and local_end==v[6],'unaccounted ZIP bytes')
 return rows,end

def execute_one(row,deadline):
 start=time.monotonic();remaining=deadline-start
 if remaining<=1:return {'index':row['index'],'status':'aggregate_budget_exhausted'}
 source=ROOT/'inputs'/str(row['index']);out=ROOT/'streams'/str(row['index']);source.write_bytes(row['raw'])
 try:
  with out.open('xb') as handle:
   p=subprocess.run([sys.executable,str(ROOT/'limited_exec.py'),str(row['index'])],stdout=handle,stderr=subprocess.PIPE,timeout=min(600,remaining),env={'PATH':'/usr/bin:/bin','LANG':'C'},cwd=ROOT)
  require(p.returncode==0,'compressor failed '+str(p.returncode));stream=out.read_bytes();dec=zlib.decompressobj(-15);raw=dec.decompress(stream)+dec.flush()
  require(dec.eof and not dec.unused_data and not dec.unconsumed_tail and raw==row['raw'],'new DEFLATE differs')
  return {'index':row['index'],'status':'verified','old_bytes':len(row['stream']),'new_bytes':len(stream),'saved_bytes':max(0,len(row['stream'])-len(stream)),'stream_sha256':SHA(stream),'elapsed_seconds':time.monotonic()-start}
 except subprocess.TimeoutExpired:return {'index':row['index'],'status':'bounded_timeout','elapsed_seconds':time.monotonic()-start}

def main():
 data=ORIGINAL.read_bytes();require(len(data)==24597739 and SHA(data)==EXPECTED,'wrong original candidate')
 rows,end=parse(data);require(len(rows)==274,'entry closure');require(not (ROOT/'recompressed.ipa').exists() and not (ROOT/'recompressed.ipa').is_symlink(),'output already exists')
 (ROOT/'inputs').mkdir();(ROOT/'streams').mkdir();start=time.monotonic();deadline=start+1600
 metadata={'source_commit':'df1517dd8518113a245e4d203a04b8a0e02ab493','binary_sha256':SHA((ROOT/'zopfli').read_bytes()),'compiler_sha256':SHA(pathlib.Path('/usr/bin/cc').resolve().read_bytes()),'original_sha256':EXPECTED,'original_bytes':len(data),'iterations':5,'workers':2,'process_memory_limit':2*1024**3,'aggregate_timeout_seconds':1600,'entry_timeout_seconds':600}
 (ROOT/'experiment-start.json').write_text(json.dumps(metadata,indent=2))
 todo=sorted([r for r in rows if len(r['raw'])>=1024],key=lambda r:r['compressed'],reverse=True);results=[]
 with cf.ThreadPoolExecutor(max_workers=2) as pool:
  pending={};it=iter(todo)
  for _ in range(2):
   r=next(it,None)
   if r is not None:pending[pool.submit(execute_one,r,deadline)]=r
  while pending:
   done,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
   for f in done:
    row=pending.pop(f);result=f.result();results.append(result)
    (ROOT/'progress.json').write_text(json.dumps({'completed':len(results),'eligible_entries':len(todo),'saved_bytes':sum(x.get('saved_bytes',0) for x in results),'elapsed_seconds':time.monotonic()-start,'results':results},indent=2))
    print(json.dumps({'name':row['name'],**result}),flush=True)
    r=next(it,None) if time.monotonic()<deadline-1 else None
    if r is not None:pending[pool.submit(execute_one,r,deadline)]=r
 chosen={x['index']:x for x in results if x['status']=='verified' and x['saved_bytes']>0};body=bytearray();central=bytearray()
 for row in rows:
  stream=(ROOT/'streams'/str(row['index'])).read_bytes() if row['index'] in chosen else row['stream'];local=bytearray(row['local']);header=bytearray(row['central']);struct.pack_into('<I',local,18,len(stream));struct.pack_into('<I',header,20,len(stream));struct.pack_into('<I',header,42,len(body));body+=local+stream;central+=header
 ending=bytearray(end);struct.pack_into('<II',ending,12,len(central),len(body));output=bytes(body+central+ending);final_rows,_=parse(output)
 for old,new in zip(rows,final_rows):
  require(old['raw']==new['raw'] and old['name']==new['name'],'payload changed')
  ol=bytearray(old['local']);nl=bytearray(new['local']);ol[18:22]=nl[18:22]=b'\0'*4;require(ol==nl,'local metadata changed')
  oc=bytearray(old['central']);nc=bytearray(new['central']);oc[20:24]=nc[20:24]=b'\0'*4;oc[42:46]=nc[42:46]=b'\0'*4;require(oc==nc,'central metadata changed')
 require(SHA((ROOT/'zopfli').read_bytes())==metadata['binary_sha256'],'compressor changed');require(SHA(ORIGINAL.read_bytes())==EXPECTED,'original modified');require(len(output)<=len(data),'archive grew')
 with (ROOT/'recompressed.ipa').open('xb') as final:final.write(output)
 with zipfile.ZipFile(ROOT/'recompressed.ipa') as z:require(z.testzip() is None,'independent ZIP CRC failure')
 metadata.update(final_bytes=len(output),final_sha256=SHA(output),saved_bytes=len(data)-len(output),target_met=len(output)<=24000000,changed_compressed_streams=len(chosen),elapsed_seconds=time.monotonic()-start,all_uncompressed_bytes_identical=True,all_archive_metadata_preserved=True,original_unchanged=True,signatures_bytes_identical=True,device_executed=False,results=results)
 (ROOT/'result.json').write_text(json.dumps(metadata,indent=2));print('FINAL',json.dumps({k:v for k,v in metadata.items() if k!='results'}),flush=True)
if __name__=='__main__':main()
