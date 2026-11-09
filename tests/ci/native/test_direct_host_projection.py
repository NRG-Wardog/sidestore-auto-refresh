import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy
import json
from tests.ci.native import test_preservation_localization_projection as historical
from ci.qualification.native import russian_setup_projection as russian
from ci.qualification.native import refresh_truth_setup_projection as truth
from ci.qualification.native import refresh_observation_setup_projection as observation
import hashlib
import unittest
from unittest.mock import patch
from ci.qualification.native import direct_host_projection as P
from ci.qualification.native import preservation_localization_projection as L

class DirectHostProjectionTests(unittest.TestCase):
    def test_preservation_copy_binding_accepts_only_exact_successor(self):
        value={'input_sha256':dict(L.PINS)}
        value['input_sha256'][P.PATH]=P.specification()['after_sha256']
        before=copy.deepcopy(value);L.bind(value);self.assertEqual(value,before)
        value['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):L.bind(value)

    def test_composed_russian_truth_observation_preservation_chain(self):
        helper=historical.LocalizationSuccessor();helper.setUp();self.addCleanup(helper.doCleanups)
        value=json.loads((historical.HERE/'setup-state-recovery-contract.json').read_text())
        for module in (russian,truth,observation):
            extension=module.extension()[0]
            value['input_sha256'].update(extension['candidate_inputs'])
        value['input_sha256'].update(L.PINS)
        actual=P.specification()['after_sha256']
        value['input_sha256'][P.PATH]=actual
        before=copy.deepcopy(value)
        composed=L.add(observation.add(truth.add(russian.add(value))))
        self.assertEqual(value,before)
        self.assertEqual(L.variant(composed),L.CURRENT)
        self.assertEqual(composed['input_sha256'][P.PATH],actual)
        self.assertEqual(len(composed['case_names']['setup-localization']),2079)
        tampered=copy.deepcopy(composed)
        tampered['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):L.variant(tampered)
        # Both the pre-preservation observation validator and final validator
        # must accept only the exact successor, rather than masking the failure.
        pre=observation.add(truth.add(russian.add(value)))
        self.assertEqual(observation.variant(pre),observation.CURRENT)
        pre['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):observation.variant(pre)

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
