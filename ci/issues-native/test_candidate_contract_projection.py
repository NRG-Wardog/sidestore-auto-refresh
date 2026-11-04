import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from project_candidate_contracts import project, sha, add_reviewed_host_coverage, HERE, HOST_CONTEXT_INPUT, HOST_ADDITIONAL_CASES, IDENTITY_CANDIDATE_INPUT, CLASSIFIER_DEPENDENCY_TEST, LEGACY_RISK_KEY_MANIFEST
import json


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'LiveContainer/source.swift'
        self.path.parent.mkdir()
        self.data = b'candidate source\n'
        self.path.write_bytes(self.data)
        self.blob = hashlib.sha1(b'blob ' + str(len(self.data)).encode() + b'\0' + self.data).hexdigest()
        self.graph = {'owners': {'LiveContainer': ['NRG-Wardog/LiveContainer', 'a' * 40]},
                      'reviewed_deltas': {'LiveContainer': {'files': {'source.swift': self.blob}}}}
        self.contract = {'input_sha256': {'LiveContainer/source.swift': sha(b'original')},
                         'delivered_baseline': {'input_sha256': {'old': 'frozen'}, 'owners': {'old': 'pin'}},
                         'case_names': ['must_still_pass'], 'suites': ['original']}

    def call(self, contract=None):
        def git(root, *args):
            return 'a' * 40 if args[-1] == 'HEAD' else self.blob
        with patch('project_candidate_contracts.git', side_effect=git):
            return project(contract or self.contract, self.graph, self.root)

    def test_preservation_inventory_exact_input_not_arbitrary_fixture_allowance(self):
        import preservation_localization_projection as preservation
        path = preservation.INVENTORY_TEST
        fixture = self.root / path
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(self.data)
        relative = path.removeprefix('LiveContainer/')
        self.graph['reviewed_deltas']['LiveContainer']['files'][relative] = self.blob
        with patch('project_candidate_contracts.git', side_effect=lambda root, *args: 'a' * 40 if args[-1] == 'HEAD' else self.blob):
            with self.assertRaisesRegex(ValueError, 'Unreviewed preservation inventory'):
                project(self.contract, self.graph, self.root, (path,))
            with patch.dict(preservation.PINS, {path: sha(self.data)}):
                projected, _ = project(self.contract, self.graph, self.root, (path,))
                self.assertEqual(projected['input_sha256'][path], sha(self.data))
                changed = copy.deepcopy(self.graph)
                changed['reviewed_deltas']['LiveContainer']['files'].pop(relative)
                with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
                    project(self.contract, changed, self.root, (path,))
                with self.assertRaisesRegex(ValueError, 'unreviewed additive'):
                    project(self.contract, self.graph, self.root, (path + '.extra',))

    def test_preservation_localization_harness_requires_exact_reviewed_bytes(self):
        import preservation_localization_projection as preservation
        inventory, harness = preservation.INVENTORY_TEST, preservation.HARNESS
        for path in (inventory, harness):
            file = self.root / path; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(self.data)
            self.graph['reviewed_deltas']['LiveContainer']['files'][path.removeprefix('LiveContainer/')] = self.blob
        contract = copy.deepcopy(self.contract); contract['input_sha256'][harness] = '0' * 64
        with patch('project_candidate_contracts.git', side_effect=lambda root, *args: 'a' * 40 if args[-1] == 'HEAD' else self.blob), patch.dict(preservation.PINS, {inventory: sha(self.data)}):
            with self.assertRaisesRegex(ValueError, 'Unreviewed preservation localization harness'):
                project(contract, self.graph, self.root, (inventory,))
            with patch.dict(preservation.PINS, {harness: sha(self.data)}):
                projected, _ = project(contract, self.graph, self.root, (inventory,))
                self.assertEqual(projected['input_sha256'][harness], sha(self.data))
                (self.root / harness).unlink()
                with self.assertRaisesRegex(ValueError, 'nonregular contract input'):
                    project(contract, self.graph, self.root, (inventory,))

    def test_only_current_hashes_rebound(self):
        before = copy.deepcopy(self.contract)
        result, changes = self.call()
        self.assertEqual(self.contract, before)
        self.assertEqual(result.pop('input_sha256'), {'LiveContainer/source.swift': sha(self.data)})
        before.pop('input_sha256')
        self.assertEqual(result, before)
        self.assertEqual(changes['LiveContainer/source.swift']['reviewed_blob'], self.blob)

    def test_reviewed_additive_input_is_blob_bound_and_original_untouched(self):
        contract = copy.deepcopy(self.contract)
        contract['input_sha256'] = {}
        path = 'LiveContainer/source.swift'
        with patch('project_candidate_contracts.ADDITIONAL_INPUTS', {'test': (path,)}):
            with patch('project_candidate_contracts.git', side_effect=lambda root, *args: 'a' * 40 if args[-1] == 'HEAD' else self.blob):
                result, changes = project(contract, self.graph, self.root, (path,))
                self.assertEqual(result['input_sha256'][path], sha(self.data))
                self.assertIsNone(changes[path]['before_sha256'])
                self.assertEqual(contract['input_sha256'], {})
                self.graph['reviewed_deltas'] = {}
                with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
                    project(contract, self.graph, self.root, (path,))
        with self.assertRaisesRegex(ValueError, 'unreviewed additive'):
            project(contract, self.graph, self.root, ('unknown.swift',))

    def test_classifier_control_requires_exact_reviewed_blob(self):
        path = self.root / CLASSIFIER_DEPENDENCY_TEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)
        relative = CLASSIFIER_DEPENDENCY_TEST.removeprefix('LiveContainer/')
        self.graph['reviewed_deltas']['LiveContainer']['files'][relative] = self.blob
        with patch('project_candidate_contracts.git', side_effect=lambda root, *args: 'a' * 40 if args[-1] == 'HEAD' else self.blob):
            result, _ = project(self.contract, self.graph, self.root, (CLASSIFIER_DEPENDENCY_TEST,))
            self.assertEqual(result['input_sha256'][CLASSIFIER_DEPENDENCY_TEST], sha(self.data))
            self.assertEqual(result['case_names'], self.contract['case_names'])
            path.write_bytes(b'unreviewed change')
            with self.assertRaises(ValueError):
                project(self.contract, self.graph, self.root, (CLASSIFIER_DEPENDENCY_TEST,))
            path.write_bytes(self.data)
            del self.graph['reviewed_deltas']['LiveContainer']['files'][relative]
            with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
                project(self.contract, self.graph, self.root, (CLASSIFIER_DEPENDENCY_TEST,))

    def test_legacy_risk_manifest_requires_exact_reviewed_blob(self):
        path = self.root / LEGACY_RISK_KEY_MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)
        relative = LEGACY_RISK_KEY_MANIFEST.removeprefix('LiveContainer/')
        self.graph['reviewed_deltas']['LiveContainer']['files'][relative] = self.blob
        with patch('project_candidate_contracts.git', side_effect=lambda root, *args: 'a' * 40 if args[-1] == 'HEAD' else self.blob):
            result, _ = project(self.contract, self.graph, self.root, (LEGACY_RISK_KEY_MANIFEST,))
            self.assertEqual(result['input_sha256'][LEGACY_RISK_KEY_MANIFEST], sha(self.data))
            self.assertEqual(result['case_names'], self.contract['case_names'])
            path.write_bytes(b'unreviewed change')
            with self.assertRaises(ValueError):
                project(self.contract, self.graph, self.root, (LEGACY_RISK_KEY_MANIFEST,))
            path.write_bytes(self.data)
            del self.graph['reviewed_deltas']['LiveContainer']['files'][relative]
            with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
                project(self.contract, self.graph, self.root, (LEGACY_RISK_KEY_MANIFEST,))

    def test_host_additions_preserve_every_frozen_case_and_baseline(self):
        original = json.loads((HERE / 'setup-state-recovery-contract.json').read_text())
        original['input_sha256'][HOST_CONTEXT_INPUT] = 'a' * 64
        original['input_sha256'][IDENTITY_CANDIDATE_INPUT] = 'b' * 64
        result = add_reviewed_host_coverage(original)
        new_cases = result['case_names']['host-refresh-bundle']
        self.assertEqual(len(new_cases), 35)
        self.assertEqual([c for c in new_cases if c not in HOST_ADDITIONAL_CASES], original['case_names']['host-refresh-bundle'])
        self.assertEqual(result['delivered_baseline'], original['delivered_baseline'])
        altered = copy.deepcopy(original)
        altered['case_names']['host-refresh-bundle'].pop()
        with self.assertRaisesRegex(ValueError, 'original host cases'):
            add_reviewed_host_coverage(altered)
        del original['input_sha256'][HOST_CONTEXT_INPUT]
        with self.assertRaisesRegex(ValueError, 'not blob-bound'):
            add_reviewed_host_coverage(original)

    def test_unmodified_inputs_need_no_delta(self):
        self.contract['input_sha256']['LiveContainer/source.swift'] = sha(self.data)
        self.graph['reviewed_deltas'] = {}
        self.assertEqual(self.call(), (self.contract, {}))

    def test_unreviewed_or_modified_working_bytes_reject(self):
        self.graph['reviewed_deltas'] = {}
        with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
            self.call()
        self.graph['reviewed_deltas'] = {'LiveContainer': {'files': {'source.swift': self.blob}}}
        self.path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'working bytes differ'):
            self.call()

    def test_owner_head_path_and_symlink_reject(self):
        with patch('project_candidate_contracts.git', return_value='b' * 40):
            with self.assertRaisesRegex(ValueError, 'head drift'):
                project(self.contract, self.graph, self.root)
        bad = copy.deepcopy(self.contract)
        bad['input_sha256'] = {'../escape': sha(b'')}
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.call(bad)
        self.path.unlink()
        self.path.symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            self.call()


if __name__ == '__main__':
    unittest.main(verbosity=2)
