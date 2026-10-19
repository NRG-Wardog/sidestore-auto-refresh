"""Offline admission negatives and synthetic activation; no execution evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'migration')]
import p0_full_login_diagnostics as login
import maintained_sources as gate
import maintained_package_evidence as package
import verify_candidate_ipa as ipa
import combined_build_evidence as collector
import finalize_p0_full_login_candidate as finalizer
from prepare_candidate_inventory import snapshot


def write(path, value):
    raw = login.dump(value); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    return login.sha(raw)


class PendingTests(unittest.TestCase):
    def test_all_new_routes_reject_before_reads_writes_or_processes(self):
        pins = {'source_basis': login.BASIS}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'pins.json'; write(path, pins)
            with patch.object(login, 'REVIEW_SHA256', None), patch.object(login, 'read', side_effect=AssertionError('untrusted metadata read')), patch.object(login.subprocess, 'run', side_effect=AssertionError('process started')):
                calls = [lambda: gate.load_pins(path), lambda: gate.contract_basis(pins),
                    lambda: gate.expected_anisette_evidence(pins), lambda: package.contract_files(pins),
                    lambda: gate.verify_all(root, pins), lambda: gate.acquire(root, pins),
                    lambda: package.collect_contract_evidence(root/'output', pins)]
                for call in calls:
                    with self.subTest(call=call), self.assertRaisesRegex(ValueError, 'pending'): call()
                self.assertFalse((root/'output').exists())

    def test_constructor_and_active_literal_checks_are_mandatory(self):
        with self.assertRaisesRegex(ValueError, 'diagnostic keys'):
            ipa.verify_constructor_diagnostic_binaries(b'', b'', b'', {'source_basis': login.BASIS})
        manifest = login.parse((ROOT/login.DIRECTORY/'anisette-generated/maintained-source-manifest.json').read_bytes())
        literals = [x['value'].encode() for x in manifest['required_compiled_evidence']['literals']]
        for missing in literals:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                ipa.verify_isolated_anisette_binary(b'\0'.join(v for v in literals if v != missing), manifest)

    def test_strict_json_rejects_duplicate_float_and_nonfinite(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":1.0}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): login.parse(raw)

    def test_source_only_closure_is_required_after_acquisition_not_preflight(self):
        text = (ROOT/'.github/workflows/livecontainer-build.yml').read_text()
        preflight = text.split('  signin-preflight:', 1)[1].split('  source-and-host-build:', 1)[0]
        self.assertIn("--pattern 'test_p0_signin*.py'", preflight)
        name = 'test_maintained_login_trace_closure.py'
        self.assertTrue((ROOT/'tests'/name).is_file())
        self.assertNotIn(name, {p.name for p in (ROOT/'tests').glob('test_p0_signin*.py')})
        self.assertIn(name, {p.name for p in (ROOT/'tests').glob('test*.py')})
        full = text.split('      - name: Run repository checks and historical patch regressions', 1)[1].split('      - name:', 1)[0]
        self.assertIn('--start-directory builder/tests', full)
        self.assertNotIn('--pattern', full)
        self.assertIn('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE: ${{ github.workspace }}/work/LiveContainer', full)

    def test_workflow_separates_historical_and_current_ak(self):
        text = (ROOT/'.github/workflows/livecontainer-build.yml').read_text()
        self.assertIn('P0_CXX_ANISETTE_TEST_SOURCE: ${{ github.workspace }}/work/LegacyCxxAnisetteKit', text)
        self.assertIn('P0_LOGIN_ANISETTE_TEST_SOURCE: ${{ github.workspace }}/work/AnisetteKit', text)
        self.assertIn('work/LegacyCxxAnisetteKit rev-parse HEAD)" = 161435bcc0344e1a9853669455c1ca8d9dda2bf1', text)
        self.assertIn('work/LegacyCxxAnisetteKit rev-parse HEAD^{tree})" = 0df50020ddd774a5b7ab941944501f1ecd591a4a', text)
        self.assertLess(text.index('Acquire and verify maintained runtime sources'), text.index('Build unified host before transport compilation'))
        self.assertLess(text.index('Verify maintained host and authentication sources'), text.index('Execute full layout regression'))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.stage = self.root/'metadata'
        shutil.copytree(ROOT/login.DIRECTORY, self.stage)
        self.review = login.parse((self.stage/'reviewed-approval.json').read_bytes())
        receipt = login.native_receipt(self.stage, accepted=True)
        self.review['files']['native-receipt.json'] = write(self.stage/'native-receipt.json', receipt)
        self.review['native_validation'] = login.native_validation(self.review['files']['native-receipt.json'], accepted=True)
        self.anchor = write(self.stage/'reviewed-approval.json', self.review)
        self.pins = {'schema_version': 1, 'integration_baseline': gate.BASELINE, 'source_basis': login.BASIS,
            'contract_registry_sha256': self.review['files']['compatibility-registry.json'],
            'owners': {o: {k:v for k,v in row.items() if k != 'tree'} for o,row in self.review['owners'].items()},
            'native_validation': {**self.review['native_validation'], 'review_sha256': self.anchor}}

    def approve(self):
        with patch.object(login, 'REVIEW_SHA256', self.anchor): return login.approval(ROOT, self.stage)

    def rehash(self, name, value):
        self.review['files'][name] = write(self.stage/name, value)
        self.anchor = write(self.stage/'reviewed-approval.json', self.review)

    def test_complete_normalized_schema_and_pins(self):
        _, review, receipt = self.approve()
        with patch.object(login, 'REVIEW_SHA256', self.anchor): login.validate_pins(self.pins, review)
        self.assertFalse(receipt['device_login_proven']); self.assertFalse(receipt['final_sidestore_commit_directly_compiled'])
        self.assertEqual(len(self.review['owners']), 7)
        self.assertEqual(len(login.parse((self.stage/'submodules.json').read_bytes())), 5)

    def test_all_historical_bytes_and_contract_edges_preserved(self):
        prior_root, prior, _ = login.historical.approval(ROOT)
        for name in login.inherited_names(prior) - {'maintained-sources.json'}:
            self.assertEqual((self.stage/'historical-p2'/name).read_bytes(), (prior_root/name).read_bytes())
        old = login.parse((prior_root/'compatibility-registry.json').read_bytes())
        new = login.parse((self.stage/'compatibility-registry.json').read_bytes())
        self.assertEqual(new['contracts'], old['contracts']); self.assertEqual(new['edges'], old['edges'])
        for owner in set(login.TUPLES)-login.CHANGED:
            name = 'owners/'+owner+'/runtime-contract.json'
            self.assertEqual((self.stage/name).read_bytes(), (prior_root/name).read_bytes())

    def test_rehashed_native_scope_or_bool_schema_forgery_rejected(self):
        original = login.parse((self.stage/'native-receipt.json').read_bytes())
        for key, value in [('schema_version', True), ('device_login_proven', True), ('final_sidestore_commit_directly_compiled', True), ('normal_release_completed', True), ('scope', 'full device acceptance')]:
            document = copy.deepcopy(original); document[key] = value
            self.rehash('native-receipt.json', document)
            with self.subTest(key=key), self.assertRaises(ValueError): self.approve()

    def test_rehashed_original_receipt_cannot_replace_review(self):
        name = 'evidence/phase-b/independent-review.json'; document = login.parse((self.stage/name).read_bytes()); document['device_login_proven'] = True
        self.rehash(name, document)
        with self.assertRaises(ValueError): self.approve()

    def test_rehashed_source_inventory_or_contract_forgery(self):
        name = 'source-inventory.json'; value = login.parse((self.stage/name).read_bytes()); value['LiveContainer']['tree'] = 'a'*40
        self.rehash(name, value)
        with self.assertRaises(ValueError): self.approve()

    def test_rehashed_child_origin_forgery(self):
        name = 'submodules.json'; value = login.parse((self.stage/name).read_bytes()); value['LiveContainer/litehook']['repository'] = 'https://example.invalid/replacement'
        self.rehash(name, value)
        with self.assertRaises(ValueError): self.approve()

    def test_rehashed_lock_forgery(self):
        name = 'resolver-locks/SideStore.Package.resolved.after'; value = login.parse((self.stage/name).read_bytes()); value['pins'][0]['state']['revision'] = 'a'*40
        self.rehash(name, value)
        with self.assertRaises(ValueError): self.approve()

    def test_parent_tree_owner_basis_and_extra_field_forgery(self):
        original = copy.deepcopy(self.review)
        for kind in ('parent', 'tree', 'owner', 'basis', 'schema', 'extra'):
            self.review = copy.deepcopy(original)
            if kind == 'parent': self.review['integration_parent'] = 'a'*40
            elif kind == 'tree': self.review['owners']['LiveContainer']['tree'] = 'a'*40
            elif kind == 'owner': self.review['owners']['AnisetteKit']['commit'] = login.CHAINS['AnisetteKit'][0]
            elif kind == 'basis': self.review['source_basis'] = login.historical.BASIS
            elif kind == 'schema': self.review['schema_version'] = True
            else: self.review['extra'] = True
            self.anchor = write(self.stage/'reviewed-approval.json', self.review)
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.approve()

    def test_extra_file_rejected_even_without_inventory_entry(self):
        (self.stage/'extra.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'extra or missing'): self.approve()

    def test_metadata_symlink_rejected(self):
        path = self.stage/'capture-summary.json'; raw = path.read_bytes(); path.unlink(); outside = self.root/'outside'; outside.write_bytes(raw); path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'linked'): self.approve()

    def test_active_contract_package_roundtrip_and_rehashed_forgery(self):
        original = login.approval
        def selected(integration_root, metadata_root=None):
            return original(integration_root, self.stage if metadata_root is None else metadata_root)
        with patch.object(login, 'REVIEW_SHA256', self.anchor), patch.object(login, 'approval', side_effect=selected):
            output = self.root/'output'; hashes = package.collect_contract_evidence(output, self.pins)
            package.verify_contract_evidence(output, hashes, self.pins)
            name = 'native-receipt.json'; target = output/package.CONTRACT_DIRECTORY/name
            value = login.parse(target.read_bytes()); value['device_login_proven'] = True; hashes[name] = write(target, value)
            with self.assertRaises(ValueError): package.verify_contract_evidence(output, hashes, self.pins)

    def test_pin_substitutions_rejected(self):
        for kind in ('commit', 'tree', 'path', 'basis', 'receipt', 'schema'):
            pins = copy.deepcopy(self.pins)
            if kind == 'commit': pins['owners']['AnisetteKit']['commit'] = login.CHAINS['AnisetteKit'][0]
            elif kind == 'tree': pins['owners']['AnisetteKit']['tree'] = login.TUPLES['AnisetteKit'][1]
            elif kind == 'path': pins['owners']['SideStore']['path'] = 'other/root'
            elif kind == 'basis': pins['source_basis'] = login.historical.BASIS
            elif kind == 'receipt': pins['native_validation']['native_receipt_sha256'] = 'a'*64
            else: pins['schema_version'] = True
            with patch.object(login, 'REVIEW_SHA256', self.anchor), self.subTest(kind=kind), self.assertRaises(ValueError): login.validate_pins(pins, self.review)


class ActiveRealSourcePackageTests(unittest.TestCase):
    def test_actual_source_package_roundtrip_and_cross_generation_rejection(self):
        if login.REVIEW_SHA256 is None:
            with self.assertRaisesRegex(ValueError, 'pending'): login.approval(ROOT)
            return
        path = ROOT/'migration/maintained-sources.json'; pins = gate.load_pins(path)
        self.assertEqual(pins['source_basis'], login.BASIS)
        current = os.environ.get('P0_LOGIN_ANISETTE_TEST_SOURCE'); historical = os.environ.get('P0_CXX_ANISETTE_TEST_SOURCE')
        self.assertTrue(current and historical, 'explicit current and historical AK roots required')
        source, old = Path(current), Path(historical)
        self.assertNotEqual(source.resolve(), old.resolve())
        self.assertEqual(gate.git(source, 'rev-parse', 'HEAD').decode().strip(), login.TUPLES['AnisetteKit'][0])
        self.assertEqual(gate.git(old, 'rev-parse', 'HEAD').decode().strip(), login.CHAINS['AnisetteKit'][0])
        root, _ = gate.contract_basis(pins); expected = gate.expected_anisette_evidence(pins)
        binary = b'\0'.join(row['value'].encode() for row in expected['required_compiled_evidence']['literals'])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'evidence'; manifest = root/'anisette-generated/maintained-source-manifest.json'
            binding = collector.collect_isolated_anisette_evidence(source, manifest, output, binary, path)
            ipa.verify_isolated_anisette_evidence(output, binding, binary, pins)
            with self.assertRaises(Exception): collector.collect_isolated_anisette_evidence(old, manifest, Path(temporary)/'wrong', binary, path)
            changed = output/ipa.ANISETTE_EVIDENCE_DIRECTORY/'Sources/LoginDiagnosticTrace.swift'
            changed.write_bytes(changed.read_bytes()+b'\n// synthetic mutation\n')
            binding['source_sha256']['Sources/LoginDiagnosticTrace.swift'] = hashlib.sha256(changed.read_bytes()).hexdigest()
            with self.assertRaises(ValueError): ipa.verify_isolated_anisette_evidence(output, binding, binary, pins)


class FinalizerSafetyTests(unittest.TestCase):
    def test_fresh_output_only_and_traversal_rejected(self):
        files = {'a.json': b'{}'}; review = {'files': {'a.json': login.sha(b'{}')}}
        summary = {'candidate_review_sha256': login.sha(login.dump(review)), 'activated': False}
        with tempfile.TemporaryDirectory() as temporary:
            # macOS may expose its temporary root through /var -> /private/var.
            # Canonicalize our owned fixture; the finalizer must reject aliases.
            root = Path(temporary).resolve()
            output = root/'candidate'; finalizer.write_candidate(output, files, review, summary)
            with self.assertRaises(ValueError): finalizer.write_candidate(output, files, review, summary)
            files = {'../escape': b'{}'}; review = {'files': {'../escape': login.sha(b'{}')}}; summary['candidate_review_sha256'] = login.sha(login.dump(review))
            with self.assertRaises(ValueError): finalizer.write_candidate(root/'other', files, review, summary)
            self.assertFalse((root/'escape').exists())

    def test_symlink_parent_rejected_and_canonical_owned_fixture_passes(self):
        files = {'a.json': b'{}'}; review = {'files': {'a.json': login.sha(b'{}')}}
        summary = {'candidate_review_sha256': login.sha(login.dump(review)), 'activated': False}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root/'ordinary'; target.mkdir()
            alias = root/'alias'; alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'fresh ordinary output directory required'):
                finalizer.write_candidate(alias/'candidate', files, review, summary)
            self.assertEqual(list(target.iterdir()), [])
            output = alias.resolve()/'candidate'
            finalizer.write_candidate(output, files, review, summary)
            self.assertEqual((output/'metadata/a.json').read_bytes(), b'{}')
            self.assertEqual((output/'candidate-review.json').read_bytes(), login.dump(review))
            self.assertEqual((output/'candidate-summary.json').read_bytes(), login.dump(summary))


if __name__ == '__main__': unittest.main()
