"""Synthetic package orchestration controls; genuine boundary capture, no native qualification."""
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
import zipfile
import owned_oda_package_admission as a

HERE = Path(__file__).resolve().parent


class PackageControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.trusted = self.root / 'trusted'; self.trusted.mkdir()
        shutil.copytree(HERE / 'reviewed-native', self.trusted / 'reviewed-native')
        shutil.copyfile(HERE / 'owned-oda-source-contract.json', self.trusted / 'owned-oda-source-contract.json')
        gate = patch.object(a, 'HERE', self.trusted); gate.start(); self.addCleanup(gate.stop)
        contract, _, graph, _, oda = a.trusted(); self.oda = oda
        self.manifest = {'commit': contract['native_builder'], 'run_id': 123, 'run_attempt': 2}
        self.native = {'workflow_commit': self.manifest['commit'], 'run_id': '123', 'run_attempt': '2',
                       'status': 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'requested_graph': graph['owners'],
                       'source_owned_oda_qualification_executed': True, 'physical_device_execution': False,
                       'authentication_executed': False, 'live_authentication_executed': False}
        capture = (HERE / 'fixtures/actual-macos-boundary-25fc5b5d.json').read_bytes()
        self.assertEqual(a.sha(capture), '2718f227d8647520bba7b284986fa92d6c6284c2c7d225bcd87f94b6f160c156')
        self.raw = {a.PREFIX + 'input_boundary.log': capture,
                    a.PREFIX + 'native.log': b'PASS: control, filesystem, pinned-header syntax, actual C ABI with mock engine\nPASS: pinned real Unicorn synthetic ARM64 procedures\n',
                    a.PREFIX + 'historical_engine.log': json.dumps({'schema': 'owned-oda-historical-engine-v1', 'source': oda.source_identity(graph),
                        'unicorn_archive_sha256': oda.UNICORN_SHA, 'engine_library_sha256_observed': 'd'*64,
                        'fixture_sha256': {'android_tls': 'e'*64, 'constructor_diagnostics': 'f'*64},
                        'checks': {'android_tls': 83, 'constructor_diagnostics': 2105}, 'ADI_executed': False,
                        'device_validation': False, 'all_passed': True}).encode(),
                    a.PREFIX + 'legacy.log': b'Ran 9 tests in 0.1s\nOK\n', a.PREFIX + 'logging.log': b'Ran 13 tests in 0.1s\nOK\n',
                    a.PREFIX + 'swift.log': ''.join("Test Case '-[AnisetteKitTests.OwnedOnDeviceAnisetteAttemptTests " + name + "]' passed (0.01 seconds).\n" for name in oda.SWIFT_TESTS).encode(),
                    a.PREFIX + 'startup.log': b'synthetic startup orchestration control\n',
                    a.STARTUP: json.dumps({'passed': True, 'unicorn_sha256': oda.UNICORN_SHA,
                        'verified_inputs': {'recognized_libraries': oda.LIBRARIES, 'archive_sha256_observed': 'c'*64},
                        'commands': [{'returncode': 0, 'stdout': '', 'stderr': ''}, {'returncode': 0, 'stdout': '', 'stderr': ''},
                            {'returncode': 0, 'stdout': json.dumps(dict.fromkeys(oda.STARTUP_FLAGS, 1)), 'stderr': ''}]}).encode()}
        self.receipt = {'schema': 'owned-oda-candidate-v1', 'source': oda.source_identity(graph), 'all_passed': True,
                        'device_or_authentication_proven': False, 'unicorn_sha256': oda.UNICORN_SHA,
                        'input_boundary_source_inputs': copy.deepcopy(oda.INPUT_BOUNDARY_FILES), 'suites': {}}
        self.reseal(); self.write_archive()

    def reseal(self):
        self.receipt['suites'] = {name: {'status': 'PASS', 'returncode': 0, 'log_sha256': a.sha(self.raw[a.PREFIX + name + '.log'])} for name in a.SUITES}
        self.receipt['startup_receipt_sha256'] = a.sha(self.raw[a.STARTUP])
        self.raw[a.RECEIPT] = json.dumps(self.receipt).encode()
        self.native['owned_oda_qualification_receipt_sha256'] = a.sha(self.raw[a.RECEIPT])

    def write_archive(self, *, missing=(), duplicate=None, bad_mode=None, extra=None):
        self.native_raw = json.dumps(self.native).encode(); (self.workspace / 'native-receipt.json').write_bytes(self.native_raw)
        self.archive = self.root / 'native.zip'
        rows = {'receipt.json': self.native_raw, **self.raw, 'run_owned_oda.py': b'raise RuntimeError("artifact code must never execute")'}
        if extra: rows.update(extra)
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, raw in rows.items():
                if name in missing: continue
                info = zipfile.ZipInfo(name); info.create_system = 3
                info.external_attr = (0o120777 if name == bad_mode else 0o100644) << 16
                z.writestr(info, raw)
            if duplicate:
                info = zipfile.ZipInfo(duplicate); info.create_system = 3; info.external_attr = 0o100644 << 16
                z.writestr(info, rows[duplicate])
        self.expected = {'artifact_id': 3, 'bytes': self.archive.stat().st_size, 'sha256': a.sha(self.archive.read_bytes())}

    def project(self):
        return a.project(self.archive, self.workspace, self.manifest, self.expected)

    def verify(self):
        return a.verify(self.workspace, self.manifest, self.expected)

    def test_roundtrip_fixed_nine_files_with_real_validator_and_no_subprocess(self):
        before = self.archive.read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('No subprocess allowed')), patch('subprocess.check_output', side_effect=AssertionError('No Git allowed')):
            result = self.project(); self.assertEqual(result, self.verify())
        self.assertFalse(result['device_or_authentication_proven']); self.assertFalse(result['native_execution_by_adapter'])
        with zipfile.ZipFile(self.workspace / a.CAPSULE) as z: self.assertEqual(set(z.namelist()), set(a.FILES))
        self.assertEqual(len(result['reference_sha256']), 9)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_ambient_import_cannot_replace_sealed_graph_validator(self):
        poison = types.ModuleType('verify_candidate_graph')
        poison.load_candidate_graph = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unreviewed import'))
        with patch.dict(sys.modules, {'verify_candidate_graph': poison}):
            self.project()
            self.assertIs(sys.modules['verify_candidate_graph'], poison)

    def test_exact_source_import_closure_rejects_all_snapshot_tampering(self):
        for name in a.TRUSTED_FILES:
            path = self.trusted / 'reviewed-native' / name; original = path.read_bytes(); path.write_bytes(original + b'\n')
            with self.subTest(name=name), self.assertRaises(ValueError): self.project()
            path.write_bytes(original)

    def test_manifest_builder_and_run_types_are_strict(self):
        for field, bad in [('commit', 'f'*40), ('run_id', True), ('run_attempt', False), ('run_id', 999)]:
            original = self.manifest[field]; self.manifest[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError): self.project()
            self.manifest[field] = original

    def test_aggregate_identity_graph_qualification_and_no_auth_claims(self):
        for field, bad in [('run_attempt', '999'), ('requested_graph', {}), ('source_owned_oda_qualification_executed', 1),
                           ('physical_device_execution', True), ('authentication_executed', True),
                           ('live_authentication_executed', 0), ('status', 'PASS')]:
            original = self.native[field]; self.native[field] = bad; self.write_archive()
            with self.subTest(field=field), self.assertRaises(ValueError): self.project()
            self.native[field] = original

    def test_each_missing_nested_member_is_rejected(self):
        for name in a.FILES:
            self.write_archive(missing=(name,))
            with self.subTest(name=name), self.assertRaises(ValueError): self.project()

    def test_unbound_nested_receipt_rejected(self):
        self.native['owned_oda_qualification_receipt_sha256'] = '0'*64; self.write_archive()
        with self.assertRaisesRegex(ValueError, 'aggregate hash'): self.project()

    def test_transitive_log_or_startup_tamper_rejected(self):
        for name in a.FILES[1:]:
            original = self.raw[name]; self.raw[name] += b'changed'; self.write_archive()
            with self.subTest(name=name), self.assertRaises(ValueError): self.project()
            self.raw[name] = original

    def test_resealed_false_security_or_positive_guard_fails_semantics(self):
        name = a.PREFIX + 'input_boundary.log'; original = self.raw[name]
        for field in ('different_library_rejected', 'different_state_rejected', 'different_identifier_rejected',
                      'short_identifier_rejected', 'empty_input_rejected', 'oversized_input_rejected',
                      'retargeted_library_alias_rejected', 'retargeted_state_alias_rejected',
                      'matching_realpath_directories_admitted', 'legacy_matching_realpath_directories_rejected'):
            value = json.loads(original); value['results'][field] = False
            self.raw[name] = json.dumps(value).encode(); self.reseal(); self.write_archive()
            with self.subTest(field=field), self.assertRaises(ValueError): self.project()
        self.raw[name] = original

    def test_resealed_runtime_and_schema_claims_fail_semantics(self):
        name = a.PREFIX + 'input_boundary.log'; original = self.raw[name]
        for field, bad in [('runtime_returncode', False), ('runtime_returncode', 1), ('qualification_passed', False),
                           ('qualification_passed', 1), ('source_sha256', '0'*64), ('fixture_sha256', '0'*64), ('results', {})]:
            value = json.loads(original); value[field] = bad
            self.raw[name] = json.dumps(value).encode(); self.reseal(); self.write_archive()
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError): self.project()

    def test_resealed_source_descriptor_and_device_claim_rejected(self):
        for mutation in ('source', 'descriptor', 'auth'):
            original = copy.deepcopy(self.receipt)
            if mutation == 'source': self.receipt['source']['commit'] = 'f'*40
            elif mutation == 'descriptor': self.receipt['input_boundary_source_inputs'] = {}
            else: self.receipt['device_or_authentication_proven'] = True
            self.reseal(); self.write_archive()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.project()
            self.receipt = original

    def test_old_six_suite_receipt_rejected(self):
        self.receipt['suites'].pop('input_boundary')
        self.raw[a.RECEIPT] = json.dumps(self.receipt).encode()
        self.native['owned_oda_qualification_receipt_sha256'] = a.sha(self.raw[a.RECEIPT]); self.write_archive()
        with self.assertRaisesRegex(ValueError, 'suite closure'): self.project()

    def test_actual_archive_hash_and_symlink_root_leaves_required(self):
        wrong = dict(self.expected, sha256='0'*64)
        with self.assertRaises(ValueError): a.project(self.archive, self.workspace, self.manifest, wrong)
        alias = self.root / 'linked.zip'; alias.symlink_to(self.archive)
        with self.assertRaises(ValueError): a.project(alias, self.workspace, self.manifest, self.expected)
        self.project(); path = self.workspace / a.CAPSULE; path.rename(self.workspace / 'real.zip'); path.symlink_to(self.workspace / 'real.zip')
        with self.assertRaises(ValueError): self.verify()

    def test_archive_duplicate_or_selected_symlink_member_rejected(self):
        with self.assertWarns(UserWarning): self.write_archive(duplicate=a.RECEIPT)
        with self.assertRaises(ValueError): self.project()
        self.write_archive(bad_mode=a.PREFIX+'input_boundary.log')
        with self.assertRaises(ValueError): self.project()

    def test_final_verifier_rechecks_semantics_after_consistently_resealed_capsule(self):
        self.project(); name = a.PREFIX+'input_boundary.log'
        value = json.loads(self.raw[name]); value['qualification_passed'] = False
        self.raw[name] = json.dumps(value).encode(); self.reseal()
        native_raw = json.dumps(self.native).encode(); (self.workspace/'native-receipt.json').write_bytes(native_raw)
        with zipfile.ZipFile(self.workspace/a.CAPSULE, 'w') as z:
            for key, raw in self.raw.items():
                info=zipfile.ZipInfo(key);info.create_system=3;info.external_attr=0o100444<<16;z.writestr(info,raw)
        report=json.loads((self.workspace/a.REPORT).read_text());capsule=(self.workspace/a.CAPSULE).read_bytes()
        report.update(capsule_bytes=len(capsule),capsule_sha256=a.sha(capsule),native_receipt_sha256=a.sha(native_raw),
                      owned_receipt_sha256=a.sha(self.raw[a.RECEIPT]),reference_sha256={key:a.sha(raw) for key,raw in self.raw.items()})
        (self.workspace/a.REPORT).write_text(json.dumps(report))
        with self.assertRaises(ValueError): self.verify()

    def test_final_report_closed_types_lineage_and_scope(self):
        self.project(); path=self.workspace/a.REPORT; original=path.read_bytes()
        for field,bad in [('native_identity',{}),('source_archive',dict(self.expected,artifact_id=True)),
                          ('capsule_bytes',True),('reviewed_source_contract_sha256','0'*64),
                          ('reference_sha256',{}),('device_or_authentication_proven',True),('extra',True)]:
            value=json.loads(original);value[field]=bad;path.write_text(json.dumps(value))
            with self.subTest(field=field),self.assertRaises(ValueError):self.verify()
            path.write_bytes(original)

    def test_final_capsule_requires_exact_nine_members_even_with_updated_digest(self):
        self.project(); path=self.workspace/a.CAPSULE
        with zipfile.ZipFile(path,'a') as z:
            info=zipfile.ZipInfo('unexpected.log');info.create_system=3;info.external_attr=0o100444<<16;z.writestr(info,b'extra')
        report=json.loads((self.workspace/a.REPORT).read_text());data=path.read_bytes();report.update(capsule_bytes=len(data),capsule_sha256=a.sha(data))
        (self.workspace/a.REPORT).write_text(json.dumps(report))
        with self.assertRaises(ValueError):self.verify()


if __name__ == '__main__': unittest.main(verbosity=2)
