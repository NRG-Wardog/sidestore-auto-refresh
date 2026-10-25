from pathlib import Path
import unittest
import json
import os
import subprocess
import sys
import tempfile
import release_coverage as policy


class ReleaseCoverageTests(unittest.TestCase):
    def row(self):
        return [{'target': 'App', 'buildSettings': {'CONFIGURATION': 'Release',
                 'ENABLE_CODE_COVERAGE': 'NO', 'DEBUG_INFORMATION_FORMAT': 'dwarf-with-dsym'}}]

    def test_explicit_no_preserves_diagnostic_setting(self):
        self.assertEqual(policy.settings(self.row())[0]['settings']['DEBUG_INFORMATION_FORMAT'], 'dwarf-with-dsym')

    def test_missing_yes_debug_and_duplicate_rejected(self):
        for mutation in ({'ENABLE_CODE_COVERAGE': 'YES'}, {'ENABLE_CODE_COVERAGE': None},
                         {'CONFIGURATION': 'Debug'}, {'GCC_INSTRUMENT_PROGRAM_FLOW_ARCS': 'YES'}):
            rows=self.row(); rows[0]['buildSettings'].update(mutation)
            with self.assertRaises(ValueError): policy.settings(rows)
        conflicting = self.row() + self.row(); conflicting[1]['buildSettings']['UNLISTED'] = 'different'
        with self.assertRaises(ValueError): policy.settings(conflicting)
        with self.assertRaises(ValueError): policy.settings([])

    def test_exact_full_row_duplicates_only(self):
        rows = self.row() + self.row()
        self.assertEqual(len(policy.settings(rows)), 1)
        rows[1] = dict(reversed(list(rows[1].items())))
        rows[1]['buildSettings'] = dict(reversed(list(rows[1]['buildSettings'].items())))
        self.assertEqual(len(policy.settings(rows)), 1)
        for location in ('metadata', 'unlisted_setting', 'listed_setting'):
            changed = self.row() + self.row()
            if location == 'metadata': changed[1]['project'] = 'different-project'
            elif location == 'unlisted_setting': changed[1]['buildSettings']['UNLISTED'] = 'different'
            else: changed[1]['buildSettings']['DEBUG_INFORMATION_FORMAT'] = 'dwarf'
            with self.subTest(location=location), self.assertRaises(ValueError): policy.settings(changed)

    def test_duplicate_rows_each_require_validation(self):
        for key, value in (('CONFIGURATION','Debug'), ('ENABLE_CODE_COVERAGE','YES'),
                           ('CLANG_COVERAGE_MAPPING_LINKER_ARGS','YES')):
            rows = self.row() + self.row(); rows[1]['buildSettings'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): policy.settings(rows)
        self.assertEqual(len(policy.settings(self.support_row()*2, True)), 1)

    def test_secondary_mapping_and_linker_coverage_are_rejected(self):
        for key in ('CLANG_COVERAGE_MAPPING', 'CLANG_COVERAGE_MAPPING_LINKER_ARGS',
                    'CLANG_CLANG_COVERAGE_MAPPING_LINKER_ARGS', 'SWIFTC_CLANG_COVERAGE_MAPPING_LINKER_ARGS'):
            rows = self.row(); rows[0]['buildSettings'][key] = 'YES'
            with self.subTest(key=key), self.assertRaises(ValueError): policy.settings(rows)
            rows[0]['buildSettings'][key] = 'NO'
            self.assertEqual(policy.settings(rows)[0]['settings'][key], 'NO')

    def test_link_commands_cannot_reintroduce_coverage(self):
        clean = b'/trusted/swiftc -module-name SideStoreSupport -Osize -c source.swift\n'
        for compiler in ('clang', 'clang++'):
            link = ('/trusted/' + compiler + ' -dynamiclib -fprofile-instr-generate -o App\n** BUILD SUCCEEDED **\n').encode()
            with self.subTest(compiler=compiler), self.assertRaises(ValueError):
                policy.build_log(clean + link, True)
        self.assertTrue(policy.build_log(clean + b'/trusted/clang -dynamiclib -o App\n** BUILD SUCCEEDED **', True)['native_build_succeeded'])

    def test_native_log_requires_success_and_compiler(self):
        good=b'/trusted/clang -Os -g file.c\n** BUILD SUCCEEDED **\n'
        self.assertFalse(policy.build_log(good)['coverage_flags_present'])
        for data in (b'', b'** BUILD SUCCEEDED **', b'/trusted/swiftc -O file.swift'):
            with self.assertRaises(ValueError): policy.build_log(data)
        for flag in ('-fprofile-instr-generate', '-fcoverage-mapping', '-profile-generate', '-profile-coverage-mapping', '-fprofile-arcs', '-ftest-coverage'):
            with self.assertRaises(ValueError): policy.build_log(good+(' '+flag+' ').encode())

    def test_workflow_both_actual_logs_no_test_log_substitution(self):
        text=(Path(__file__).resolve().parents[2]/'.github/workflows/issues-native.yml').read_text()
        self.assertIn('-alltargets', text)
        self.assertIn('--inputs evidence/LiveContainer-build.log evidence/SideStore-build.log', text)
        self.assertIn('--output evidence/release-coverage-settings.json', text)
        self.assertIn('test_release_coverage.py | tee', text)


    def support_row(self):
        row = self.row()[0]
        row['target'] = 'SideStoreSupport'
        row['buildSettings']['SWIFT_OPTIMIZATION_LEVEL'] = '-Osize'
        return [row]

    def test_support_settings_opt_in(self):
        self.assertEqual(policy.settings(self.support_row(), True)[0]['target'], 'SideStoreSupport')
        for rows in (self.row(),):
            with self.assertRaises(ValueError): policy.settings(rows, True)
        rows = self.support_row(); rows[0]['buildSettings']['SWIFT_OPTIMIZATION_LEVEL'] = '-O'
        with self.assertRaises(ValueError): policy.settings(rows, True)
        rows = self.support_row(); rows[0]['target'] = 'OtherSupport'
        with self.assertRaises(ValueError): policy.settings(rows, True)

    def support_log(self, options='-Osize', module='SideStoreSupport'):
        return ('    builtin-SwiftDriver -- /trusted/swiftc -module-name ' + module + ' ' + options +
                ' -whole-module-optimization source.swift\n** BUILD SUCCEEDED **\n').encode()

    def test_support_compiler_effective_last_mode(self):
        for options in ('-Osize', '-O -Osize', '-Onone -Osize'):
            report = policy.build_log(self.support_log(options), True)
            self.assertEqual(report['support_size_policy']['compiler_command_count'], 1)
            self.assertEqual(len(report['support_size_policy']['compiler_commands_sha256']), 64)
        for options in ('-O', '-Osize -O', '-Osize -Onone', '-Osize -Ounchecked', '-DLOOKS_LIKE_Osize', '-O -Xcc -Osize', '-O -Xfrontend -Osize', ''):
            with self.assertRaises(ValueError): policy.build_log(self.support_log(options), True)

    def test_support_missing_wrong_or_echo_command(self):
        for data in (self.support_log(module='OtherSupport'),
                     b'echo /trusted/swiftc -module-name SideStoreSupport -Osize\n** BUILD SUCCEEDED **',
                     b'** BUILD SUCCEEDED **',
                     self.support_log().replace(b'** BUILD SUCCEEDED **', b'')):
            with self.assertRaises(ValueError): policy.build_log(data, True)
        with self.assertRaises(ValueError): policy.build_log(self.support_log('-Osize -fprofile-arcs'), True)

    def test_quoted_compiler_path(self):
        log = self.support_log().replace(b'/trusted/swiftc', b'"/trusted path/swiftc"')
        self.assertEqual(policy.build_log(log, True)['support_size_policy']['compiler_command_count'], 1)

    def test_support_all_commands_must_match(self):
        with self.assertRaises(ValueError):
            policy.build_log(self.support_log() + self.support_log('-O'), True)
        report = policy.build_log(self.support_log() * 2, True)
        self.assertEqual(report['support_size_policy']['compiler_command_count'], 2)

    def test_support_cli_from_alternate_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, GITHUB_SHA='a'*40, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1')
            for mode in ('settings', 'logs'):
                lc = root / ('lc-' + mode); ss = root / ('ss-' + mode); out = root / (mode + '.json')
                if mode == 'settings':
                    lc.write_text(json.dumps(self.support_row()*2)); ss.write_text(json.dumps(self.row()*2))
                else:
                    lc.write_bytes(self.support_log()); ss.write_bytes(b'/trusted/clang -Os source.c\n** BUILD SUCCEEDED **')
                subprocess.run([sys.executable, '-B', str(Path(policy.__file__).resolve()), mode,
                                '--inputs', str(lc), str(ss), '--output', str(out), '--require-support-size'],
                               cwd=root, env=env, check=True)
                report = json.loads(out.read_text())
                if mode == 'settings':
                    for source in report['effective_settings_inputs'].values():
                        self.assertEqual(source['input_row_count'],2)
                        self.assertEqual(source['unique_target_count'],1)
                        self.assertEqual(source['exact_duplicate_row_count'],1)
                        self.assertEqual(len(source['exact_duplicate_full_row_sha256'][0]),64)
                self.assertTrue(report['support_size_policy']['required'])
                self.assertEqual(report['support_size_policy']['effective_optimization'], '-Osize')

if __name__ == '__main__': unittest.main()
