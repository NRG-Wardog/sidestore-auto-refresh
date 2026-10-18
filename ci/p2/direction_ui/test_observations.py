import copy,unittest
from observations import STAGES,PREFERENCE_LABELS,verify_rows
class ObservationTests(unittest.TestCase):
 def fixture(self):
  rows=[];prefs=[]
  for i,(stage,code,native,title,pstage) in enumerate(STAGES):
   rows.append(dict(stage=stage,language=code,selectedNativeName=native,selectedValueVisible=True,settingsTabVisible=True,applicationForeground=True,pickerLabel='Language, '+native,pickerValue=native,pickerChildLabels=[],settingsTabLabel=title,pickerFrame=dict(x=0,y=1,width=100,height=50)))
   prefs.append(dict(stage=pstage,label=PREFERENCE_LABELS[code],before='0'if i==0 else'1',after='1',seededThroughUI=i==0))
  return dict(identity={'commit':'fixture'},status='observed',launchCount=1,observations=rows,preferenceReceipts=prefs)
 def test_all_four_languages(self):
  r=self.fixture();self.assertEqual(verify_rows(r,r['identity'])['observations'],5)
 def test_missing_misordered_or_reset(self):
  r=self.fixture()
  for change in [lambda r:r['observations'].pop(),lambda r:r['observations'].reverse(),lambda r:r['preferenceReceipts'][2].update(after='0'),lambda r:r['preferenceReceipts'][0].update(before='1'),lambda r:r.update(launchCount=2),lambda r:r['observations'][3].update(settingsTabLabel='Settings')]:
   bad=copy.deepcopy(r);change(bad)
   with self.assertRaises(ValueError):verify_rows(bad,r['identity'])
 def test_no_fake_native_value(self):
  r=self.fixture();r['observations'][1].update(pickerValue='',pickerLabel='Language',pickerChildLabels=[])
  with self.assertRaises(ValueError):verify_rows(r,r['identity'])
if __name__=='__main__':unittest.main()
