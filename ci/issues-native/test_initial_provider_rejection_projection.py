import copy
from pathlib import Path
import unittest
import initial_provider_rejection_projection as p

class InitialProviderRejectionProjection(unittest.TestCase):
    def base(self):
        return {'input_sha256': dict(p.PINS, **p.WIRE_PINS), 'success_markers': {'auth':'historical marker'},
                'historical_cases': ['a', 'b'], 'delivered_baseline': {'immutable': True}}

    def test_add_is_exact_additive_and_leaves_old_claims_unchanged(self):
        before = self.base()
        original = copy.deepcopy(before)
        result = p.add(before)
        self.assertEqual(before, original)
        self.assertTrue(p.validate(result))
        result.pop(p.TAG)
        self.assertEqual(result, original)
        with self.assertRaises(ValueError):
            p.add(p.add(before))

    def test_missing_modified_unknown_inputs_and_descriptor_rejected(self):
        good = p.add(self.base())
        for path in dict(p.PINS, **p.WIRE_PINS):
            for missing in (True, False):
                bad = copy.deepcopy(good)
                if missing:
                    bad['input_sha256'].pop(path)
                else:
                    bad['input_sha256'][path] = '0' * 64
                with self.assertRaises(ValueError):
                    p.validate(bad)
        for mutate in (lambda x:x.pop(p.TAG),
                       lambda x:x[p.TAG].update(extra=True),
                       lambda x:x['input_sha256'].update({p.ROOT+'/Unknown.swift':'0'*64})):
            bad=copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                p.validate(bad)

    def test_missing_duplicate_unknown_marker_rejected(self):
        good=p.add(self.base())
        p.verify_runtime(good,'historical marker\n'+p.MARKER+'\n')
        for text in ('',p.MARKER+'\n'+p.MARKER,p.MARKER.replace('cases=27','cases=26'),
                     p.MARKER+'\nINITIAL_PROVIDER_REJECTION_AUTH_PASS unknown',
                     p.MARKER.replace('native_provider=false','native_provider=true')):
            with self.assertRaises(ValueError):
                p.verify_runtime(good,text)

    def test_extraction_requires_every_exact_input_and_source_only_limits(self):
        good=p.add(self.base())
        evidence={'inputs':{path.removeprefix(p.SIGN):sha for path,sha in p.PINS.items()},
                  'network':False,'native_provider':False,'swift_executed':False}
        p.verify_extraction(good,evidence)
        for path in evidence['inputs']:
            bad=copy.deepcopy(evidence)
            bad['inputs'].pop(path)
            with self.assertRaises(ValueError):
                p.verify_extraction(good,bad)
        for key in ('network','native_provider','swift_executed'):
            bad=copy.deepcopy(evidence)
            bad[key]=True
            with self.assertRaises(ValueError):
                p.verify_extraction(good,bad)

    def test_historical_contracts_need_no_new_runtime_claim(self):
        old={'input_sha256':{p.GENERATOR:'0'*64}}
        self.assertFalse(p.validate(old))
        p.verify_runtime(old,'historical marker')
        p.verify_extraction(old,{})

    def test_current_workflows_register_controls(self):
        root=Path(__file__).resolve().parents[2]
        for name in ('issues-native','cold-vpn-preflight','cold-vpn-native','issue56-native',
                     'refresh-truth-preflight'):
            text=(root/'.github/workflows'/ (name+'.yml')).read_text()
            self.assertIn('python3 -B harness/ci/issues-native/test_initial_provider_rejection_projection.py',text)
        gate=(root/'ci/issues-native/verify_comprehensive_diagnostics.py').read_text()
        self.assertEqual(gate.count('initial_provider_rejection.verify_runtime(contract,'),2)
        self.assertIn('initial_provider_rejection.verify_extraction(contract, auth)',gate)

if __name__=='__main__':
    unittest.main()
