"""Parser tests only: synthetic byte structures are never execution receipts."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile

import p2_archive as a
import p2_commands as c
import p2_product as p
import p2_sources as s
import replay_p2_capture as r

HERE = Path(__file__).resolve().parent
RAW = Path(os.environ['P2_TEST_NATIVE_EVIDENCE']) if os.environ.get('P2_TEST_NATIVE_EVIDENCE') else None
UI = Path(os.environ['P2_TEST_UI_EVIDENCE']) if os.environ.get('P2_TEST_UI_EVIDENCE') else None
HOST_REPO = Path(os.environ['P2_TEST_HOST_REPO']) if os.environ.get('P2_TEST_HOST_REPO') else None
RUNNER_REPO = Path(os.environ['P2_TEST_RUNNER_REPO']) if os.environ.get('P2_TEST_RUNNER_REPO') else None


def archive(rows):
    data = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(data,'w') as z:
            for name,raw,mode in rows:
                info = zipfile.ZipInfo(name); info.create_system=3;info.external_attr=mode<<16
                z.writestr(info,raw)
    data.seek(0)
    return data


class ArchiveTests(unittest.TestCase):
    def test_duplicate_json_and_nonfinite_rejected(self):
        for raw in ('{"a":1,"a":2}','{"x":NaN}','{"x":1e10000}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):a.parse(raw)

    def test_unsafe_paths_rejected(self):
        for name in ('../bad','/absolute','a//b','a/./b','a\\b'):
            with self.subTest(name=name),self.assertRaises(ValueError):a.Archive(archive([(name,b'x',stat.S_IFREG|0o644)]))

    def test_duplicate_and_type_alias_rejected(self):
        for names in (('a','a'),('a/','a')):
            with self.subTest(names=names),self.assertRaises(ValueError):
                a.Archive(archive([(name,b'' if name.endswith('/') else b'x', (stat.S_IFDIR if name.endswith('/') else stat.S_IFREG)|0o644) for name in names]))

    def test_symlinks_rejected_by_default(self):
        with self.assertRaises(ValueError):a.Archive(archive([('link',b'target',stat.S_IFLNK|0o777)]))

    def test_link_escape_cycle_and_dangling_rejected(self):
        for target in ('../outside','link','missing'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                a.Archive(archive([('link',target.encode(),stat.S_IFLNK|0o777)]),links=True)

    def test_non_directory_ancestor_rejected(self):
        with self.assertRaises(ValueError):a.Archive(archive([('a',b'x',stat.S_IFREG|0o644),('a/b',b'y',stat.S_IFREG|0o644)]))

    def test_member_size_bound(self):
        with patch.object(a,'MAX_FILE',2),self.assertRaises(ValueError):a.Archive(archive([('x',b'123',stat.S_IFREG|0o644)]))

    def test_index_is_complete_and_exact(self):
        for index in ({},{'x':'0'*64},{'x':a.sha(b'x'),'extra':a.sha(b'')}):
            with self.subTest(index=index),a.Archive(archive([('x',b'x',stat.S_IFREG|0o644),('evidence-index.json',json.dumps(index).encode(),stat.S_IFREG|0o644)])) as z:
                with self.assertRaises(ValueError):z.index()

    def test_internal_link_is_read_as_data(self):
        with a.Archive(archive([('target',b'x',stat.S_IFREG|0o644),('link',b'target',stat.S_IFLNK|0o777)]),links=True) as z:
            self.assertEqual(z.resolve('link'),'target')
            self.assertEqual(z.inventory['link']['target'],'target')


class SourceAndCommandTests(unittest.TestCase):
    @unittest.skipUnless(HOST_REPO,'requires P2_TEST_HOST_REPO')
    def test_actual_git_tree_reconstructs(self):
        repo = HOST_REPO
        rows = s.entries(repo,s.PINS['LiveContainer'][0])
        self.assertEqual(s.tree_oid(rows),s.PINS['LiveContainer'][1])
        rows[next(iter(rows))]['gitObject']='a'*40
        self.assertNotEqual(s.tree_oid(rows),s.PINS['LiveContainer'][1])

    def test_absolute_paths_reject_aliases(self):
        for name in ('relative','/a/../b','/a//b','/a\\b'):
            with self.subTest(name=name),self.assertRaises(ValueError):s.absolute(name)

    def test_exact_settings_warning_only(self):
        udid='747A45CF-D549-4ED5-B7BB-A6CAC3AA5AC0';value='[]\n'
        warning='\n\n--- xcodebuild: WARNING: Using the first of multiple matching destinations:\n'+''.join(
            '{ platform:iOS Simulator, arch:'+arch+', id:'+udid+', OS:26.5, name:P2-Compile-39172103613 }\n' for arch in ('arm64','x86_64'))
        c.verify_settings_output(value,'[]'+warning,udid,39172103613)
        for bad in ('[]'+warning.replace(udid,'00000000-0000-0000-0000-000000000000'),'[]'+warning+'other stderr\n','{}\n'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):c.verify_settings_output(value,bad,udid,39172103613)

    @unittest.skipUnless(RAW,'requires P2_TEST_NATIVE_EVIDENCE')
    def test_bad_owned_command_states_rejected(self):
        original=json.loads((RAW/'commands/001.json').read_text())
        class Evidence:
            files={'commands/001.json':'x','commands/001.log':'x'}
            def json(self,name):return row
            def raw(self,name):return b'output\n'
        for change in ('returncode','primaryError','finalization','quiescent','pipesJoined','leaderReaped','signals','errors','reapedBeforeSignals'):
            row=copy.deepcopy(original)
            if change=='returncode':row[change]=1
            elif change=='primaryError':row[change]='failed'
            elif change=='finalization':row[change]=True
            elif change=='signals':row['ownedProcessGroup'][change]=[9]
            elif change=='errors':row['ownedProcessGroup'][change]=['unsettled']
            else:row['ownedProcessGroup'][change]=False
            with self.subTest(change=change),self.assertRaises(ValueError):c.Commands(Evidence())

    @unittest.skipUnless(RAW,'requires P2_TEST_NATIVE_EVIDENCE')
    def test_raw_command_context_cannot_be_swapped(self):
        row=json.loads((RAW/'commands/001.json').read_text())
        class Evidence:
            files={'commands/001.json':'x','commands/001.log':'x'}
            def json(self,name):return row
            def raw(self,name):return b'output\n'
        commands=c.Commands(Evidence())
        with self.assertRaises(ValueError):commands.take(['git','-C','/wrong','rev-parse','HEAD'])

    @unittest.skipUnless(RAW,'requires P2_TEST_NATIVE_EVIDENCE')
    def test_dependency_sha256_is_explicitly_an_unverified_observation(self):
        value=json.loads((RAW/'litehook-provenance.json').read_text())
        original=s.source_proof(value,'litehook')
        changed=copy.deepcopy(value)
        name=next(k for k,v in changed['files'].items() if v['mode'] in ('100644','100755'))
        changed['files'][name]['sha256']='0'*64
        observed=s.source_proof(changed,'litehook')
        self.assertEqual({k:original[k] for k in ('commit','tree')},{k:observed[k] for k in ('commit','tree')})
        self.assertNotEqual(original['files'][name]['sha256'],observed['files'][name]['sha256'])
        # The digest is never substituted for immutable Git object identity.
        for field in ('gitObject','mode','path'):
            changed=copy.deepcopy(value)
            if field=='gitObject':changed['files'][name][field]='a'*40
            elif field=='mode':changed['files'][name][field]='100755' if changed['files'][name][field]=='100644' else '100644'
            else:changed['files'][name+'-mutated']=changed['files'].pop(name)
            with self.subTest(field=field),self.assertRaises(ValueError):s.source_proof(changed,'litehook')

    @unittest.skipUnless(RAW,'requires P2_TEST_NATIVE_EVIDENCE')
    def test_dependency_raw_hash_object_mismatch_rejected(self):
        proof=json.loads((RAW/'litehook-provenance.json').read_text())
        records=sorted((RAW/'commands').glob('*.json'),key=lambda p:int(p.stem))
        first=next(i for i,path in enumerate(records) if json.loads(path.read_text())['command']==
                   ['git','-C',proof['root'],'rev-parse','HEAD'])
        count=5+sum(row['mode'] in ('100644','100755') for row in proof['files'].values())
        rows=[(json.loads(path.read_text()),path.with_suffix('.log').read_text()) for path in records[first:first+count]]
        command=c.Commands.__new__(c.Commands);command.rows=rows;command.number=0
        command.proof(proof)
        self.assertEqual(command.number,len(rows))
        position=next(i for i,(row,_) in enumerate(rows) if 'hash-object' in row['command'])
        changed=copy.deepcopy(rows);changed[position]=(changed[position][0],'a'*40+'\n\n')
        command.rows=changed;command.number=0
        with self.assertRaises(ValueError):command.proof(proof)


class ProductTests(unittest.TestCase):
    @staticmethod
    def thin(platform=7):
        return struct.pack('<8I',0xfeedfacf,0x0100000c,0,2,1,24,0,0)+struct.pack('<6I',0x32,24,platform,0,0,0)

    def test_macho_simulator_header_only(self):
        self.assertEqual(p.macho_platforms(self.thin()),[7])
        for raw in (self.thin(2),self.thin()[:35],b'not executable'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):p.macho_platforms(raw)

    def test_macho_command_bounds(self):
        raw=bytearray(self.thin());struct.pack_into('<I',raw,36,1024)
        with self.assertRaises(ValueError):p.macho_platforms(bytes(raw))

    def test_printf_argument_mutations_rejected(self):
        for value in ('%n','%2$lld','%*lld','%lld %lld','no argument'):
            with self.subTest(value=value),self.assertRaises(ValueError):p.check_format(value,1)

    def test_literal_reader_never_evaluates_code(self):
        with self.assertRaises(ValueError):p.literal(b'VALUE = __import__("os").getcwd()','VALUE')


class AuthenticMetadataAndScopeTests(unittest.TestCase):
    @unittest.skipUnless(RUNNER_REPO,'requires P2_TEST_RUNNER_REPO')
    def test_sealed_actual_runner_inputs(self):
        raw,inputs=r.sealed_inputs(RUNNER_REPO)
        self.assertEqual(a.sha(raw),r.INPUT_SHA);self.assertEqual(len(inputs['files']),14)

    @unittest.skipUnless(all(os.environ.get(k) for k in ('P2_TEST_NATIVE_ZIP','P2_TEST_RUN_JSON','P2_TEST_ARTIFACT_JSON')),'requires authenticated API/ZIP fixture paths')
    def test_run_identity_and_api_digest_mutations_rejected(self):
        run=json.loads(Path(os.environ['P2_TEST_RUN_JSON']).read_text())
        artifact=json.loads(Path(os.environ['P2_TEST_ARTIFACT_JSON']).read_text())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args=SimpleNamespace(zip=Path(os.environ['P2_TEST_NATIVE_ZIP']),run_json=root/'run.json',artifact_json=root/'artifact.json')
            for field in ('conclusion','head_sha','run_attempt','digest'):
                rdata=copy.deepcopy(run);adata=copy.deepcopy(artifact)
                if field=='digest':adata[field]='sha256:'+'0'*64
                elif field=='head_sha':rdata[field]='a'*40
                elif field=='run_attempt':rdata[field]=2
                else:rdata[field]='failure'
                args.run_json.write_text(json.dumps(rdata));args.artifact_json.write_text(json.dumps(adata))
                with self.subTest(field=field),self.assertRaises(ValueError):r.metadata(args)

    @unittest.skipUnless(UI,'requires P2_TEST_UI_EVIDENCE')
    def test_parent_scope_mutations_rejected(self):
        original=json.loads((UI/'ui-result.json').read_text());r.validate_ui_result(original)
        for key in ('serviceFailureUnresolved','backendReadinessProven','fullAppAcceptance','finalCompositionAcceptance','hostBuiltThisRun'):
            value=copy.deepcopy(original);value[key]=not value[key]
            with self.subTest(key=key),self.assertRaises(ValueError):r.validate_ui_result(value)

    @unittest.skipUnless(all(os.environ.get(k) for k in ('P2_TEST_NATIVE_CANDIDATE','P2_TEST_UI_BASIS')),'requires real replay candidate outputs')
    def test_candidate_and_parent_scopes_remain_separate(self):
        native=json.loads(Path(os.environ['P2_TEST_NATIVE_CANDIDATE']).read_text())
        ui=json.loads(Path(os.environ['P2_TEST_UI_BASIS']).read_text())
        self.assertEqual(native['status'],'CAPTURED_FOR_INDEPENDENT_REVIEW')
        self.assertTrue(all(value is False for value in native['claims'].values()))
        self.assertNotEqual(native['identity']['run_url'],ui['run_url'])
        self.assertEqual(native['tested_owners']['LiveContainer']['commit'],s.PINS['LiveContainer'][0])
        self.assertEqual(ui['source_commit'],r.UI_SOURCE)
        self.assertIs(ui['service_failure_unresolved'],True)
        for key in ('full_app_acceptance','final_composition_acceptance','composed_ui_reexecuted'):self.assertIs(ui[key],False)


if __name__=='__main__':unittest.main(verbosity=2)
