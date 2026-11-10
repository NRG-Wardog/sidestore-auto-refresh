"""Finite macOS qualification framework seal. Never an iOS signing policy.

Apple default omitted headers remain byte/mode/link-bound by the independent
original inventory. Runtime resources cannot be omitted by resource rules.
"""
import hashlib
import plistlib
import struct

SEAL_DIR='Versions/A/_CodeSignature'
SEAL_FILE=SEAL_DIR+'/CodeResources'
BINARY='Versions/A/OpenSSL'
INFO='Versions/A/Resources/Info.plist'

def require(ok,message):
    if not ok:raise ValueError(message)
def sha(data):return hashlib.sha256(data).hexdigest()

def verify_inventory(before,after):
    require(SEAL_DIR not in before and SEAL_FILE not in before,'original framework already has a resource seal')
    require(set(after)==set(before)|{SEAL_DIR,SEAL_FILE},'framework seal added/removed unexpected paths')
    require(after[SEAL_DIR]=={'kind':'directory','mode':0o755},'seal directory mode/type differs')
    require(after[SEAL_FILE].get('kind')=='file' and after[SEAL_FILE].get('mode')==0o644 and set(after[SEAL_FILE])=={'kind','mode','sha256'},'seal file mode/type differs')
    for path,row in before.items():
        actual=after[path]
        if path==BINARY:
            require({k:v for k,v in row.items() if k!='sha256'}=={k:v for k,v in actual.items() if k!='sha256'},'binary mode/type changed')
        else:require(actual==row,'original framework metadata changed: '+path)

def verify_resources(document,original,read_file):
    require(type(document) is dict and {'files2','rules2'}<=set(document)<= {'files','files2','rules','rules2'},'CodeResources section closure differs')
    # Rules are recorded, never interpreted to waive independent coverage.
    for name in ('rules','rules2'):
        if name in document:require(type(document[name]) is dict,'resource rules malformed')
    available={path[len('Versions/A/'):]:row for path,row in original.items() if path.startswith('Versions/A/') and row.get('kind')=='file' and path!=BINARY}
    covered=set(); modern=set()
    for table in ('files','files2'):
        entries=document.get(table,{})
        require(type(entries) is dict,'resource file table malformed')
        for path,row in entries.items():
            require(type(path) is str and path in available,'resource entry outside original file closure')
            data=read_file('Versions/A/'+path)
            require(sha(data)==available[path]['sha256'],'original resource bytes changed')
            if type(row) is bytes:
                require(table=='files' and row==hashlib.sha1(data).digest(),'legacy resource digest differs')
            else:
                require(type(row) is dict and set(row)<= {'hash','hash2','optional'} and bool(set(row)&{'hash','hash2'}),'unsupported resource entry policy')
                if 'optional' in row:require(type(row['optional']) is bool,'invalid optional resource marker')
                if 'hash' in row:require(row['hash']==hashlib.sha1(data).digest(),'resource SHA1 differs')
                if 'hash2' in row:require(row['hash2']==hashlib.sha256(data).digest(),'resource SHA256 differs')
                if table=='files2':require('hash2' in row,'modern resource lacks SHA256')
            covered.add(path)
            if table=='files2':modern.add(path)
    required={path for path in available if not path.startswith('Headers/') and path!='Resources/Info.plist'}
    require(required<=modern,'runtime resource omitted from modern seal')
    omitted=sorted(set(available)-covered-{'Resources/Info.plist'})
    require(all(path.startswith('Headers/') for path in omitted),'non-header resource omitted')
    return {'modern_sealed_files':sorted(modern),'all_sealed_files':sorted(covered),'default_omitted_headers_independently_inventory_bound':omitted,'custom_resource_rules_supplied':False,'rules_used_to_waive_runtime_coverage':False}

def verify_signature(data,info,resources,guard):
    shape=guard.snapshot(data,host_legacy_dyld=True)
    offset,padding,entries=guard._checked_signature_entries(data)
    require(set(entries)=={0,2,0x10000},'host signature slot closure differs')
    require(entries[2]==bytes.fromhex('fade0c010000000c00000000') and entries[0x10000]==bytes.fromhex('fade0b0100000008'),'host ad-hoc requirements/CMS policy differs')
    child=entries[0];require(len(child)>=88,'host CodeDirectory truncated')
    fields=struct.unpack_from('>9I4B4I4Q',child)
    magic,length,version,flags,hashes,ident,special,pages,limit=fields[:9]
    hs,ht,platform,power=fields[9:13]
    spare2,scatter,team,spare3,limit64,execbase,execlimit,execflags=fields[13:]
    require((magic,length,version,flags,ident,special,hs,ht,platform,power)==(0xfade0c02,len(child),0x20400,2,88,3,32,2,0,14),'unreviewed host framework CodeDirectory policy')
    require(spare2==scatter==team==spare3==limit64==execbase==execflags==0,'host team/scatter/runtime signing policy differs')
    text=[row for row in shape['segments'] if bytes.fromhex(row[0])==b'__TEXT']
    require(len(text)==1 and text[0][3]==0 and text[0][4]==execlimit,'host executable segment binding differs')
    metadata=plistlib.loads(info);identifier=metadata.get('CFBundleIdentifier')
    require(identifier=='org.openssl.qualification.lts.synthetic','host framework identifier differs')
    encoded=identifier.encode()+b'\0'
    require(child[88:88+len(encoded)]==encoded and hashes==88+len(encoded)+3*32,'host identifier/hash layout differs')
    require(limit==offset and pages==(offset+16383)//16384 and hashes+pages*32==length,'host code-page coverage differs')
    for page in range(pages):
        require(child[hashes+page*32:hashes+(page+1)*32]==hashlib.sha256(data[page*16384:min((page+1)*16384,offset)]).digest(),'host code-page hash mismatch')
    for slot,payload in ((1,info),(2,entries[2]),(3,resources)):
        require(child[hashes-slot*32:hashes-(slot-1)*32]==hashlib.sha256(payload).digest(),'host special-slot hash mismatch')
    return {'policy':'host-only-Apple-adhoc-v20400-sha256-framework','code_pages_verified':pages,'special_slots_verified':[1,2,3],'info_plist_sha256':sha(info),'code_resources_sha256':sha(resources),'binary_sha256':sha(data),'signature_padding_bytes':padding,'device_execution_verified':False}

def verify(root,before,after,guard):
    verify_inventory(before,after)
    info=(root/INFO).read_bytes();resources=(root/SEAL_FILE).read_bytes();data=(root/BINARY).read_bytes()
    require(sha(info)==before[INFO]['sha256'] and sha(resources)==after[SEAL_FILE]['sha256'] and sha(data)==after[BINARY]['sha256'],'host seal inventory hash binding differs')
    coverage=verify_resources(plistlib.loads(resources),before,lambda path:(root/path).read_bytes())
    signature=verify_signature(data,info,resources,guard)
    return {'schema':'host-framework-resource-seal-v1','resource_coverage':coverage,'signature':signature,'only_original_binary_and_exact_seal_changed':True,'ios_policy_changed':False}
