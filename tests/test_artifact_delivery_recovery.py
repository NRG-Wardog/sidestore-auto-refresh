"""Upload transport retries cannot replace candidate acceptance gates."""
import itertools
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()


def step(name):
    return WORKFLOW.split('      - name: ' + name + '\n', 1)[1].split('      - name:', 1)[0]


class ArtifactDeliveryRecoveryTests(unittest.TestCase):
    def test_uploads_are_bounded_identical_payloads_with_distinct_names(self):
        names = ['Upload combined LiveContainer IPA', 'Retry combined LiveContainer IPA upload',
                 'Recover combined LiveContainer IPA upload']
        blocks = [step(name) for name in names]
        paths = [block.split('          path: |\n', 1)[1].split('          if-no-files-found:', 1)[0] for block in blocks]
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(paths[0], paths[2])
        self.assertIn('artifacts/debug-evidence/candidate-provenance.json', paths[0])
        artifact_names = [block.split('          name: ', 1)[1].splitlines()[0] for block in blocks]
        self.assertEqual(len(set(artifact_names)), 3)
        for block in blocks:
            self.assertIn('continue-on-error: true', block)
            self.assertIn('timeout-minutes: 5', block)
            self.assertIn('if-no-files-found: error', block)
        self.assertNotIn('        if:', blocks[0])  # default success() preserves all prior gates
        self.assertIn("!cancelled() && steps.upload_ipa.outcome == 'failure'", blocks[1])
        self.assertIn("!cancelled() && steps.retry_upload_ipa.outcome == 'failure'", blocks[2])

    def test_require_upload_rejects_all_non_success_combinations(self):
        block = step('Require a successful candidate upload')
        script = '\n'.join(line[10:] for line in block.split('        run: |\n', 1)[1].splitlines())
        for outcomes in itertools.product(['success', 'failure', 'skipped', 'cancelled'], repeat=3):
            env = dict(os.environ, ARTIFACT_ID='123', GITHUB_OUTPUT=os.devnull, **dict(zip(['PRIMARY', 'RETRY', 'RECOVERY'], outcomes)))
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
            self.assertEqual(result.returncode == 0, 'success' in outcomes, outcomes)
        self.assertNotIn('continue-on-error:', block)

    def test_preserves_binary_with_original_provenance_after_upload_failure(self):
        block = step('Preserve verified IPA after upload trouble')
        self.assertIn("!cancelled() && steps.upload_ipa.outcome == 'failure'", block)
        self.assertIn('cp artifacts/LiveContainer-SideStore-v3.0.3-rc.ipa artifacts/debug-evidence/recovery/', block)
        self.assertIn('artifacts/builder-commit.txt artifacts/candidate-package-verification.json', block)
        symbols = step('Preserve matching crash symbols and generated startup source')
        self.assertIn('if: always()', symbols)
        self.assertIn('path: artifacts/debug-evidence', symbols)

    def test_download_routes_to_successful_attempt_and_still_verifies_exact_bytes(self):
        download = step('Download uploaded candidate for delivery verification')
        self.assertIn('artifact-ids: ${{ steps.confirmed_candidate.outputs.artifact-id }}', download)
        self.assertIn('merge-multiple: true', download)
        self.assertNotIn('          name:', download)
        gate = step('Require a successful candidate upload')
        for attempt in ('upload_ipa', 'retry_upload_ipa', 'recovery_upload_ipa'):
            self.assertIn(f"steps.{attempt}.outcome == 'success' && steps.{attempt}.outputs.artifact-id", gate)
        verify = step('Verify the actual uploaded candidate and evidence')
        self.assertIn('--ipa artifacts/downloaded-ipa/LiveContainer-SideStore-v3.0.3-rc.ipa', verify)
        self.assertIn('--provenance artifacts/downloaded-debug-evidence/candidate-provenance.json', verify)
        self.assertIn('--builder-commit "$GITHUB_SHA"', verify)
        self.assertNotIn('continue-on-error:', verify)
        self.assertNotIn('        if:', verify)
        self.assertLess(WORKFLOW.index('Require a successful candidate upload'),
                        WORKFLOW.index('Download uploaded candidate for delivery verification'))
        self.assertLess(WORKFLOW.index('Verify embedded CoreDevice transport executable'),
                        WORKFLOW.index('Upload combined LiveContainer IPA'))

    def test_success_without_confirmed_numeric_artifact_id_fails_closed(self):
        block = step('Require a successful candidate upload')
        script = '\n'.join(line[10:] for line in block.split('        run: |\n', 1)[1].splitlines())
        for artifact_id in ('', '0', '-1', '1,2', 'undefined', '123junk'):
            env = dict(os.environ, PRIMARY='success', RETRY='skipped', RECOVERY='skipped',
                       ARTIFACT_ID=artifact_id, GITHUB_OUTPUT=os.devnull)
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0, artifact_id)
