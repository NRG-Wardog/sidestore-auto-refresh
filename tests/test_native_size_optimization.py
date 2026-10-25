"""Same-run ownership/provenance controls; never substitutes for native strip CI."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import optimize_native_candidate as adapter
from test_macho_stab_guard import fixture


class SameRunOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.receipt = self.root / 'receipt.json'
        self.identity = {'run_id': 'new-run-not-hardcoded', 'run_attempt': '2', 'workflow_commit': 'new-source-commit'}
        self.receipt.write_text(json.dumps({**self.identity, 'status': 'COMPILE_ONLY_PASS_REQUIRES_REVIEW'}))
        self.original = self.root / 'initial.ipa'
        with zipfile.ZipFile(self.original, 'w') as archive:
            archive.writestr('Payload/LiveContainer.app/Info.plist', b'fixture metadata')
            for name in {**adapter.TARGETS, **adapter.UNSIGNED_HELPER_TARGETS}.values(): archive.writestr(name, fixture())
        self.transfers = {owner: self.root / owner for owner in ('LiveContainer','SideStore')}
        for key,(owner,name) in {**adapter.DSYMS, **adapter.UNSIGNED_HELPER_DSYMS}.items():
            root = self.transfers[owner]
            (root / 'evidence').mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.receipt, root / 'evidence/receipt.json')
            bundle = root / 'Symbols' / name
            dwarf = bundle / 'Contents/Resources/DWARF' / ('ZSign.dylib' if key == 'ZSign' else key)
            dwarf.parent.mkdir(parents=True); dwarf.write_bytes(fixture(False))
            (bundle / 'Contents/Info.plist').write_bytes(b'complete matching bundle metadata')
            manifest = root / 'retained-files.json'
            value = json.loads(manifest.read_text()) if manifest.exists() else {'symbols': {}}
            value['symbols'][name] = adapter.retained_inventory(bundle)
            manifest.write_text(json.dumps(value))

    def test_unsigned_helper_set_is_finite_and_missing_dsym_fails_closed(self):
        self.assertEqual(set(adapter.UNSIGNED_HELPER_TARGETS), {'SideStoreSupport', 'ZSign'})
        self.assertNotIn('LiveWidgetExtension', adapter.UNSIGNED_HELPER_TARGETS)
        path = self.transfers['LiveContainer'] / 'Symbols/SideStoreSupport.framework.dSYM'
        shutil.rmtree(path)
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaises((ValueError, FileNotFoundError)): self.call()
            transform.assert_not_called()

    def test_unsigned_helper_changed_uuid_fails_before_transform(self):
        root = self.transfers['LiveContainer']
        bundle = root / 'Symbols/ZSign.dylib.dSYM'
        dwarf = bundle / 'Contents/Resources/DWARF/ZSign.dylib'
        data = dwarf.read_bytes()
        # This independently changes the LC_UUID bytes in the matching fixture.
        data = data.replace(b'0123456789abcdef', b'X'*16)
        self.assertNotEqual(data, dwarf.read_bytes())
        dwarf.write_bytes(data)
        manifest = root / 'retained-files.json'
        value = json.loads(manifest.read_text())
        value['symbols'][bundle.name] = adapter.retained_inventory(bundle)
        manifest.write_text(json.dumps(value))
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaisesRegex(ValueError, 'UUID'): self.call()
            transform.assert_not_called()

    def generated(self):
        rows = {}
        receipt = json.loads(self.receipt.read_text())
        with zipfile.ZipFile(self.original, 'a') as archive:
            for key, path in adapter.OPENSSL_TARGETS.items():
                archive.writestr(path, fixture())
                root = self.root / key
                root.mkdir()
                binary = root / 'OpenSSL'; binary.write_bytes(fixture())
                bundle = root / 'OpenSSL.framework.dSYM'
                dwarf = bundle / 'Contents/Resources/DWARF/OpenSSL'
                dwarf.parent.mkdir(parents=True); dwarf.write_bytes(fixture(False))
                (bundle / 'Contents/Info.plist').write_bytes(b'complete generated bundle')
                payload = root / 'payload.json'
                payload.write_text(json.dumps({'status': 'EXACT_GENERATED_DEVICE_BYTES_LINKED_NOT_RUNTIME_PROVEN',
                    'generated_inputs_unchanged': True, 'generated_binary_sha256': adapter.digest(binary),
                    'native_binary_sha256': adapter.digest(binary)}))
                receipt['generated_embedded_openssl_payload_receipt_sha256' if key == 'OpenSSLEmbedded'
                        else 'generated_openssl_payload_receipt_sha256'] = adapter.digest(payload)
                rows[key] = dict(binary=binary, dsym=bundle, payload_receipt=payload)
        self.receipt.write_text(json.dumps(receipt))
        for root in self.transfers.values(): shutil.copyfile(self.receipt, root/'evidence/receipt.json')
        return rows

    def call(self, generated=None, **kwargs):
        return adapter.optimize_same_run_ipa(self.original, expected_sha=adapter.digest(self.original),
            transfer_roots=self.transfers, receipt_path=self.receipt, work=self.root/'work',
            output=self.root/'output', retained=self.root/'retained', run_identity=self.identity,
            generated_openssl=generated, **kwargs)

    def test_external_retention_snapshot_binds_first_stage(self):
        expected = {owner: adapter.digest(root/'retained-files.json') for owner, root in self.transfers.items()}
        with patch.object(adapter, 'transform_verified_inputs', side_effect=self.synthetic_transform):
            _, report = self.call(expected_transfer_manifests=expected)
        self.assertEqual(report['native_transfer_manifest_sha256'], expected)

    def test_self_consistent_manifest_rewrite_rejected_before_transform(self):
        expected = {owner: adapter.digest(root/'retained-files.json') for owner, root in self.transfers.items()}
        path = self.transfers['SideStore']/'retained-files.json'
        path.write_text(path.read_text()+'\n')
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaisesRegex(ValueError, 'pre-packaging retention manifest'):
                self.call(expected_transfer_manifests=expected)
            transform.assert_not_called()

    def test_retention_snapshot_rechecked_after_transform(self):
        expected = {owner: adapter.digest(root/'retained-files.json') for owner, root in self.transfers.items()}
        def mutate(*args, **kwargs):
            self.synthetic_transform(*args, **kwargs)
            path = self.transfers['SideStore']/'retained-files.json'
            path.write_text(path.read_text()+'\n')
        with patch.object(adapter, 'transform_verified_inputs', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'sealed native retention manifest changed'):
                self.call(expected_transfer_manifests=expected)

    @staticmethod
    def synthetic_transform(original, targets, diagnostics, sidecar, **kwargs):
        output, report = kwargs['output'], kwargs['report']
        shutil.copyfile(original, output / 'LiveContainer-SideStore-debug-stabs-experiment.ipa')
        shutil.copyfile(sidecar, output / sidecar.name)
        report.update(status='SYNTHETIC_TEST_ONLY', final_ipa_sha256=adapter.digest(original),
                      retained_debug_sidecar_sha256=adapter.digest(sidecar))

    def test_same_run_sources_and_original_retained_without_fixed_run_constants(self):
        before = self.original.read_bytes()
        with patch.object(adapter, 'transform_verified_inputs', side_effect=self.synthetic_transform) as transform:
            result, report = self.call()
        self.assertEqual(transform.call_count, 1)
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(result.read_bytes(), before)
        with zipfile.ZipFile(self.root/'retained/retained-debug-diagnostics.zip') as archive:
            self.assertEqual(archive.read('OriginalIPA/initial-combined.ipa'), before)
            for _,(_,name) in {**adapter.DSYMS, **adapter.UNSIGNED_HELPER_DSYMS}.items():
                self.assertIn('Symbols/'+name+'/Contents/Info.plist', archive.namelist())
        self.assertEqual(report['same_run_identity'], self.identity)

    def test_dsym_manifest_or_receipt_mutation_rejects_before_transform(self):
        root = self.transfers['SideStore']
        (root/'evidence/receipt.json').write_bytes(b'other run')
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaisesRegex(ValueError, 'symbol receipt differs'): self.call()
            transform.assert_not_called()
        shutil.copyfile(self.receipt, root/'evidence/receipt.json')
        bundle = root/'Symbols/SideStore.app.dSYM'
        (bundle/'Contents/Info.plist').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'dSYM manifest differs'): self.call()

    def test_escaping_symbols_and_transform_failure_cannot_publish_retained_success(self):
        bundle = self.transfers['SideStore']/'Symbols/SideStore.app.dSYM'
        link = bundle/'escape'; link.symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError, 'escapes owned bundle'): self.call()
        link.unlink()
        with patch.object(adapter, 'transform_verified_inputs', side_effect=ValueError('strict verification failed')):
            with self.assertRaisesRegex(ValueError, 'strict verification failed'): self.call()
        self.assertFalse((self.root/'retained').exists())
        self.assertEqual(json.loads((self.root/'output/experiment.json').read_text())['status'], 'FAILED')
        self.assertFalse(list((self.root/'output').glob('*.ipa')))

    def test_failure_evidence_copy_never_contains_staged_ipa_or_symbol_zip(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'ci/issues-native/package_candidate.py'
        spec = importlib.util.spec_from_file_location('native_package_size_test', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        staging = self.root/'packaging-size-products'; staging.mkdir()
        for name in ('partial.ipa', 'retained-debug-diagnostics.zip', 'failure.json', 'codesign.log'):
            (staging/name).write_bytes(b'controlled fixture')
        evidence = self.root/'package-evidence/size-reports'
        module.copy_size_reports(staging,evidence)
        self.assertEqual({p.name for p in evidence.iterdir()}, {'failure.json','codesign.log'})
        self.assertFalse(list((self.root/'package-evidence').rglob('*.ipa')))
        self.assertFalse(list((self.root/'package-evidence').rglob('*.zip')))
        source = path.read_text()
        self.assertIn("initial_root = Path('packaging-size-inputs')",source)
        self.assertIn("output=Path('packaging-size-products')",source)
        self.assertNotIn("Path('package-evidence/initial-combined.ipa')",source)

    def test_actual_symlink_parent_and_alternate_cwd_preserve_identity(self):
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        self.assertNotEqual(str(alias), str(alias.resolve(strict=True)))
        self.root = alias
        self.original = alias/'initial.ipa'; self.receipt = alias/'receipt.json'
        self.transfers = {owner: alias/owner for owner in ('LiveContainer','SideStore')}
        previous = Path.cwd()
        try:
            os.chdir(alias)
            with patch.object(adapter, 'transform_verified_inputs', side_effect=self.synthetic_transform):
                result, _ = self.call()
            self.assertTrue(result.samefile(result.resolve(strict=True)))
            self.assertTrue((alias/'retained/retained-debug-diagnostics.zip').is_file())
        finally:
            os.chdir(previous)

    def test_shared_transform_is_artifact_independent_and_rejects_wrong_bound_hash(self):
        import inspect
        import experiment_strip_stabs as shared
        source = inspect.getsource(shared.transform_verified_inputs)
        self.assertNotIn('IPA_SHA', source)
        self.assertNotIn('GREEN_COMMIT', source)
        self.assertNotIn('ARTIFACTS', source)
        with patch.object(shared, 'run') as tools:
            with self.assertRaisesRegex(ValueError, 'bound initial IPA hash differs'):
                shared.transform_verified_inputs(self.original, {}, {}, self.root/'unused',
                    work=self.root/'work', output=self.root/'output', report={},
                    expected_original_sha='0'*64)
            tools.assert_not_called()

    def test_symbols_changed_during_transform_fail_closed(self):
        def mutate(*args, **kwargs):
            self.synthetic_transform(*args, **kwargs)
            (self.transfers['SideStore']/'Symbols/SideStore.app.dSYM/Contents/Info.plist').write_bytes(b'changed')
        with patch.object(adapter, 'transform_verified_inputs', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'symbols changed during optimization'): self.call()
        self.assertFalse((self.root/'retained').exists())
        self.assertEqual(json.loads((self.root/'output/experiment.json').read_text())['status'], 'FAILED')

    def test_generated_pair_is_bound_retained_and_explicitly_opted_in(self):
        generated = self.generated()
        with patch.object(adapter, 'transform_verified_inputs', side_effect=self.synthetic_transform) as transform:
            self.call(generated)
        self.assertTrue(transform.call_args.kwargs['include_generated_openssl'])
        self.assertEqual(set(transform.call_args.args[1]), set(adapter.TARGETS) | set(adapter.OPENSSL_TARGETS) | set(adapter.UNSIGNED_HELPER_TARGETS))
        with zipfile.ZipFile(self.root/'retained/retained-debug-diagnostics.zip') as archive:
            for key in adapter.OPENSSL_TARGETS:
                self.assertEqual(archive.read('OriginalBinaries/'+key), fixture())
                self.assertIn('Symbols/'+key+'/OpenSSL.framework.dSYM/Contents/Info.plist', archive.namelist())

    def test_generated_unbound_receipt_or_binary_rejected(self):
        generated = self.generated()
        generated['OpenSSLHost']['payload_receipt'].write_text('{}')
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaisesRegex(ValueError, 'not same-run bound'): self.call(generated)
            transform.assert_not_called()

    def test_generated_wrong_dsym_rejected(self):
        generated = self.generated()
        dwarf = generated['OpenSSLHost']['dsym']/'Contents/Resources/DWARF/OpenSSL'
        data = dwarf.read_bytes(); dwarf.write_bytes(data.replace(b'0123456789abcdef', b'X'*16))
        with patch.object(adapter, 'transform_verified_inputs') as transform:
            with self.assertRaisesRegex(ValueError, 'dSYM UUID differs'): self.call(generated)
            transform.assert_not_called()

    def test_generated_binary_mutation_after_transform_rejected(self):
        generated = self.generated()
        def mutate(*args, **kwargs):
            self.synthetic_transform(*args, **kwargs)
            generated['OpenSSLEmbedded']['binary'].write_bytes(b'changed')
        with patch.object(adapter, 'transform_verified_inputs', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'generated optimization input changed'): self.call(generated)
        self.assertFalse((self.root/'retained').exists())




class TwoStageAdapterTests(unittest.TestCase):
    """Orchestration-only fakes; native structural/signature evidence is separate."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.initial=self.root/'initial.ipa'; self.initial.write_bytes(b'initial'*100)
        self.first=self.root/'first.ipa'; self.first.write_bytes(b'first'*100)
        self.sidecar=self.root/'symbols.zip'; self.sidecar.write_bytes(b'full retained diagnostics')
        self.output=self.root/'output'; self.output.mkdir()
        self.retained=self.root/'retained'; self.retained.mkdir()
        self.identity={'run_id':'run','run_attempt':'1','workflow_commit':'commit'}
        self.report={'status':'STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED',
            'original_ipa_sha256':adapter.digest(self.initial),'final_ipa_sha256':adapter.digest(self.first),
            'retained_debug_sidecar_sha256':adapter.digest(self.sidecar),
            'diagnostics':{k:{'uuid':'same-uuid','dwarf_sha256':'same-dwarf'} for k in adapter.TARGETS},
            'before_size':{'uncompressed_bundle_bytes':700},'after_size':{'uncompressed_bundle_bytes':500},
            'transformations':{'stage1':'fixture-only'}}

    def driver(self, first, sidecar, out):
        out.mkdir(); (out/'retained').mkdir()
        shutil.copyfile(first,out/'retained/original.ipa')
        shutil.copyfile(sidecar,out/'retained/matching-debug-diagnostics.zip')
        final=out/'local-symbol-qualified.ipa'; final.write_bytes(b'final'*50)
        report={'schema':'local-symbol-diagnostic-v1',
            'status':'LOCAL_SYMBOL_PACKAGE_QUALIFIED_DEVICE_AND_RELEASE_UNQUALIFIED',
            **self.identity,'packaging_approved':True,'signature_verified':True,'ipa_emitted':True,'runtime_proven':False,
            'input_ipa_sha256':adapter.digest(first),'retained_symbols_sha256':adapter.digest(sidecar),
            'targets':{k:{} for k in adapter.TARGETS},'diagnostics':self.report['diagnostics'],
            'final_ipa_sha256':adapter.digest(final),'final_ipa_bytes':final.stat().st_size,
            'original_ipa_bytes':first.stat().st_size,'archive_bytes_saved':first.stat().st_size-final.stat().st_size}
        adapter.write(out/'diagnostic.json',report)
        return report

    def call(self, driver=None):
        with patch.object(adapter,'run_local_symbol_stage',side_effect=driver or self.driver), \
                patch('ipa_size_report.report',return_value={'uncompressed_bundle_bytes':250}):
            return adapter.add_local_symbol_stage(self.initial,self.first,self.sidecar,self.output,
                                                 self.retained,self.report,self.identity)

    def test_exact_chain_both_receipts_retained_and_actual_total(self):
        initial,first,symbols=map(adapter.digest,(self.initial,self.first,self.sidecar))
        final=self.call()
        self.assertEqual([initial,first,symbols],list(map(adapter.digest,(self.initial,self.first,self.sidecar))))
        self.assertEqual(self.report['final_ipa_sha256'],adapter.digest(final))
        self.assertNotIn('transformations',self.report)
        self.assertEqual(self.report['debug_stabs_transformations'],{'stage1':'fixture-only'})
        self.assertEqual(set(self.report['local_symbol_targets']),set(adapter.TARGETS))
        stages=self.report['stages']
        self.assertEqual(stages['debug_stabs']['final_ipa_sha256'],stages['local_symbols']['input_ipa_sha256'])
        self.assertEqual(self.report['archive_bytes_saved'],sum(s['archive_bytes_saved'] for s in stages.values()))
        self.assertEqual(self.report['archive_bytes_saved'],self.initial.stat().st_size-final.stat().st_size)
        self.assertTrue((self.retained/'stab-stage-receipt.json').is_file())
        self.assertTrue((self.retained/'local-stage-receipt.json').is_file())
        self.assertEqual(adapter.digest(self.retained/'local-symbol-stage-inputs/original.ipa'),first)
        self.assertEqual(adapter.digest(self.retained/'local-symbol-stage-inputs/matching-debug-diagnostics.zip'),symbols)

    def test_unqualified_result_fails_closed(self):
        def fake(*args):
            report=self.driver(*args); report['signature_verified']=False
            adapter.write(args[2]/'diagnostic.json',report)
            return report
        with self.assertRaisesRegex(ValueError,'qualification'):self.call(fake)
        self.assertNotIn('stages',self.report)

    def test_mismatched_previous_stage_or_sidecar_rejected_before_driver(self):
        self.report['final_ipa_sha256']='wrong'
        with patch.object(adapter,'run_local_symbol_stage') as run:
            with self.assertRaisesRegex(ValueError,'first-stage'):adapter.add_local_symbol_stage(
                self.initial,self.first,self.sidecar,self.output,self.retained,self.report,self.identity)
            run.assert_not_called()

    def test_wrong_run_input_sizes_dsym_and_output_are_rejected(self):
        changes=[('run_id','other'),('input_ipa_sha256','other'),('archive_bytes_saved',0),
                 ('final_ipa_sha256','other'),('targets',{}),('diagnostics',{})]
        for key,value in changes:
            with self.subTest(key=key):
                def fake(*args):
                    report=self.driver(*args); report[key]=value
                    adapter.write(args[2]/'diagnostic.json',report)
                    return report
                with self.assertRaises(ValueError):self.call(fake)
                shutil.rmtree(self.output/'local-symbol-stage')
        self.assertNotIn('stages',self.report)

    def test_input_mutation_or_different_persisted_receipt_rejected(self):
        def fake(*args):
            report=self.driver(*args); self.sidecar.write_bytes(b'mutation'); return report
        with self.assertRaisesRegex(ValueError,'immutable'):self.call(fake)
        self.assertNotIn('stages',self.report)

    def test_persisted_receipt_and_retained_link_are_rejected(self):
        def fake(*args):
            report=self.driver(*args); report['run_attempt']='different-only-in-return'; return report
        with self.assertRaisesRegex(ValueError,'persisted'):self.call(fake)
        shutil.rmtree(self.output/'local-symbol-stage')
        def linked(*args):
            report=self.driver(*args)
            saved=args[2]/'retained/original.ipa'; saved.unlink(); saved.symlink_to(self.first)
            return report
        with self.assertRaisesRegex(ValueError,'archive closure'):self.call(linked)
        self.assertNotIn('stages',self.report)

    def test_same_target_wrong_dsym_is_not_accepted(self):
        import copy
        def fake(*args):
            report=self.driver(*args); report['diagnostics']=copy.deepcopy(report['diagnostics'])
            report['diagnostics']['SideStore']['dwarf_sha256']='different'
            adapter.write(args[2]/'diagnostic.json',report); return report
        with self.assertRaisesRegex(ValueError,'dSYM binding'):self.call(fake)
        self.assertNotIn('stages',self.report)

    def test_stage2_is_explicit_opt_in_not_default(self):
        import inspect
        self.assertIs(inspect.signature(adapter.optimize_same_run_ipa).parameters['include_local_symbols'].default,False)


if __name__ == "__main__": unittest.main()
