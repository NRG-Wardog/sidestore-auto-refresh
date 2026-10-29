import copy,unittest
import refresh_truth_host_bundle_projection as r
class HostTruthProjection(unittest.TestCase):
 def test_exact_three_additional_controls_preserve_input_and_old_count(self):
  original={'input_sha256':{r.PATH:r.SHA},'untouched':['old']};before=copy.deepcopy(original)
  current=r.add(original);self.assertEqual(original,before);self.assertEqual(current['untouched'],['old'])
  self.assertEqual(r.assertions(original,38),38);self.assertEqual(r.assertions(original,34),34);self.assertEqual(r.assertions(current,38),41)
 def test_wrong_source_or_stale_count_fails(self):
  current=r.add({'input_sha256':{r.PATH:r.SHA}})
  with self.assertRaises(ValueError):r.assertions(current,34)
  with self.assertRaises(ValueError):r.add(current)
  for section in ('input_sha256',r.KEY):
   wrong=copy.deepcopy(current)
   if section=='input_sha256':wrong[section][r.PATH]='0'*64
   else:wrong[section]['source_assertion_count']=42
   with self.assertRaises(ValueError):r.assertions(wrong,38)
  with self.assertRaises(ValueError):r.add({'input_sha256':{r.PATH:'0'*64}})
if __name__=='__main__':unittest.main()
