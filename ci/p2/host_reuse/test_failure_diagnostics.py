"""Portable hostile-input tests; every receipt and attachment is synthetic.

Run with Python's unittest runner. No Xcode, app, simulator, native executable,
network, credentials, or successful Settings observation is used by this suite.
"""
import copy
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('failure_diagnostics_under_test', HERE / 'failure_diagnostics.py')
FD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FD)

IDENTITY = {
    'bundleIdentifier': 'example.synthetic.host',
    'executableSHA256': 'a' * 64,
    'sourceCommit': 'b' * 40,
    'sourceTree': 'c' * 40,
    'builderCommit': 'd' * 40,
}
GENERATED_ENVIRONMENT = {'SYNTHETIC_GENERATED_INPUT': '/synthetic/runner'}
# This is deliberately not a real policy receipt. The callback verifies exact
# delegation to the separate V2 policy gate, whose own tests validate its schema.
POLICY_SENTINEL = {'syntheticV2Policy': 'checked-by-callback'}
AX_MAXIMUM = 524288
PNG = b'\x89PNG\r\n\x1a\nsynthetic non-executable fixture'
AX = 'synthetic accessibility \u05e2\u05d1\u05e8\u05d9\u05ea \U0001f600\n'.encode('utf-8')
STEPS = [
    ('original-report', 'complete'), ('state-before', 'begin'),
    ('state-before', 'complete'), ('foreground-wait', 'begin'),
    ('foreground-wait', 'complete'), ('state-after', 'begin'),
    ('state-after', 'complete'), ('screenshot', 'begin'),
    ('screenshot', 'complete'), ('accessibility', 'begin'),
    ('accessibility', 'complete'), ('finish', 'complete'),
]


def original_report():
    return {'identity': copy.deepcopy(IDENTITY), 'status': 'blocked',
            'stage': 'genuine-host-launch', 'launchCount': 1, 'navigation': [],
            'observations': [], 'launchPolicy': copy.deepcopy(POLICY_SENTINEL)}


def diagnostic_rows(*, returned=True, elapsed=1.0, after=4, before=1,
                    observed=None, retained=None):
    observed = len(AX) if observed is None else observed
    retained = observed if retained is None else retained
    start = 103.25
    finish = start + elapsed
    within_budget = elapsed <= 10
    reasons = (["wait-returned-false"] if not returned else [])
    reasons += (["wait-budget-exceeded"] if not within_budget else [])
    reasons += (["not-foreground"] if after != 4 else [])
    steps = STEPS[:7] + ([('finish', 'skipped')] if reasons else STEPS[7:])
    details = [
        {'attachment': 'host-probe-observations.json'}, {},
        {'state': before, 'stateName': FD.STATE_NAMES[before]},
        {'requestedState': 'runningForeground', 'timeoutSeconds': 10},
        {'requestedState': 'runningForeground', 'timeoutSeconds': 10,
         'returned': returned, 'startedMonotonicSeconds': start,
         'finishedMonotonicSeconds': finish, 'elapsedSeconds': elapsed,
         'withinBudget': within_budget}, {},
        {'state': after, 'stateName': FD.STATE_NAMES[after]},
    ]
    if reasons:
        details.append({'outcome': 'skipped', 'reasons': reasons})
    else:
        details.extend([
            {'attachment': 'host-probe-failure.png'},
            {'attachment': 'host-probe-failure.png'},
            {'attachment': 'host-probe-failure.txt', 'maximumBytes': AX_MAXIMUM},
            {'attachment': 'host-probe-failure.txt', 'maximumBytes': AX_MAXIMUM,
             'observedUTF8Bytes': observed, 'retainedUTF8Bytes': retained,
             'truncated': observed > AX_MAXIMUM},
            {'outcome': 'captured'},
        ])
    return [{'schemaVersion': 1, 'identity': copy.deepcopy(IDENTITY),
             'originalStatus': 'blocked', 'originalStage': 'genuine-host-launch',
             'launchCount': 1, 'launchCallEntered': True, 'launchCallReturned': False, 'sequence': i,
             'stage': stage, 'event': event,
             'monotonicSeconds': 100.0 + i if i < 4 else finish + .25 + i - 4,
             'details': detail}
            for i, ((stage, event), detail) in enumerate(zip(steps, details))]


def marked(marker, value):
    return marker + json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def output_for(rows=None, original=None):
    rows = diagnostic_rows() if rows is None else rows
    original = original_report() if original is None else original
    return '\n'.join([marked(FD.ORIGINAL_MARKER, original)] + [marked(FD.MARKER, r) for r in rows])


class ReceiptFixture(unittest.TestCase):
    def setUp(self):
        self.policy_calls = []

    def policy_gate(self, report, generated_environment):
        FD.exact(report['launchPolicy'], POLICY_SENTINEL)
        FD.exact(generated_environment, GENERATED_ENVIRONMENT)
        self.policy_calls.append((copy.deepcopy(report), copy.deepcopy(generated_environment)))

    def parse(self, output=None, rows=None, original=None):
        return FD.parse(output_for(rows, original) if output is None else output,
                        IDENTITY, GENERATED_ENVIRONMENT, self.policy_gate)

    def assert_diagnosis_only(self, receipt):
        self.assertIs(receipt['diagnosticOnly'], True)
        self.assertIs(receipt['testResultReplaced'], False)
        self.assertIs(receipt['settingsAcceptance'], False)
        self.assertNotIn(receipt['status'], ('passed', 'observed', 'accepted'))
        if receipt['originalReport'] is not None:
            self.assertEqual(receipt['originalReport']['status'], 'blocked')
            self.assertEqual(receipt['originalReport']['stage'], 'genuine-host-launch')
            self.assertEqual(receipt['originalReport']['launchCount'], 1)
            self.assertEqual(receipt['originalReport']['navigation'], [])
            self.assertEqual(receipt['originalReport']['observations'], [])

    def reject_rows(self, mutate):
        rows = diagnostic_rows()
        mutate(rows)
        with self.assertRaises(ValueError):
            self.parse(rows=rows)


class DiagnosticSequenceTests(ReceiptFixture):
    def test_complete_capture_is_twelve_events_and_never_promotes(self):
        rows, original = diagnostic_rows(), original_report()
        saved = copy.deepcopy((rows, original))
        receipt = self.parse(rows=rows, original=original)
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(receipt['errors'], [])
        self.assertEqual(len(receipt['events']), 12)
        self.assertEqual(receipt['events'], rows)
        self.assertEqual([(r['stage'], r['event']) for r in receipt['events']], STEPS)
        self.assertTrue(all(r['launchCallReturned'] is False for r in receipt['events']))
        self.assertEqual((rows, original), saved)
        self.assertEqual(self.policy_calls, [(original, GENERATED_ENVIRONMENT)])
        self.assert_diagnosis_only(receipt)

    def test_every_capture_prefix_is_partial_diagnosis_only(self):
        rows = diagnostic_rows()
        for length in range(12):
            with self.subTest(length=length):
                receipt = self.parse(rows=rows[:length])
                self.assertEqual(receipt['status'], 'partial')
                self.assertEqual(receipt['events'], rows[:length])
                self.assertEqual(receipt['errors'], ['Incomplete diagnostic sequence'])
                self.assert_diagnosis_only(receipt)

    def test_all_seven_ineligible_combinations_have_exact_eight_event_skip(self):
        for returned, within_budget, foreground in itertools.product((False, True), repeat=3):
            if returned and within_budget and foreground:
                continue
            with self.subTest(returned=returned, within_budget=within_budget, foreground=foreground):
                rows = diagnostic_rows(returned=returned, elapsed=1 if within_budget else 10.5,
                                       after=4 if foreground else 3)
                receipt = self.parse(rows=rows)
                reasons = ([] if returned else ['wait-returned-false'])
                reasons += ([] if within_budget else ['wait-budget-exceeded'])
                reasons += ([] if foreground else ['not-foreground'])
                self.assertEqual(len(receipt['events']), 8)
                self.assertEqual(receipt['status'], 'complete')
                self.assertEqual(rows[-1]['details'], {'outcome': 'skipped', 'reasons': reasons})
                self.assertEqual(receipt['errors'], ['Diagnostic capture skipped: ' + ', '.join(reasons)])
                self.assert_diagnosis_only(receipt)
                for length in range(8):
                    partial = self.parse(rows=rows[:length])
                    self.assertEqual(partial['status'], 'partial')
                    self.assert_diagnosis_only(partial)

    def test_all_documented_state_values_and_names_are_supported(self):
        for state in range(5):
            with self.subTest(state=state):
                self.assertEqual(self.parse(rows=diagnostic_rows(before=state))['status'], 'complete')
                self.assertEqual(self.parse(rows=diagnostic_rows(after=state))['status'], 'complete')

    def test_unrelated_log_lines_and_prefixes_are_allowed(self):
        log = '\n'.join('2026 synthetic runner: ' + line for line in output_for().splitlines())
        receipt = self.parse(output='unrelated line\n\n' + log + '\nmore runner noise')
        self.assertEqual(receipt['status'], 'complete')
        self.assert_diagnosis_only(receipt)

    def test_no_markers_is_not_reached_without_policy_callback(self):
        for output in ('', 'ordinary successful-route output\n', 'P2_FAILED_LAUNCH_DIAGNOSTIC_V0:{}'):
            with self.subTest(output=output):
                receipt = self.parse(output=output)
                self.assertEqual(receipt['status'], 'not-reached')
                self.assertEqual(receipt['events'], [])
                self.assertIsNone(receipt['originalReport'])
                self.assertEqual(receipt['errors'], [])
                self.assert_diagnosis_only(receipt)
        self.assertEqual(self.policy_calls, [])

    def test_unbound_diagnostic_and_late_original_are_rejected(self):
        row = marked(FD.MARKER, diagnostic_rows()[0])
        original = marked(FD.ORIGINAL_MARKER, original_report())
        for output in (row, row + '\n' + original, row + '\n' + output_for()):
            with self.subTest(output=output[:80]), self.assertRaises(ValueError):
                self.parse(output=output)

    def test_repeated_original_at_any_position_is_rejected(self):
        lines = output_for().splitlines()
        for position in range(1, len(lines) + 1):
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.parse(output='\n'.join(lines[:position] + [lines[0]] + lines[position:]))

    def test_repeated_or_missing_diagnostic_at_any_position_is_rejected(self):
        original_rows = diagnostic_rows()
        for position in range(12):
            with self.subTest(operation='repeat', position=position), self.assertRaises(ValueError):
                rows = copy.deepcopy(original_rows)
                rows.insert(position, copy.deepcopy(rows[position]))
                self.parse(rows=rows)
            if position < 11:  # A missing final event is a valid partial prefix.
                with self.subTest(operation='remove', position=position), self.assertRaises(ValueError):
                    rows = copy.deepcopy(original_rows)
                    del rows[position]
                    for i, row in enumerate(rows):
                        row['sequence'] = i
                    self.parse(rows=rows)

    def test_reordered_steps_fail_even_after_rebinding_sequences_and_times(self):
        for position in range(11):
            rows = diagnostic_rows()
            rows[position], rows[position + 1] = rows[position + 1], rows[position]
            for i, row in enumerate(rows):
                row['sequence'] = i
                row['monotonicSeconds'] = 200 + i
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_thirteenth_event_is_rejected(self):
        rows = diagnostic_rows()
        extra = copy.deepcopy(rows[-1]); extra['sequence'] = 12
        with self.assertRaises(ValueError):
            self.parse(rows=rows + [extra])

    def test_events_after_skipped_finish_are_rejected(self):
        rows = diagnostic_rows(returned=False)
        extra = copy.deepcopy(rows[-1]); extra['sequence'] = 8
        with self.assertRaises(ValueError):
            self.parse(rows=rows + [extra])

    def test_skip_is_not_allowed_for_eligible_capture(self):
        rows = diagnostic_rows()[:8]
        rows[-1].update(stage='finish', event='skipped', details={'outcome': 'skipped', 'reasons': []})
        with self.assertRaises(ValueError):
            self.parse(rows=rows)

    def test_capture_is_not_allowed_if_any_eligibility_condition_fails(self):
        for kwargs in ({'returned': False}, {'elapsed': 10.5}, {'after': 3}):
            rows = diagnostic_rows(**kwargs)[:7] + diagnostic_rows()[7:]
            for i, row in enumerate(rows):
                if i >= 7:
                    row['monotonicSeconds'] = rows[6]['monotonicSeconds'] + i - 6
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_skip_reasons_are_exact_ordered_and_nonempty(self):
        for reasons in ([], ['not-foreground'], ['unknown'], ['wait-returned-false'] * 2,
                        ['not-foreground', 'wait-budget-exceeded', 'wait-returned-false'],
                        'wait-returned-false', None):
            rows = diagnostic_rows(returned=False, elapsed=11, after=3)
            rows[-1]['details']['reasons'] = reasons
            with self.subTest(reasons=reasons), self.assertRaises(ValueError):
                self.parse(rows=rows)


class DiagnosticEnvelopeTests(ReceiptFixture):
    def test_original_envelope_is_exact_and_preserved(self):
        original = original_report()
        for field in original:
            changed = copy.deepcopy(original); del changed[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.parse(original=changed)
        for extra in ('launchCallReturned', 'settingsAcceptance', 'testResultReplaced', 'extra'):
            changed = copy.deepcopy(original); changed[extra] = False
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.parse(original=changed)

    def test_original_must_be_single_blocked_launch_with_no_navigation_or_observations(self):
        mutations = [('status', 'observed'), ('status', True), ('stage', 'ordinary-settings-tab-navigation'),
                     ('launchCount', 0), ('launchCount', 2), ('launchCount', True), ('launchCount', 1.0),
                     ('navigation', [{'action': 'tap'}]), ('observations', [{'language': 'en'}]),
                     ('navigation', {}), ('observations', None)]
        for field, value in mutations:
            original = original_report(); original[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.parse(original=original)

    def test_original_and_every_event_bind_all_five_identity_fields(self):
        for field in IDENTITY:
            for position in range(-1, 12):
                rows, original = diagnostic_rows(), original_report()
                target = original if position == -1 else rows[position]
                target['identity'][field] += '-foreign'
                with self.subTest(field=field, position=position), self.assertRaises(ValueError):
                    self.parse(rows=rows, original=original)

    def test_identity_missing_extra_and_wrong_type_fields_fail(self):
        identities = [None, [], dict(IDENTITY, unexpected='x')]
        for field in IDENTITY:
            missing = dict(IDENTITY); del missing[field]; identities.append(missing)
            changed = dict(IDENTITY); changed[field] = True; identities.append(changed)
        for identity in identities:
            for location in ('original', 'event'):
                rows, original = diagnostic_rows(), original_report()
                (original if location == 'original' else rows[0])['identity'] = identity
                with self.subTest(identity=identity, location=location), self.assertRaises(ValueError):
                    self.parse(rows=rows, original=original)

    def test_every_diagnostic_envelope_field_is_required(self):
        for field in diagnostic_rows()[0]:
            rows = diagnostic_rows(); del rows[0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_extra_fields_cannot_claim_acceptance_or_replace_result(self):
        for field in ('settingsAcceptance', 'testResultReplaced', 'observations', 'navigation', 'extra'):
            rows = diagnostic_rows(); rows[0][field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_launch_call_must_be_unreturned_in_every_event(self):
        for position in range(12):
            for value in (True, 0, 1, 'false', None):
                rows = diagnostic_rows(); rows[position]['launchCallReturned'] = value
                with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)

    def test_exact_integer_and_string_fields_reject_type_confusion(self):
        mutations = [('schemaVersion', True), ('schemaVersion', 1.0), ('schemaVersion', 2),
                     ('launchCount', True), ('launchCount', 1.0), ('launchCount', 0), ('launchCount', 2),
                     ('sequence', False), ('sequence', 0.0), ('sequence', '0'), ('sequence', -1),
                     ('originalStatus', 'observed'), ('originalStage', 'before-launch'),
                     ('stage', None), ('event', ['complete']), ('details', []), ('details', None)]
        for field, value in mutations:
            rows = diagnostic_rows(); rows[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_details_schema_is_exact_at_every_capture_step(self):
        for position in range(12):
            rows = diagnostic_rows(); rows[position]['details']['unexpected'] = 1
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.parse(rows=rows)
        for position in (0, 7, 8, 9, 10):
            rows = diagnostic_rows(); rows[position]['details']['attachment'] = '../foreign.json'
            with self.subTest(attachment_position=position), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_app_state_code_and_name_cannot_disagree(self):
        for position in (2, 6):
            for value in (True, 4.0, '4', -1, 5, None):
                rows = diagnostic_rows(); rows[position]['details']['state'] = value
                with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)
            rows = diagnostic_rows(); rows[position]['details']['stateName'] = 'notRunning' if position == 6 else 'runningForeground'
            with self.subTest(position=position, mismatch=True), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_policy_callback_is_required_even_for_empty_prefix(self):
        receipt = self.parse(rows=[])
        self.assertEqual(len(self.policy_calls), 1)
        self.assertEqual(receipt['originalReport']['launchPolicy'], POLICY_SENTINEL)

    def test_policy_callback_failure_cannot_be_overridden_by_capture(self):
        for policy in ({}, {'schemaVersion': 1}, dict(POLICY_SENTINEL, extra=True)):
            original = original_report(); original['launchPolicy'] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.parse(original=original)
        def deny(report, generated):
            raise ValueError('Synthetic V2 gate denial')
        with self.assertRaisesRegex(ValueError, 'Synthetic V2 gate denial'):
            FD.parse(output_for(), IDENTITY, GENERATED_ENVIRONMENT, deny)


class DiagnosticTimingTests(ReceiptFixture):
    def test_launch_call_must_have_been_entered_in_every_event(self):
        for position in range(12):
            for value in (False, 0, 1, 'true', None):
                rows = diagnostic_rows(); rows[position]['launchCallEntered'] = value
                with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)

    def test_wait_zero_and_exact_ten_seconds_remain_eligible(self):
        for elapsed in (0, 0.0, 10, 10.0):
            with self.subTest(elapsed=elapsed):
                receipt = self.parse(rows=diagnostic_rows(elapsed=elapsed))
                self.assertEqual(len(receipt['events']), 12)
                self.assert_diagnosis_only(receipt)

    def test_over_budget_even_by_small_amount_requires_skip(self):
        receipt = self.parse(rows=diagnostic_rows(elapsed=10.000001))
        self.assertEqual(len(receipt['events']), 8)
        self.assertEqual(receipt['events'][-1]['details']['reasons'], ['wait-budget-exceeded'])

    def test_equal_event_timestamps_are_valid(self):
        rows = diagnostic_rows(elapsed=0)
        for row in rows:
            row['monotonicSeconds'] = 100
        rows[4]['details'].update(startedMonotonicSeconds=100, finishedMonotonicSeconds=100)
        self.assertEqual(self.parse(rows=rows)['status'], 'complete')

    def test_every_runner_timestamp_must_be_numeric_finite_nonnegative(self):
        for value in (True, False, '100', None, -0.001, float('nan'), float('inf'), -float('inf'), 10 ** 400):
            rows = diagnostic_rows(); rows[0]['monotonicSeconds'] = value
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_reversed_event_timestamp_at_every_position_fails(self):
        for position in range(1, 12):
            rows = diagnostic_rows(); rows[position]['monotonicSeconds'] = rows[position - 1]['monotonicSeconds'] - .1
            with self.subTest(position=position), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_wait_times_reject_nonfinite_overflow_negative_and_type_confusion(self):
        for field in ('startedMonotonicSeconds', 'finishedMonotonicSeconds', 'elapsedSeconds'):
            for value in (True, False, None, '1', -1, float('nan'), float('inf'), -float('inf'), 10 ** 400):
                rows = diagnostic_rows(); rows[4]['details'][field] = value
                with self.subTest(field=field, value=str(value)[:40]), self.assertRaises(ValueError):
                    self.parse(rows=rows)

    def test_wait_must_start_after_its_begin_and_finish_before_completion_receipt(self):
        for start, finish in ((101.5, 102.5), (102.5, 103.5), (104.5, 103.5), (103.25, 106.25)):
            rows = diagnostic_rows()
            rows[4]['details'].update(startedMonotonicSeconds=start, finishedMonotonicSeconds=finish,
                                      elapsedSeconds=finish - start)
            with self.subTest(start=start, finish=finish), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_elapsed_must_match_runner_clock_delta(self):
        for elapsed in (0, 1.001, 9, 10):
            rows = diagnostic_rows(); rows[4]['details']['elapsedSeconds'] = elapsed
            with self.subTest(elapsed=elapsed), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_budget_flag_must_equal_measured_elapsed_budget(self):
        for elapsed in (1, 10, 10.001):
            rows = diagnostic_rows(elapsed=elapsed)
            rows[4]['details']['withinBudget'] = not rows[4]['details']['withinBudget']
            with self.subTest(elapsed=elapsed), self.assertRaises(ValueError):
                self.parse(rows=rows)

    def test_wait_booleans_are_exact_booleans(self):
        for field in ('returned', 'withinBudget'):
            for value in (0, 1, 'true', None):
                rows = diagnostic_rows(); rows[4]['details'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)

    def test_requested_state_and_ten_second_timeout_are_exact(self):
        for position in (3, 4):
            for field, value in (('requestedState', 'runningBackground'), ('timeoutSeconds', 20),
                                 ('timeoutSeconds', 10.0), ('timeoutSeconds', True), ('timeoutSeconds', '10')):
                rows = diagnostic_rows(); rows[position]['details'][field] = value
                with self.subTest(position=position, field=field, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)


class DiagnosticAXTests(ReceiptFixture):
    def test_untruncated_zero_and_limit_are_valid(self):
        for size in (0, 1, AX_MAXIMUM - 1, AX_MAXIMUM):
            with self.subTest(size=size):
                receipt = self.parse(rows=diagnostic_rows(observed=size, retained=size))
                self.assertEqual(receipt['errors'], [])
                self.assert_diagnosis_only(receipt)

    def test_all_four_valid_utf8_truncation_boundaries_are_diagnosis_only(self):
        for retained in range(AX_MAXIMUM - 3, AX_MAXIMUM + 1):
            receipt = self.parse(rows=diagnostic_rows(observed=AX_MAXIMUM + 4, retained=retained))
            with self.subTest(retained=retained):
                self.assertEqual(receipt['errors'], ['Diagnostic AX snapshot truncated'])
                self.assert_diagnosis_only(receipt)

    def test_invalid_utf8_size_and_truncation_claims_fail(self):
        cases = [(-1, 0), (0, -1), (3, 2), (3, 4), (0, 1),
                 (AX_MAXIMUM + 1, AX_MAXIMUM + 1), (AX_MAXIMUM + 1, AX_MAXIMUM - 4),
                 (AX_MAXIMUM + 1, 0)]
        for observed, retained in cases:
            with self.subTest(observed=observed, retained=retained), self.assertRaises(ValueError):
                self.parse(rows=diagnostic_rows(observed=observed, retained=retained))

    def test_ax_metadata_rejects_wrong_types_and_limits(self):
        for field in ('observedUTF8Bytes', 'retainedUTF8Bytes'):
            for value in (True, False, 1.0, '1', None):
                rows = diagnostic_rows(); rows[10]['details'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)
        for value in (0, 1, 'false', None, True):
            rows = diagnostic_rows(); rows[10]['details']['truncated'] = value
            with self.subTest(truncated=value), self.assertRaises(ValueError):
                self.parse(rows=rows)
        for position in (9, 10):
            for value in (AX_MAXIMUM - 1, AX_MAXIMUM + 1, float(AX_MAXIMUM), True):
                rows = diagnostic_rows(); rows[position]['details']['maximumBytes'] = value
                with self.subTest(position=position, maximum=value), self.assertRaises(ValueError):
                    self.parse(rows=rows)

    def test_truncated_flag_cannot_hide_truncation(self):
        rows = diagnostic_rows(observed=AX_MAXIMUM + 1, retained=AX_MAXIMUM)
        rows[10]['details']['truncated'] = False
        with self.assertRaises(ValueError):
            self.parse(rows=rows)


class DiagnosticJSONTests(ReceiptFixture):
    def test_duplicate_top_level_and_nested_json_keys_fail(self):
        raw = output_for()
        mutations = [raw.replace('"status":"blocked"', '"status":"blocked","status":"blocked"', 1),
                     raw.replace('"schemaVersion":1', '"schemaVersion":1,"schemaVersion":1', 1),
                     raw.replace('"bundleIdentifier":', '"bundleIdentifier":"foreign","bundleIdentifier":', 1),
                     raw.replace('"attachment":"host-probe-observations.json"',
                                 '"attachment":"host-probe-observations.json","attachment":"host-probe-observations.json"', 1)]
        for index, output in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.parse(output=output)

    def test_json_nan_infinity_and_float_overflow_fail(self):
        for token in ('NaN', 'Infinity', '-Infinity', '1e309', '-1e309'):
            output = output_for().replace('"monotonicSeconds":100.0', '"monotonicSeconds":' + token, 1)
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.parse(output=output)

    def test_nonobject_or_malformed_json_fail_for_both_markers(self):
        original = marked(FD.ORIGINAL_MARKER, original_report())
        for raw in ('null', '[]', 'true', '"string"', '1', '{', '{} trailing', '{}{}'):
            for marker in (FD.ORIGINAL_MARKER, FD.MARKER):
                output = marker + raw if marker == FD.ORIGINAL_MARKER else original + '\n' + marker + raw
                with self.subTest(marker=marker, raw=raw), self.assertRaises(ValueError):
                    self.parse(output=output)

    def test_original_and_event_raw_json_are_bounded_by_utf8_bytes(self):
        original = marked(FD.ORIGINAL_MARKER, original_report())
        event = marked(FD.MARKER, diagnostic_rows()[0])
        for line, prefix in ((original, ''), (event, original + '\n')):
            marker = FD.ORIGINAL_MARKER if not prefix else FD.MARKER
            raw = line[len(marker):]
            padding = ' ' * (32768 - len(raw.encode('utf-8')))
            receipt = self.parse(output=prefix + marker + raw + padding)
            self.assertEqual(receipt['status'], 'partial')
            with self.assertRaisesRegex(ValueError, 'Oversized'):
                self.parse(output=prefix + marker + raw + padding + ' ')
        # Multibyte strings must not exploit character counts as byte counts.
        rows = diagnostic_rows(); rows[0]['identity']['bundleIdentifier'] = '\u00e9' * 20000
        raw = json.dumps(rows[0], ensure_ascii=False)
        self.assertLess(len(raw), 32768)
        self.assertGreater(len(raw.encode('utf-8')), 32768)
        with self.assertRaisesRegex(ValueError, 'Oversized'):
            self.parse(output=original + '\n' + FD.MARKER + raw)

    def test_event_marker_embedded_in_json_cannot_create_extra_evidence(self):
        original = original_report()
        original['launchPolicy']['injected'] = FD.MARKER + '{}'
        with self.assertRaises(ValueError):
            self.parse(original=original)


class DiagnosticAttachmentTests(ReceiptFixture):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def write(self, name, raw):
        path = self.directory / name
        path.write_bytes(raw)
        return path

    def attachments(self, receipt, *, ax=AX, png=PNG):
        values = {'host-probe-observations.json': receipt['originalReport']}
        values.update({'host-probe-failed-launch-diagnostic-' + str(row['sequence']) + '.json': row
                       for row in receipt['events']})
        files = {name: self.write(name, json.dumps(value, indent=2, ensure_ascii=False).encode('utf-8'))
                 for name, value in values.items()}
        if any(r['stage'] == 'screenshot' for r in receipt['events']):
            files['host-probe-failure.png'] = self.write('host-probe-failure.png', png)
        if any(r['stage'] == 'accessibility' for r in receipt['events']):
            files['host-probe-failure.txt'] = self.write('host-probe-failure.txt', ax)
        return files

    def test_all_fifteen_exact_attachments_are_hashed_without_promotion(self):
        receipt = self.parse()
        before = copy.deepcopy(receipt)
        paths = self.attachments(receipt)
        verified = FD.verify_attachments(receipt, paths)
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['errors'], [])
        self.assertEqual(len(verified['files']), 15)
        for name, path in paths.items():
            raw = path.read_bytes()
            self.assertEqual(verified['files'][name]['bytes'], len(raw))
            self.assertEqual(verified['files'][name]['sha256'], hashlib.sha256(raw).hexdigest())
        for name in ('host-probe-failure.png', 'host-probe-failure.txt'):
            self.assertIs(verified['files'][name]['completionObserved'], True)
        self.assertEqual(receipt, before)
        self.assert_diagnosis_only(receipt)

    def test_skipped_capture_verifies_only_original_and_eight_event_json_files(self):
        receipt = self.parse(rows=diagnostic_rows(returned=False))
        verified = FD.verify_attachments(receipt, self.attachments(receipt))
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(len(verified['files']), 9)
        self.assertFalse(any(name.endswith(('.png', '.txt')) for name in verified['files']))
        self.assert_diagnosis_only(receipt)

    def test_every_missing_json_attachment_is_explicitly_incomplete(self):
        receipt = self.parse()
        for name in [n for n in self.attachments(receipt) if n.endswith('.json')]:
            paths = self.attachments(receipt); del paths[name]
            with self.subTest(name=name):
                verified = FD.verify_attachments(receipt, paths)
                self.assertEqual(verified['status'], 'incomplete')
                self.assertEqual(verified['errors'], ['Missing diagnostic attachment: ' + name])
                self.assertNotIn(name, verified['files'])
                self.assert_diagnosis_only(receipt)

    def test_missing_completed_capture_files_are_explicitly_incomplete(self):
        receipt = self.parse()
        for names in (('host-probe-failure.png',), ('host-probe-failure.txt',),
                      ('host-probe-failure.png', 'host-probe-failure.txt')):
            paths = self.attachments(receipt)
            for name in names:
                del paths[name]
            with self.subTest(names=names):
                verified = FD.verify_attachments(receipt, paths)
                self.assertEqual(verified['status'], 'incomplete')
                self.assertEqual(verified['errors'], ['Missing completed capture: ' + name for name in names])

    def test_all_json_payloads_must_exactly_match_bound_stdout_receipt(self):
        receipt = self.parse()
        for name in [n for n in self.attachments(receipt) if n.endswith('.json')]:
            paths = self.attachments(receipt)
            value = json.loads(paths[name].read_text()); value['identity']['sourceCommit'] = 'f' * 40
            paths[name].write_text(json.dumps(value))
            with self.subTest(name=name), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, paths)

    def test_per_event_attachments_cannot_be_swapped(self):
        receipt = self.parse(); paths = self.attachments(receipt)
        first, second = ('host-probe-failed-launch-diagnostic-' + str(i) + '.json' for i in (1, 5))
        paths[first], paths[second] = paths[second], paths[first]
        with self.assertRaises(ValueError):
            FD.verify_attachments(receipt, paths)

    def test_json_attachments_reject_duplicates_nonfinite_and_invalid_utf8(self):
        receipt = self.parse()
        name = 'host-probe-failed-launch-diagnostic-0.json'
        for mode in ('duplicate', 'nonfinite', 'utf8', 'type'):
            paths = self.attachments(receipt)
            raw = paths[name].read_bytes()
            if mode == 'duplicate':
                raw = raw.replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1')
            elif mode == 'nonfinite':
                raw = raw.replace(b'"monotonicSeconds": 100.0', b'"monotonicSeconds": 1e309')
            elif mode == 'type':
                raw = raw.replace(b'"schemaVersion": 1', b'"schemaVersion": true')
            else:
                raw = b'\xff'
            paths[name].write_bytes(raw)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, paths)

    def test_partial_begin_can_retain_capture_without_claiming_completion(self):
        for length, name in ((8, 'host-probe-failure.png'), (10, 'host-probe-failure.txt')):
            receipt = self.parse(rows=diagnostic_rows()[:length])
            paths = self.attachments(receipt)
            verified = FD.verify_attachments(receipt, paths)
            with self.subTest(length=length):
                self.assertEqual(verified['status'], 'verified')
                self.assertIs(verified['files'][name]['completionObserved'], False)
                self.assertEqual(receipt['status'], 'partial')
                self.assert_diagnosis_only(receipt)
                del paths[name]
                verified = FD.verify_attachments(receipt, paths)
                self.assertEqual(verified['status'], 'verified')

    def test_capture_file_requires_matching_begin_even_for_partial_and_skip(self):
        for rows in (diagnostic_rows()[:7], diagnostic_rows(returned=False)):
            receipt = self.parse(rows=rows)
            for name, raw in (('host-probe-failure.png', PNG), ('host-probe-failure.txt', AX)):
                paths = self.attachments(receipt); paths[name] = self.write(name, raw)
                with self.subTest(length=len(rows), name=name), self.assertRaises(ValueError):
                    FD.verify_attachments(receipt, paths)

    def test_unexpected_attachment_names_and_late_event_files_fail(self):
        for rows, name in ((diagnostic_rows(), 'foreign.json'),
                           (diagnostic_rows(), 'host-probe-failed-launch-diagnostic-12.json'),
                           (diagnostic_rows(), 'host-probe-failed-launch-diagnostic-01.json'),
                           (diagnostic_rows()[:3], 'host-probe-failed-launch-diagnostic-3.json'),
                           (diagnostic_rows(returned=False), 'host-probe-failed-launch-diagnostic-8.json')):
            receipt = self.parse(rows=rows); paths = self.attachments(receipt)
            paths[name] = self.write(name, b'{}')
            with self.subTest(length=len(rows), name=name), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, paths)

    def test_png_requires_signature_and_sixteen_megabyte_bound(self):
        receipt = self.parse()
        for raw in (b'', b'not png', b'\x89PNG\r\n\x1a', b'x' + PNG,
                    PNG[:8] + b'x' * (16 * 1024 * 1024 - 7)):
            paths = self.attachments(receipt, png=raw)
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, paths)
        paths = self.attachments(receipt, png=PNG[:8] + b'x' * (16 * 1024 * 1024 - 8))
        self.assertEqual(FD.verify_attachments(receipt, paths)['status'], 'verified')

    def test_ax_is_strict_utf8_and_matches_retained_byte_count(self):
        receipt = self.parse()
        for raw in (AX[:-1], AX + b'x', b'\xff', b'\xc0\xaf', b'\xed\xa0\x80', b'\xf0\x9f\x98',
                    b'x' * (AX_MAXIMUM + 1)):
            paths = self.attachments(receipt, ax=raw)
            with self.subTest(length=len(raw), prefix=raw[:4]), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, paths)

    def test_partial_ax_also_rejects_invalid_utf8_and_oversize(self):
        receipt = self.parse(rows=diagnostic_rows()[:10])
        for raw in (b'\xff', b'x' * (AX_MAXIMUM + 1)):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, self.attachments(receipt, ax=raw))

    def test_truncated_utf8_snapshots_hash_exact_retained_bytes(self):
        samples = ['x' * AX_MAXIMUM + 'x',
                   'x' * (AX_MAXIMUM - 1) + '\u05d0',
                   'x' * (AX_MAXIMUM - 2) + '\u20ac',
                   'x' * (AX_MAXIMUM - 3) + '\U0001f600']
        for text in samples:
            raw = text.encode('utf-8'); kept = raw[:AX_MAXIMUM]
            while True:
                try:
                    kept.decode('utf-8'); break
                except UnicodeDecodeError:
                    kept = kept[:-1]
            receipt = self.parse(rows=diagnostic_rows(observed=len(raw), retained=len(kept)))
            verified = FD.verify_attachments(receipt, self.attachments(receipt, ax=kept))
            with self.subTest(retained=len(kept)):
                self.assertEqual(verified['status'], 'verified')
                self.assertEqual(verified['files']['host-probe-failure.txt']['sha256'], hashlib.sha256(kept).hexdigest())
                self.assertIn('Diagnostic AX snapshot truncated', receipt['errors'])
                self.assert_diagnosis_only(receipt)

    def test_not_reached_leaves_normal_route_attachments_to_inherited_gate(self):
        receipt = self.parse(output='')
        for names in ([], ['host-probe-observations.json'], ['host-probe-launch.png', 'host-probe-en-before.txt'],
                      ['host-probe-failure.png', 'host-probe-failure.txt']):
            paths = {name: self.write(name, b'synthetic normal-route payload') for name in names}
            with self.subTest(names=names):
                self.assertEqual(FD.verify_attachments(receipt, paths),
                                 {'status': 'not-reached', 'files': {}, 'errors': []})

    def test_not_reached_rejects_unbound_diagnostic_event_attachments(self):
        receipt = self.parse(output='')
        for name in ('host-probe-failed-launch-diagnostic-0.json',
                     'host-probe-failed-launch-diagnostic-12.json',
                     'host-probe-failed-launch-diagnostic-unexpected.txt'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                FD.verify_attachments(receipt, {name: self.write(name, b'{}')})


if __name__ == '__main__':
    unittest.main(verbosity=2)
