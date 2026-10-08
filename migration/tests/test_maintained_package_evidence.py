"""Exercise the real collector/verifier input path before an expensive native build."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import combined_build_evidence as collector
import maintained_package_evidence as maintained
import maintained_sources
import test_combined_build_evidence as legacy_fixture
from verify_candidate_ipa import verify_source_evidence


class MaintainedPackageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / 'work/LiveContainer'
        self.side = self.root / 'work/EmbeddedSideStore'
        self.output = self.root / 'evidence'
        self.pins = maintained_sources.load_pins(ROOT / 'migration/maintained-sources.json')
        self.host_names = (set(collector.HOST_SOURCE_PATHS + collector.V3_HOST_SOURCE_PATHS)
                           - maintained.LEGACY_HOST_MANIFESTS)
        self.side_names = set(collector.EMBEDDED_SOURCE_PATHS) - maintained.LEGACY_EMBEDDED_MANIFESTS
        for base, names in ((self.host, self.host_names), (self.side, self.side_names)):
            for name in names:
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('synthetic pinned source ' + name + '\n')
        # Separate real Git owners, including the actual nested package paths.
        for owner, root in [('SideSign', self.side / 'Dependencies/SideSign'),
                            ('minimuxer', self.side / 'Dependencies/minimuxer'),
                            ('LiveContainer', self.host), ('SideStore', self.side)]:
            self.git(root, 'init', '-q')
            self.git(root, 'config', 'user.name', 'Packaging Fixture')
            self.git(root, 'config', 'user.email', 'fixture@example.invalid')
            self.git(root, 'add', '.')
            self.git(root, 'commit', '-qm', 'Synthetic immutable fixture')
            commit = self.git(root, 'rev-parse', 'HEAD').strip()
            self.pins['owners'][owner]['commit'] = commit
            self.pins['owners'][owner]['source_checkpoint'] = commit
        self.pins['native_validation']['tested_children']['SideStore']['Dependencies/minimuxer'] = (
            self.pins['owners']['minimuxer']['commit'])
        self.pin_path = self.root / 'pins.json'
        self.pin_path.write_text(json.dumps(self.pins))

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.PIPE)

    def collect(self):
        hashes, contracts = collector.collect_source_evidence(
            self.host, self.side, self.output, 'v3.0.3-rc', self.pins)
        return {'generated_source_sha256': hashes, 'source_evidence_kind': maintained.EVIDENCE_KIND,
                'maintained_source_contract_sha256': contracts}

    def verify(self, evidence):
        verify_source_evidence(self.output, evidence, 'v3.0.3-rc', self.pins)

    def test_real_collection_and_verification_need_no_legacy_patch_receipts(self):
        evidence = self.collect()
        self.verify(evidence)
        self.assertEqual(set(evidence['generated_source_sha256']),
                         self.host_names | {'embedded/' + name for name in self.side_names})
        self.assertEqual(len(evidence['maintained_source_contract_sha256']), 9)
        for name in maintained.LEGACY_HOST_MANIFESTS:
            self.assertFalse((self.host / name).exists())
            self.assertFalse((self.output / 'generated' / name).exists())
        self.assertFalse((self.output / 'embedded-generated/.combined-refresh-contract.json').exists())

    def test_cli_preflight_runs_without_ipa_build_products_or_github_identity(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/combined_build_evidence.py'),
            'preflight', '--product', 'v3.0.3-rc', '--source', str(self.host),
            '--side-source', str(self.side), '--maintained-runtime-pins', str(self.pin_path)],
            env={key: value for key, value in os.environ.items() if not key.startswith('GITHUB_')},
            check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['source_files'], len(self.host_names) + len(self.side_names))
        self.assertEqual(report['maintained_runtime_sources'], self.pins)
        self.assertFalse(self.output.exists())

    def test_full_collector_uses_the_same_inventory_and_contract_binding(self):
        build = self.root / 'build'
        build.mkdir()
        ipa, output, _, _, argv, _, env = legacy_fixture.CandidateEvidenceTests.prepare_collect_fixture(build)
        argv[argv.index('--source') + 1] = str(self.host)
        argv[argv.index('--side-source') + 1] = str(self.side)
        argv[2:2] = ['--maintained-runtime-pins', str(self.pin_path)]
        env.update({key: self.pins['owners'][owner]['commit']
                    for owner, key in maintained_sources.ENV_KEYS.items()})
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, 'argv', argv):
            collector.main()
        provenance = json.loads((output / 'candidate-provenance.json').read_bytes())
        verify_source_evidence(output, provenance, 'v3.0.3-rc', self.pins)
        self.assertEqual(provenance['maintained_runtime_sources'], self.pins)
        self.assertEqual(provenance['raw_ipa_sha256'], hashlib.sha256(ipa.read_bytes()).hexdigest())
        self.assertEqual(provenance['generated_source_sha256'], self.collect()['generated_source_sha256'])

    def test_missing_real_source_fails_in_preflight(self):
        (self.host / 'SideStoreSupport/SideStore.swift').unlink()
        with self.assertRaisesRegex(ValueError, 'missing maintained evidence file'):
            self.collect()

    def test_changed_source_cannot_pass_by_rehashing_its_output(self):
        (self.host / 'SideStoreSupport/SideStore.swift').write_text('changed compiler input')
        with self.assertRaisesRegex(ValueError, 'differs from pinned blob'):
            self.collect()

    def test_wrong_child_commit_fails_in_preflight(self):
        self.pins['owners']['SideSign']['commit'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'source commit mismatch: SideSign'):
            self.collect()

    def test_linked_compiler_input_fails_in_preflight(self):
        path = self.host / 'SideStoreSupport/SideStore.swift'
        copy = self.root / 'source-copy.swift'
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
        with self.assertRaisesRegex(ValueError, 'linked maintained evidence path'):
            self.collect()

    def test_missing_and_mismatched_copied_source_still_fail(self):
        evidence = self.collect()
        path = self.output / 'generated/SideStoreSupport/SideStore.swift'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'source evidence file is missing'):
            self.verify(evidence)
        path.write_bytes(original + b'changed')
        with self.assertRaisesRegex(ValueError, 'source evidence hash mismatch'):
            self.verify(evidence)

    def test_missing_and_extra_source_inventory_still_fail(self):
        evidence = self.collect()
        missing = copy.deepcopy(evidence)
        missing['generated_source_sha256'].pop('SideStoreSupport/SideStore.swift')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            self.verify(missing)
        extra = self.output / 'generated/.lc-app-layout.json'
        extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'files do not match'):
            self.verify(evidence)
        evidence['generated_source_sha256']['.lc-app-layout.json'] = hashlib.sha256(extra.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            self.verify(evidence)

    def test_legacy_mode_does_not_accept_maintained_inventory(self):
        evidence = self.collect()
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            verify_source_evidence(self.output, evidence, 'v3.0.3-rc')

    def test_missing_or_wrong_evidence_kind_is_rejected(self):
        evidence = self.collect()
        for kind in (None, 'generated-patches'):
            evidence['source_evidence_kind'] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'source evidence kind'):
                self.verify(evidence)

    def test_missing_manifest_is_rejected(self):
        evidence = self.collect()
        (self.output / maintained.CONTRACT_DIRECTORY / 'owners/LiveContainer/runtime-contract.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing maintained evidence file'):
            self.verify(evidence)

    def test_missing_or_changed_authoritative_metadata_fails_preflight(self):
        alternate = self.root / 'builder'
        files = maintained.contract_files(self.pins)
        for name, data in files.items():
            target = alternate / 'migration/contracts' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        name = 'owners/SideStore/runtime-contract.json'
        target = alternate / 'migration/contracts' / name
        with mock.patch.object(maintained, 'ROOT', alternate):
            target.unlink()
            with self.assertRaisesRegex(ValueError, 'missing maintained evidence file'):
                self.collect()
            target.write_bytes(files[name] + b'\n')
            with self.assertRaisesRegex(ValueError, 'contract metadata hash mismatch'):
                self.collect()

    def test_rehashed_manifest_cannot_bless_changed_contract(self):
        evidence = self.collect()
        name = 'owners/LiveContainer/runtime-contract.json'
        path = self.output / maintained.CONTRACT_DIRECTORY / name
        path.write_bytes(path.read_bytes() + b'\n')
        evidence['maintained_source_contract_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'contract metadata hash mismatch'):
            self.verify(evidence)

    def test_changed_registry_cannot_self_authorize(self):
        evidence = self.collect()
        name = 'compatibility-registry.json'
        path = self.output / maintained.CONTRACT_DIRECTORY / name
        path.write_bytes(path.read_bytes() + b'\n')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        evidence['maintained_source_contract_sha256'][name] = digest
        with self.assertRaisesRegex(ValueError, 'contract registry hash mismatch'):
            self.verify(evidence)
        self.pins['contract_registry_sha256'] = digest
        with self.assertRaisesRegex(ValueError, 'unapproved package contract registry'):
            self.verify(evidence)

    def test_missing_hash_extra_file_and_linked_contract_are_rejected(self):
        evidence = self.collect()
        missing = copy.deepcopy(evidence)
        missing['maintained_source_contract_sha256'].pop('compatibility-registry.json')
        with self.assertRaisesRegex(ValueError, 'hash inventory mismatch'):
            self.verify(missing)
        extra = self.output / maintained.CONTRACT_DIRECTORY / 'extra.json'
        extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'file inventory mismatch'):
            self.verify(evidence)
        extra.unlink()
        extra.symlink_to(self.output / maintained.CONTRACT_DIRECTORY / 'compatibility-registry.json')
        with self.assertRaisesRegex(ValueError, 'linked maintained contract evidence'):
            self.verify(evidence)

    def test_workflow_preflight_precedes_every_expensive_stage(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        release = workflow.split('  source-and-host-build:', 1)[1]
        preflight = release.index('combined_build_evidence.py preflight')
        self.assertLess(release.index('maintained_sources.py acquire'), preflight)
        for stage in ('Acquire immutable historical regression fixtures', 'Select Xcode',
                      'Run repository checks', 'Build unified host', 'Build idevice', 'Build embedded SideStore'):
            self.assertLess(preflight, release.index(stage), stage)


if __name__ == '__main__':
    unittest.main()
