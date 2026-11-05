import copy
from pathlib import Path
import shlex
import unittest
from verify_lc_scheme_settings import verify

ROOT=Path(__file__).resolve().parents[2]
TEXT=(ROOT/'.github/workflows/issues-native.yml').read_text()

class SchemeSettingsTests(unittest.TestCase):
    def rows(self):
        return [{'target':'LiveContainer','buildSettings':{'CONFIGURATION':'Release',
            'ENABLE_CODE_COVERAGE':'NO','CLANG_COVERAGE_MAPPING':'NO'}}]

    def test_explicit_primary_policy_and_exact_repeats(self):
        self.assertEqual(verify(self.rows())['target'],'LiveContainer')
        self.assertEqual(verify(self.rows()*2)['target'],'LiveContainer')

    def test_wrong_target_missing_mapping_and_conflicts_fail(self):
        for change in ({'CLANG_COVERAGE_MAPPING':'YES'},{'CLANG_COVERAGE_MAPPING':None},
                       {'ENABLE_CODE_COVERAGE':'YES'},{'CONFIGURATION':'Debug'},
                       {'CLANG_COVERAGE_MAPPING_LINKER_ARGS':'YES'}):
            rows=self.rows(); rows[0]['buildSettings'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):verify(rows)
        wrong=self.rows();wrong[0]['target']='SideStoreSupport'
        with self.assertRaises(ValueError):verify(wrong)
        with self.assertRaises(ValueError):verify(self.rows()+wrong)
        conflicting=self.rows()+self.rows();conflicting[1]['buildSettings']['UNLISTED']='changed'
        with self.assertRaises(ValueError):verify(conflicting)

    def test_query_actual_build_argv_parity(self):
        block=TEXT.split('      - name: Compile unsigned LiveContainer app and extensions\n',1)[1].split('      - name:',1)[0]
        raw=block[block.index('          xcodebuild '):].split('2>&1 | tee',1)[0].replace('\\\n',' ')
        actual=shlex.split(raw)
        probe=TEXT.split('          if xcodebuild -project sources/LiveContainer/LiveContainer.xcodeproj -scheme LiveContainer',1)[1]
        probe='xcodebuild -project sources/LiveContainer/LiveContainer.xcodeproj -scheme LiveContainer'+probe.split(' > "$RUNNER_TEMP/LiveContainer-scheme-settings.json"',1)[0]
        query=shlex.split(probe.replace('\\\n',' '))
        query=[arg.replace('native-LiveContainer-settings.xcresult','native-LiveContainer-build.xcresult') for arg in query]
        self.assertEqual(actual[-1],'build')
        self.assertEqual(query,actual[:-1]+['-showBuildSettings','-json'])
        self.assertEqual(actual.count('CLANG_COVERAGE_MAPPING=NO'),1)
        self.assertNotIn('-enableCodeCoverage',actual)
        side=TEXT.split('      - name: Compile unsigned SideStore app and extensions\n',1)[1].split('      - name:',1)[0]
        self.assertNotIn('CLANG_COVERAGE_MAPPING=',side)
        self.assertIn('release_coverage.py settings --require-support-size',TEXT)
        self.assertIn('release_coverage.py logs --require-support-size',TEXT)

if __name__=='__main__':unittest.main()
