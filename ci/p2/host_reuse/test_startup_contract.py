"""Exact one-OK exception cannot clear backend failure or relax Settings proof."""
import copy
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import startup_contract as SC
import test_reuse as inherited
RUN = inherited.RUN
HERE = Path(__file__).resolve().parent


def sample(count=2):
    report = {'identity': {'sourceCommit': RUN.HP.SOURCE, 'sourceTree': RUN.HP.TREE,
                          'builderCommit': 'b' * 40, 'bundleIdentifier': RUN.HP.BUNDLE_ID,
                          'executableSHA256': 'a' * 64}, 'navigation': []}
    if count:
        report['navigation'].append({'action': 'known-notification-later', 'title': SC.NOTICE_TITLE, 'body': SC.NOTICE_BODY,
            'actualStaticTextLabels': [SC.NOTICE_TITLE, SC.NOTICE_BODY], 'buttons': ['Allow Notifications', 'Later'],
            'tapped': 'Later', 'hittableBeforeTap': True, 'tapAttempted': True, 'tapReturned': True,
            'originalAlertAbsent': True, 'successor': 'none' if count == 1 else 'exact-service-failure'})
    if count == 2:
        report['serviceFailure'] = {'diagnosticCode': SC.ERROR_CODE, 'title': SC.ERROR_TITLE, 'body': SC.ERROR_BODY,
            'actualStaticTextLabels': [SC.ERROR_TITLE, SC.ERROR_BODY], 'buttons': ['Copy Diagnostics', 'OK'],
            'presentationOnly': True, 'resolved': False, 'backendReadinessProven': False,
            'hittableBeforeTap': True, 'preTapInventoryVerified': True, 'acknowledgementAttempted': True, 'acknowledgementReturned': True,
            'alertAbsentAfterAcknowledgement': True}
        report['navigation'].append({'action': 'known-service-error-ok', 'title': SC.ERROR_TITLE, 'body': SC.ERROR_BODY,
            'actualStaticTextLabels': [SC.ERROR_TITLE, SC.ERROR_BODY], 'buttons': ['Copy Diagnostics', 'OK'],
            'tapped': 'OK', 'hittableBeforeTap': True, 'preTapInventoryVerified': True, 'tapAttempted': True, 'tapReturned': True,
            'allAlertsAbsentAfterTap': True})
    return report


def events(report):
    first = copy.deepcopy(report); first['phase'] = 'identified'; first['navigation'] = first['navigation'][:1]
    for key in ('preTapInventoryVerified', 'hittableBeforeTap', 'acknowledgementAttempted', 'acknowledgementReturned', 'alertAbsentAfterAcknowledgement'):
        first['serviceFailure'][key] = False
    last = copy.deepcopy(report); last['phase'] = 'acknowledged'
    return [first, last]


def encoded(rows):
    return '\n'.join(SC.MARKER + json.dumps(row) for row in rows)


class StartupContractTests(unittest.TestCase):
    def test_three_exact_routes(self):
        for count in (0, 1, 2):
            report = sample(count); names = SC.verify_navigation(report)
            self.assertEqual(len(names), 0 if count == 0 else 4)
            if count == 2:
                self.assertFalse(SC.service_failure(report, True)['resolved'])

    def test_wrong_order_repeated_or_unknown_actions(self):
        for mutate in (lambda r:r['navigation'].reverse(), lambda r:r['navigation'].append(copy.deepcopy(r['navigation'][1])),
                       lambda r:r['navigation'][1].update(action='retry'), lambda r:r['navigation'][1].update(tapped='Copy Diagnostics'),
                       lambda r:r['navigation'][0].update(successor='none'), lambda r:r['navigation'][0].update(successor='unclassified')):
            report=sample(); mutate(report)
            with self.subTest(report=report), self.assertRaises(ValueError): SC.verify_navigation(report)

    def test_each_missing_changed_added_field_rejected(self):
        for target in ('first', 'second', 'fault'):
            template = sample(); row = template['serviceFailure'] if target == 'fault' else template['navigation'][target == 'second']
            for key in list(row):
                for mode in ('missing', 'wrong'):
                    report=sample(); value=report['serviceFailure'] if target=='fault' else report['navigation'][target=='second']
                    if mode=='missing': del value[key]
                    else: value[key] = None
                    with self.subTest(target=target,key=key,mode=mode), self.assertRaises(ValueError): SC.verify_navigation(report)
            report=sample();value=report['serviceFailure'] if target=='fault' else report['navigation'][target=='second'];value['extra']='bad'
            with self.assertRaises(ValueError): SC.verify_navigation(report)

    def test_integer_bool_substitution_rejected(self):
        for target in ('first','second','fault'):
            template=sample(); row=template['serviceFailure'] if target=='fault' else template['navigation'][target=='second']
            for key,value in row.items():
                if type(value) is not bool:continue
                report=sample(); changed=report['serviceFailure'] if target=='fault' else report['navigation'][target=='second'];changed[key]=int(value)
                with self.subTest(target=target,key=key),self.assertRaises(ValueError):SC.verify_navigation(report)

    def test_exact_error_code_body_buttons_required(self):
        for key,value in [('diagnosticCode','SS-LAUNCH-C11-L11'),('body',SC.ERROR_BODY+' '),('title','SideStore '),
                          ('buttons',['Copy Diagnostics','OK','Retry']),('resolved',True),('backendReadinessProven',True)]:
            report=sample();report['serviceFailure'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):SC.verify_navigation(report)

    def test_missing_or_orphaned_fault_rejected(self):
        report=sample();del report['serviceFailure']
        with self.assertRaises(ValueError):SC.verify_navigation(report)
        for count in (0,1):
            report=sample(count);report['serviceFailure']=sample()['serviceFailure']
            with self.assertRaises(ValueError):SC.verify_navigation(report)

    def test_reappeared_alert_and_incomplete_taps_rejected(self):
        for target,key in [('first','originalAlertAbsent'),('second','allAlertsAbsentAfterTap'),('second','tapReturned'),
                           ('second','tapAttempted'),('second','hittableBeforeTap'),('fault','alertAbsentAfterAcknowledgement')]:
            report=sample();row=report['serviceFailure'] if target=='fault' else report['navigation'][target=='second'];row[key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):SC.verify_navigation(report)

    def test_partial_fault_retained_but_never_complete(self):
        report=events(sample())[0]
        self.assertFalse(SC.service_failure(report)['resolved'])
        with self.assertRaises(ValueError):SC.service_failure(report,complete=True)
        report['serviceFailure']['acknowledgementReturned']=True
        with self.assertRaises(ValueError):SC.service_failure(report)

    def test_bounded_and_unique_labels(self):
        for labels in ([SC.ERROR_TITLE], [SC.ERROR_TITLE,SC.ERROR_BODY,SC.ERROR_BODY], [SC.ERROR_TITLE,SC.ERROR_BODY]+['x']*63,
                       [SC.ERROR_TITLE,SC.ERROR_BODY,'x'*16385], [SC.ERROR_TITLE,SC.ERROR_BODY,1], [SC.ERROR_TITLE,SC.ERROR_BODY,'unexpected'], [SC.ERROR_TITLE,SC.ERROR_BODY,'']):
            report=sample();report['serviceFailure']['actualStaticTextLabels']=labels
            with self.subTest(labels=str(labels)[:100]),self.assertRaises(ValueError):SC.verify_navigation(report)

    def test_exact_two_durable_events_match_final_report(self):
        report=sample();actual=SC.startup_events(encoded(events(report)), report['identity'],RUN.CAP.strict_json)
        self.assertEqual(SC.verify_startup_events(actual,report),{'events':2,'serviceFailureUnresolved':True,'backendRecoveryProven':False})
        for count in (0,1):self.assertEqual(SC.verify_startup_events([],sample(count))['events'],0)

    def test_event_missing_duplicate_reordered_foreign_identity(self):
        report=sample(); good=events(report)
        for rows in ([good[1]],good+good[:1],list(reversed(good)),[good[0],good[0]]):
            with self.assertRaises(ValueError):SC.startup_events(encoded(rows),report['identity'],RUN.CAP.strict_json)
        changed=copy.deepcopy(good);changed[0]['identity']['builderCommit']='c'*40
        with self.assertRaises(ValueError):SC.startup_events(encoded(changed),report['identity'],RUN.CAP.strict_json)
        for rows in ([],good[:1]):
            with self.assertRaises(ValueError):SC.verify_startup_events(rows,report)

    def test_durable_event_cannot_drop_fault_or_change_labels(self):
        report=sample()
        for modify in (lambda e:e[0].pop('serviceFailure'),lambda e:e[0].update(extra=1),
                       lambda e:e[0]['serviceFailure'].update(acknowledgementAttempted=True),
                       lambda e:e[1]['serviceFailure']['actualStaticTextLabels'].append('unbound'),
                       lambda e:e[1]['navigation'][0]['actualStaticTextLabels'].append('unbound')):
            rows=events(report);modify(rows)
            with self.assertRaises(ValueError):SC.startup_events(encoded(rows),report['identity'],RUN.CAP.strict_json)

    def test_raw_event_strict_json_and_size(self):
        for raw in ('{}','{"x":1,"x":2}','{"x":NaN}','x'*2097153):
            with self.assertRaises((ValueError,KeyError)):SC.startup_events(SC.MARKER+raw,sample()['identity'],RUN.CAP.strict_json)

    def test_final_receipt_mismatch_rejected(self):
        report=sample(); rows=events(report);report['identity']['builderCommit']='d'*40
        with self.assertRaises(ValueError):SC.verify_startup_events(rows,report)
        with self.assertRaises(ValueError):SC.verify_startup_events(rows,sample(0))

    def test_unchanged_observation_verifier_except_startup_block(self):
        old=inspect.getsource(RUN.HP.verify_observations)
        a=old.index("    navigation = report.get('navigation')");b=old.index('    for row, code, native, stage in zip(')
        expected=old[:a]+'    expected_names |= verify_navigation(report)\n'+old[b:]
        expected=expected.replace('def verify_observations(directory, identity, builder_commit):','def verify_observations(directory, identity, builder_commit, host):\n    attachment_map, strict_json, sha = host.attachment_map, host.strict_json, host.sha\n    SOURCE, TREE, BUNDLE_ID = host.SOURCE, host.TREE, host.BUNDLE_ID')
        self.assertEqual(inspect.getsource(SC.verify_observations),expected)
        self.assertIn("len(rows) <= 16",inspect.getsource(RUN.HP.attachment_map))

    def test_swift_single_ok_exact_identity_and_durable_order(self):
        source=(HERE/'HostSettingsProbeTests.swift.in').read_text()
        action=source.split('    private func acknowledgeExactServiceFailure()',1)[1].split('    private func picker()',1)[0]
        self.assertEqual(action.count('ok.tap()'),1)
        self.assertLess(action.index('serviceFailure = ['),action.index('publishStartupReceipt("identified")'))
        self.assertLess(action.index('publishStartupReceipt("identified")'),action.index('ok.tap()'))
        self.assertLess(action.index('serviceFailure?["alertAbsentAfterAcknowledgement"] = allAbsent'),action.index('publishStartupReceipt("acknowledged")'))
        self.assertLess(action.index('publishStartupReceipt("acknowledged")'),action.index('noUnexpectedAlerts()'))
        self.assertIn('"resolved": false',action);self.assertIn('"backendReadinessProven": false',action)
        self.assertIn('XCTAssertNil(serviceFailure',action)
        self.assertIn('.containing(.staticText, identifier: body)',action)
        self.assertNotIn('app.alerts.firstMatch',action)
        self.assertEqual(action.count('XCTAssertEqual(exactAlerts.count, 1'),2)
        self.assertLess(action.index('serviceFailure?["preTapInventoryVerified"] = true'),action.index('ok.tap()'))
        self.assertIn('XCTAssertEqual(app.alerts.count, 1)',action)
        self.assertIn('"Copy Diagnostics", "OK"',action)
        for forbidden in ('.activate(','.launch(','.terminate(','.press(','.typeText(','.buttons["Copy Diagnostics"].tap', 'status = "observed"'):
            self.assertNotIn(forbidden,action)
        self.assertEqual(source.count('app.launch()'),1)
        later=source.split('    private func cancelKnownNotificationExplanationIfPresent()',1)[1].split('    private func publishStartupReceipt',1)[0]
        self.assertIn('app.alerts.matching(NSPredicate(format: "label == %@", title))',later)
        self.assertIn('original.count == 0',later)
        self.assertIn('XCTAssertEqual(remaining, 1',later)
        self.assertNotIn('capture("service-error-after")',source)
        teardown=source.split('override func tearDownWithError()',1)[1].split('private func noUnexpectedAlerts()',1)[0]
        self.assertLess(teardown.index('report["serviceFailure"]'),teardown.index('JSONSerialization'))

    def test_swift_unexpected_alert_guard_unchanged(self):
        source=(HERE/'HostSettingsProbeTests.swift.in').read_text()
        body=source.split('    private func noUnexpectedAlerts()',1)[1].split('    private func cancelKnownNotificationExplanationIfPresent()',1)[0]
        self.assertEqual(body, ''' {
        XCTAssertEqual(app.alerts.count, 0, "Unexpected app/entitlement/authentication prompt blocks probe")
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        XCTAssertEqual(springboard.alerts.count, 0, "OS permission/system prompt blocks probe")
    }

''')

    def test_full_observations_keep_identity_language_and_attachment_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();report=sample();report.update(status='observed',launchCount=1,observations=[])
            names={'host-probe-observations.json','host-probe-launch.png','host-probe-launch.txt'}|SC.verify_navigation(report)
            for stage,code,native,title in [('en-before','en','English','Settings'),('he-selected','he','עברית','הגדרות'),('en-returned','en','English','Settings')]:
                report['observations'].append(dict(stage=stage,language=code,selectedNativeName=native,selectedValueVisible=True,settingsTabVisible=True,applicationForeground=True,pickerLabel='Language',pickerValue=native,pickerChildLabels=[],settingsTabLabel=title,pickerFrame=dict(x=0,y=0,width=100,height=40)))
                names|={'host-probe-'+stage+'.png','host-probe-'+stage+'.txt'}
            for name in names:
                (root/name).write_bytes(b'\x89PNG\r\n\x1a\n' if name.endswith('.png') else b'AX')
            rows={name:root/name for name in names}
            def verify():
                (root/'host-probe-observations.json').write_text(json.dumps(report))
                with patch.object(RUN.HP,'attachment_map',return_value=rows):return SC.verify_observations(root,{'executableSHA256':'a'*64},'b'*40,RUN.HP)
            self.assertEqual(len(verify()['attachments']),13)
            good=copy.deepcopy(report)
            for mutate in (lambda r:r.update(launchCount=2),lambda r:r['identity'].update(sourceCommit='c'*40),
                           lambda r:r['observations'][1].update(settingsTabLabel='Settings'),lambda r:r['observations'][0].update(pickerValue='bad'),
                           lambda r:r['observations'][2]['pickerFrame'].update(width=0),lambda r:r.pop('serviceFailure')):
                report=copy.deepcopy(good);mutate(report)
                with self.assertRaises(ValueError):verify()
            report=copy.deepcopy(good);rows.pop('host-probe-notice-after.png')
            with self.assertRaises(ValueError):verify()

    def test_runtime_fault_summary_survives_later_failure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(RUN,'SC',SC):
            root=Path(tmp).resolve();(root/'e').mkdir();probe=RUN.ReuseProbe(root/'a',root/'w',root/'e','seal')
            probe.report['errors']=['Native test did not pass'];probe.retain_service_failure(sample())
            self.assertTrue(probe.report['serviceFailureUnresolved']);self.assertFalse(probe.report['backendReadinessProven'])
            self.assertFalse(probe.report['fullAppAcceptance']);self.assertEqual(probe.report['status'],'BLOCKED')
            self.assertEqual(probe.report['errors'],['Native test did not pass'])
            self.assertEqual(json.loads((root/'e/unresolved-service-failure.json').read_text()),sample()['serviceFailure'])

    def test_runtime_stream_survives_missing_native_export(self):
        report=sample()
        with tempfile.TemporaryDirectory() as tmp,patch.object(RUN,'SC',SC):
            root=Path(tmp).resolve();(root/'e').mkdir();probe=RUN.ReuseProbe(root/'a',root/'w',root/'e','seal')
            probe.identity={'bundleIdentifier':RUN.HP.BUNDLE_ID,'executableSHA256':'a'*64};probe.report['probeBuilderCommit']='b'*40
            probe.collect_startup_events(encoded(events(report)[:1]))
            self.assertTrue(probe.report['serviceFailureUnresolved']);self.assertEqual(probe.report['status'],'BLOCKED')
            self.assertEqual(len(json.loads((root/'e/startup-action-events.json').read_text())),1)
            with self.assertRaises(ValueError):SC.verify_startup_events(probe.startup_receipts,report)

    def test_runtime_startup_collection_precedes_native_assertion(self):
        body=inspect.getsource(RUN.ReuseProbe.execute)
        self.assertLess(body.index('self.collect_startup_events('),body.index('self.harvest_results()'))
        self.assertLess(body.index('self.harvest_results()'),body.index("'Native test did not pass'"))
        self.assertLess(body.index('SC.verify_startup_events('),body.index("'postnavigation'"))
        self.assertIn("'native-settings-test', 240",body)


if __name__=='__main__':unittest.main()
