"""Strict original ZIP32 parser, no extraction or payload execution."""
import pathlib,struct,zlib
from contract import require,SHA
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
  require(start+compressed<=v[6],'payload beyond central directory');dec=zlib.decompressobj(-15);raw=dec.decompress(stream,size+1)
  require(dec.eof and not dec.unused_data and not dec.unconsumed_tail and len(raw)==size and zlib.crc32(raw)==crc,'original DEFLATE integrity')
  total+=size;require(total<=64*1024**2,'aggregate input bound')
  rows.append({'index':i,'name':name.decode('ascii'),'local':local,'central':central,'stream':stream,'raw':raw,'sha256':SHA(raw),'compressed':compressed})
  pos+=len(central);local_end=start+compressed
 require(pos==len(data)-22 and local_end==v[6],'unaccounted ZIP bytes')
 return rows,end
