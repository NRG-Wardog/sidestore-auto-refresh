"""Exact source-owned startup acknowledgement; the service fault stays unresolved.

The observation verifier below is the pinned host verifier with only its startup
navigation block replaced. All picker, frame, identity and attachment gates remain.
"""
import math

NOTICE_TITLE = 'Stay Informed About Refreshes'
NOTICE_BODY = 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.'
ERROR_TITLE = 'SideStore'
ERROR_CODE = 'SS-LAUNCH-C11-L12'
ERROR_BODY = 'The embedded SideStore process could not be launched.\nError ID: ' + ERROR_CODE
# Exact finite code/body pairs, never a prefix, suffix or numeric-code pattern.
ERROR_BODIES = {
    ERROR_CODE: ERROR_BODY,
    'SS-LAUNCH-C11-L15': 'The embedded SideStore process could not be launched.\nError ID: SS-LAUNCH-C11-L15',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def labels(value, title, body):
    require(isinstance(value, list) and len(value) <= 64 and
            all(isinstance(x, str) and len(x.encode('utf-8')) <= 16384 for x in value), 'Unbounded startup labels')
    require(value.count(title) == 1 and value.count(body) == 1, 'Exact startup labels missing or duplicated')
    return value


def service_failure(report, complete=False):
    """Recognize the exact fault even when a later action/assertion fails."""
    if 'serviceFailure' not in report:
        return None
    fault = report['serviceFailure']
    require(isinstance(fault, dict), 'Malformed service failure')
    code = fault.get('diagnosticCode')
    require(isinstance(code, str) and code in ERROR_BODIES, 'Unapproved service-error code')
    body = ERROR_BODIES[code]
    actual = labels(fault.get('actualStaticTextLabels'), ERROR_TITLE, body)
    require(sorted(actual) == sorted([ERROR_TITLE, body]), 'Extra service-error text')
    flags = ('preTapInventoryVerified', 'hittableBeforeTap', 'acknowledgementAttempted', 'acknowledgementReturned', 'alertAbsentAfterAcknowledgement')
    require(all(type(fault.get(k)) is bool for k in flags), 'Invalid service acknowledgement flags')
    expected = {'diagnosticCode': code, 'title': ERROR_TITLE, 'body': body,
                'actualStaticTextLabels': actual, 'buttons': ['Copy Diagnostics', 'OK'],
                'presentationOnly': True, 'resolved': False, 'backendReadinessProven': False,
                **{key: fault[key] for key in flags}}
    require(fault == expected and fault['presentationOnly'] is True and fault['resolved'] is False and
            fault['backendReadinessProven'] is False, 'Service fault changed, omitted or misrepresented as recovered')
    require(not fault['acknowledgementReturned'] or fault['acknowledgementAttempted'], 'Returned without attempted acknowledgement')
    require(not fault['acknowledgementAttempted'] or (fault['hittableBeforeTap'] and fault['preTapInventoryVerified']), 'Acknowledgement without hittable action')
    require(not fault['alertAbsentAfterAcknowledgement'] or fault['acknowledgementReturned'], 'Absence without returned acknowledgement')
    if complete:
        require(all(fault[key] is True for key in flags), 'Incomplete service acknowledgement')
    return fault


def verify_navigation(report):
    actions = report.get('navigation')
    require(isinstance(actions, list) and len(actions) <= 2, 'Unexpected startup/navigation actions')
    fault = service_failure(report, complete=True)
    if not actions:
        require(fault is None, 'Orphaned service fault')
        return set()
    first = actions[0]
    require(isinstance(first, dict), 'Malformed first action')
    actual = labels(first.get('actualStaticTextLabels'), NOTICE_TITLE, NOTICE_BODY)
    expected = {'action': 'known-notification-later', 'title': NOTICE_TITLE, 'body': NOTICE_BODY,
                'actualStaticTextLabels': actual, 'buttons': ['Allow Notifications', 'Later'], 'tapped': 'Later',
                'hittableBeforeTap': True, 'tapAttempted': True, 'tapReturned': True,
                'originalAlertAbsent': True, 'successor': 'none' if len(actions) == 1 else 'exact-service-failure'}
    require(first == expected and all(first[k] is True for k in ('hittableBeforeTap', 'tapAttempted', 'tapReturned', 'originalAlertAbsent')),
            'Unreviewed or incomplete Later action')
    if len(actions) == 1:
        require(fault is None, 'Fault omitted from action sequence')
    else:
        require(fault is not None, 'Service acknowledgement success omits unresolved fault')
        second = actions[1]
        expected = {'action': 'known-service-error-ok', 'title': ERROR_TITLE, 'body': fault['body'],
                    'actualStaticTextLabels': fault['actualStaticTextLabels'], 'buttons': ['Copy Diagnostics', 'OK'],
                    'tapped': 'OK', 'hittableBeforeTap': True, 'preTapInventoryVerified': True, 'tapAttempted': True, 'tapReturned': True,
                    'allAlertsAbsentAfterTap': True}
        require(second == expected and all(second[k] is True for k in ('preTapInventoryVerified', 'hittableBeforeTap', 'tapAttempted', 'tapReturned', 'allAlertsAbsentAfterTap')),
                'Unreviewed, repeated or incomplete service acknowledgement')
    return {'host-probe-notice-before.png', 'host-probe-notice-before.txt',
            'host-probe-notice-after.png', 'host-probe-notice-after.txt'}


def verify_observations(directory, identity, builder_commit, host):
    attachment_map, strict_json, sha = host.attachment_map, host.strict_json, host.sha
    SOURCE, TREE, BUNDLE_ID = host.SOURCE, host.TREE, host.BUNDLE_ID
    rows = attachment_map(directory)
    report = strict_json(rows['host-probe-observations.json'].read_text())
    expected = {'sourceCommit': SOURCE, 'sourceTree': TREE, 'builderCommit': builder_commit,
                'bundleIdentifier': BUNDLE_ID, 'executableSHA256': identity['executableSHA256']}
    require(report.get('identity') == expected, 'Observation identity differs from built/installed product')
    require(report.get('status') == 'observed' and type(report.get('launchCount')) is int and report['launchCount'] == 1,
            'Incomplete or modified launch path')
    observations = report.get('observations')
    require(isinstance(observations, list) and len(observations) == 3, 'Missing picker observations')
    expected_names = {'host-probe-observations.json', 'host-probe-launch.png', 'host-probe-launch.txt'}
    expected_names |= verify_navigation(report)
    for row, code, native, stage in zip(observations, ['en', 'he', 'en'], ['English', 'עברית', 'English'],
                                      ['en-before', 'he-selected', 'en-returned']):
        require(row.get('stage') == stage and row.get('language') == code and row.get('selectedNativeName') == native and
                row.get('selectedValueVisible') is True and row.get('settingsTabVisible') is True and
                row.get('applicationForeground') is True, 'Invalid selected-language observation')
        label, value, children = row.get('pickerLabel'), row.get('pickerValue'), row.get('pickerChildLabels')
        require(isinstance(label, str) and isinstance(value, str) and isinstance(children, list) and all(isinstance(x, str) for x in children), 'Missing raw selected-value evidence')
        require(value == native or children.count(native) == 1 or native in label.split(', '), 'Raw picker evidence does not show selected language')
        require(row.get('settingsTabLabel') == ('הגדרות' if code == 'he' else 'Settings'), 'Raw Settings tab label differs')
        frame = row.get('pickerFrame')
        require(isinstance(frame, dict) and set(frame) == {'x', 'y', 'width', 'height'} and all(type(v) in (int, float) and math.isfinite(v) for v in frame.values()) and frame['width'] > 0 and frame['height'] > 0, 'Invalid picker rectangle')
        for suffix in ('.png', '.txt'):
            name = 'host-probe-' + stage + suffix
            expected_names.add(name)
            require(name in rows, 'Missing screenshot/AX: ' + name)
    require(set(rows) == expected_names, 'Missing or unexpected successful probe attachments')
    for name, path in rows.items():
        if name.endswith('.png'):
            require(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), 'Invalid screenshot')
        elif name.endswith('.txt'):
            require(0 < path.stat().st_size <= 512 * 1024 and path.read_text().strip(), 'Missing/oversized AX snapshot')
    return {'report': report, 'attachments': {name: {'sha256': sha(path), 'bytes': path.stat().st_size} for name, path in rows.items()}}


MARKER = 'P2_STARTUP_ACKNOWLEDGEMENT_V1:'


def startup_events(output, identity, strict_json):
    events = []
    for line in output.splitlines():
        if MARKER not in line:
            continue
        raw = line.split(MARKER, 1)[1]
        require(len(raw.encode('utf-8')) <= 2 * 1024 * 1024, 'Oversized startup receipt')
        event = strict_json(raw)
        require(isinstance(event, dict) and set(event) == {'identity', 'phase', 'navigation', 'serviceFailure'}, 'Invalid startup receipt schema')
        require(event['identity'] == identity, 'Startup receipt identity changed')
        require(len(events) < 2 and event['phase'] == ['identified', 'acknowledged'][len(events)], 'Duplicate or reordered startup receipt')
        fault = service_failure(event, complete=event['phase'] == 'acknowledged')
        require(fault is not None, 'Missing durable unresolved service fault')
        if event['phase'] == 'identified':
            require(all(fault[key] is False for key in ('preTapInventoryVerified', 'hittableBeforeTap', 'acknowledgementAttempted', 'acknowledgementReturned', 'alertAbsentAfterAcknowledgement')), 'Identified receipt occurs after action')
            actions = event['navigation']
            require(isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict), 'Invalid identified action sequence')
            first = actions[0]
            actual = labels(first.get('actualStaticTextLabels'), NOTICE_TITLE, NOTICE_BODY)
            require(first == {'action': 'known-notification-later', 'title': NOTICE_TITLE, 'body': NOTICE_BODY,
                    'actualStaticTextLabels': actual, 'buttons': ['Allow Notifications', 'Later'], 'tapped': 'Later',
                    'hittableBeforeTap': True, 'tapAttempted': True, 'tapReturned': True,
                    'originalAlertAbsent': True, 'successor': 'exact-service-failure'} and
                    all(first[k] is True for k in ('hittableBeforeTap', 'tapAttempted', 'tapReturned', 'originalAlertAbsent')), 'Invalid identified predecessor')
        else:
            verify_navigation(event)
            require(event['navigation'][0] == events[0]['navigation'][0], 'Startup predecessor changed')
            require(all(event['serviceFailure'][key] == events[0]['serviceFailure'][key] for key in
                        ('diagnosticCode', 'title', 'body', 'actualStaticTextLabels', 'buttons', 'presentationOnly', 'resolved', 'backendReadinessProven')), 'Service fault changed between receipts')
        events.append(event)
    return events


def verify_startup_events(events, report):
    verify_navigation(report)
    if 'serviceFailure' not in report:
        require(events == [], 'Unexpected service receipts without final fault')
    else:
        require(len(events) == 2, 'Missing durable pre/post OK receipts')
        require(events[-1]['identity'] == report['identity'] and events[-1]['navigation'] == report['navigation'] and
                events[-1]['serviceFailure'] == report['serviceFailure'], 'Durable startup receipt differs from final report')
    return {'events': len(events), 'serviceFailureUnresolved': 'serviceFailure' in report,
            'backendRecoveryProven': False}
