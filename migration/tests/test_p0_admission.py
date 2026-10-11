"""Fresh reconstructed-gate tests; synthetic fixtures never grant native readiness."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import p0_maintained_sources as p0
import maintained_sources as legacy
import maintained_package_evidence as package

def h(text): return hashlib.sha256(('SYNTHETIC ONLY '+text).encode()).hexdigest()
def c(text): return hashlib.sha1(('SYNTHETIC ONLY '+text).encode()).hexdigest()
def encoded(value): return (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        pending = patch.object(p0, "P0_REVIEW_SHA256", None)
        pending.start(); self.addCleanup(pending.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root/p0.DIRECTORY
        self.directory.mkdir(parents=True)
    def tearDown(self): self.temp.cleanup()
    def fixture(self):
        owners = {name: {'repository': 'https://github.com/NRG-Wardog/'+name+'.git', 'commit': c(name), 'tree': c(name+' tree'),
                         'source_checkpoint': checkpoint, 'path': legacy.PATHS[name]} for name, checkpoint in p0.CHECKPOINTS.items()}
        native = {'source_basis': p0.BASIS, 'readiness_scope': 'eligible_for_gated_full_build', 'review_sha256': None,
                  'run_url': 'https://github.com/NRG-Wardog/LiveContainer/actions/runs/123', 'run_attempt': 1,
                  'host_commit': c('host'), 'host_tree': c('host tree'), 'artifact_id': 123, 'artifact_sha256': h('artifact'),
                  'approved_inputs_sha256': h('inputs'), 'evidence_index_sha256': h('index'),
                  'tested_owners': {name: {'commit': row['commit'], 'tree': row['tree']} for name, row in owners.items()},
                  'tested_children': {'SideSign': {}, 'SideStore': {'Dependencies/SideSign': owners['SideSign']['commit'], 'Dependencies/minimuxer': owners['minimuxer']['commit']}}}
        children = {'SideStore/Dependencies/'+name: {'repository': owners[name]['repository'], 'declared_repository': owners[name]['repository'],
                    'commit': owners[name]['commit'], 'tree': owners[name]['tree']} for name in ('SideSign', 'minimuxer')}
        files = {'source-evidence.json': {}, 'source-inventory.json': {}, 'submodules.json': children}
        files.update({'owners/'+name+'.json': {} for name in owners})
        files['compatibility-registry.json'] = {'owners': {name: {'manifest_path': 'owners/'+name+'.json', 'manifest_sha256': p0.sha(encoded({}))} for name in owners},
                                                'evidence': {'path': 'source-evidence.json', 'sha256': p0.sha(encoded({}))}}
        paths = ['Native/Loader/android_tls_runtime.h', 'Native/Loader/elf_loader_emulator.cpp', 'Native/Loader/elf_loader_emulator.h',
                 'Native/anisette_core_uc.cpp', 'Native/Loader/adi_consumption_debug.h', 'Native/include/anisette_core.h', 'Sources/AnisetteDataProvider.swift']
        literals = ['V3_CHECKED_ANISETTE_STAGING_V1', 'Checked OTP staging failed', 'v2|%u|%u|%u', '|%d,%d,%d,%d,%d,%u,%u,%d', 'v3_native_consumption']
        files['anisette-generated/maintained-source-manifest.json'] = {'source_basis': p0.BASIS, 'anisettekit_revision': owners['AnisetteKit']['commit'],
                  'files': [{'path': path, 'sha256': h(path)} for path in paths], 'required_compiled_evidence': {'literals': [{'value': value} for value in literals]}}
        phases = {}
        for name in ('SideSign', 'SideStore'):
            phase = {key: native[key] for key in ('run_url', 'run_attempt', 'host_commit', 'host_tree', 'artifact_id', 'artifact_sha256', 'approved_inputs_sha256', 'evidence_index_sha256')}
            phase.update(status='CAPTURED_FOR_REVIEW', resolver_tested_commit=owners[name]['commit'], resolver_tested_tree=owners[name]['tree'],
                         lock_before_sha256=h(name+' before'), lock_sha256=h(name+' after'), origin_hash={'present': False, 'value': None})
            phases[name] = phase
            files[name+'-resolver.json'] = {**phase, 'purpose': 'independently_reviewed_p0_swiftpm_resolution', 'owner': name,
                                           'source_basis': p0.BASIS, 'anisette_commit': owners['AnisetteKit']['commit']}
        files['native-receipt.json'] = {'status': 'INDEPENDENTLY_REVIEWED_P0_NATIVE_PASS', 'source_basis': p0.BASIS, 'native_validation': native,
                                       'native_builds': {'SideSign': 'PASS_MACOS_BUILD_AND_TEST', 'SideStore': 'PASS_UNSIGNED_IPHONEOS', 'LiveContainer': 'PASS_UNSIGNED_IPHONEOS'},
                                       'canonical_native_fork83': {'checks': 83, 'failures': 0, 'source_owner': owners['AnisetteKit']['commit'], 'engine_archive_sha256': p0.FORK_SHA256},
                                       'phase_evidence': phases, 'tested_submodules': copy.deepcopy(children)}
        review = {'schema_version': 1, 'source_basis': p0.BASIS, 'integration_parent': '767234eb704745ce915c0750eee2c31061ac4f78',
                  'owners': owners, 'files': {}, 'native_validation': native}
        return review, files
    def seal_synthetic(self, review, files):
        for name, value in files.items():
            path = self.directory/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded(value))
            review['files'][name] = p0.sha(path.read_bytes())
        raw = encoded(review)
        (self.directory/'reviewed-approval.json').write_bytes(raw)
        return p0.sha(raw)
    def check(self, review, files):
        with patch.object(p0, 'P0_REVIEW_SHA256', self.seal_synthetic(review, files)):
            return p0.approval(self.root)
    def test_anchor_stays_none(self):
        self.assertIsNone(p0.P0_REVIEW_SHA256)
        with patch.object(p0, 'read', side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError): p0.approval(self.root)
    def test_pending_dispatch_rejected(self):
        path = self.root/'pins.json';path.write_text(json.dumps({'source_basis': p0.BASIS}))
        with self.assertRaises(ValueError): legacy.load_pins(path)
    def test_pending_package_rejected(self):
        with self.assertRaises(ValueError): package.contract_files({'source_basis': p0.BASIS})
    def test_pending_acquire_never_calls_subprocess(self):
        with patch.object(p0.subprocess, 'run') as run:
            with self.assertRaises(ValueError): p0.acquire(self.root, {}, legacy)
        run.assert_not_called()
    def test_synthetic_schema_positive_is_not_native_acceptance(self):
        review, files = self.fixture()
        self.assertEqual(self.check(review, files)[1]['source_basis'], p0.BASIS)
    def test_valid_anisette_positive_control(self):
        review, files = self.fixture()
        with patch.object(p0, 'P0_REVIEW_SHA256', self.seal_synthetic(review, files)):
            self.assertEqual(len(p0.anisette_manifest({'owners': review['owners']}, self.root)['files']), 7)
    def test_missing_runtime_path_rejected(self):
        review, files = self.fixture();files['anisette-generated/maintained-source-manifest.json']['files'].pop()
        with patch.object(p0, 'P0_REVIEW_SHA256', self.seal_synthetic(review, files)):
            with self.assertRaises(ValueError): p0.anisette_manifest({'owners': review['owners']}, self.root)
    def test_missing_compiled_literal_rejected(self):
        review, files = self.fixture();files['anisette-generated/maintained-source-manifest.json']['required_compiled_evidence']['literals'].pop()
        with patch.object(p0, 'P0_REVIEW_SHA256', self.seal_synthetic(review, files)):
            with self.assertRaises(ValueError): p0.anisette_manifest({'owners': review['owners']}, self.root)
    def test_metadata_tamper_rejected(self):
        review, files = self.fixture();anchor=self.seal_synthetic(review, files)
        (self.directory/'native-receipt.json').write_text('{}')
        with patch.object(p0, 'P0_REVIEW_SHA256', anchor):
            with self.assertRaises(ValueError): p0.approval(self.root)
    def test_old_source_basis_rejected(self):
        review, files = self.fixture();review['source_basis']='maintained-adi-consumption-v2'
        with self.assertRaises(ValueError): self.check(review, files)
    def test_old_native_owner_rejected(self):
        review, files = self.fixture();files['native-receipt.json']['canonical_native_fork83']['source_owner']=c('old AK')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_missing_registry_owner_rejected(self):
        review, files = self.fixture();files['compatibility-registry.json']['owners'].pop('jktcp')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_resolver_run_mismatch_rejected(self):
        review, files = self.fixture();files['SideSign-resolver.json']['run_url']='https://github.com/NRG-Wardog/LiveContainer/actions/runs/456'
        with self.assertRaises(ValueError): self.check(review, files)
    def test_resolver_artifact_mismatch_rejected(self):
        review, files = self.fixture();files['SideStore-resolver.json']['artifact_id']=456
        with self.assertRaises(ValueError): self.check(review, files)
    def test_resolver_tree_mismatch_rejected(self):
        review, files = self.fixture();files['SideStore-resolver.json']['resolver_tested_tree']=c('different')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_resolver_index_mismatch_rejected(self):
        review, files = self.fixture();files['SideSign-resolver.json']['evidence_index_sha256']=h('different')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_resolver_lock_mismatch_rejected(self):
        review, files = self.fixture();files['SideStore-resolver.json']['lock_sha256']=h('different')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_child_map_native_binding_required(self):
        review, files = self.fixture();files['native-receipt.json']['tested_submodules']={}
        with self.assertRaises(ValueError): self.check(review, files)
    def test_child_origin_protocol_rejected(self):
        review, files = self.fixture();files['submodules.json']['SideStore/Dependencies/SideSign']['repository']='file:///tmp/other'
        with self.assertRaises(ValueError): self.check(review, files)
    def test_missing_named_child_rejected(self):
        review, files = self.fixture();files['submodules.json'].pop('SideStore/Dependencies/minimuxer')
        with self.assertRaises(ValueError): self.check(review, files)
    def test_child_source_closure_missing_rejected(self):
        roots={name:self.root/name for name in p0.CHECKPOINTS}
        for root in roots.values(): root.mkdir()
        mock=Mock();mock.entries.return_value={}
        with self.assertRaises(ValueError): p0.verify_children(roots, {'idevice/cpp/plist_ffi':{}}, mock)
    def test_child_source_unknown_rejected(self):
        roots={name:self.root/name for name in p0.CHECKPOINTS}
        for root in roots.values(): root.mkdir()
        mock=Mock();mock.entries.return_value={'unknown':('160000','commit',c('child'))}
        with self.assertRaises(ValueError): p0.verify_children(roots, {}, mock)
        mock.verify_origin.assert_not_called()
    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(ValueError): p0.parse('{"a":1,"a":2}')
    def test_unsafe_metadata_paths_rejected(self):
        for name in ('../x','/x','a//b','a/./b','a/.git/b','a\\b'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): p0.read(self.root,name)
    def test_legacy_unmodified_functions_preserved(self):
        before=ast.parse(subprocess.check_output(['git', '-C', str(ROOT), 'show', '767234eb704745ce915c0750eee2c31061ac4f78:scripts/maintained_sources.py'], text=True))
        after=ast.parse((ROOT/'scripts/maintained_sources.py').read_text())
        old={n.name:ast.dump(n,include_attributes=False) for n in before.body if isinstance(n,ast.FunctionDef)}
        changed={'contract_basis','expected_anisette_evidence','anisette_source_hashes','load_pins','verify_all','acquire'}
        for n in after.body:
            if isinstance(n,ast.FunctionDef) and n.name not in changed:self.assertEqual(ast.dump(n,include_attributes=False),old[n.name],n.name)
        self.assertEqual(legacy.DIAGNOSTIC_BASIS,'maintained-adi-consumption-v2')
        self.assertEqual(legacy.DIAGNOSTIC_REGISTRY,'2333ff8e03dea9fa4b8620e64e15ec76cb6a870a2d61c42dd057ddce0c13354f')

if __name__ == '__main__': unittest.main()
