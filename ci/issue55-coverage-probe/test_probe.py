import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock
import probe

ROOT=Path(__file__).resolve().parents[2]

class ProbeTests(unittest.TestCase):
    def test_scheme_result_matches_real_build_except_action(self):
        source=Path('/tmp/source');temp=Path('/tmp/probe')
        workflow=(ROOT/'.github/workflows/issues-native.yml').read_text()
        block=workflow.split('      - name: Compile unsigned LiveContainer app and extensions\n',1)[1].split('      - name:',1)[0]
        raw=block[block.index('          xcodebuild '):].split('2>&1 | tee',1)[0].replace('\\\n',' ')
        args=shlex.split(raw)
        args=[arg.replace('sources/LiveContainer',str(source)).replace('$RUNNER_TEMP/',str(temp/'scheme-result')+'/') for arg in args]
        self.assertEqual(args[-1],'build')
        self.assertEqual(probe.command(source,temp,'scheme-result'),args[:-1]+['-showBuildSettings','-json'])

    def test_variants_only_change_deliberate_controls(self):
        source=Path('/tmp/source');temp=Path('/tmp/probe')
        base=probe.command(source,temp,'scheme-result')
        for name,extra in [('scheme-result-action-no',['-enableCodeCoverage','NO']),
                           ('scheme-result-setting-no',['CLANG_COVERAGE_MAPPING=NO'])]:
            actual=[arg.replace('/'+name+'/', '/scheme-result/') for arg in probe.command(source,temp,name)]
            self.assertEqual(actual,base[:-2]+extra+base[-2:])
        for name in ('alltargets','scheme','scheme-result','scheme-result-action-no','scheme-result-setting-no'):
            args=probe.command(source,temp,name)
            self.assertNotIn('build',args);self.assertNotIn('test',args)
            self.assertIn('-showBuildSettings',args)
        self.assertNotIn('-resultBundlePath',probe.command(source,temp,'scheme'))
        self.assertNotIn('-derivedDataPath',probe.command(source,temp,'alltargets'))

    def test_tracked_inventory_detects_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'Global.xcconfig').write_text('original')
            subprocess.run(['git','add','Global.xcconfig'],cwd=root,check=True)
            subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=root,check=True)
            original=probe.inventory(root)
            (root/'Global.xcconfig').write_text('changed')
            with self.assertRaisesRegex(ValueError,'HEAD blob/mode'): probe.inventory(root)

    def test_full_original_duplicate_check_includes_hidden_values(self):
        row={'target':'App','buildSettings':{'unlisted':'first'}}
        result=probe.duplicate_rows(json.dumps([row,row]).encode())
        self.assertEqual(result['duplicate_rows'],1)
        self.assertTrue(result['all_full_original_duplicates_identical'])
        changed={'target':'App','buildSettings':{'unlisted':'second'}}
        result=probe.duplicate_rows(json.dumps([row,changed]).encode())
        self.assertFalse(result['all_full_original_duplicates_identical'])
        self.assertNotIn('second',json.dumps(result))

    def test_query_timeout_is_bounded_and_does_not_abort_next_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with mock.patch.object(probe.subprocess,'run',side_effect=[
                    subprocess.TimeoutExpired(['xcodebuild'],120,stderr=b'timeout'),
                    subprocess.CompletedProcess(['xcodebuild'],0,stderr=b'')]) as run:
                first=probe.run_settings_query(['xcodebuild'],root/'first.json')
                second=probe.run_settings_query(['xcodebuild'],root/'second.json')
                self.assertEqual(first.returncode,124)
                self.assertEqual(second.returncode,0)
                self.assertTrue(all(call.kwargs['timeout']==120 for call in run.call_args_list))

    def test_workflow_is_bounded_and_read_only(self):
        text=(ROOT/'.github/workflows/diagnose-release-coverage.yml').read_text()
        self.assertIn('contents: read',text)
        self.assertIn('validation/issue55-release-settings-probe',text)
        self.assertNotIn('contents: write',text)
        self.assertNotIn('openssl',text.lower())
        self.assertIn('submodules: false',text)
        self.assertIn('timeout-minutes: 15',text)
        self.assertNotIn('continue-on-error',text)

if __name__=='__main__':unittest.main()
