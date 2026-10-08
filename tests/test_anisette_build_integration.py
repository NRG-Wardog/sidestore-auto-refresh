"""Bind checked normal OTP staging to the resolved sources and packaged binary."""
import copy
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import combined_build_evidence as collector
import verify_candidate_ipa as verifier
import patch_anisette_isolated_otp as native


class AnisetteBuildIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "DerivedData/SourcePackages/checkouts/AnisetteKit"
        self.output = self.root / "debug-evidence"
        self.manifest = self.root / "after-build.json"
        self.expected = native.expected_evidence()
        fixture = Path(native.__file__).resolve().parents[1] / "tests/fixtures/pinned_anisettekit"
        for name in native.PATHS:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(native.transform(name, (fixture / name).read_text()))
        self.manifest.write_text(json.dumps(self.expected, sort_keys=True) + "\n")
        self.executable = (b"linked C provider\0" + verifier.ANISETTE_COMPILED_MARKER + b"\0" +
                           verifier.ANISETTE_COMPILED_LITERAL + b"\0")

    def collect(self):
        # The native patch suite exercises the Git/CLI validator. Here its
        # success models the exact post-build boundary so copy races, artifact
        # substitution and binary omissions can be tested independently.
        with mock.patch.object(collector.subprocess, "run") as check:
            binding = collector.collect_isolated_anisette_evidence(
                self.source, self.manifest, self.output, self.executable)
        check.assert_called_once_with([sys.executable,
            str(ROOT / "scripts/patch_anisette_isolated_otp.py"), str(self.source), "--verify"],
            check=True, capture_output=True, text=True)
        return binding

    def test_maintained_collector_uses_read_only_exact_checkout_gate(self):
        pins = self.root / "maintained-pins.json"
        with mock.patch.object(collector.subprocess, "run") as check:
            binding = collector.collect_isolated_anisette_evidence(
                self.source, self.manifest, self.output, self.executable, pins)
        check.assert_called_once_with([sys.executable,
            str(ROOT / "scripts/maintained_sources.py"), "anisette", "--pins", str(pins),
            "--anisette-source", str(self.source)], check=True, capture_output=True, text=True)
        verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)

    def test_workflow_verifies_maintained_resolved_checkout_and_frozen_build(self):
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text()
        block = workflow.split("- name: Build embedded SideStore\n", 1)[1].split(
            "- name: Package and verify", 1)[0]
        resolved = block.index('test "$resolved" -eq 1')
        apply = block.index('maintained_sources.py anisette --anisette-source "$ANISETTE"')
        self.assertNotIn('patch_anisette_isolated_otp.py', block)
        before = block.index('anisette-isolated-otp-before-build.json')
        build = block.index('xcodebuild -project work/EmbeddedSideStore/AltStore.xcodeproj')
        after = block.index('anisette-isolated-otp-after-build.json')
        compare = block.index('cmp artifacts/dependencies/anisette-isolated-otp-before-build.json')
        self.assertEqual(sorted([resolved, apply, before, build, after, compare]),
                         [resolved, apply, before, build, after, compare])
        self.assertIn('-disableAutomaticPackageResolution', block[build:after])
        self.assertIn('-onlyUsePackageVersionsFromResolvedFile', block[build:after])
        self.assertNotIn('-resolvePackageDependencies', block[apply:])
        self.assertEqual(block.count('maintained_sources.py anisette --anisette-source "$ANISETTE"'), 2)
        self.assertIn('ANISETTE="$RUNNER_TEMP/embedded-sidestore-derived-data/SourcePackages/checkouts/AnisetteKit"', block)
        package = workflow.split('- name: Package and verify', 1)[1].split(
            '- name: Verify embedded CoreDevice', 1)[0]
        self.assertIn('--anisette-source "$RUNNER_TEMP/embedded-sidestore-derived-data/SourcePackages/checkouts/AnisetteKit"', package)
        self.assertIn('--anisette-manifest artifacts/dependencies/anisette-isolated-otp-after-build.json', package)
        self.assertIn('verify_candidate_ipa.py', package)

    def test_preflight_patterns_are_disjoint_strict_and_before_ui(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        focused = workflow.split('  signin-preflight:\n', 1)[1].split('  source-and-host-build:', 1)[0]
        patterns = ('test_p0_signin*.py', 'test_anisette*.py')
        for pattern in patterns:
            self.assertEqual(focused.count("--pattern '" + pattern + "'"), 1)
        self.assertLess(focused.index("--pattern 'test_anisette*.py'"),
                        focused.index('- name: Execute selected sign-in test lane'))
        self.assertEqual(focused.count('--allowlist builder/scripts/required_test_skip_allowlist.json'), 2)
        self.assertEqual(json.loads((ROOT / 'scripts/required_test_skip_allowlist.json').read_text()), [])
        self.assertIn('ref: ${{ env.LEGACY_EMBEDDED_SIDESTORE_REF }}', focused)
        self.assertIn('EMBEDDED_SIDESTORE_TEST_SOURCE: ${{ github.workspace }}/work/AnisettePreflightSideStore', focused)
        self.assertIn('rev-parse HEAD)" = "$LEGACY_EMBEDDED_SIDESTORE_REF"', focused)
        self.assertEqual(focused.count('artifacts/logs/signin-preflight-anisette-test-timings.json'), 2)
        self.assertIn('tee artifacts/logs/signin-preflight-anisette-tests.log', focused)
        names = [path.name for path in (ROOT / 'tests').glob('test_*.py')]
        selected = [{name for name in names if fnmatch.fnmatchcase(name, pattern)} for pattern in patterns]
        self.assertFalse(selected[0] & selected[1])
        self.assertIn('test_anisette_build_integration.py', selected[1])
        release = workflow.split('  source-and-host-build:', 1)[1]
        check = release.split('- name: Run repository checks and historical patch regressions', 1)[1].split('- name:', 1)[0]
        self.assertIn('--start-directory builder/tests', check)
        self.assertNotIn('--pattern', check)

    def test_exact_sources_manifest_and_binary_are_bound_without_changing_inputs(self):
        before = {name: (self.source / name).read_bytes() for name in native.PATHS}
        binding = self.collect()
        verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)
        self.assertEqual(binding['source_sha256'],
                         {entry['path']: entry['prepared_sha256'] for entry in self.expected['files']})
        self.assertEqual(binding['executable_sha256'], hashlib.sha256(self.executable).hexdigest())
        self.assertEqual(before, {name: (self.source / name).read_bytes() for name in native.PATHS})
        self.assertEqual((self.output / verifier.ANISETTE_EVIDENCE_DIRECTORY /
                          verifier.ANISETTE_EVIDENCE_MANIFEST).read_bytes(), self.manifest.read_bytes())

    def test_collector_provenance_contains_the_checked_native_source_binding(self):
        from test_combined_build_evidence import CandidateEvidenceTests
        ipa, output, _host, _side, argv, env_keys, env = CandidateEvidenceTests.prepare_collect_fixture(self.root)
        side_name = 'Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore'
        with zipfile.ZipFile(ipa) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members[side_name] += self.executable
        with zipfile.ZipFile(ipa, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        argv[argv.index('--side-source'):argv.index('--side-source')] = [
            '--anisette-source', str(self.source), '--anisette-manifest', str(self.manifest)]
        with mock.patch.object(collector.subprocess, 'run'):
            CandidateEvidenceTests.invoke_collect(argv, env_keys, env)
        provenance = json.loads((output / 'candidate-provenance.json').read_text())
        verifier.verify_isolated_anisette_evidence(output, provenance['isolated_anisette_otp'], members[side_name])
        self.assertEqual(provenance['raw_ipa_sha256'], hashlib.sha256(ipa.read_bytes()).hexdigest())

    def test_maintained_collector_records_actual_anisette_dependency(self):
        from test_combined_build_evidence import CandidateEvidenceTests, evidence as fixture_collector
        from maintained_sources import ENV_KEYS
        from maintained_package_evidence import LEGACY_HOST_MANIFESTS, LEGACY_EMBEDDED_MANIFESTS
        ipa, output, _host, _side, argv, env_keys, env = CandidateEvidenceTests.prepare_collect_fixture(self.root)
        side_name = 'Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore'
        with zipfile.ZipFile(ipa) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members[side_name] += self.executable
        with zipfile.ZipFile(ipa, 'w') as archive:
            for name, data in members.items(): archive.writestr(name, data)
        pins = json.loads((ROOT / 'migration/maintained-sources.json').read_bytes())
        for value in pins['owners'].values(): value['commit'] = value['source_checkpoint']
        pin_path = self.root / 'maintained-pins.json'
        pin_path.write_text(json.dumps(pins))
        for owner, key in ENV_KEYS.items(): env[key] = pins['owners'][owner]['commit']
        env_keys += ('ANISETTE_REF',)
        argv[argv.index('--side-source'):argv.index('--side-source')] = [
            '--anisette-source', str(self.source), '--anisette-manifest', str(self.manifest),
            '--maintained-runtime-pins', str(pin_path)]
        # This test isolates the Anisette binding. The maintained packaging
        # suite exercises real Git owners and rejects changed/missing inputs.
        sources = {name: (_host / name).read_bytes()
                   for name in set(collector.HOST_SOURCE_PATHS + collector.V3_HOST_SOURCE_PATHS)
                   - LEGACY_HOST_MANIFESTS}
        sources.update({'embedded/' + name: (_side / name).read_bytes()
                        for name in set(collector.EMBEDDED_SOURCE_PATHS) - LEGACY_EMBEDDED_MANIFESTS})
        with mock.patch.object(collector.subprocess, 'run'), \
                mock.patch.object(fixture_collector, 'read_pinned_sources', return_value=sources) as source_gate:
            CandidateEvidenceTests.invoke_collect(argv, env_keys, env)
        source_gate.assert_called_once()
        provenance = json.loads((output / 'candidate-provenance.json').read_text())
        self.assertEqual(provenance['dependencies']['ANISETTE_REF'], pins['owners']['AnisetteKit']['commit'])
        verifier.verify_source_evidence(output, provenance, 'v3.0.3-rc', pins)
        with mock.patch.dict(os.environ, {}, clear=True):
            verifier.verify_maintained_provenance(provenance, pins)

    def test_native_checkout_rejection_stops_collection_before_any_copy(self):
        for cause in ('wrong pinned revision', 'Anisette source drift'):
            with self.subTest(cause=cause), mock.patch.object(collector.subprocess, 'run',
                    side_effect=subprocess.CalledProcessError(1, 'native verify', stderr=cause)):
                with self.assertRaises(subprocess.CalledProcessError):
                    collector.collect_isolated_anisette_evidence(
                        self.source, self.manifest, self.output, self.executable)
            self.assertFalse(self.output.exists())

    def test_source_drift_between_native_verification_and_copy_is_rejected(self):
        target = self.source / native.PATHS[0]
        target.write_bytes(target.read_bytes() + b'\n// unexpected drift\n')
        with self.assertRaisesRegex(ValueError, 'changed after verification'):
            self.collect()

    def test_rehashed_manifest_cannot_bless_wrong_pin_or_changed_transform(self):
        binding = self.collect()
        path = self.output / verifier.ANISETTE_EVIDENCE_DIRECTORY / verifier.ANISETTE_EVIDENCE_MANIFEST
        for field, value in (('anisettekit_revision', 'a' * 40),
                             ('native_symbol', 'other_symbol'), ('marker', 'other_marker')):
            changed = copy.deepcopy(self.expected)
            changed[field] = value
            data = json.dumps(changed).encode()
            path.write_bytes(data)
            binding['manifest_sha256'] = hashlib.sha256(data).hexdigest()
            with self.assertRaisesRegex(ValueError, 'pinned transformation'):
                verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)

    def test_copied_source_drift_and_rehashed_source_binding_fail_closed(self):
        binding = self.collect()
        name = native.PATHS[0]
        path = self.output / verifier.ANISETTE_EVIDENCE_DIRECTORY / name
        path.write_bytes(path.read_bytes() + b'\n// drift\n')
        with self.assertRaisesRegex(ValueError, 'prepared source hash mismatch'):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)
        binding['source_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'pinned transformation'):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)

    def test_missing_extra_and_linked_source_evidence_is_rejected(self):
        binding = self.collect()
        directory = self.output / verifier.ANISETTE_EVIDENCE_DIRECTORY
        extra = directory / 'stale.swift'
        extra.write_text('// stale')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)
        extra.unlink()
        path = directory / native.PATHS[0]
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)
        path.symlink_to(self.source / native.PATHS[0])
        with self.assertRaisesRegex(ValueError, 'contains a link'):
            verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)

    def test_absent_compiled_api_and_other_executable_binding_fail_closed(self):
        binding = self.collect()
        for absent in (b'', verifier.ANISETTE_COMPILED_MARKER,
                       verifier.ANISETTE_COMPILED_LITERAL,
                       b'V3_ISOLATED_ANISETTE_OTP_V1\0Isolated OTP staging failed\0'):
            with self.subTest(absent=absent), self.assertRaisesRegex(ValueError, 'compiled checked'):
                verifier.verify_isolated_anisette_evidence(self.output, binding, absent)
        with self.assertRaisesRegex(ValueError, 'another executable'):
            verifier.verify_isolated_anisette_evidence(self.output, binding,
                                                       self.executable + b'changed')
        with self.assertRaisesRegex(ValueError, 'binding is missing'):
            verifier.verify_isolated_anisette_evidence(self.output, None, self.executable)

    def test_active_staging_gate_accepts_binary_without_dormant_isolated_code(self):
        self.assertEqual(verifier.ANISETTE_COMPILED_MARKER, b'V3_CHECKED_ANISETTE_STAGING_V1')
        self.assertEqual(verifier.ANISETTE_COMPILED_LITERAL, b'Checked OTP staging failed')
        self.assertNotIn(b'V3_ISOLATED_ANISETTE_OTP_V1', self.executable)
        self.assertNotIn(b'get_anisette_headers_isolated_uc', self.executable)
        binding = self.collect()
        verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)

    def test_old_manifest_cannot_certify_the_new_active_binary_gate(self):
        binding = self.collect()
        old = copy.deepcopy(self.expected)
        old['marker'] = 'V3_ISOLATED_ANISETTE_OTP_V1'
        old['compiled_literal'] = 'Isolated OTP staging failed'
        with mock.patch.object(native, 'expected_evidence', return_value=old):
            with self.assertRaisesRegex(ValueError, 'active compiled staging gate disagree'):
                verifier.verify_isolated_anisette_evidence(self.output, binding, self.executable)


if __name__ == '__main__':
    unittest.main()
