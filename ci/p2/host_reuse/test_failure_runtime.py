"""Failure-only collection cannot replace native outcome or broaden launch scope."""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import failure_diagnostics as FD
import test_reuse as inherited

RUN = inherited.RUN
HERE = Path(__file__).resolve().parent


class FailureRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve(strict=True); (root / 'evidence').mkdir()
        self.probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
        self.probe.identity = {'bundleIdentifier': 'synthetic.app', 'executableSHA256': 'a' * 64, 'irrelevantProductMetadata': 'not-in-Swift'}
        self.probe.generated_environment = {'SYNTHETIC': 'bound'}
        self.probe.report['probeBuilderCommit'] = 'b' * 40
        self.probe.report['errors'] = ['Native test did not pass']
        self.addCleanup(patch.stopall)
        patch.object(RUN, 'FD', FD).start()

    def test_not_reached_retained_without_promoting_failure(self):
        self.probe.collect_failure_diagnostics('ordinary output')
        result = json.loads((self.probe.evidence / 'failed-launch-diagnostics.json').read_text())
        self.assertEqual(result['status'], 'not-reached')
        self.assertEqual(self.probe.report['status'], 'BLOCKED')
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])
        self.assertFalse(self.probe.report['hostLaunched'])

    def test_identity_is_exact_swift_subset_not_full_product(self):
        result = {'status': 'not-reached', 'errors': []}
        with patch.object(FD, 'parse', return_value=result) as parse:
            self.probe.collect_failure_diagnostics('raw')
        self.assertEqual(parse.call_args.args[1], {
            'bundleIdentifier': 'synthetic.app', 'executableSHA256': 'a' * 64,
            'sourceCommit': RUN.HP.SOURCE, 'sourceTree': RUN.HP.TREE, 'builderCommit': 'b' * 40})

    def test_invalid_stream_is_secondary_and_retained(self):
        self.probe.collect_failure_diagnostics(FD.MARKER + '{}')
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])
        self.assertEqual(len(self.probe.report['diagnosticCaptureErrors']), 1)
        self.assertEqual(json.loads((self.probe.evidence / 'failed-launch-diagnostics.json').read_text())['status'], 'invalid')

    def test_receipt_write_failure_cannot_mask_primary(self):
        with patch.object(RUN.CAP, 'write', side_effect=OSError('synthetic storage error')):
            self.probe.collect_failure_diagnostics('ordinary output')
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])
        self.assertEqual(len(self.probe.report['diagnosticCaptureErrors']), 2)

    def test_partial_stream_survives_missing_xcresult(self):
        receipt = {'status': 'partial', 'errors': ['Incomplete diagnostic sequence']}
        with patch.object(FD, 'parse', return_value=receipt):
            self.probe.collect_failure_diagnostics('synthetic partial')
        self.probe.harvest_results()
        self.assertEqual(json.loads((self.probe.evidence / 'failed-launch-diagnostics.json').read_text()), receipt)
        self.assertIn('Failure-only xcresult unavailable; stream retained independently', self.probe.report['diagnosticCaptureErrors'])
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])

    def test_attachment_error_is_secondary_and_deduplicated(self):
        self.probe.failure_diagnostics = {'status': 'partial'}
        with patch.object(RUN.HP, 'attachment_map', side_effect=ValueError('missing export')):
            self.probe.collect_failure_attachments(); self.probe.collect_failure_attachments()
        self.assertEqual(len(self.probe.report['diagnosticCaptureErrors']), 1)
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])

    def test_completed_diagnostic_does_not_admit_route(self):
        receipt = {'status': 'complete', 'errors': [], 'settingsAcceptance': False}
        with patch.object(FD, 'parse', return_value=receipt): self.probe.collect_failure_diagnostics('synthetic complete')
        with patch.object(RUN.HP, 'attachment_map', return_value={}), patch.object(FD, 'verify_attachments', return_value={'status': 'verified', 'errors': [], 'files': {}}):
            self.probe.collect_failure_attachments()
        self.assertEqual(self.probe.report['status'], 'BLOCKED')
        self.assertFalse(self.probe.report['hostLaunched'])
        self.assertEqual(self.probe.report['errors'], ['Native test did not pass'])

    def test_runtime_order_and_limits_stay_strict(self):
        source = inspect.getsource(RUN.ReuseProbe.execute)
        self.assertLess(source.index('self.collect_failure_diagnostics('), source.index('self.harvest_results()'))
        self.assertLess(source.index('self.harvest_results()'), source.index("'Native test did not pass'"))
        self.assertLess(source.index("'Native test did not pass'"), source.index('HP.verify_summary('))
        self.assertIn("receipt = self.command(self.native_command(), 'native-settings-test', 240)", source)
        self.assertIn("self.failure_diagnostics['status'] == 'not-reached'", source)
        self.assertEqual(RUN.EXPERIMENT_SECONDS, 1080); self.assertEqual(RUN.FINALIZATION_SECONDS, 360)
        self.assertIn('timeout-minutes: 40', (HERE.parents[2] / '.github/workflows/p2-host-reuse.yml').read_text())
        diagnostic = inspect.getsource(RUN.ReuseProbe.collect_failure_diagnostics) + inspect.getsource(RUN.ReuseProbe.collect_failure_attachments)
        for forbidden in ('checked_command(', 'self.command(', "['status'] =", "['errors'].append", "['hostLaunched'] ="):
            self.assertNotIn(forbidden, diagnostic)

    def test_swift_original_receipt_precedes_observation_and_single_launch(self):
        source = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
        teardown = source.split('    override func tearDownWithError()', 1)[1].split('    private func noUnexpectedAlerts()', 1)[0]
        self.assertLess(teardown.index('FileHandle.standardOutput.write(line)'), teardown.index('add(receipt)'))
        self.assertLess(teardown.index('add(receipt)'), teardown.index('try captureFailedLaunchDiagnostics()'))
        self.assertLess(teardown.index('add(receipt)'), teardown.index('app.state'))
        self.assertIn('if status != "observed", launchCallReturned, launchCount == 1,', teardown)
        capture = source.split('    private func captureFailedLaunchDiagnostics()', 1)[1].split('    override func tearDownWithError()', 1)[0]
        self.assertIn('launchCount == 1, launchCallEntered, !launchCallReturned, navigation.isEmpty, observations.isEmpty', capture)
        self.assertEqual(capture.count('app.wait('), 1); self.assertEqual(capture.count('app.state'), 2)
        self.assertEqual(capture.count('app.screenshot()'), 1); self.assertEqual(capture.count('app.debugDescription'), 1)
        for forbidden in ('app.launch(', '.activate(', '.tap(', 'status = ', 'stage = ', 'launchCount +=', 'XCTAssert', 'XCTFail('): self.assertNotIn(forbidden, capture)
        self.assertIn('let waitTimeout: TimeInterval = 10', capture)
        self.assertNotIn('override func record', source); self.assertNotIn('continueAfterFailure = true', source)
        self.assertEqual(source.count('app.launch()'), 1)
        self.assertIn('app.launch()\n        launchCallReturned = true\n', source)

    def test_successful_route_bytes_unchanged_except_observational_flag(self):
        source = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
        route = source[source.index('    private func picker()'):].replace('        launchCallReturned = true\n', '', 1).replace('        launchCallEntered = true\n', '', 1)
        self.assertEqual(hashlib.sha256(route.encode()).hexdigest(), 'f987aad34ee02b82cbdb4189cc20e2b78ab4c8ae36dfc4d5b3b1b3ad97e4d4cd')
        setup = source.split('    override func setUpWithError()', 1)[1].split('    private func attachText', 1)[0]
        self.assertEqual(setup, ' throws {\n        continueAfterFailure = false\n    }\n\n')
        self.assertEqual(source.count('launchCallReturned = true'), 1)
        self.assertEqual(source.count('launchCallEntered = true'), 1)
        self.assertIn('guard publishLaunchPolicy() else { return }\n        launchCallEntered = true\n        app.launch()', source)


if __name__ == '__main__': unittest.main()
