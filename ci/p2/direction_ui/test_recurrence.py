import copy,json,tempfile,unittest
from pathlib import Path
import observations as O
class RecurrenceTests(unittest.TestCase):
 def test_absent_and_incomplete(self):
  self.assertEqual(O.verify_recurrence({'settingsRecurrence':[]},{}),set())
  for value in [None,[{}],[{},{},{}]]:
   with self.assertRaises(ValueError):O.verify_recurrence({'settingsRecurrence':value},{})
 def test_exact_durable_recurrence(self):
  O.strict_json=json.loads
  fault={'diagnosticCode':'SS-LAUNCH-C11-L15','title':'SideStore','body':'exact retained body','alertAbsentAfterAcknowledgement':True}
  events=[]
  for phase in ['identified','acknowledged']:
   events.append(dict(phase=phase,stage='settings-entry-service-recurrence',diagnosticCode=fault['diagnosticCode'],title=fault['title'],body=fault['body'],actualStaticTextLabels=sorted([fault['title'],fault['body']]),buttons=['Copy Diagnostics','OK'],presentationOnly=True,resolved=False,backendReadinessProven=False,**{k:phase=='acknowledged'for k in ['preTapInventoryVerified','tapAttempted','tapReturned','allAlertsAbsentAfterTap']}))
  report={'identity':{'source':'test'},'serviceFailure':fault,'settingsRecurrence':events}
  with tempfile.TemporaryDirectory()as d:
   rows={}
   for e in events:
    n='host-probe-settings-recurrence-'+e['phase']+'.json';p=Path(d)/n;p.write_text(json.dumps({'identity':report['identity'],'receipt':e}));rows[n]=p
   self.assertEqual(len(O.verify_recurrence(report,rows)),6)
   for change in [lambda r:r['settingsRecurrence'][1].update(body='different'),lambda r:r['settingsRecurrence'][1].update(tapReturned=False),lambda r:r['settingsRecurrence'][1].update(resolved=True),lambda r:r['serviceFailure'].update(alertAbsentAfterAcknowledgement=False),lambda r:r['settingsRecurrence'][0].update(tapAttempted=True),lambda r:r['settingsRecurrence'][1].update(tapReturned=1),lambda r:r.update(identity={'source':'other'})]:
    bad=copy.deepcopy(report);change(bad)
    with self.assertRaises(ValueError):O.verify_recurrence(bad,rows)
 def test_native_scope(self):
  text=Path(__file__).with_name('HostSettingsProbeTests.swift.in').read_text()
  self.assertEqual(text.count('acknowledgeSettingsRecurrenceIfPresent()'),2)
  self.assertIn('settings.tap()\n        acknowledgeSettingsRecurrenceIfPresent()\n        preference(',text)
  method=text.split('private func acknowledgeSettingsRecurrenceIfPresent()')[1].split('private func reveal(')[0]
  self.assertEqual(method.count('ok.tap()'),1)
  self.assertNotIn('while ',method);self.assertNotIn('for ',method)
  self.assertIn('observations.isEmpty && preferenceReceipts.isEmpty',method)
if __name__=='__main__':unittest.main()
