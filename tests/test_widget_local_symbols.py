import struct
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from test_macho_stab_guard import fixture,alter_command
from test_macho_local_symbol_guard import remove_fixture_local
from macho_stab_guard import commands,snapshot
from macho_local_symbol_guard import classify,verify_local_removal
from widget_local_symbols import POLICY,keep_list,transform


def widget(kind=0x0e,*,indirect=False):
    data=fixture(False,indirect_wrong=indirect);end=commands(data)[1]
    section=struct.pack('<16s16sQQIIIIIIII',b'__text',b'__TEXT',0x11f4,12,500,2,0,0,0x80000400,0,0,0)
    data=bytearray(data[:104]+section+data[104:end]+data[end+80:])
    struct.pack_into('<I',data,12,2);struct.pack_into('<I',data,20,end-32+80)
    struct.pack_into('<I',data,36,152);struct.pack_into('<I',data,96,1)
    raw=next(raw for cmd,raw in commands(data)[2] if cmd==2);offset=struct.unpack_from('<I',raw,8)[0]
    data[offset+4]=kind;data[offset+5]=1;struct.pack_into('<Q',data,offset+8,0x11f4)
    return bytes(data)


class WidgetLocalPolicyTests(unittest.TestCase):
    def test_true_local_only_and_exact_keepers(self):
        data=widget();after=remove_fixture_local(data)
        content,report=keep_list(data)
        self.assertEqual(content,b'_public\n_undefined\n');self.assertEqual(report['eligible_true_local_records'],1)
        self.assertEqual(verify_local_removal(data,after,**POLICY)['removed_local_records'],1)
        with self.assertRaises(ValueError):classify(data) # default dylib policy unchanged
    def test_private_externals_never_eligible(self):
        data=widget(0x1e);_,eligible,protected=classify(data,**POLICY)
        self.assertFalse(eligible);self.assertEqual(sum(protected.values()),3)
        with self.assertRaisesRegex(ValueError,'protected'):verify_local_removal(data,remove_fixture_local(data),**POLICY)
    def test_indirect_local_never_eligible(self):
        _,eligible,_=classify(widget(indirect=True),**POLICY);self.assertFalse(eligible)
    def test_nabs_not_eligible_and_mode_must_protect_private(self):
        _,eligible,_=classify(widget(2),**POLICY);self.assertFalse(eligible)
        with self.assertRaises(ValueError):classify(widget(),unsigned_executable=True)
    def test_export_code_metadata_uuid_and_protected_mutation_rejected(self):
        before=widget();after=remove_fixture_local(before)
        for old,new in ((b'_public',b'_publix'),(b'_undefined',b'_undefineX'),(b'export-trie',b'export-triX'),
                        (b'CODE_CONTENT',b'CODE_CONTENX'),(b'0123456789abcdef',b'X123456789abcdef')):
            with self.subTest(old=old),self.assertRaises(ValueError):verify_local_removal(before,after.replace(old,new),**POLICY)
    def test_unsafe_keeper_names_rejected(self):
        with self.assertRaisesRegex(ValueError,'unsafe'):keep_list(widget().replace(b'_public',b'_publi\n'))
    def test_tool_flags_and_receipt_failclosed(self):
        from experiment_strip_stabs import digest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();tool=root/'strip';tool.write_bytes(b'tool-identity-only-no-execution')
            report={'tool':{'path':str(tool),'sha256':digest(tool)}};before=widget()
            def fake(command,**kwargs):
                self.assertEqual(command[1:3],['-x','-s']);self.assertNotIn('-S',command)
                Path(command[command.index('-o')+1]).write_bytes(remove_fixture_local(before))
                return type('Result',(),{'returncode':0,'stdout':b'','stderr':b''})()
            with patch('widget_local_symbols.subprocess.run',side_effect=fake):after=transform(before,root,report)
            self.assertEqual(report['true_local_stage']['qualification']['removed_local_records'],1)
            self.assertEqual(report['true_local_stage']['output_sha256'],__import__('hashlib').sha256(after).hexdigest())
