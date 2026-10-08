"""Adversarial acquisition, compiler-input and no-rewrite cutover regression tests."""
import copy
import hashlib
import importlib.util
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
import maintained_sources as gate


class PinPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pins.json'
        self.pins = json.loads((ROOT / 'migration/maintained-sources.json').read_bytes())

    def write(self):
        self.path.write_text(json.dumps(self.pins))

    def complete(self):
        for value in self.pins['owners'].values():
            value['commit'] = value['source_checkpoint']

    def test_pending_production_inputs_fail_before_acquisition(self):
        self.write()
        with mock.patch.object(gate, 'acquire') as acquisition:
            with mock.patch.object(sys, 'argv', ['maintained_sources.py', 'acquire', '--pins', str(self.path)]):
                with self.assertRaisesRegex(ValueError, 'final published commit is required'):
                    gate.main()
        acquisition.assert_not_called()

    def test_complete_exact_policy_is_accepted(self):
        self.complete(); self.write()
        self.assertEqual(gate.load_pins(self.path), self.pins)

    def test_floating_missing_and_fake_pending_values_are_rejected(self):
        self.complete()
        for value in ('main', 'migration/runtime-source-141776ba', 'abc123', '', None, 'PENDING'):
            self.pins['owners']['SideSign']['commit'] = value
            self.write()
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.load_pins(self.path)

    def test_owner_repository_and_contract_substitution_rejected(self):
        self.complete()
        for field, value in [('repository', 'https://github.com/mahee96/AnisetteKit.git'), ('path', '../../outside')]:
            bad = copy.deepcopy(self.pins)
            bad['owners']['AnisetteKit'][field] = value
            self.path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): gate.load_pins(self.path)
        self.pins['contract_registry_sha256'] = 'a' * 64
        self.write()
        with self.assertRaises(ValueError): gate.load_pins(self.path)

    def test_missing_extra_and_duplicate_owners_fail(self):
        self.complete()
        bad = copy.deepcopy(self.pins)
        del bad['owners']['minimuxer']
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError): gate.load_pins(self.path)
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate'): gate.load_pins(self.path)


class GitInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'owner'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'source.swift').write_text('let approved = true\n')
        (self.root / '.gitignore').write_text('*.hidden\ntarget/\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True, stderr=subprocess.PIPE)

    def verify(self, **kwargs):
        return gate.verify_checkout(self.root, self.commit, **kwargs)

    def test_exact_source_tree_passes_without_changing_any_bytes(self):
        before = (self.root / 'source.swift').read_bytes()
        self.assertEqual(self.verify()['commit'], self.commit)
        self.assertEqual(before, (self.root / 'source.swift').read_bytes())
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_canonical_symlink_root_is_a_legitimate_checkout(self):
        alias = self.root.parent / 'symlink-root'
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(gate.verify_checkout(alias, self.commit)['commit'], self.commit)

    def test_core_worktree_cannot_hide_untracked_compiler_input(self):
        redirected = self.root.parent / 'redirected-worktree'
        redirected.mkdir()
        (self.root / 'injected.swift').write_text('unapproved compiler input')
        self.git('config', 'core.worktree', str(redirected))
        with self.assertRaisesRegex(ValueError, 'untracked input'): self.verify()

    def test_linked_worktree_gitfile_is_supported(self):
        linked = self.root.parent / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), self.commit)
        self.assertTrue((linked / '.git').is_file())
        self.assertEqual(gate.verify_checkout(linked, self.commit)['commit'], self.commit)

    def test_shallow_owner_is_rejected_even_at_checkpoint(self):
        shallow = self.root.parent / 'shallow'
        subprocess.run(['git', 'clone', '--depth=1', self.root.as_uri(), str(shallow)],
                       check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'shallow owner history'):
            gate.verify_checkout(shallow, self.commit, require_full_history=True)

    def test_genuine_submodule_gitfile_is_supported(self):
        parent = self.root.parent / 'parent'
        parent.mkdir()
        subprocess.run(['git', '-C', str(parent), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(parent), '-c', 'protocol.file.allow=always',
                        'submodule', 'add', str(self.root), 'child'], check=True, capture_output=True)
        child = parent / 'child'
        self.assertTrue((child / '.git').is_file())
        self.assertEqual(gate.verify_checkout(child, self.commit, require_full_history=True)['commit'], self.commit)

    def test_wrong_commit_and_mode_fail(self):
        with self.assertRaisesRegex(ValueError, 'wrong commit'):
            gate.verify_checkout(self.root, '0' * 40)
        (self.root / 'source.swift').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'mode drift'): self.verify()

    def test_assume_unchanged_cannot_hide_source_mutation(self):
        self.git('update-index', '--assume-unchanged', 'source.swift')
        (self.root / 'source.swift').write_text('let approved = false\n')
        self.assertEqual(self.git('status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, 'compiler input differs'): self.verify()

    def test_untracked_and_ignored_sources_fail(self):
        for name in ('injected.swift', 'injected.hidden'):
            path = self.root / name
            path.write_text('unapproved')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'untracked input'): self.verify()
            path.unlink()

    def test_only_explicit_build_outputs_are_tolerated_after_build(self):
        (self.root / 'target').mkdir()
        (self.root / 'target/lib.a').write_bytes(b'build product')
        with self.assertRaisesRegex(ValueError, 'untracked input'): self.verify(owner='idevice')
        self.verify(owner='idevice', after_build=True)
        (self.root / 'injected.swift').write_text('unapproved')
        with self.assertRaisesRegex(ValueError, 'untracked input'): self.verify(owner='idevice', after_build=True)

    def test_declared_sidebackup_outputs_are_allowed_only_after_build(self):
        archive = self.root / 'build/sidebackup.xcarchive/Payload/SideBackup.app/SideBackup'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'synthetic build product')
        (self.root / 'build/SideBackup.ipa').write_bytes(b'synthetic packaged product')
        with self.assertRaisesRegex(ValueError, 'untracked input'): self.verify(owner='SideStore')
        self.verify(owner='SideStore', after_build=True)

    def test_sidebackup_does_not_allow_other_build_or_swiftpm_sources(self):
        for name in ('build/injected.swift', '.swiftpm/injected.swift', 'build/SideBackup.ipa.swift'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('unapproved compiler input')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'untracked input'):
                self.verify(owner='SideStore', after_build=True)
            path.unlink()

    def test_sidebackup_allowance_never_exempts_tracked_source(self):
        (self.root / 'source.swift').write_text('unapproved tracked source')
        with self.assertRaisesRegex(ValueError, 'compiler input differs'):
            self.verify(owner='SideStore', after_build=True)

    def test_directory_substitution_fails(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'source.swift').unlink()
        (outside / 'source.swift').write_text('let approved = true\n')
        (self.root / 'source.swift').symlink_to(outside / 'source.swift')
        with self.assertRaisesRegex(ValueError, 'regular file'): self.verify()

    def test_missing_child_repository_cannot_resolve_to_parent(self):
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.commit + ',child')
        self.git('commit', '-qm', 'Child')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'child').mkdir()
        with self.assertRaisesRegex(ValueError, 'missing actual checkout'): self.verify()

    def test_staged_changes_are_rejected_even_when_working_bytes_restored(self):
        file = self.root / 'source.swift'
        file.write_text('let staged = true\n')
        self.git('add', 'source.swift')
        file.write_text('let approved = true\n')
        with self.assertRaisesRegex(ValueError, 'index changed'): self.verify()


class BuildAttributionTests(unittest.TestCase):
    def setUp(self):
        import plistlib
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.roots = {name: self.base / name for name in gate.OWNERS}
        self.framework = self.roots['minimuxer'] / 'DeviceGateway/LocalBinary/IDevice.xcframework'
        self.info = {'AvailableLibraries': [{'LibraryIdentifier': 'ios-arm64', 'LibraryPath': 'libidevice_ffi.a',
            'HeadersPath': 'Headers', 'SupportedPlatform': 'ios', 'SupportedArchitectures': ['arm64']}]}
        self.write(self.framework / 'Info.plist', plistlib.dumps(self.info))
        self.write(self.framework / 'ios-arm64/libidevice_ffi.a', b'!<arch> synthetic fixture')
        self.write(self.roots['idevice'] / 'target/aarch64-apple-ios/release/libidevice_ffi.a', b'!<arch> synthetic fixture')
        for path in ('ffi/idevice.h', 'cpp/include/idevice.h', 'swift/include/idevice.h'):
            self.write(self.roots['idevice'] / path, b'void fixture(void);')
        self.write(self.framework / 'ios-arm64/Headers/idevice.h', b'void fixture(void);')
        self.write(self.framework / 'ios-arm64/Headers/module.modulemap', b'module fixture {}')
        self.write(self.roots['idevice'] / 'swift/include/module.modulemap', b'module fixture {}')

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_exact_archive_header_and_module_staging_pass(self):
        report = gate.verify_build_products(self.roots)
        self.assertEqual(report['library_sha256'], hashlib.sha256(b'!<arch> synthetic fixture').hexdigest())

    def test_different_archive_is_rejected(self):
        self.write(self.framework / 'ios-arm64/libidevice_ffi.a', b'wrong archive')
        with self.assertRaisesRegex(ValueError, 'differs from built Rust'): gate.verify_build_products(self.roots)

    def test_different_generated_header_is_rejected(self):
        self.write(self.roots['idevice'] / 'cpp/include/idevice.h', b'wrong header')
        with self.assertRaisesRegex(ValueError, 'header mismatch'): gate.verify_build_products(self.roots)

    def test_wrong_platform_and_escaped_slice_are_rejected(self):
        import plistlib
        self.info['AvailableLibraries'][0]['SupportedPlatform'] = 'macos'
        self.write(self.framework / 'Info.plist', plistlib.dumps(self.info))
        with self.assertRaisesRegex(ValueError, 'platform/architecture'): gate.verify_build_products(self.roots)
        self.info['AvailableLibraries'][0]['SupportedPlatform'] = 'ios'
        self.info['AvailableLibraries'][0]['LibraryPath'] = '../../../../elsewhere.a'
        self.write(self.framework / 'Info.plist', plistlib.dumps(self.info))
        with self.assertRaisesRegex(ValueError, 'escaped/missing'): gate.verify_build_products(self.roots)


class ResolverAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.roots = {name: self.base / name for name in gate.OWNERS}
        self.root = self.base / 'SourcePackages/checkouts/AnisetteKit'
        self.root.mkdir(parents=True)
        for command in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.invalid')):
            subprocess.run(['git', '-C', str(self.root), *command], check=True)
        (self.root / 'source.cpp').write_text('void fixture() {}')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'Fixture'], check=True)
        self.commit = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        self.lock = {'pins': [{'identity':'anisettekit', 'kind':'remoteSourceControl',
            'location':'https://github.com/NRG-Wardog/AnisetteKit.git', 'state':{'revision':self.commit}}], 'version':3}
        lock = self.roots['SideStore'] / gate.APP_LOCK
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps(self.lock))
        local = {'sidesign':self.roots['SideSign'], 'minimuxer':self.roots['minimuxer'],
            'common':self.roots['minimuxer'] / 'Common', 'devicegateway':self.roots['minimuxer'] / 'DeviceGateway'}
        self.deps = [{'packageRef':{'identity':k, 'kind':'fileSystem', 'location':str(v)}} for k,v in local.items()]
        self.deps.append({'packageRef':{'identity':'anisettekit', 'kind':'remoteSourceControl',
            'location':'https://github.com/NRG-Wardog/AnisetteKit.git'}, 'subpath':'AnisetteKit',
            'state':{'name':'sourceControlCheckout', 'checkoutState':{'revision':self.commit}}})
        self.state = self.base / 'SourcePackages/workspace-state.json'

    def verify(self):
        self.state.write_text(json.dumps({'object':{'dependencies':self.deps}}))
        return gate.verify_resolution(self.roots, self.root)

    def test_complete_effective_graph_passes(self):
        self.assertEqual(self.verify()['anisettekit']['commit'], self.commit)

    def test_remote_substitution_and_floating_revision_fail(self):
        self.deps[-1]['packageRef']['location'] = 'https://github.com/other/AnisetteKit.git'
        with self.assertRaisesRegex(ValueError, 'remote pin mismatch'): self.verify()

    def test_wrong_local_package_and_missing_package_fail(self):
        self.deps[0]['packageRef']['location'] = str(self.base / 'other')
        with self.assertRaisesRegex(ValueError, 'wrong local package'): self.verify()
        self.deps.pop(0)
        with self.assertRaisesRegex(ValueError, 'omitted expected'): self.verify()

    def test_escaped_remote_checkout_fails(self):
        self.deps[-1]['subpath'] = '../../elsewhere'
        with self.assertRaisesRegex(ValueError, 'escaped remote'): self.verify()

    def test_dirty_actual_remote_fails(self):
        (self.root / 'source.cpp').write_text('void drift() {}')
        with self.assertRaisesRegex(ValueError, 'compiler input differs'): self.verify()


class MaintainedArtifactProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.pins = json.loads((ROOT / 'migration/maintained-sources.json').read_bytes())
        for value in self.pins['owners'].values():
            value['commit'] = value['source_checkpoint']
        self.evidence = {'maintained_runtime_sources':copy.deepcopy(self.pins), 'dependencies':{
            key:self.pins['owners'][owner]['commit'] for owner,key in gate.ENV_KEYS.items()}}

    def verify(self, pins=True):
        from verify_candidate_ipa import verify_maintained_provenance
        with mock.patch.dict(os.environ, {}, clear=True):
            verify_maintained_provenance(self.evidence, self.pins if pins else None)

    def test_all_seven_actual_dependency_revisions_pass(self):
        self.verify()

    def test_missing_actual_anisette_revision_fails(self):
        del self.evidence['dependencies']['ANISETTE_REF']
        with self.assertRaisesRegex(ValueError, 'AnisetteKit'): self.verify()

    def test_old_upstream_or_wrong_actual_anisette_revision_fails(self):
        self.evidence['dependencies']['ANISETTE_REF'] = '1f5a7e36553cc865b873f222b87a6486c0bcc7bf'
        with self.assertRaisesRegex(ValueError, 'AnisetteKit'): self.verify()

    def test_rehashed_intended_map_cannot_bless_actual_owner_drift(self):
        self.evidence['maintained_runtime_sources']['owners']['AnisetteKit']['commit'] = 'a' * 40
        self.evidence['dependencies']['ANISETTE_REF'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'differs from approved'): self.verify()

    def test_maintained_evidence_cannot_enter_legacy_verification(self):
        with self.assertRaisesRegex(ValueError, 'independently approved'): self.verify(pins=False)

    def test_legacy_evidence_keeps_legacy_compatibility(self):
        del self.evidence['maintained_runtime_sources']
        del self.evidence['dependencies']['ANISETTE_REF']
        self.verify(pins=False)


class WorkflowTests(unittest.TestCase):
    def test_builder_acquires_baseline_history_before_required_tests(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        release = workflow.split('  source-and-host-build:', 1)[1]
        checkout = release.split('      - uses: actions/checkout@v4', 1)[1].split('      - name:', 1)[0]
        self.assertIn('path: builder', checkout)
        self.assertIn('fetch-depth: 0', checkout)
        self.assertLess(release.index('fetch-depth: 0'), release.index('run_required_tests.py'))

    def test_archived_workflow_is_exact_baseline(self):
        old = subprocess.check_output(['git', '-C', str(ROOT), 'show', gate.BASELINE + ':.github/workflows/livecontainer-build.yml'])
        self.assertEqual((ROOT / 'migration/historical/livecontainer-build-141776ba.yml').read_bytes(), old)

    def test_release_path_has_no_runtime_patch_invocations(self):
        run = (ROOT / '.github/workflows/livecontainer-build.yml').read_text().split('  source-and-host-build:', 1)[1]
        mutations = [line for line in run.splitlines() if 'builder/scripts/patch_' in line]
        self.assertEqual(len(mutations), 1)
        self.assertIn('patch_combined_refresh_contract.py --verify-ipa ', mutations[0])
        self.assertNotIn('cargo fmt --manifest-path', run)
        self.assertNotIn('.package(name: "AnisetteKit", path:', run)
        self.assertLess(run.index('maintained_sources.py env'), run.index('maintained_sources.py acquire'))
        self.assertIn('maintained-sources-before-native-build.json', run)
        self.assertIn('maintained-sources-after-native-build.json', run)
        for command in ('verify_candidate_ipa.py', 'run_issue25_rendering.py',
                        'combined_build_evidence.py collect', 'package_livecontainer_combined.py'):
            self.assertIn(command, run)

    def test_registry_bytes_are_reviewed_anchor(self):
        actual = hashlib.sha256((ROOT / 'migration/contracts/compatibility-registry.json').read_bytes()).hexdigest()
        self.assertEqual(actual, gate.REGISTRY)


if __name__ == '__main__':
    unittest.main()
