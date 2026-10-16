"""P1 admission negatives; synthetic receipts here never grant native readiness."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT))
import maintained_sources as gate
import maintained_package_evidence as package
import p0_maintained_sources as p0
import p1_maintained_sources as p1
import prepare_candidate_inventory as inventory


def encoded(value): return (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()
def h(value): return hashlib.sha256(('SYNTHETIC ONLY '+value).encode()).hexdigest()


class P1AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT/p0.DIRECTORY, self.root/p0.DIRECTORY)
        shutil.copytree(ROOT/p1.DIRECTORY, self.root/p1.DIRECTORY)
        self.directory = self.root/p1.DIRECTORY
        self.review = p0.parse((self.directory/'reviewed-approval.json').read_bytes())
        self.native = {'source_basis': p1.BASIS, 'readiness_scope': 'eligible_for_gated_full_build',
                       'review_sha256': None, 'run_url': 'https://github.com/NRG-Wardog/LiveContainer/actions/runs/123',
                       'run_attempt': 1, 'host_commit': '1'*40, 'host_tree': '2'*40, 'artifact_id': 123,
                       'artifact_sha256': h('artifact'), 'approved_inputs_sha256': h('inputs'),
                       'evidence_index_sha256': h('index'),
                       'tested_owners': {'LiveContainer': {'commit': p1.HOST_COMMIT, 'tree': p1.HOST_TREE}},
                       'inherited_p0_review_sha256': p0.P0_REVIEW_SHA256}
        self.receipt = {'status': 'INDEPENDENTLY_REVIEWED_P1_NATIVE_PASS', 'source_basis': p1.BASIS,
                        'native_validation': self.native, 'native_builds': {'LiveContainer': 'PASS_UNSIGNED_IPHONEOS'},
                        'native_suites': {'cleanup': {'tests': 8, 'failures': 0, 'skipped': 0}},
                        'ui_validation': {'status': 'PASS', 'source_sha256': p1.AFTER['sha256'], 'legacy_reports': 14,
                                          'device_classes': 2, 'signin_cases': 8, 'validated_screenshots': 24},
                        'behavior_validation': copy.deepcopy(p1.BEHAVIOR),
                        'inherited_p0_review_sha256': p0.P0_REVIEW_SHA256}
        self.review['native_validation'] = self.native

    def seal(self):
        (self.directory/'native-receipt.json').write_bytes(encoded(self.receipt))
        for name in self.review['files']:
            self.review['files'][name] = p0.sha((self.directory/name).read_bytes())
        raw = encoded(self.review); (self.directory/'reviewed-approval.json').write_bytes(raw)
        return p0.sha(raw)

    def pins(self, anchor):
        return {'schema_version': 1, 'integration_baseline': gate.BASELINE, 'source_basis': p1.BASIS,
                'contract_registry_sha256': self.review['files']['compatibility-registry.json'],
                'owners': {n: {k: v for k, v in row.items() if k != 'tree'} for n, row in self.review['owners'].items()},
                'native_validation': {**self.native, 'review_sha256': anchor}}

    def check(self):
        with patch.object(p1, 'P1_REVIEW_SHA256', self.seal()): return p1.approval(self.root)

    def test_pending_anchor_fails_before_caller_metadata(self):
        with patch.object(p1, 'P1_REVIEW_SHA256', None), patch.object(p1, 'read', side_effect=AssertionError('read')):
            with self.assertRaises(ValueError): p1.approval(self.root)

    def test_pending_acquire_never_executes(self):
        with patch.object(p1, 'P1_REVIEW_SHA256', None), patch.object(p1.subprocess, 'run') as run:
            with self.assertRaises(ValueError): p1.acquire(self.root, {}, gate)
        run.assert_not_called()

    def test_synthetic_positive_does_not_assign_production_readiness(self):
        self.assertEqual(self.check()[1]['owners']['LiveContainer']['commit'], p1.HOST_COMMIT)

    def test_wrong_parent_tree_commit_or_extra_path_rejected(self):
        for field in ('baseline_commit', 'tree', 'commit', 'changes'):
            with self.subTest(field=field):
                before = copy.deepcopy(self.review['transition'])
                if field == 'changes': self.review['transition'][field]['extra.swift'] = copy.deepcopy(p1.TRANSITION['changes'][p1.SHELL])
                else: self.review['transition'][field] = '0'*40
                with self.assertRaises(ValueError): self.check()
                self.review['transition'] = before

    def test_changed_unaffected_owner_rejected(self):
        self.review['owners']['SideStore']['commit'] = 'f'*40
        with self.assertRaises(ValueError): self.check()

    def test_rehashed_changed_lock_or_child_fact_rejected(self):
        for name in ('submodules.json', 'SideSign-resolver.json', 'resolver-locks/SideStore.Package.resolved.after'):
            with self.subTest(name=name):
                path=self.directory/name; before=path.read_bytes(); path.write_bytes(before+b'\n')
                with self.assertRaises(ValueError): self.check()
                path.write_bytes(before)

    def test_wrong_shell_inventory_hash_rejected_even_if_resealed(self):
        path=self.directory/'source-inventory.json'; value=p0.parse(path.read_bytes())
        value['LiveContainer']['changes'][p1.SHELL]['after']['sha256']=h('wrong shell')
        path.write_bytes(encoded(value))
        with self.assertRaises(ValueError): self.check()

    def test_p0_native_host_receipt_cannot_prove_p1(self):
        self.native['tested_owners']['LiveContainer']={'commit': p1.HOST_PARENT, 'tree': p1.HOST_PARENT_TREE}
        with self.assertRaises(ValueError): self.check()

    def test_p0_run_cannot_be_relabelled(self):
        self.native['run_url']=p0.approval(self.root)[1]['native_validation']['run_url']
        with self.assertRaises(ValueError): self.check()

    def test_absent_host_build_or_ui_or_cleanup_fails(self):
        for key in ('native_builds', 'ui_validation', 'native_suites', 'behavior_validation'):
            with self.subTest(key=key):
                old=self.receipt.pop(key)
                with self.assertRaises(ValueError): self.check()
                self.receipt[key]=old

    def test_missing_p1_capability_and_skipped_checks_rejected(self):
        for target, key, wrong in ((self.receipt['ui_validation'], 'source_sha256', p1.BEFORE['sha256']),
                                   (self.receipt['ui_validation'], 'validated_screenshots', 23),
                                   (self.receipt['native_suites']['cleanup'], 'skipped', 1)):
            with self.subTest(key=key):
                old=target[key]; target[key]=wrong
                with self.assertRaises(ValueError): self.check()
                target[key]=old

    def test_missing_targeted_behavior_case_or_device_rejected(self):
        for key, wrong in (('test_count', 9), ('device_classes', ['phone']),
                           ('case_ids', p1.BEHAVIOR['case_ids'][:-1]), ('passed', False)):
            with self.subTest(key=key):
                old=self.receipt['behavior_validation'][key]
                self.receipt['behavior_validation'][key]=wrong
                with self.assertRaises(ValueError):self.check()
                self.receipt['behavior_validation'][key]=old

    def test_tampered_receipt_rejected_without_rehashing_anchor(self):
        anchor=self.seal();(self.directory/'native-receipt.json').write_bytes(b'{}\n')
        with patch.object(p1,'P1_REVIEW_SHA256',anchor):
            with self.assertRaises(ValueError): p1.approval(self.root)

    def test_receipt_identity_mismatch_rejected(self):
        self.receipt['native_validation']=copy.deepcopy(self.native)
        self.receipt['native_validation']['artifact_sha256']=h('other artifact')
        with self.assertRaises(ValueError): self.check()

    def test_frozen_p0_evidence_must_be_identical(self):
        path=self.directory/'inherited/p0-native-basis-v1/native-receipt.json';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError): self.check()

    def test_caller_only_pin_override_is_rejected(self):
        anchor=self.seal(); pins=self.pins(anchor)
        with patch.object(p1,'P1_REVIEW_SHA256',anchor):
            p1.load_pins(pins,self.root,gate)
            pins['owners']['LiveContainer']['commit']=p1.HOST_PARENT
            with self.assertRaises(ValueError): p1.load_pins(pins,self.root,gate)
            with self.assertRaises(ValueError): p1.contract_files(pins,self.root)

    def test_package_roundtrip_preserves_both_proofs(self):
        anchor=self.seal(); pins=self.pins(anchor); output=self.root/'output'; output.mkdir()
        with patch.object(p1,'P1_REVIEW_SHA256',anchor),patch.object(package,'ROOT',self.root):
            hashes=package.collect_contract_evidence(output,pins)
            package.verify_contract_evidence(output,hashes,pins)
            self.assertIn('inherited/p0-native-basis-v1/native-receipt.json',hashes)
            path=output/package.CONTRACT_DIRECTORY/'native-receipt.json';path.write_bytes(b'{}\n')
            hashes['native-receipt.json']=p0.sha(path.read_bytes())
            with self.assertRaises(ValueError): package.verify_contract_evidence(output,hashes,pins)

    def test_pending_dispatch_and_package_routes_fail_closed(self):
        path=self.root/'pins.json';path.write_bytes(encoded({'source_basis':p1.BASIS}))
        with patch.object(p1,'P1_REVIEW_SHA256',None),patch.object(gate,'ROOT',self.root),patch.object(package,'ROOT',self.root):
            with self.assertRaises(ValueError):gate.load_pins(path)
            with self.assertRaises(ValueError):gate.contract_basis({'source_basis':p1.BASIS})
            with self.assertRaises(ValueError):package.contract_files({'source_basis':p1.BASIS})
            with self.assertRaises(ValueError):gate.expected_anisette_evidence({'source_basis':p1.BASIS})

    def test_actual_transition_rejects_wrong_git_parent(self):
        legacy=Mock();legacy.git.return_value=(p1.HOST_COMMIT+' '+'a'*40+'\n').encode()
        with self.assertRaises(ValueError):p1.verify_transition(self.root,legacy)

    def test_actual_transition_rejects_missing_capability(self):
        legacy=Mock();legacy.ROOT=ROOT
        legacy.git.side_effect=[(p1.HOST_COMMIT+' '+p1.HOST_PARENT+'\n').encode(),
                                (p1.HOST_PARENT_TREE+'\n').encode(),(p1.HOST_TREE+'\n').encode(),b'wrong source']
        with patch.object(inventory,'snapshot',return_value=p1.TRANSITION):
            with self.assertRaises(ValueError):p1.verify_transition(self.root,legacy)

    def test_original_p0_gate_and_metadata_remain_unchanged(self):
        # Paths are anchored explicitly, independent of the test process cwd.
        names=['scripts/p0_maintained_sources.py']+[str(p.relative_to(ROOT)) for p in (ROOT/p0.DIRECTORY).rglob('*') if p.is_file()]
        for name in names:
            before=subprocess.check_output(['git','-C',str(ROOT),'show',p1.INTEGRATION_PARENT+':'+name])
            self.assertEqual((ROOT/name).read_bytes(),before,name)


if __name__=='__main__':unittest.main()
