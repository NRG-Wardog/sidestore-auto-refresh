import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import russian_setup_projection as r
import verify_setup_state_recovery as gate

class RussianSetupProjectionTests(unittest.TestCase):
    def setUp(self):
        self.extension,self.original,self.digest=r.extension()
        self.contract=json.loads((r.HERE/'setup-state-recovery-contract.json').read_text())
        self.contract['input_sha256'].update(self.extension['candidate_inputs'])
    def test_adds_only_exact467_russian_cases_preserving1292_order_and_baseline(self):
        before=copy.deepcopy(self.contract);after=r.add(self.contract)
        self.assertEqual(self.contract,before)
        self.assertEqual(after['delivered_baseline'],before['delivered_baseline'])
        self.assertEqual([x for x in after['case_names']['setup-localization'] if x in set(self.original)],self.original)
        self.assertEqual(len(after['case_names']['setup-localization']),1759)
        self.assertEqual(r.variant(after),{'cases':1759,'values':920,'baseline_missing':344})
        self.assertEqual(gate.markers(after)['localization-run'],'SETUP_LOCALIZATION_RENDERER_PASS cases=1759')
    def test_original_projection_keeps_frozen_constants(self):
        self.assertEqual(r.variant(self.contract),{'cases':1292,'values':736,'baseline_missing':160})
        self.assertEqual((gate.LOCALIZATION_CASE_COUNT,gate.LOCALIZATION_VALUE_COUNT,gate.LOCALIZATION_BASELINE_MISSING),(1292,736,160))
        self.assertEqual(gate.markers()['localization-run'],'SETUP_LOCALIZATION_RENDERER_PASS cases=1292')
    def test_drop_reorder_or_unexpected_locale_rejected(self):
        for action in ['drop','reorder','locale']:
            value=r.add(self.contract);cases=value['case_names']['setup-localization']
            if action=='drop':cases.pop()
            elif action=='reorder':cases[0],cases[1]=cases[1],cases[0]
            else:cases[-1]=cases[-1].replace('-ru','-de') if '-ru' in cases[-1] else 'unknown-de'
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'ordered case'):r.variant(value)
    def test_removed_original_case_cannot_be_rebased(self):
        self.contract['case_names']['setup-localization'].pop()
        with self.assertRaisesRegex(ValueError,'original ordered'):r.add(self.contract)
    def test_unbound_catalog_generator_or_correction_map_rejected(self):
        for path in self.extension['candidate_inputs']:
            value=r.add(self.contract);value['input_sha256'][path]='0'*64
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'source hashes'):r.variant(value)
    def test_wrong_extension_identity_rejected(self):
        value=r.add(self.contract);value['russian_setup_extension']['manifest_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'identity'):r.variant(value)

if __name__=='__main__':unittest.main()
