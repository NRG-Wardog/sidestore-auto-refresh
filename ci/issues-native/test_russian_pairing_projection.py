import copy
import json
import unittest
import tempfile
import hashlib
from pathlib import Path
import russian_pairing_projection as r
import verify_comprehensive_diagnostics as gate

class RussianPairingTests(unittest.TestCase):
    def setUp(self):
        self.manifest,self.original,_=r.extension()
        self.contract=json.loads((r.HERE/'comprehensive-diagnostics-contract.json').read_text())
        self.contract['input_sha256'].update(self.manifest['candidate_inputs'])
    def test_exact_additive_case_resource_and_original_order(self):
        before=copy.deepcopy(self.contract); result=r.add(self.contract)
        self.assertEqual(before,self.contract)
        self.assertEqual([c for c in result['pairing_recovery_case_names'] if c!=r.CASE],self.original)
        self.assertEqual(len(result['pairing_recovery_case_names']),32)
        self.assertEqual(r.resources(result),(r.RESOURCE,))
        self.assertEqual(r.resources(self.contract),())
        self.assertEqual(len(gate.PAIRING_RECOVERY_OUTPUTS),6)
        self.assertEqual(gate.PAIRING_RECOVERY_MARKER,'PAIRING_RECOVERY_BOUNDARY_PASS')
    def test_dropped_reordered_or_extra_case_rejected(self):
        for action in ('drop','reorder','extra'):
            result=r.add(self.contract);cases=result['pairing_recovery_case_names']
            if action=='drop':cases.pop()
            elif action=='reorder':cases.reverse()
            else:cases.append('localized-command-recovery-deferred-de')
            with self.subTest(action=action),self.assertRaises(ValueError):r.resources(result)
    def test_unbound_source_rejected_before_projection_and_after(self):
        for key in self.manifest['candidate_inputs']:
            result=r.add(self.contract);result['input_sha256'][key]='0'*64
            with self.subTest(path=key),self.assertRaises(ValueError):r.resources(result)
        self.contract['input_sha256'][key]='0'*64
        with self.assertRaises(ValueError):r.add(self.contract)
    def test_extension_identity_required(self):
        result=r.add(self.contract);result['russian_pairing_extension']['manifest_sha256']='0'*64
        with self.assertRaises(ValueError):r.resources(result)
    def test_cannot_drop_original_then_rebase(self):
        self.contract['pairing_recovery_case_names'].pop()
        with self.assertRaises(ValueError):r.add(self.contract)
    def test_extraction_and_final_export_enforce_exact_candidate_closure(self):
        contract=r.add(self.contract)
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);root=out/'pairing-recovery';root.mkdir()
            names=gate.PAIRING_RECOVERY_OUTPUTS+r.resources(contract)
            for name in names:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
            hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
            source={'status':'generated_not_compiled','schema':'pairing-recovery-sources-v1',
                    'case_names':contract['pairing_recovery_case_names'],
                    'input_sha256':{k:contract['input_sha256'][p] for k,p in gate.PAIRING_RECOVERY_INPUT_PATHS.items()},
                    'fixture_inputs':{Path(p).name:contract['input_sha256'][p] for p in gate.PAIRING_RECOVERY_FIXTURE_PATHS},
                    'baseline_store_sha256':gate.PAIRING_RECOVERY_BASELINE_STORE_SHA256,
                    'output_sha256':hashes,'fixture_sha256':hashes['fixture.swift'],
                    'owned_paths':['synthetic-boundary'],'doubles':['synthetic-boundary']}
            (root/'sources.json').write_text(json.dumps(source))
            gate.pairing_recovery_extraction(out,contract,before_compile=True)
            source['case_names']=self.original
            (root/'sources.json').write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError,'case closure'):gate.pairing_recovery_extraction(out,contract,before_compile=True)
            source['case_names']=contract['pairing_recovery_case_names'];(root/'sources.json').write_text(json.dumps(source))
            (root/r.RESOURCE).unlink()
            with self.assertRaises(ValueError):gate.pairing_recovery_extraction(out,contract,before_compile=True)
            export={'schema':'pairing-recovery-cases-v1','status':'pass','fake_only':True,'native_pairing_executed':False,
                    'cases':contract['pairing_recovery_case_names'],'case_count':32}
            (root/'cases.json').write_text(json.dumps(export))
            gate.pairing_recovery_export(out,contract)
            export['cases']=self.original;export['case_count']=31
            (root/'cases.json').write_text(json.dumps(export))
            with self.assertRaises(ValueError):gate.pairing_recovery_export(out,contract)
if __name__=='__main__':unittest.main()
