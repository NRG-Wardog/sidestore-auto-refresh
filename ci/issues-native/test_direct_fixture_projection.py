import copy
import hashlib
import unittest
from unittest.mock import patch
import direct_fixture_projection as D
import early_progress_fixture_projection as E
import test_early_progress_fixture_projection as historical

class DirectFixtureProjectionTests(unittest.TestCase):
    def test_composed_direct_progress_readiness_preserves_cases_and_sources(self):
        helper=historical.EarlyProgressFixtureTests();helper.setUp()
        current=copy.deepcopy(helper.raw)
        after=D.specifications()[E.GENERATOR]['after_sha256']
        current['input_sha256'][E.GENERATOR]=after
        before=copy.deepcopy(current);bound=E.add(current)
        self.assertEqual(before,current)
        self.assertEqual(bound['input_sha256'][E.GENERATOR],after)
        E.verify_static(helper.value,bound)
        self.assertEqual(bound[E.KEY]['runtime_case_count'],24)
        self.assertEqual(helper.value['check_count'],121)
        for profile,name,key in ((False,'early-refresh-boundaries','suite_inputs'),(True,'profile-preconditions','additive_suite_inputs')):
            self.assertEqual(E.inputs(bound,current[key][name],profile),bound[key][name])
        bound['input_sha256'][E.GENERATOR]='0'*64
        with self.assertRaises(ValueError):E.verify_static(helper.value,bound)

    def test_inverse_proves_unchanged_original_assertions(self):
        old=b'cases\nold\nassertions\n';new=b'cases\nnew\nassertions\n'
        spec={'x':{'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest(),'edits':[{'after_lines':[1,2],'before':'old\n','after':'new\n'}]}}
        with patch.object(D,'specifications',return_value=spec):
            self.assertTrue(D.validate_bytes('x',new));self.assertFalse(D.validate_bytes('x',old))
            changed=new.replace(b'assertions',b'weakened');spec['x']['after_sha256']=hashlib.sha256(changed).hexdigest()
            with self.assertRaises(ValueError):D.validate_bytes('x',changed)

    def test_closed_source_set_and_historical_early_pin(self):
        specs=D.specifications()
        self.assertEqual(set(specs),{E.GENERATOR,'LiveContainer/.ci/comprehensive-diagnostics/generate_refresh_failure_fixture.py'})
        self.assertEqual(specs[E.GENERATOR]['before_sha256'],E.GENERATOR_SHA)

if __name__=='__main__':unittest.main(verbosity=2)
