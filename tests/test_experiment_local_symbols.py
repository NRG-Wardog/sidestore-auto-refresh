import json
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from types import SimpleNamespace
from test_macho_stab_guard import fixture, alter_command
from test_macho_local_symbol_guard import remove_fixture_local
from macho_stab_guard import commands
import experiment_local_symbols as experiment
from experiment_strip_stabs import TARGETS,DSYMS,digest


def add_synthetic_stab(data):
    rows=commands(data)[2];sym=next(raw for cmd,raw in rows if cmd==2)
    off,count,strings,size=struct.unpack_from('<4I',sym,8)
    dysym=struct.unpack('<20I',next(raw for cmd,raw in rows if cmd==0xb))
    marker=b'radr://5614542\0';oldname=data[strings+2:data.index(b'\0',strings+2)+1];delta=16+len(marker)-len(oldname)
    result=(data[:off]+struct.pack('<IBBHQ',2,0x3c,0,0,90260802)+data[off:]).replace(oldname,marker)
    result=alter_command(result,2,12,count+1);result=alter_command(result,2,16,strings+16);result=alter_command(result,2,20,size+delta-16)
    for offset,value in ((12,dysym[3]+1),(16,dysym[4]+1),(24,dysym[6]+1),(56,dysym[14]+delta)):
        result=alter_command(result,0xb,offset,value)
    result=bytearray(result)
    for i in range(1,count+1):
        location=off+16*i;idx=struct.unpack_from('<I',result,location)[0]
        if idx>=2+len(oldname):struct.pack_into('<I',result,location,idx+delta-16)
    pos=32
    for cmd,raw in commands(bytes(result))[2]:
        if cmd==0x19 and raw[8:24].rstrip(b'\0')==b'__LINKEDIT':
            for field in (32,48):struct.pack_into('<Q',result,pos+field,struct.unpack_from('<Q',raw,field)[0]+delta)
        pos+=len(raw)
    for i in range(dysym[15]):
        location=dysym[14]+delta+4*i;value=struct.unpack_from('<I',result,location)[0]
        if not value&0xc0000000:struct.pack_into('<I',result,location,value+1)
    return bytes(result)


class LocalExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.ipa=self.root/'input.ipa';self.symbols=self.root/'symbols.zip'
        with zipfile.ZipFile(self.ipa,'w') as archive:
            for path in TARGETS.values():archive.writestr(path,fixture(False))
        with zipfile.ZipFile(self.symbols,'w') as archive:
            for key,(_,bundle) in DSYMS.items():
                prefix='Symbols/'+bundle+'/Contents/'
                archive.writestr(prefix+'Info.plist',b'fixture metadata')
                archive.writestr(prefix+'Resources/DWARF/'+key,fixture(False))
        self.sha=digest(self.ipa);self.symbolsha=digest(self.symbols)
    def run_experiment(self,inspect=True):
        return experiment.experiment(self.ipa,self.sha,self.symbols,self.symbolsha,self.root/'output',inspect_only=inspect)
    def test_inspection_only_has_no_tool_execution_or_ipa_output(self):
        with patch.object(experiment.subprocess,'run') as run:report=self.run_experiment()
        run.assert_not_called();self.assertFalse(report['ipa_emitted']);self.assertFalse(report['packaging_approved'])
        self.assertEqual(list((self.root/'output').rglob('*.ipa')),[])
        self.assertEqual(digest(self.ipa),self.sha)
    def test_input_hash_mismatch_rejected_before_output(self):
        self.sha='0'*64
        with self.assertRaisesRegex(ValueError,'artifact hash'):self.run_experiment()
        self.assertFalse((self.root/'output').exists())
    def test_wrong_valid_dsym_uuid_rejected(self):
        with zipfile.ZipFile(self.symbols,'r') as z:entries={n:z.read(n) for n in z.namelist()}
        with zipfile.ZipFile(self.symbols,'w') as z:
            for name,data in entries.items():z.writestr(name,data.replace(b'0123456789abcdef',b'X123456789abcdef'))
        self.symbolsha=digest(self.symbols)
        with self.assertRaisesRegex(ValueError,'UUID differs'):self.run_experiment()
    def test_nonmac_execution_fails_closed_with_receipt(self):
        with patch.object(experiment.sys,'platform','linux'):
            with self.assertRaisesRegex(ValueError,'macOS'):self.run_experiment(False)
        report=json.loads((self.root/'output/diagnostic.json').read_text())
        self.assertEqual(report['status'],'FAILED');self.assertFalse(report['signature_verified'])
        self.assertFalse(report['ipa_emitted'])
    def test_symlinked_input_and_preexisting_output_rejected(self):
        link=self.root/'alias.ipa';link.symlink_to(self.ipa);self.ipa=link
        with self.assertRaisesRegex(ValueError,'artifact hash'):self.run_experiment()
        self.ipa=link.resolve();(self.root/'output').mkdir()
        with self.assertRaisesRegex(ValueError,'fresh diagnostic'):self.run_experiment()

    def test_both_rejected_outputs_captured_and_overall_still_fails(self):
        output=self.root/'measurement';output.mkdir()
        targets={key:fixture(False) for key in TARGETS}
        diagnostics={key:{'uuid':experiment.image_uuid(data)} for key,data in targets.items()}
        report={'targets':{key:{} for key in targets}}
        def fake_strip(command,**kwargs):
            self.assertEqual(kwargs['timeout'],120)
            Path(command[command.index('-o')+1]).write_bytes(fixture(True))
            return SimpleNamespace(returncode=0,stdout=b'synthetic strip diagnostic')
        with patch.object(experiment.subprocess,'run',side_effect=fake_strip) as run, \
             patch.object(experiment,'runtime_save_list',return_value=(b'_public\n',{'synthetic_test_only':True})):
            with self.assertRaisesRegex(ValueError,'one or more'):experiment.measure_all_targets(targets,diagnostics,output,report)
        self.assertEqual(run.call_count,2)
        self.assertEqual(set(report['target_errors']),set(TARGETS))
        for key in TARGETS:
            observed=json.loads((output/key/'after-strip-observed.json').read_text())
            self.assertFalse(observed['accepted'])
            self.assertEqual(observed['snapshot']['stab_count'],1)
            self.assertEqual(observed['stab_records'][0]['name_hex'],b'debug.c'.hex())
            self.assertIn('STAB',report['target_errors'][key]['message'])
            self.assertFalse((output/key/'structural.json').exists())


    def test_finite_apple_marker_retained_without_second_strip(self):
        before=fixture(False,local=b'_local'+b'x'*40);final=add_synthetic_stab(remove_fixture_local(before))
        output=self.root/'finite-marker';output.mkdir()
        report={'targets':{'LiveContainerSwiftUI':{}}}
        def fake_strip(command,**kwargs):
            self.assertIn('-x',command);self.assertNotIn('-S',command)
            Path(command[command.index('-o')+1]).write_bytes(final)
            return SimpleNamespace(returncode=0,stdout=b'synthetic Apple marker')
        with patch.object(experiment.subprocess,'run',side_effect=fake_strip):
            experiment.measure_all_targets({'LiveContainerSwiftUI':before},
                {'LiveContainerSwiftUI':{'uuid':experiment.image_uuid(before)}},output,report)
        row=report['targets']['LiveContainerSwiftUI']['measurement']
        self.assertEqual(row['removed_local_records'],1)
        self.assertTrue(row['apple_compatibility_sentinel_retained'])
        self.assertFalse(row['packaging_approved'])

if __name__=='__main__':unittest.main()
