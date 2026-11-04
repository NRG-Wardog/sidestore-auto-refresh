import copy
import hashlib
import unittest
from unittest.mock import patch
import direct_host_projection as P
import preservation_localization_projection as L

class DirectHostProjectionTests(unittest.TestCase):
    def test_preservation_copy_binding_accepts_only_exact_successor(self):
        value={'input_sha256':dict(L.PINS)}
        value['input_sha256'][P.PATH]=P.specification()['after_sha256']
        before=copy.deepcopy(value);L.bind(value);self.assertEqual(value,before)
        value['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):L.bind(value)

    def test_inverse_proves_untouched_remainder(self):
        old=b'copy\nold\nkept\n';new=b'copy\nnew\nkept\n'
        spec={'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest(),'edits':[{'after_lines':[1,2],'before':'old\n','after':'new\n'}]}
        with patch.object(P,'specification',return_value=spec):
            self.assertTrue(P.validate_bytes(new));self.assertFalse(P.validate_bytes(old))
            changed=new.replace(b'copy',b'corrupted');spec['after_sha256']=hashlib.sha256(changed).hexdigest()
            with self.assertRaises(ValueError):P.validate_bytes(changed)

    def test_frozen_copy_source_remains_baseline(self):
        self.assertEqual(P.specification()['before_sha256'],L.PINS[P.PATH])

if __name__=='__main__':unittest.main(verbosity=2)
