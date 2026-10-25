"""Independent bounded DER/CodeDirectory checks; never trusts the fixture chain."""
import hashlib
from pathlib import Path
import plistlib
import struct

class DER:
    def __init__(self, raw, tag, body, end):
        self.raw, self.tag, self.body, self.end = raw, tag, body, end
    @property
    def value(self): return self.raw[self.body:self.end]
    def children(self):
        result, offset = [], self.body
        while offset < self.end:
            child, offset = parse(self.raw, offset, self.end)
            result.append(child)
            if len(result) > 128: raise ValueError('Too many DER children')
        return result

def parse(raw, offset=0, limit=None):
    limit = len(raw) if limit is None else limit
    start = offset
    if offset + 2 > limit: raise ValueError('Truncated DER')
    tag, size = raw[offset:offset+2]; offset += 2
    if tag & 31 == 31: raise ValueError('Unexpected high-tag DER')
    if size & 128:
        width = size & 127
        if width == 0 or width > 4 or offset + width > limit: raise ValueError('Invalid DER length')
        if raw[offset] == 0: raise ValueError('Nonminimal DER length')
        size = int.from_bytes(raw[offset:offset+width], 'big'); offset += width
        if size < 128: raise ValueError('Nonminimal long DER length')
    end = offset + size
    if end > limit: raise ValueError('Truncated DER value')
    node = DER(raw, tag, offset, end); node.start = start
    return node, end

def one(raw, tag):
    node, end = parse(raw)
    if end != len(raw) or node.tag != tag: raise ValueError('Unexpected DER root')
    return node

def oid(node):
    if node.tag != 6 or not node.value: raise ValueError('Expected OID')
    parts, value = [], 0
    for b in node.value:
        value = value * 128 + (b & 127)
        if not b & 128: parts.append(value); value = 0
    if node.value[-1] & 128: raise ValueError('Truncated OID')
    first = parts.pop(0); arc = min(first // 40, 2)
    return '.'.join(map(str, [arc, first - 40*arc] + parts))

def require(ok, label):
    if not ok: raise ValueError(label)

def code_directory(raw, code, hash_type):
    require(len(raw) >= 88, 'Short code directory')
    magic, length, version, flags, hashes, identity, special, slots, limit = struct.unpack('>9I', raw[:36])
    size, algorithm, spare, page = raw[36:40]
    require((magic,length,version,flags,limit,algorithm,page) ==
            (0xfade0c02,len(raw),0x20400,0,len(code),hash_type,12), 'Code-directory header differs')
    digest = hashlib.sha1 if hash_type == 1 else hashlib.sha256
    require(size == digest().digest_size and slots == (len(code)+4095)//4096, 'Code-slot shape differs')
    require(identity == 88 and raw[identity:].split(b'\0',1)[0] == b'invalid.synthetic.fixture', 'Fixture identifier differs')
    spare2, scatter, team, spare3, limit64, execbase, execlimit, execflags = struct.unpack('>IIIIQQQQ',raw[40:88])
    require((spare,spare2,scatter,spare3,limit64,execbase,execlimit,execflags)==(0,0,0,0,0,0,len(code),1),'Code directory execution fields differ')
    require(identity < team < hashes and raw[team:].split(b'\0',1)[0]==b'SYNTHETIC','Fixture team differs')
    require(hashes-special*size >= 88 and hashes+slots*size == len(raw), 'Code-slot bounds differ')
    require(raw[hashes-special*size:hashes] == b'\0'*(special*size), 'Unexpected special slots')
    for i in range(slots):
        require(raw[hashes+i*size:hashes+(i+1)*size] == digest(code[i*4096:(i+1)*4096]).digest(), 'Code page digest differs')

def verify(root):
    root = Path(root)
    code = (root/'code.bin').read_bytes()
    require(code == bytes((i*31+7)&255 for i in range(8193)), 'Synthetic code bytes changed')
    primary, alternate = (root/'cd-sha1.bin').read_bytes(), (root/'cd-sha256.bin').read_bytes()
    code_directory(primary,code,1); code_directory(alternate,code,2)
    cms = (root/'cms.der').read_bytes(); require(len(cms) <= 1_048_576, 'CMS too large')
    wrapper = (root/'cms-slot.bin').read_bytes()
    require(wrapper == struct.pack('>II',0xfade0b01,len(cms)+8)+cms, 'CMS slot wrapper differs')
    content = one(cms,0x30).children(); require(len(content)==2 and oid(content[0])=='1.2.840.113549.1.7.2', 'Not SignedData')
    require(content[1].tag==0xa0, 'Missing explicit SignedData')
    signed = one(content[1].value,0x30).children()
    require(len(signed)==5 and signed[-1].tag==0x31 and signed[-2].tag==0xa0, 'Unexpected SignedData structure')
    algorithms=signed[1].children(); require(signed[1].tag==0x31 and len(algorithms)==1 and oid(algorithms[0].children()[0])=='2.16.840.1.101.3.4.2.1','SignedData digest set differs')
    encap = signed[2].children(); require(len(encap)==1 and oid(encap[0])=='1.2.840.113549.1.7.1', 'Content is not detached data')
    certs = signed[3].children()
    cert = (root/'synthetic-cert.der').read_bytes()
    require(sum(c.raw[c.start:c.end]==cert for c in certs)==1, 'Synthetic signer certificate absent/duplicated')
    signers = signed[-1].children(); require(len(signers)==1, 'Signer count differs')
    signer = signers[0].children(); require(len(signer)==6 and signer[3].tag==0xa0 and signer[5].tag==4, 'Signer shape differs')
    tbs=one(cert,0x30).children()[0].children(); require(tbs[0].tag==0xa0,'Synthetic certificate version differs')
    signer_id=signer[1].children(); require(len(signer_id)==2,'Signer identifier shape differs')
    encoded=lambda n:n.raw[n.start:n.end]
    require(encoded(signer_id[0])==encoded(tbs[3]) and encoded(signer_id[1])==encoded(tbs[1]),'CMS signer issuer/serial mismatch')
    attr_encodings=[encoded(n) for n in signer[3].children()];require(attr_encodings==sorted(attr_encodings),'Signed attributes are not canonical DER SET')
    require(oid(signer[2].children()[0])=='2.16.840.1.101.3.4.2.1', 'CMS digest is not SHA256')
    require(oid(signer[4].children()[0])=='1.2.840.113549.1.1.1', 'CMS signature is not RSA')
    attributes = {}
    for attribute in signer[3].children():
        pair=attribute.children(); require(len(pair)==2 and pair[1].tag==0x31,'Invalid attribute')
        name=oid(pair[0]); require(name not in attributes, 'Duplicate signed attribute')
        values=pair[1].children(); require(len(values)==1,'Unexpected attribute value count')
        attributes[name]=values[0]
    require(set(attributes)=={'1.2.840.113549.1.9.3','1.2.840.113549.1.9.4','1.2.840.113549.1.9.5',
                              '1.2.840.113635.100.9.1','1.2.840.113635.100.9.2'},'Signed attribute set differs')
    require(oid(attributes['1.2.840.113549.1.9.3'])=='1.2.840.113549.1.7.1','Signed content type differs')
    digest=attributes['1.2.840.113549.1.9.4']; require(digest.tag==4 and digest.value==hashlib.sha256(primary).digest(),'Detached CMS digest differs')
    plist=attributes['1.2.840.113635.100.9.1']; require(plist.tag==4,'Apple plist is not octet string')
    require(plistlib.loads(plist.value)=={'cdhashes':[hashlib.sha1(primary).digest(),hashlib.sha256(alternate).digest()[:20]]},'Apple CDHash plist differs')
    agile=attributes['1.2.840.113635.100.9.2']; require(agile.tag==0x30,'Apple digest agility shape differs')
    values=agile.children(); require(len(values)==2 and oid(values[0])=='2.16.840.1.101.3.4.2.1' and values[1].tag==4 and values[1].value==hashlib.sha256(alternate).digest(),'Apple digest agility differs')
    attrs=signer[3].raw[signer[3].start:signer[3].end]
    (root/'signed-attributes.der').write_bytes(b'\x31'+attrs[1:])
    (root/'cms-signature.bin').write_bytes(signer[5].value)
    return ['actual-code-directory-page-hashes','actual-CMS-detached-digest','actual-Apple-CDHash-plist','actual-Apple-digest-agility']

if __name__=='__main__':
    import json,sys
    print(json.dumps(verify(sys.argv[1])))
