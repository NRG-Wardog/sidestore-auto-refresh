import copy,unittest
import early_readiness_projection as P
class EarlyDependencyTests(unittest.TestCase):
 def setUp(self):
  self.old={'input_sha256':{P.API:'a'*64,P.GENERATOR:P.GENERATOR_SHA},'suite_inputs':{'early-refresh-boundaries':{'production':['original'], 'support':['one'], 'fixture':['original']}},'additive_suite_inputs':{'profile-preconditions':{'support':['one',P.GENERATOR,'doubles']}}}
 def test_exact_additive_dependency_and_original_retained(self):
  before=copy.deepcopy(self.old);new=P.add(self.old);self.assertEqual(self.old,before)
  self.assertEqual(new['suite_inputs']['early-refresh-boundaries']['support'],['one',P.API])
  self.assertEqual(new['additive_suite_inputs']['profile-preconditions']['support'],['one',P.API,P.GENERATOR,'doubles'])
  self.assertEqual(new['suite_inputs']['early-refresh-boundaries']['production'],['original'])
 def test_unbound_unknown_or_duplicate_dependency_rejected(self):
  new=P.add(self.old)
  with self.assertRaises(ValueError):P.binding(self.old)
  for key,value in [('path','unknown'),('source_sha256','b'*64),('generator_sha256','b'*64)]:
   with self.assertRaises(ValueError):P.binding({**new,P.KEY:{**new[P.KEY],key:value}})
  with self.assertRaises(ValueError):P.add(new)
  with self.assertRaises(ValueError):P.inputs(new,new['suite_inputs']['early-refresh-boundaries'])
 def test_historical_dependency_closure_unchanged(self):
  old=copy.deepcopy(self.old);old['input_sha256'][P.GENERATOR]='c'*64
  self.assertEqual(P.inputs(old,old['suite_inputs']['early-refresh-boundaries']),old['suite_inputs']['early-refresh-boundaries'])
if __name__=='__main__':unittest.main(verbosity=2)
