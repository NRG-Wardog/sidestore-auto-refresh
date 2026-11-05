import copy
import hashlib
import unittest
from unittest.mock import patch
import direct_service_projection as P
import preservation_host_projection as H
import test_preservation_successor_projection as historical
from verify_setup_state_recovery import host_bundle_variant

class DirectServiceProjectionTests(unittest.TestCase):
    def test_exact_successor_keeps_all_preservation_cases_and_real_hash(self):
        helper=historical.PreservationSuccessors();helper.setUp();value=helper.host_base()
        value['input_sha256'][P.PATH]=P.specification()['after_sha256']
        before=copy.deepcopy(value);result=H.add(value)
        self.assertEqual(value,before)
        self.assertEqual(result['input_sha256'][P.PATH],before['input_sha256'][P.PATH])
        self.assertEqual(H.normalized(result)['case_names'],before['case_names'])
        self.assertEqual(host_bundle_variant(result)[1:],(35,41))
        self.assertTrue(H.binding(result))

    def test_unknown_successor_cannot_reuse_preservation_proof(self):
        helper=historical.PreservationSuccessors();helper.setUp();value=helper.host_base()
        value['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):H.add(value)

    def test_inverse_checks_exact_source_and_preserved_remainder(self):
        prefix=b'struct V3LegacyReplacementBinding: Equatable {\n}\n'
        before=prefix+b'unchanged\nold\nretained\n';after=prefix+b'unchanged\nnew\nretained\n'
        spec={'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),
              'edits':[{'after_lines':[3,4],'before':'old\n','after':'new\n'}]}
        with patch.object(P,'specification',return_value=spec):
            self.assertTrue(P.validate_bytes(after));self.assertFalse(P.validate_bytes(before))
            with self.assertRaises(ValueError):P.validate_bytes(after+b'change')
            changed=after.replace(b'retained',b'mutated')
            spec['after_sha256']=hashlib.sha256(changed).hexdigest()
            with self.assertRaises(ValueError):P.validate_bytes(changed)

    def test_manifest_base_matches_frozen_preservation_source(self):
        self.assertEqual(P.specification()['before_sha256'],H.PINS[P.PATH])
        self.assertGreater(len(P.specification()['edits']),0)

if __name__=='__main__':unittest.main(verbosity=2)
