import copy,unittest
import legacy_replacement_host_projection as p
class ProjectionTests(unittest.TestCase):
 def setUp(self):
  self.value={'input_sha256':{p.PATH:p.SHA,p.SERVICE:p.SERVICE_SHA},'case_names':['unchanged'],'delivered_baseline':{'pin':'frozen'},'additive_suite_inputs':{'host-refresh-bundle':{'production':['prior.swift'],'fixture':['prior.py']}}}
 def test_only_exact_source_dependency_added(self):
  old=copy.deepcopy(self.value);result=p.add(self.value)
  self.assertEqual(old,self.value);self.assertEqual(result['case_names'],old['case_names']);self.assertEqual(result['delivered_baseline'],old['delivered_baseline'])
  self.assertEqual(result['additive_suite_inputs']['host-refresh-bundle'],p.inputs(result,old['additive_suite_inputs']['host-refresh-bundle']))
  self.assertEqual(result['additive_suite_inputs']['host-refresh-bundle']['production'],['prior.swift',p.SERVICE])
 def test_old_variant_is_identical(self):
  original=self.value['additive_suite_inputs']['host-refresh-bundle']
  self.assertEqual(p.inputs(self.value,original),original)
 def test_missing_modified_duplicate_dependency_rejected(self):
  for path in (p.PATH,p.SERVICE):
   changed=copy.deepcopy(self.value);changed['input_sha256'][path]='0'*64
   with self.assertRaises(ValueError):p.add(changed)
   changed['input_sha256'].pop(path)
   with self.assertRaises(ValueError):p.add(changed)
  value=p.add(self.value)
  with self.assertRaises(ValueError):p.add(value)
  with self.assertRaises(ValueError):p.inputs(value,value['additive_suite_inputs']['host-refresh-bundle'])
if __name__=='__main__':unittest.main()
