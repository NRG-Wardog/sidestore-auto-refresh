import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy,unittest
from pathlib import Path
from ci.qualification.native import profile_static_closure_projection as p
class ProfileStaticSuccessorTests(unittest.TestCase):
 def setUp(self):
  self.contract={'input_sha256':dict(p.PINS)}
  self.value={'check_count':32,'checks':list(p.CURRENT_CHECKS),'fixture_inputs_sha256':{k.rsplit('/',1)[1]:v for k,v in p.PINS.items()}}
 def test_exact_preserves_prior_order_and_adds_only_seven(self):
  self.assertEqual(p.validate(self.value,self.contract),32)
  self.assertEqual([n for n in self.value['checks'] if n not in p.ADDITIONAL_CHECKS],p.PRIOR_CHECKS)
  self.assertEqual(len(p.PRIOR_CHECKS),25);self.assertEqual(len(p.ADDITIONAL_CHECKS),7)
 def test_missing_extra_duplicate_reordered_replaced_checks_rejected(self):
  names=p.CURRENT_CHECKS
  for bad in [names[:-1],names+['extra'],names[:-1]+[names[0]],list(reversed(names)),names[:-1]+['unknown']]:
   changed=copy.deepcopy(self.value);changed['checks']=bad;changed['check_count']=len(bad)
   with self.assertRaises(ValueError):p.validate(changed,self.contract)
 def test_count_must_match_current_closure(self):
  for count in [25,31,33,True,'32']:
   changed=copy.deepcopy(self.value);changed['check_count']=count
   with self.assertRaises(ValueError):p.validate(changed,self.contract)
 def test_changed_missing_source_or_receipt_pin_rejected(self):
  for path in p.PINS:
   for missing in (True,False):
    contract=copy.deepcopy(self.contract)
    if missing:contract['input_sha256'].pop(path)
    else:contract['input_sha256'][path]='0'*64
    with self.assertRaises(ValueError):p.validate(self.value,contract)
    value=copy.deepcopy(self.value);name=path.rsplit('/',1)[1]
    if missing:value['fixture_inputs_sha256'].pop(name)
    else:value['fixture_inputs_sha256'][name]='0'*64
    with self.assertRaises(ValueError):p.validate(value,self.contract)
 def test_historical_default_unchanged_and_cannot_claim_current_checks(self):
  old={'check_count':25,'checks':['old'+str(i) for i in range(25)]}
  
  for digest in p.PRIOR_GENERATORS:self.assertEqual(p.validate(old,{'input_sha256':{p.GENERATOR:digest}}),25)
  for digest in [None,'0'*64]:
   with self.assertRaises(ValueError):p.validate(old,{'input_sha256':{p.GENERATOR:digest}})
  with self.assertRaises(ValueError):p.validate(old,self.contract)
 def test_mandatory_workflow_controls(self):
  root=(_CIPath(__file__).resolve().parents[3])
  for name, job in (('focused-diagnostics.yml', 'refresh_truth'), ('native-build.yml', None)):
   text=workflow_job(root/'.github/workflows'/name, job)
   self.assertEqual(text.count('python3 -B harness/tests/ci/native/test_profile_static_closure_projection.py'),1)
if __name__=='__main__':unittest.main()
