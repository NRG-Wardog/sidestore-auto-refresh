"""Execute workflow shell with owned fake Xcode, not native build evidence."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from capture_release_settings import capture

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / '.github/workflows/issues-native.yml').read_text()
BLOCK = WORKFLOW.split('      - name: Verify effective Release instrumentation settings for both projects\n', 1)[1].split('      - name:', 1)[0]
SCRIPT = '\n'.join(line[10:] for line in BLOCK.split('        run: |\n', 1)[1].splitlines())


class SettingsCacheTests(unittest.TestCase):
    def test_resolver_owned_path_and_no_protocol_expansion(self):
        resolver = WORKFLOW.split('      - name: Capture genuine app lock transition', 1)[1].split('      - name:', 1)[0]
        derived = re.search(r'-derivedDataPath "([^"\n]+)"', resolver).group(1)
        self.assertIn('packages="' + derived + '/SourcePackages"', SCRIPT)
        self.assertIn('query_args+=(-clonedSourcePackagesDirPath "$packages")', SCRIPT)
        self.assertNotIn('-derivedDataPath ', SCRIPT.split('          done',1)[0].split('done',1)[0])
        self.assertIn('-alltargets', SCRIPT)
        self.assertIn('-onlyUsePackageVersionsFromResolvedFile -disableAutomaticPackageResolution', SCRIPT)
        self.assertNotIn('GIT_ALLOW_PROTOCOL=', SCRIPT)
        self.assertEqual(WORKFLOW.count('GIT_ALLOW_PROTOCOL=https:file'), 1)
        self.assertIn('GIT_ALLOW_PROTOCOL: https', WORKFLOW)

    def test_bash3_nounset_array_is_always_nonempty(self):
        self.assertIn('set -euo pipefail', SCRIPT)
        self.assertNotIn('set +u', SCRIPT)
        self.assertNotRegex(SCRIPT, r'\w+_args=\(\)')
        self.assertIn('query_args=(-project "sources/$owner/$project" -alltargets', SCRIPT)
        self.assertIn('xcodebuild "${query_args[@]}"', SCRIPT)
        self.assertLess(SCRIPT.index('query_args=(-project'), SCRIPT.index('query_args+=('))

    def run_shell(self, status=0, missing_cache=False, linked_cache=False, fail_owner=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'harness').symlink_to(ROOT, target_is_directory=True)
            tools = root/'bin'; tools.mkdir()
            cache = root/'tmp/native-SideStore/SourcePackages'
            cache.parent.mkdir(parents=True)
            if linked_cache:
                actual = root/'other-cache'; actual.mkdir(); cache.symlink_to(actual, target_is_directory=True)
            elif not missing_cache: cache.mkdir()
            executable = tools/'xcodebuild'
            executable.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
assert os.environ['GIT_ALLOW_PROTOCOL']=='https'
if 'sources/SideStore/AltStore.xcodeproj' in args:
 assert args[args.index('-clonedSourcePackagesDirPath')+1]==os.environ['RUNNER_TEMP']+'/native-SideStore/SourcePackages'
else:
 assert '-clonedSourcePackagesDirPath' not in args
is_scheme='-scheme' in args
if is_scheme:
 assert '-alltargets' not in args and 'CLANG_COVERAGE_MAPPING=NO' in args
else:
 assert '-alltargets' in args and '-derivedDataPath' not in args
with open('calls.jsonl','a') as out: out.write(json.dumps(args)+'\\n')
status=int(os.environ['FAKE_XCODE_STATUS'])
owner='LiveContainerScheme' if is_scheme else ('SideStore' if 'sources/SideStore/AltStore.xcodeproj' in args else 'LiveContainer')
if os.environ['FAKE_FAIL_OWNER'] and os.environ['FAKE_FAIL_OWNER']!=owner: status=0
if not status: print(json.dumps([{'target':'LiveContainer' if is_scheme else 'SideStoreSupport','buildSettings':{'CONFIGURATION':'Release','ENABLE_CODE_COVERAGE':'NO','CLANG_COVERAGE_MAPPING':'NO','SWIFT_OPTIMIZATION_LEVEL':'-Osize','UNRELATED_SECRET':'do-not-copy'}}]))
sys.exit(status)
''')
            executable.chmod(0o755)
            env = dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(root/'tmp'),
                       GIT_ALLOW_PROTOCOL='https', GITHUB_SHA='a'*40, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', FAKE_XCODE_STATUS=str(status), FAKE_FAIL_OWNER=fail_owner)
            result = subprocess.run(['bash','-c',SCRIPT], cwd=root, env=env, capture_output=True, text=True)
            reports = {p.name:json.loads(p.read_text()) for p in (root/'evidence/release-settings').glob('*.json')}
            calls = (root/'calls.jsonl').read_text().splitlines() if (root/'calls.jsonl').exists() else []
            policy = (root/'evidence/release-coverage-settings.json').exists()
            return result, reports, calls, policy

    def test_shell_success_only_sidestore_reuses_its_exact_cache(self):
        result, reports, calls, policy = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertNotIn('-clonedSourcePackagesDirPath',json.loads(calls[0]))
        self.assertIn('-clonedSourcePackagesDirPath',json.loads(calls[1]))
        self.assertEqual(set(reports), {'LiveContainer.json','SideStore.json','LiveContainer-scheme.json'})
        self.assertTrue(policy)
        self.assertNotIn('do-not-copy', json.dumps(reports))

    def test_failed_query_captured_but_never_admitted(self):
        result, reports, calls, policy = self.run_shell(status=65)
        self.assertEqual(result.returncode,65)
        self.assertEqual(len(calls),1)
        self.assertEqual(reports['LiveContainer.json']['xcodebuild_exit_code'],65)
        self.assertEqual(reports['LiveContainer.json']['parse_status'],'INVALID_JSON')
        self.assertFalse(policy)

    def test_sidestore_query_failure_preserves_both_diagnostics(self):
        result, reports, calls, policy = self.run_shell(status=65, fail_owner='SideStore')
        self.assertEqual(result.returncode,65)
        self.assertEqual(len(calls),2)
        self.assertEqual(reports['LiveContainer.json']['xcodebuild_exit_code'],0)
        self.assertEqual(reports['LiveContainer.json']['parse_status'],'PARSED')
        self.assertEqual(reports['SideStore.json']['xcodebuild_exit_code'],65)
        self.assertEqual(reports['SideStore.json']['parse_status'],'INVALID_JSON')
        self.assertFalse(policy)

    def test_actual_scheme_query_failure_retains_failure_after_alltargets_pass(self):
        result, reports, calls, policy = self.run_shell(status=65, fail_owner='LiveContainerScheme')
        self.assertEqual(result.returncode,65)
        self.assertEqual(len(calls),3)
        self.assertTrue(policy)  # All-target success must not mask scheme failure.
        self.assertEqual(reports['LiveContainer-scheme.json']['xcodebuild_exit_code'],65)
        self.assertEqual(reports['LiveContainer.json']['xcodebuild_exit_code'],0)
        self.assertEqual(reports['SideStore.json']['xcodebuild_exit_code'],0)

    def test_missing_owned_cache_has_no_fallback(self):
        result, reports, calls, policy = self.run_shell(missing_cache=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(calls),1)
        self.assertNotIn('-clonedSourcePackagesDirPath',json.loads(calls[0]))
        self.assertFalse(policy)

    def test_linked_cache_has_no_fallback(self):
        result, reports, calls, policy = self.run_shell(linked_cache=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(calls),1)
        self.assertNotIn('-clonedSourcePackagesDirPath',json.loads(calls[0]))
        self.assertFalse(policy)

    def test_early_artifact_is_failure_only_before_native_build(self):
        name='Upload early effective-settings failure diagnostics only'
        block=WORKFLOW.split('      - name: '+name+'\n',1)[1].split('      - name:',1)[0]
        self.assertIn("if: ${{ !cancelled() && steps.coverage_settings.outcome == 'failure' }}",block)
        self.assertIn('path: evidence/release-settings/',block)
        self.assertLess(WORKFLOW.index(name),WORKFLOW.index('      - name: Compile unsigned LiveContainer'))
        self.assertNotIn('continue-on-error',WORKFLOW)

    def test_diagnostic_full_row_hash_covers_unlisted_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'raw.json'
            first={'target':'App','buildSettings':{'CONFIGURATION':'Release','UNLISTED':'hidden-one'}}
            second={'target':'App','buildSettings':{'CONFIGURATION':'Release','UNLISTED':'hidden-two'}}
            source.write_text(json.dumps([first,first,second]))
            result=capture(source,0)
            hashes=[row['full_row_sha256'] for row in result['rows']]
            self.assertEqual(hashes[0],hashes[1])
            self.assertNotEqual(hashes[0],hashes[2])
            self.assertNotIn('hidden-one',json.dumps(result))
            self.assertNotIn('hidden-two',json.dumps(result))

    def test_diagnostic_missing_malformed_and_wrong_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'raw.json'
            self.assertFalse(capture(source,65)['source_exists'])
            source.write_text('unparsed secret')
            result=capture(source,65)
            self.assertEqual(result['parse_status'],'INVALID_JSON')
            self.assertNotIn('secret',json.dumps(result))
            source.write_text('{}')
            self.assertEqual(capture(source,0)['parse_status'],'NOT_ARRAY')

if __name__ == '__main__': unittest.main()
