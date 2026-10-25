"""Transport-only ZIP tests; generated samples are never native UI proof."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fragment_fixture_helpers', ROOT / 'tests/test_p0_signin_rendering.py')
helpers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helpers)
renderer = helpers.renderer
COMMIT = 'a' * 40
RUN = '123456'


class EvidenceFragmentTransportTests(unittest.TestCase):
    def fixture(self, parent, passed=True):
        root = parent / 'p0-preflight'; root.mkdir()
        (root / 'p0-preflight-verification.json').write_text(json.dumps({
            'builderCommit': COMMIT, 'ciRun': RUN, 'passed': passed, 'releaseEligible': False}))
        p0 = root / 'p0-signin'; p0.mkdir()
        maker = helpers.P0SignInRenderingEvidenceTests()
        for kind in ('phone', 'tablet'):
            export = p0 / (kind + '-attachments'); export.mkdir()
            summary = maker.make_export(export)
            (p0 / (kind + '-xctest-summary.json')).write_text(json.dumps(summary))
            (p0 / (kind + '-verification.json')).write_text(json.dumps(renderer.verify_export(export, summary)))
            (export / 'diagnostic-extra.png').write_bytes(helpers.image())
        (root / 'legacy.png').write_bytes(helpers.image())
        build = root / 'p0-build'; build.mkdir(); (build / 'unpublished.swift').write_text('compiled tree excluded')
        result = p0 / 'phone.xcresult'; result.mkdir(); (result / 'unpublished.json').write_text('{}')
        logs = parent / 'logs'; logs.mkdir(); (logs / 'native.log').write_text('test log\n')
        return root, logs

    def test_three_fragments_preserve_bytes_paths_identity_and_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, logs = self.fixture(parent)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            output = parent / 'transport'
            index = renderer.prepare_transport_fragments(root, output, COMMIT, RUN, logs)
            self.assertTrue(index['transportOnly'])
            self.assertEqual(set(index['fragments']), {'metadata', 'phone', 'tablet'})
            self.assertEqual({x['file'] for x in index['fragments'].values()}, {'metadata.zip', 'phone-images.zip', 'tablet-images.zip'})
            for kind, fragment in index['fragments'].items():
                archive = output / fragment['file']
                self.assertLessEqual(archive.stat().st_size, 31 * 1024 * 1024)
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), fragment['sha256'])
                with zipfile.ZipFile(archive) as zipped:
                    identity = json.loads(zipped.read('transport-fragment-identity.json'))
                    self.assertEqual(identity['builderCommit'], COMMIT)
                    self.assertEqual(identity['ciRun'], RUN)
                    self.assertEqual(identity['files'], fragment['files'])
                    for name, details in fragment['files'].items():
                        data = zipped.read(name)
                        expected = (parent / name).read_bytes()
                        self.assertEqual(data, expected)
                        self.assertEqual(details, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                    if kind != 'metadata':
                        self.assertEqual(len(fragment['files']), 12)
                        self.assertTrue(all(name.endswith('.png') for name in fragment['files']))
                    else:
                        self.assertIn('p0-preflight/legacy.png', fragment['files'])
                        self.assertIn('logs/native.log', fragment['files'])
                        self.assertFalse(any('p0-build' in name or '.xcresult/' in name for name in zipped.namelist()))
            self.assertTrue(all(v['passed'] for v in index['verification'].values()))
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertEqual(len(index['omittedNonAcceptancePNGs']), 2)
            # Reassembled transport has complete required PNG/proof data.
            restored = parent / 'restored'; restored.mkdir()
            for fragment in index['fragments'].values():
                with zipfile.ZipFile(output / fragment['file']) as archive: archive.extractall(restored)
            for kind in ('phone', 'tablet'):
                p0 = restored / root.name / 'p0-signin'
                self.assertTrue(renderer.verify_export(p0 / (kind + '-attachments'),
                    json.loads((p0 / (kind + '-xctest-summary.json')).read_text()))['passed'])

    def test_failed_or_missing_matrix_is_never_promoted(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent, passed=False)
            export = root / 'p0-signin/phone-attachments'
            (export / 'p0-credentials-default-copy-details.png').unlink()
            before = (root / 'p0-preflight-verification.json').read_bytes()
            index = renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)
            self.assertFalse(index['sourceManifests'][0]['passed'])
            self.assertFalse(index['replayedExportVerification']['phone']['passed'])
            self.assertTrue(index['selectionErrors'])
            self.assertEqual(before, (root / 'p0-preflight-verification.json').read_bytes())
            self.assertNotIn('passed', index)

    def test_original_command_failure_cannot_be_promoted_by_clean_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent, passed=False)
            path = root / 'p0-signin/phone-verification.json'
            original = json.loads(path.read_text())
            original.update(passed=False, failures=['bounded XCTest timeout'])
            path.write_text(json.dumps(original))
            index = renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)
            self.assertEqual(index['verification']['phone'], original)
            self.assertFalse(index['verification']['phone']['passed'])
            self.assertTrue(index['replayedExportVerification']['phone']['passed'])
            self.assertFalse(index['sourceManifests'][0]['passed'])

    def test_wrong_run_commit_missing_identity_and_stale_output_fail(self):
        for problem in ('run', 'commit', 'missing', 'stale', 'nested'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary); root, _ = self.fixture(parent); output = parent / 'transport'
                commit, run = COMMIT, RUN
                if problem == 'run': run = '9'
                elif problem == 'commit': commit = 'b' * 40
                elif problem == 'missing': (root / 'p0-preflight-verification.json').unlink()
                elif problem == 'nested': output = root / 'transport'
                else: output.mkdir(); (output / 'existing').touch()
                with self.assertRaises(ValueError): renderer.prepare_transport_fragments(root, output, commit, run)

    def test_symlinks_and_manifest_escape_fail_before_transport(self):
        for problem in ('symlink', 'directory-link', 'escape', 'fifo'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary); root, _ = self.fixture(parent)
                export = root / 'p0-signin/phone-attachments'
                if problem == 'symlink': (root / 'link.json').symlink_to(parent / 'outside')
                elif problem == 'directory-link': (root / 'linkdir').symlink_to(parent, target_is_directory=True)
                elif problem == 'fifo': os.mkfifo(root / 'blocked.txt')
                else:
                    path = export / 'manifest.json'; data = json.loads(path.read_text())
                    data[0]['attachments'][0]['exportedFileName'] = '../escape.png'; path.write_text(json.dumps(data))
                with self.assertRaises(ValueError): renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)

    def test_oversized_fragment_has_no_completed_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent); output = parent / 'transport'
            with patch.object(renderer, 'TRANSPORT_ZIP_LIMIT', 1), self.assertRaises(ValueError):
                renderer.prepare_transport_fragments(root, output, COMMIT, RUN)
            self.assertFalse((output / 'transport-index.json').exists())

    def test_overlarge_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent)
            with patch.object(renderer, 'TRANSPORT_FILE_LIMIT', 1), self.assertRaises(ValueError):
                renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)


    def test_unpublished_video_and_compiled_outputs_are_not_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent)
            video = root / 'p0-signin/phone-attachments/screen.mp4'
            with video.open('wb') as stream: stream.truncate(renderer.TRANSPORT_FILE_LIMIT + 1)
            index = renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)
            self.assertFalse(any(name.endswith('.mp4') for fragment in index['fragments'].values() for name in fragment['files']))
            self.assertTrue(index['replayedExportVerification']['phone']['passed'])

    def test_shared_index_counts_against_each_fragment_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent)
            first = renderer.prepare_transport_fragments(root, parent / 'first', COMMIT, RUN)
            limit = max(item['bytes'] for item in first['fragments'].values()) + 1
            with patch.object(renderer, 'TRANSPORT_ZIP_LIMIT', limit), self.assertRaises(ValueError):
                renderer.prepare_transport_fragments(root, parent / 'second', COMMIT, RUN)
            self.assertFalse((parent / 'second/transport-index.json').exists())

    def test_aggregate_bound_precedes_export_verifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent)
            with patch.object(renderer, 'TRANSPORT_TOTAL_LIMIT', 1), patch.object(renderer, 'verify_export') as verify:
                with self.assertRaises(ValueError): renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)
                verify.assert_not_called()

    def test_source_mutation_prevents_completed_transport_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary); root, _ = self.fixture(parent)
            original = renderer.verify_export
            def mutate(*args):
                result = original(*args)
                (root / 'legacy.png').write_bytes(b'changed after snapshot')
                return result
            with patch.object(renderer, 'verify_export', side_effect=mutate), self.assertRaises(ValueError):
                renderer.prepare_transport_fragments(root, parent / 'transport', COMMIT, RUN)
            self.assertFalse((parent / 'transport/transport-index.json').exists())


if __name__ == '__main__': unittest.main()
