"""Strict failure-only observation receipts. They never admit a Settings result."""
import hashlib
import json
import math

ORIGINAL_MARKER = 'P2_HOST_PROBE_OBSERVATIONS_V1:'
MARKER = 'P2_FAILED_LAUNCH_DIAGNOSTIC_V1:'
STATE_NAMES = {0: 'unknown', 1: 'notRunning', 2: 'runningBackgroundSuspended', 3: 'runningBackground', 4: 'runningForeground'}
BASE_STEPS = [('original-report', 'complete'), ('state-before', 'begin'), ('state-before', 'complete'),
              ('foreground-wait', 'begin'), ('foreground-wait', 'complete'), ('state-after', 'begin'), ('state-after', 'complete')]
CAPTURE_STEPS = [('screenshot', 'begin'), ('screenshot', 'complete'), ('accessibility', 'begin'), ('accessibility', 'complete'), ('finish', 'complete')]


def require(value, message):
    if not value:
        raise ValueError(message)


def finite(value):
    require(type(value) in (int, float), 'Invalid finite runner time type')
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    require(valid, 'Invalid finite runner time')
    return value


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def floating(raw):
        value = float(raw)
        require(math.isfinite(value), 'Nonfinite JSON number')
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_float=floating,
                      parse_constant=lambda _: require(False, 'Nonfinite JSON constant'))


def exact(value, expected):
    require(type(value) is type(expected), 'Wrong diagnostic value type')
    if type(expected) is dict:
        require(set(value) == set(expected), 'Unexpected diagnostic fields')
        for key in expected:
            exact(value[key], expected[key])
    elif type(expected) is list:
        require(len(value) == len(expected), 'Wrong diagnostic list count')
        for a, b in zip(value, expected):
            exact(a, b)
    else:
        require(value == expected, 'Diagnostic value differs')


def parse(output, identity, generated_environment, verify_launch_policy):
    """Accept valid partial prefixes for diagnosis; never turn a test into a pass."""
    original = None
    rows = []
    for line in output.splitlines():
        if ORIGINAL_MARKER in line:
            require(original is None and not rows, 'Repeated/late original report')
            raw = line.split(ORIGINAL_MARKER, 1)[1]
            require(len(raw.encode()) <= 32768, 'Oversized original blocked report')
            original = strict_json(raw)
            require(type(original) is dict and set(original) == {'identity', 'status', 'stage', 'launchCount', 'navigation', 'observations', 'launchPolicy'}, 'Wrong original report envelope')
            exact(original['identity'], identity)
            exact(original['status'], 'blocked'); exact(original['stage'], 'genuine-host-launch')
            exact(original['launchCount'], 1); exact(original['navigation'], []); exact(original['observations'], [])
            verify_launch_policy(original, generated_environment)
        elif MARKER in line:
            require(original is not None and len(rows) < 12, 'Diagnostic before original or too many events')
            raw = line.split(MARKER, 1)[1]
            require(len(raw.encode()) <= 32768, 'Oversized diagnostic event')
            row = strict_json(raw)
            require(type(row) is dict and set(row) == {'schemaVersion', 'identity', 'originalStatus', 'originalStage', 'launchCount', 'launchCallEntered', 'launchCallReturned', 'sequence', 'stage', 'event', 'monotonicSeconds', 'details'}, 'Wrong diagnostic envelope')
            exact(row['schemaVersion'], 1); exact(row['identity'], identity)
            exact(row['originalStatus'], 'blocked'); exact(row['originalStage'], 'genuine-host-launch')
            exact(row['launchCount'], 1); exact(row['launchCallEntered'], True); exact(row['launchCallReturned'], False); exact(row['sequence'], len(rows))
            finite(row['monotonicSeconds'])
            require(not rows or row['monotonicSeconds'] >= rows[-1]['monotonicSeconds'], 'Reversed diagnostic time')
            require(type(row['details']) is dict, 'Wrong details type')
            rows.append(row)
    if original is None:
        require(not rows, 'Unbound diagnostic events')
        return {'schemaVersion': 1, 'status': 'not-reached', 'events': [], 'originalReport': None,
                'diagnosticOnly': True, 'testResultReplaced': False, 'settingsAcceptance': False, 'errors': []}
    errors = []
    steps = BASE_STEPS
    reasons = []
    for i, row in enumerate(rows):
        if i == 7:
            wait = rows[4]['details']; after = rows[6]['details']
            if not wait['returned']: reasons.append('wait-returned-false')
            if not wait['withinBudget']: reasons.append('wait-budget-exceeded')
            if after['state'] != 4: reasons.append('not-foreground')
            steps = BASE_STEPS + ([('finish', 'skipped')] if reasons else CAPTURE_STEPS)
        require(i < len(steps), 'Extra diagnostic stage')
        exact([row['stage'], row['event']], list(steps[i]))
        d = row['details']
        if i == 0:
            exact(d, {'attachment': 'host-probe-observations.json'})
        elif i in (1, 5):
            exact(d, {})
        elif i in (2, 6):
            require(set(d) == {'state', 'stateName'} and type(d['state']) is int and d['state'] in STATE_NAMES, 'Invalid app state')
            exact(d['stateName'], STATE_NAMES[d['state']])
        elif i == 3:
            exact(d, {'requestedState': 'runningForeground', 'timeoutSeconds': 10})
        elif i == 4:
            require(set(d) == {'requestedState', 'timeoutSeconds', 'returned', 'startedMonotonicSeconds', 'finishedMonotonicSeconds', 'elapsedSeconds', 'withinBudget'}, 'Wrong wait details')
            exact(d['requestedState'], 'runningForeground'); exact(d['timeoutSeconds'], 10)
            require(type(d['returned']) is bool and type(d['withinBudget']) is bool, 'Wrong wait Boolean')
            start, finish, elapsed = (finite(d[k]) for k in ('startedMonotonicSeconds', 'finishedMonotonicSeconds', 'elapsedSeconds'))
            require(rows[3]['monotonicSeconds'] <= start <= finish <= row['monotonicSeconds'], 'Unbound wait times')
            require(abs((finish - start) - elapsed) <= 1e-6, 'Elapsed wait differs')
            exact(d['withinBudget'], elapsed <= 10)
        elif row['stage'] == 'screenshot':
            exact(d, {'attachment': 'host-probe-failure.png'})
        elif row['stage'] == 'accessibility':
            base = {'attachment': 'host-probe-failure.txt', 'maximumBytes': 524288}
            if row['event'] == 'begin':
                exact(d, base)
            else:
                require(set(d) == set(base) | {'observedUTF8Bytes', 'retainedUTF8Bytes', 'truncated'}, 'Wrong AX details')
                for key, value in base.items(): exact(d[key], value)
                observed, retained = d['observedUTF8Bytes'], d['retainedUTF8Bytes']
                require(type(observed) is int and observed >= 0 and type(retained) is int and 0 <= retained <= 524288 and type(d['truncated']) is bool, 'Invalid AX lengths')
                exact(d['truncated'], observed > 524288)
                require((not d['truncated'] and retained == observed) or (d['truncated'] and 524285 <= retained <= 524288), 'Invalid UTF8 truncation boundary')
                if d['truncated']: errors.append('Diagnostic AX snapshot truncated')
        elif row['event'] == 'skipped':
            exact(d, {'outcome': 'skipped', 'reasons': reasons})
            errors.append('Diagnostic capture skipped: ' + ', '.join(reasons))
        else:
            exact(d, {'outcome': 'captured'})
    complete = bool(rows) and rows[-1]['stage'] == 'finish'
    if not complete: errors.append('Incomplete diagnostic sequence')
    return {'schemaVersion': 1, 'status': 'complete' if complete else 'partial', 'events': rows, 'originalReport': original,
            'diagnosticOnly': True, 'testResultReplaced': False, 'settingsAcceptance': False, 'errors': errors}


def verify_attachments(receipt, rows):
    """Match each completed diagnostic to the exported source-bound test payload."""
    if receipt['status'] == 'not-reached':
        require(not any(name.startswith('host-probe-failed-launch-diagnostic-') for name in rows), 'Unbound diagnostic attachments')
        return {'status': 'not-reached', 'files': {}, 'errors': []}
    expected = {'host-probe-observations.json': receipt['originalReport']}
    for row in receipt['events']:
        expected['host-probe-failed-launch-diagnostic-' + str(row['sequence']) + '.json'] = row
    errors, files = [], {}
    for name, value in expected.items():
        if name not in rows:
            errors.append('Missing diagnostic attachment: ' + name); continue
        raw = rows[name].read_bytes(); exact(strict_json(raw.decode()), value)
        files[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    for stage, name in [('screenshot', 'host-probe-failure.png'), ('accessibility', 'host-probe-failure.txt')]:
        matching = [r for r in receipt['events'] if r['stage'] == stage]
        complete = next((r for r in matching if r['event'] == 'complete'), None)
        if name not in rows:
            if complete: errors.append('Missing completed capture: ' + name)
            continue
        require(matching, 'Unbound diagnostic capture')
        raw = rows[name].read_bytes()
        if stage == 'screenshot': require(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) <= 16 * 1024 * 1024, 'Invalid diagnostic PNG')
        else:
            raw.decode('utf-8'); require(len(raw) <= 524288, 'Oversized diagnostic AX')
            if complete: require(len(raw) == complete['details']['retainedUTF8Bytes'], 'AX bytes differ from receipt')
        files[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'completionObserved': complete is not None}
    require(set(rows) <= set(expected) | {'host-probe-failure.png', 'host-probe-failure.txt'}, 'Unexpected failed-launch probe attachment')
    return {'status': 'verified' if not errors else 'incomplete', 'files': files, 'errors': errors}
