import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from ci.qualification.native import direct_refresh_projection as P
from ci.qualification.native import current_vpn_guard_projection as C
from ci.qualification.native import grace_recovery_projection as G
from ci.qualification.native import refresh_truth_flow_projection as F

class DirectRefreshProjectionTests(unittest.TestCase):
    def test_normalization_is_exact_and_nonmutating(self):
        value={'input_sha256':{P.PATH:P.AFTER, 'other':'unchanged'}}
        before=copy.deepcopy(value)
        normalized=P.normalize(value)
        self.assertEqual(value,before)
        self.assertEqual(normalized,{'input_sha256':{P.PATH:P.BEFORE,'other':'unchanged'}})
        self.assertIs(P.normalize({'input_sha256':{P.PATH:'0'*64}}).__class__,dict)

    def test_unknown_successor_rejected_by_historical_bindings(self):
        old={'input_sha256':dict(G.FILES),'case_names':{'vpn-refresh-recovery':G.historical()}}
        current=G.add(old)
        current['input_sha256'][P.PATH]='0'*64
        with self.assertRaises(ValueError):G.count(current)

    def test_all_prior_grace_cases_remain_ordered(self):
        old={'input_sha256':dict(G.FILES),'case_names':{'vpn-refresh-recovery':G.historical()}}
        old['input_sha256'][P.PATH]=P.AFTER
        current=G.add(old)
        self.assertEqual(G.count(current),105)
        self.assertEqual(current['case_names']['vpn-refresh-recovery'],G.expected_names())
        self.assertEqual(current['input_sha256'][P.PATH],P.AFTER)

    def test_current_guard_proof_retains_historical_fields(self):
        value={'input_sha256':{C.PATH:C.EXPECTED['source_receipt_sha256'],C.GENERATOR:P.AFTER,C.BASELINE:C.EXPECTED['baseline_classifier_sha256']}}
        current=C.add(value)
        self.assertEqual(C.binding(current),C.EXPECTED)
        self.assertEqual(current['input_sha256'][P.PATH],P.AFTER)

    def test_refresh_truth_keeps_prior_cases_and_markers(self):
        ext,_=F.extension()
        inputs={**G.FILES,**ext['candidate_inputs'],P.PATH:P.AFTER}
        value={'input_sha256':inputs,'case_names':{'vpn-refresh-recovery':G.historical(),'setup-connection-evidence':F.names()['setup-connection-evidence']}}
        current=F.add(G.add(value))
        self.assertEqual(F.vpn_count(current),106)
        self.assertEqual(F.connection_count(current),26)
        self.assertEqual(current['input_sha256'][P.PATH],P.AFTER)

    def test_rejects_unknown_generator_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/P.PATH;p.parent.mkdir(parents=True);p.write_text(P.NEW)
            with self.assertRaises(ValueError):P.validate_source(root)

    def test_successor_is_one_finite_assertion_replacement(self):
        self.assertIn('ed031c9f09879ab81f5feeacb141c72ceb076a38',P.NEW)
        self.assertIn('5bf22356b3413d4377b26ed96ae8b63522855bd9',P.NEW)
        self.assertIn('finishHostRefresh:error runID:runID admissionID:dispatchAdmissionID verification:verification',P.NEW)
        self.assertNotIn('case_names',P.NEW)

if __name__=='__main__':unittest.main(verbosity=2)
