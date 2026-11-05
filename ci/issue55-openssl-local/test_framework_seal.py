import copy
import hashlib
import importlib.util
from pathlib import Path
import plistlib
import struct
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import macho_stab_guard as guard
from test_host_legacy_dyld_policy import host_fixture
spec=importlib.util.spec_from_file_location('seal',Path(__file__).with_name('framework_seal.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def signed(info,resources):
    data=host_fixture();offset=len(data);rows=guard.commands(data)[2]
    identifier=plistlib.loads(info)['CFBundleIdentifier'].encode()+b'\0'
    pages=(offset+16383)//16384;hashes=88+len(identifier)+96
    cd=struct.pack('>9I4B4I4Q',0xfade0c02,hashes+pages*32,0x20400,2,hashes,88,3,pages,offset,32,2,0,14,0,0,0,0,0,0,512,0)+identifier+bytes(96+pages*32)
    requirements=bytes.fromhex('fade0c010000000c00000000');cms=bytes.fromhex('fade0b0100000008')
    blobs=[(0,cd),(2,requirements),(0x10000,cms)];size=12+8*len(blobs)+sum(len(b) for _,b in blobs)
    raw=struct.pack('<4I',0x1d,16,offset,size)
    rows=[(cmd,bytearray(row)) for cmd,row in rows]
    for cmd,row in rows:
        if cmd==0x19 and row[8:24].rstrip(b'\0')==b'__LINKEDIT':
            struct.pack_into('<Q',row,32,offset-512+size);struct.pack_into('<Q',row,48,offset-512+size)
    joined=b''.join(bytes(row) for _,row in rows)+raw
    header=bytearray(data[:32]);struct.pack_into('<2I',header,16,len(rows)+1,len(joined))
    base=bytes(header)+joined+bytes(500-32-len(joined))+data[500:]
    child=bytearray(cd)
    for slot,payload in ((1,info),(2,requirements),(3,resources)):
        child[hashes-slot*32:hashes-(slot-1)*32]=hashlib.sha256(payload).digest()
    for page in range(pages):child[hashes+page*32:hashes+(page+1)*32]=hashlib.sha256(base[page*16384:min((page+1)*16384,offset)]).digest()
    blobs[0]=(0,bytes(child));position=12+8*len(blobs);index=b'';body=b''
    for slot,payload in blobs:index+=struct.pack('>2I',slot,position);body+=payload;position+=len(payload)
    return base+struct.pack('>3I',0xfade0cc0,size,len(blobs))+index+body

class SealTests(unittest.TestCase):
    def setUp(self):
        self.info=plistlib.dumps({'CFBundleIdentifier':'org.openssl.qualification.lts.synthetic'})
        self.resources=plistlib.dumps({'files':{},'files2':{},'rules':{},'rules2':{}})
        self.data=signed(self.info,self.resources)
        self.original={m.BINARY:{'kind':'file','mode':0o755,'sha256':'a'*64},m.INFO:{'kind':'file','mode':0o644,'sha256':m.sha(self.info)},'Versions/A/Headers/ssl.h':{'kind':'file','mode':0o644,'sha256':m.sha(b'header')}}
        self.after=copy.deepcopy(self.original);self.after[m.BINARY]['sha256']=m.sha(self.data)
        self.after.update({m.SEAL_DIR:{'kind':'directory','mode':0o755},m.SEAL_FILE:{'kind':'file','mode':0o644,'sha256':m.sha(self.resources)}})
    def test_finite_signature_all_pages_and_three_slots(self):
        proof=m.verify_signature(self.data,self.info,self.resources,guard)
        self.assertEqual(proof['special_slots_verified'],[1,2,3]);self.assertFalse(proof['device_execution_verified'])
    def test_code_info_resources_tamper_rejected(self):
        for data,info,res in ((self.data.replace(b'CODE_CONTENT',b'CODE_CONTENX'),self.info,self.resources),(self.data,self.info.replace(b'</dict>',b'<key>extra</key><string>x</string></dict>'),self.resources),(self.data,self.info,self.resources+b'x')):
            with self.assertRaises(ValueError):m.verify_signature(data,info,res,guard)
    def test_only_exact_seal_nodes_and_binary(self):m.verify_inventory(self.original,self.after)
    def test_added_path_mode_header_change_rejected(self):
        for name,row in (('unexpected',{'kind':'file','mode':0o644,'sha256':'b'*64}),('Versions/A/Headers/ssl.h',{'kind':'file','mode':0o644,'sha256':'b'*64}),(m.SEAL_DIR,{'kind':'directory','mode':0o777})):
            after=copy.deepcopy(self.after);after[name]=row
            with self.assertRaises(ValueError):m.verify_inventory(self.original,after)
    def test_default_omitted_headers_disclosed(self):
        report=m.verify_resources(plistlib.loads(self.resources),self.original,lambda p:b'header')
        self.assertEqual(report['default_omitted_headers_independently_inventory_bound'],['Headers/ssl.h'])
    def test_real_resources_require_modern_hash_despite_omit_rule(self):
        original=copy.deepcopy(self.original);original['Versions/A/Resources/data']={'kind':'file','mode':0o644,'sha256':m.sha(b'data')}
        doc={'files2':{},'rules2':{'^.*':{'omit':True}}}
        with self.assertRaises(ValueError):m.verify_resources(doc,original,lambda p:b'data')
        doc['files2']['Resources/data']={'hash2':hashlib.sha256(b'data').digest()}
        self.assertEqual(m.verify_resources(doc,original,lambda p:b'data')['modern_sealed_files'],['Resources/data'])
    def test_wrong_hash_unknown_entry_or_policy_rejected(self):
        for entries in ({'Headers/ssl.h':{'hash2':bytes(32)}},{'../escape':{'hash2':bytes(32)}},{'Headers/ssl.h':{'hash2':hashlib.sha256(b'header').digest(),'symlink':'elsewhere'}}):
            with self.assertRaises(ValueError):m.verify_resources({'files2':entries,'rules2':{}},self.original,lambda p:b'header')
    def test_default_parser_still_rejects_host_legacy(self):
        with self.assertRaises(ValueError):guard.snapshot(self.data)

if __name__=='__main__':unittest.main()
