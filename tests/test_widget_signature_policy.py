"""Synthetic signatures with exact public current recipe blobs; no native execution."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from widget_signature_policy import verify_widget_signature, IDENTIFIER
from macho_stab_guard import _checked_signature_entries
BLOBS = {2: 'fade0c0100000088000000010000000300000014fade0c0000000074000000010000000600000002000000134c697665576964676574457874656e73696f6e00000000060000000f000000060000000b000000000000000a7375626a6563742e434e000000000001000000000000000e000000010000000a2a864886f76364060201000000000000', 5: 'fade71710000020c3c3f786d6c2076657273696f6e3d22312e302220656e636f64696e673d225554462d38223f3e0a3c21444f435459504520706c697374205055424c494320222d2f2f4170706c652f2f44544420504c49535420312e302f2f454e222022687474703a2f2f7777772e6170706c652e636f6d2f445444732f50726f70657274794c6973742d312e302e647464223e0a3c706c6973742076657273696f6e3d22312e30223e0a3c646963743e0a093c6b65793e6170706c69636174696f6e2d6964656e7469666965723c2f6b65793e0a093c737472696e673e414141414131313131312e636f6d2e6b64742e6c697665636f6e7461696e65722e4c6976655769646765743c2f737472696e673e0a093c6b65793e636f6d2e6170706c652e646576656c6f7065722e7465616d2d6964656e7469666965723c2f6b65793e0a093c737472696e673e414141414131313131313c2f737472696e673e0a093c6b65793e636f6d2e6170706c652e73656375726974792e6170706c69636174696f6e2d67726f7570733c2f6b65793e0a093c61727261793e0a09093c737472696e673e67726f75702e636f6d2e5369646553746f72652e5369646553746f72653c2f737472696e673e0a093c2f61727261793e0a093c6b65793e6765742d7461736b2d616c6c6f773c2f6b65793e0a093c747275652f3e0a3c2f646963743e0a3c2f706c6973743e0a', 7: 'fade7172000000e43181d930130c0e6765742d7461736b2d616c6c6f7701010130310c23636f6d2e6170706c652e646576656c6f7065722e7465616d2d6964656e7469666965720c0a4141414141313131313130450c166170706c69636174696f6e2d6964656e7469666965720c2b414141414131313131312e636f6d2e6b64742e6c697665636f6e7461696e65722e4c69766557696467657430480c25636f6d2e6170706c652e73656375726974792e6170706c69636174696f6e2d67726f757073301f0c1d67726f75702e636f6d2e5369646553746f72652e5369646553746f7265'}

def fixture():
    offset=2588672
    children={k:bytes.fromhex(v) for k,v in BLOBS.items()}
    for slot,hs,ht in ((0,20,1),(4096,32,2)):
        pages=(offset+4095)//4096; hashes=88+len(IDENTIFIER)+7*hs
        children[slot]=struct.pack('>9I4B4I4Q',0xfade0c02,hashes+pages*hs,0x20400,0,hashes,88,7,pages,offset,
            hs,ht,0,12,0,0,0,0,0,0,2588672,17)+IDENTIFIER+bytes(hs*(7+pages))
    pos=12+8*len(children);index=b'';body=b''
    starts={}
    for slot,child in sorted(children.items()):
        starts[slot]=pos;index+=struct.pack('>2I',slot,pos);body+=child;pos+=len(child)
    blob=struct.pack('>3I',0xfade0cc0,pos,len(children))+index+body
    segment=struct.pack('<II16s4Q4I',0x19,72,b'__TEXT',0x100000000,offset,0,offset,5,5,0,0)
    cmds=segment+struct.pack('<II2Q',0x80000028,24,200,0)+struct.pack('<4I',0x1d,16,offset,len(blob))
    header=struct.pack('<8I',0xfeedfacf,0x100000c,0,2,3,len(cmds),0,0)
    data=bytearray((header+cmds).ljust(offset,b'\0')+blob)
    for slot,hs,alg in ((0,20,hashlib.sha1),(4096,32,hashlib.sha256)):
        start=offset+starts[slot];hashes=struct.unpack_from('>I',data,start+16)[0]
        for k in (2,5,7):data[start+hashes-k*hs:start+hashes-(k-1)*hs]=alg(children[k]).digest()
        for page in range((offset+4095)//4096):
            data[start+hashes+page*hs:start+hashes+(page+1)*hs]=alg(data[page*4096:min((page+1)*4096,offset)]).digest()
    return bytes(data),offset,starts

class WidgetSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data,cls.offset,cls.starts=fixture()
    def test_exact_policy_checks_both_algorithms(self):
        result=verify_widget_signature(self.data)
        self.assertEqual(result['special_slots_verified'],[2,5,7])
        self.assertEqual(set(result['code_pages_verified']),{'1','2'})
        self.assertFalse(result['info_plist_cryptographically_covered'])
        self.assertFalse(result['apple_trust_or_device_execution_verified'])
    def test_code_corruption_rejected(self):
        data=bytearray(self.data);data[1000]^=1
        with self.assertRaisesRegex(ValueError,'page hash'):verify_widget_signature(bytes(data))
    def test_each_algorithm_page_and_special_hash_rejected(self):
        for slot,hs in ((0,20),(4096,32)):
            start=self.offset+self.starts[slot];hashes=struct.unpack_from('>I',self.data,start+16)[0]
            for relative in (hashes,hashes-2*hs,hashes-5*hs,hashes-7*hs,hashes-hs):
                with self.subTest(slot=slot,relative=relative):
                    data=bytearray(self.data);data[start+relative]^=1
                    with self.assertRaises(ValueError):verify_widget_signature(bytes(data))
    def test_exact_recipe_children_rejected(self):
        for slot in (2,5,7):
            data=bytearray(self.data);data[self.offset+self.starts[slot]+10]^=1
            with self.assertRaisesRegex(ValueError,'recipe'):verify_widget_signature(bytes(data))
    def test_policy_geometry_identifier_and_coverage_rejected(self):
        for slot in (0,4096):
            for relative in (8,12,16,20,24,28,32,36,37,38,39,40,44,48,52,56,64,72,80,88):
                with self.subTest(slot=slot,field=relative):
                    data=bytearray(self.data);data[self.offset+self.starts[slot]+relative]^=1
                    with self.assertRaises(ValueError):verify_widget_signature(bytes(data))
    def test_duplicate_slot_and_truncated_signature_rejected(self):
        data=bytearray(self.data);struct.pack_into('>I',data,self.offset+20,0)
        with self.assertRaises(ValueError):verify_widget_signature(bytes(data))
        with self.assertRaises(ValueError):verify_widget_signature(self.data[:-1])

if __name__=='__main__':unittest.main()
