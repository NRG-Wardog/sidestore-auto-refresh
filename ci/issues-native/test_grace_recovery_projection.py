import copy, unittest
from unittest.mock import patch
import grace_recovery_projection as P
class GraceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.old={'input_sha256':dict(P.FILES),'case_names':{'vpn-refresh-recovery':P.historical()}}
        self.new=P.add(self.old)
    def test_exact_source_bound_addition_preserves_historical(self):
        self.assertEqual(P.count(self.old),89)
        self.assertEqual(P.count(self.new),105)
        self.assertEqual(self.new['case_names']['vpn-refresh-recovery'][:89],P.historical())
        self.assertEqual(len(set(P.expected_names())),105)
        self.assertNotIn(P.KEY,self.old)
        with self.assertRaises(ValueError):P.add(self.new)
    def test_previous97_exact_order_is_immutable(self):
        changed=list(P.ADDED);changed[0],changed[1]=changed[1],changed[0]
        with patch.object(P,'ADDED',changed):
            with self.assertRaisesRegex(ValueError,'Previous 97'):P.expected_names()
    def test_unknown_source_names_missing_reordered_or_duplicates_reject(self):
        for path in P.FILES:
            changed=copy.deepcopy(self.new);changed['input_sha256'][path]='0'*64
            with self.assertRaises(ValueError):P.count(changed)
        for names in [P.expected_names()[:-1],list(reversed(P.expected_names())),P.expected_names()+[P.ADDED[0]],P.historical()]:
            changed=copy.deepcopy(self.new);changed['case_names']['vpn-refresh-recovery']=names
            with self.assertRaises(ValueError):P.count(changed)
        changed=copy.deepcopy(self.new);changed[P.KEY]['additional_cases']=['invented']
        with self.assertRaises(ValueError):P.count(changed)
if __name__=='__main__':unittest.main(verbosity=2)
