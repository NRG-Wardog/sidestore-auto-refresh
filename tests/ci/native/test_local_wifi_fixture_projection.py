import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy,unittest
from ci.qualification.native import local_wifi_fixture_projection as P
class WiFiDependencyTests(unittest.TestCase):
 def setUp(self):
  self.old={'input_sha256':{P.PATH:P.SHA},'additive_suite_inputs':{'vpn-refresh-recovery':{'transport_fixture':['a','b','c','d','e'],'production':['unchanged']}}}
  self.new=P.add(self.old)
 def test_exact_additive_dependency_preserves_original(self):
  self.assertEqual(self.old['additive_suite_inputs']['vpn-refresh-recovery']['transport_fixture'],['a','b','c','d','e'])
  self.assertEqual(self.new['additive_suite_inputs']['vpn-refresh-recovery']['transport_fixture'],['a','b','c','d',P.PATH,'e'])
  self.assertEqual(P.inputs(self.new,self.old['additive_suite_inputs']['vpn-refresh-recovery']),self.new['additive_suite_inputs']['vpn-refresh-recovery'])
  with self.assertRaises(ValueError):P.add(self.new)
  with self.assertRaises(ValueError):P.inputs(self.new,self.new['additive_suite_inputs']['vpn-refresh-recovery'])
 def test_unknown_role_path_hash_or_unbound_source_rejects(self):
  for key in ['role','path','sha256']:
   value=copy.deepcopy(self.new);value[P.KEY][key]='unknown'
   with self.assertRaises(ValueError):P.binding(value)
  value=copy.deepcopy(self.new);value['input_sha256'][P.PATH]='0'*64
  with self.assertRaises(ValueError):P.binding(value)
if __name__=='__main__':unittest.main(verbosity=2)
