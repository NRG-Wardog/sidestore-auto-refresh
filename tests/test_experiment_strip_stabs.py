"""Offline packaging controls. Apple strip/ldid execution is never simulated as proof."""
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import experiment_strip_stabs as driver
from ipa_size_report import inspect


class ExperimentPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_signed_helper_rejected_before_any_native_transformation(self):
        from test_macho_stab_guard import fixture, signed_fixture
        paths = driver.target_policy(include_unsigned_helpers=True)
        targets = {key: fixture() for key in paths}
        targets['SideStoreSupport'] = signed_fixture(correct_hashes=True)
        original = self.root/'signed-helper.ipa'
        with zipfile.ZipFile(original, 'w') as archive:
            for key, path in paths.items(): archive.writestr(path, targets[key])
        sidecar = self.root/'sidecar.zip'; sidecar.write_bytes(b'retained fixture')
        diagnostics = {key: {'uuid': driver.image_uuid(data)} for key,data in targets.items()}
        output = self.root/'output'; output.mkdir()
        with patch.object(driver, 'size_report', return_value={}), patch.object(driver, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'helper is no longer unsigned'):
                driver.transform_verified_inputs(original, targets, diagnostics, sidecar,
                    work=self.root/'work', output=output, report={},
                    expected_original_sha=driver.digest(original), include_unsigned_helpers=True)
            run.assert_not_called()

    def archive(self):
        source = self.root / 'original.ipa'
        with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b'preserved comment'
            for path, data in {**{p: b'original binary' for p in driver.TARGETS.values()},
                               'Payload/LiveContainer.app/Info.plist': b'unchanged metadata',
                               'Payload/LiveContainer.app/Resources/icon.dat': b'unchanged resource',
                               'Payload/LiveContainer.app/link': b'Resources/icon.dat'}.items():
                info = zipfile.ZipInfo(path, date_time=(2026, 10, 25, 12, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = ((stat.S_IFLNK | 0o777) if path.endswith('/link') else (stat.S_IFREG | 0o755)) << 16
                info.comment = b'entry comment'
                archive.writestr(info, data)
        return source

    def test_repack_retains_all_non_targets_and_zip_metadata_without_extraction(self):
        source = self.archive(); before = source.read_bytes()
        replacements = {path: b'smaller' for path in driver.TARGETS.values()}
        with patch.object(driver, 'signing_inventory', return_value={'bundles': {}}), \
             patch.object(driver, 'verify_combined', return_value={'test_double_only': True}):
            driver.repackage(source, self.root / 'candidate.ipa', replacements)
        old, old_comment = inspect(source)
        new, new_comment = inspect(self.root / 'candidate.ipa')
        self.assertEqual(old_comment, new_comment)
        self.assertEqual(source.read_bytes(), before)
        for a, b in zip(old, new):
            self.assertEqual(a['external_attr'], b['external_attr'])
            if a['path'] not in replacements:
                self.assertEqual(a, b)
        with zipfile.ZipFile(self.root / 'candidate.ipa') as archive:
            self.assertEqual(archive.read('Payload/LiveContainer.app/link'), b'Resources/icon.dat')

    def test_nonallowlisted_replacement_and_existing_output_reject(self):
        source = self.archive()
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            driver.repackage(source, self.root / 'out.ipa', {'Payload/other': b'bad'})
        target = self.root / 'exists.ipa'; target.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'fresh derivative'):
            driver.repackage(source, target, {path: b'x' for path in driver.TARGETS.values()})
        self.assertEqual(target.read_bytes(), b'keep')

    def test_tool_error_retains_log_and_is_never_success(self):
        log = self.root / 'tool.log'
        with patch.object(driver.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'controlled failure')):
            with self.assertRaisesRegex(ValueError, 'trusted tool failed'):
                driver.run(['/usr/bin/codesign', '--verify', 'fixture'], log)
        self.assertEqual(log.read_bytes(), b'controlled failure')

    def test_wrong_artifact_hash_rejects_before_workspace_creation(self):
        (self.root / 'candidate.zip').write_bytes(b'wrong archive')
        work = self.root / 'work'
        with self.assertRaisesRegex(ValueError, 'pinned original artifact differs'):
            driver.inputs(self.root, work)
        self.assertFalse(work.exists())

    def test_diagnostic_probes_both_copies_and_cannot_package(self):
        source = self.root / 'source'; source.write_bytes(b'original')
        stripped = self.root / 'stripped'; stripped.write_bytes(b'stripped')
        output = self.root / 'output'; output.mkdir()
        with patch.object(driver, 'capture_signature'), patch.object(driver, 'snapshot', return_value={}), patch.object(driver, 'run') as run, \
             patch.object(driver, 'verify', return_value={'synthetic_only': True}) as structural, \
             patch.object(driver.subprocess, 'run', side_effect=[
                 subprocess.CompletedProcess([], 1, b'first failed'),
                 subprocess.CompletedProcess([], 0, b'second passed')]):
            with self.assertRaisesRegex(ValueError, 'diagnostic strict verification or structural gate failed'):
                driver.apple_adhoc_diagnostic(source, stripped, self.root, output)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(structural.call_count, 3)
        self.assertTrue((output / 'apple-adhoc-probe.json').is_file())
        self.assertFalse(list(output.glob('*.ipa')))
        for call in run.call_args_list:
            self.assertIn('--digest-algorithm=sha1,sha256', call.args[0])
            self.assertEqual(Path(call.args[0][-1]).name, 'SideStore')
        text = Path(driver.__file__).read_text()
        early = text.index("report['status'] = 'APPLE_ADHOC_DIAGNOSTIC_PASS_POLICY_REVIEW_PENDING_NO_IPA'")
        self.assertLess(text.index('return', early), text.index('verification = repackage'))

    def test_first_structural_failure_does_not_skip_second_probe_or_pass_job(self):
        source = self.root / 'source'; source.write_bytes(b'original')
        stripped = self.root / 'stripped'; stripped.write_bytes(b'stripped')
        output = self.root / 'output'; output.mkdir()
        with patch.object(driver, 'capture_signature'), patch.object(driver, 'snapshot', return_value={}), \
             patch.object(driver, 'run') as run, \
             patch.object(driver, 'verify', side_effect=[ValueError('LINKEDIT VM allocation grew'), {}, {}]) as structural, \
             patch.object(driver.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'valid on disk')):
            with self.assertRaisesRegex(ValueError, 'structural gate failed'):
                driver.apple_adhoc_diagnostic(source, stripped, self.root, output)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(structural.call_count, 3)
        receipt = json.loads((output / 'apple-adhoc-probe.json').read_text())
        self.assertFalse(receipt['original']['structural_pass'])
        self.assertEqual(receipt['original']['strict_verify_exit_code'], 0)
        self.assertTrue(receipt['stripped-ldid']['structural_pass'])
        self.assertTrue(receipt['stripped-ldid']['green_to_final_structural_pass'])
        self.assertEqual(structural.call_args_list[1].args, (b'original', b'stripped'))
        self.assertEqual(structural.call_args_list[1].kwargs, {})
        self.assertEqual(structural.call_args_list[2].args, (b'stripped', b'stripped'))
        self.assertTrue(structural.call_args_list[2].kwargs['preserve_stabs'])
        for label in receipt:
            self.assertTrue((output / (label + '-apple-snapshots.json')).is_file())
        self.assertFalse(list(output.glob('*.ipa')))

    def test_adhoc_product_mode_cannot_be_combined_with_diagnostic_mode(self):
        result = subprocess.run([sys.executable, '-B', driver.__file__, '--artifacts', 'unused',
                                 '--work', 'unused', '--output', 'unused',
                                 '--apple-adhoc-package', '--apple-adhoc-diagnostic-only'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not allowed with argument', result.stderr)
        text = Path(driver.__file__).read_text()
        validation = text.index("report['apple_adhoc_policy'] = verify_apple_adhoc_reseal")
        strict = text.index("run(['/usr/bin/codesign', '--verify', '--strict'", validation)
        self.assertLess(validation, strict)
        self.assertLess(strict, text.index('verification = repackage'))

    def test_actual_cli_and_exact_transform_arguments_remain_bounded(self):
        script = Path(driver.__file__)
        process = subprocess.run([sys.executable, '-B', str(script), '--help'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0)
        for option in ('--artifacts', '--work', '--output', '--inspect-only'):
            self.assertIn(option, process.stdout)
        text = script.read_text()
        self.assertIn("['xcrun', '--toolchain', 'default', 'strip', '-S', '-o', str(candidate), str(source)]", text)
        self.assertIn("['ldid', '-S', str(candidate)]", text)
        self.assertIn("['/usr/bin/codesign', '--verify', '--strict', '--verbose=4', str(candidate)]", text)
        self.assertNotIn("'strip', '-x'", text)
        self.assertNotIn("'strip', '-s'", text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
