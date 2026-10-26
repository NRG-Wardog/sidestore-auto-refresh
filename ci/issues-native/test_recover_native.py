"""Recovery admission negatives; actual retained closure is also checked before packaging."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import recover_native as recovery


class RecoveryTests(unittest.TestCase):
    def test_historical_evidence_bytes_are_authenticated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); p = root/'test.json'; p.write_text('{}')
            hashes = {'test.json':recovery.digest(p)}
            recovery.verify_evidence(root, hashes)
            p.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'changed'):
                recovery.verify_evidence(root, hashes)

    def test_missing_file_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                recovery.verify_evidence(Path(temp), {'missing':'0'*64})

    def test_external_path_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(strict=True)
            for name in ('../receipt.json', '/receipt.json'):
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    recovery.verify_evidence(root, {name:'0'*64})

    def test_linked_root_rejected_before_member_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(strict=True)
            real = root/'real'; real.mkdir()
            link = root/'linked'; link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Expected real evidence root'):
                recovery.verify_evidence(link, {'../receipt.json':'0'*64})

    def test_symlink_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'real').write_text('data'); (root/'link').symlink_to('real')
            with self.assertRaises(ValueError): recovery.digest(root/'link')

    def test_same_native_identity_cannot_impersonate_recovery(self):
        with self.assertRaisesRegex(ValueError, 'new packaging run'):
            recovery.verify(Path('/not-read'), {}, recovery.IDENTITY)

    def test_malformed_packaging_identity_rejected(self):
        with self.assertRaises(ValueError): recovery.verify(Path('/not-read'), {}, {'run_id':'new'})

    def test_other_native_receipt_rejected_before_any_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'receipt.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'fixed native receipt'):
                recovery.verify(root, {}, dict(workflow_commit='1'*40,run_id='2',run_attempt='1'))

    def test_retained_mode_and_added_symbol_rejected(self):
        from optimize_native_candidate import retained_inventory
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); app=root/'Products/Test.app'; app.mkdir(parents=True)
            (app/'binary').write_bytes(b'fixture'); symbols=root/'Symbols'; symbols.mkdir()
            bundle=symbols/'Test.dSYM'; bundle.mkdir(); (bundle/'DWARF').write_bytes(b'fixture')
            manifest={'app':retained_inventory(app), 'symbols':{'Test.dSYM':retained_inventory(bundle)}}
            (root/'retained-files.json').write_text(json.dumps(manifest))
            recovery.verify_retained_transfers({'Test':root})
            old=(app/'binary').stat().st_mode & 0o7777
            (app/'binary').chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'bytes/modes/links'):
                recovery.verify_retained_transfers({'Test':root})
            (app/'binary').chmod(old); (symbols/'extra').mkdir()
            with self.assertRaisesRegex(ValueError, 'closure'):
                recovery.verify_retained_transfers({'Test':root})

    def test_acquisition_identity_and_inventory_bound(self):
        from recover_native_artifacts import ARTIFACTS, inventory
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); evidence=root/'evidence'; evidence.mkdir(); (evidence/'file').write_text('original')
            identity=dict(workflow_commit='1'*40,run_id='2',run_attempt='1')
            report=dict(schema='fixed-native-artifact-restoration-v1', original_native=recovery.IDENTITY,
                artifacts=ARTIFACTS, recovery_identity=dict(GITHUB_SHA='1'*40,GITHUB_RUN_ID='2',GITHUB_RUN_ATTEMPT='1'),
                native_execution_performed=False, original_evidence_modified=False,
                original_evidence_inventory_sha256=hashlib.sha256(json.dumps(inventory(evidence),sort_keys=True).encode()).hexdigest())
            path=root/'acquisition.json'; path.write_text(json.dumps(report))
            recovery.verify_acquisition(evidence,path,identity)
            report['recovery_identity']['GITHUB_RUN_ID']='3'; path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'identity'):
                recovery.verify_acquisition(evidence,path,identity)
            report['recovery_identity']['GITHUB_RUN_ID']='2'; path.write_text(json.dumps(report))
            (evidence/'file').chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'tree changed'):
                recovery.verify_acquisition(evidence,path,identity)

    def test_historical_parser_snapshot_and_current_host_binding(self):
        import hashlib, shutil, types
        import acquire_openssl_host_qualification as host
        source = Path(__file__).resolve().parents[2]
        policy = json.loads((source/'ci/issues-native/historical-source-map.json').read_text())
        row = policy['sources']['scripts/macho_stab_guard.py']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(strict=True)
            for name in ('ci/issues-native/historical-source-map.json', row['historical_file'], 'scripts/macho_stab_guard.py'):
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target)
            (root/'unchanged.txt').write_text('original')
            mapping={'scripts/macho_stab_guard.py':row['historical_sha256'],'unchanged.txt':recovery.digest(root/'unchanged.txt')}
            pin=host.manifest()
            receipt={'qualification_identity':{'workflow_commit':pin['head_sha'],'run_id':str(pin['run_id']),'run_attempt':str(pin['run_attempt'])},
                     'source_sha256':{'scripts/macho_stab_guard.py':row['current_sha256']}}
            receipt_path=root/'host.json';receipt_path.write_text(json.dumps(receipt));pin={**pin,'receipt_sha256':recovery.digest(receipt_path)}
            actual=host.qualification_module()
            fake=types.SimpleNamespace(SOURCES=actual.SOURCES,NATIVE=actual.NATIVE,
                verify_host_local_qualification=lambda *args:{'fixture_only':True})
            identity=dict(workflow_commit='1'*40,run_id='2',run_attempt='1')
            with patch.object(host,'manifest',return_value=pin),patch.object(host,'qualification_module',return_value=fake):
                result=recovery.verify_qualification_sources(root,mapping,identity,receipt_path)
                self.assertFalse(result['current_parser_is_historical_parser'])
                for name in (row['historical_file'],'scripts/macho_stab_guard.py','unchanged.txt'):
                    target=root/name;original=target.read_bytes();target.write_bytes(original+b'changed')
                    with self.subTest(name=name),self.assertRaisesRegex(ValueError,'Historical evidence changed'):
                        recovery.verify_qualification_sources(root,mapping,identity,receipt_path)
                    target.write_bytes(original)
                for field,value in [('sources',{**policy['sources'],'extra':row}),('native_commit','0'*40)]:
                    changed={**policy,field:value};(root/'ci/issues-native/historical-source-map.json').write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError,'mapping closure'):
                        recovery.verify_qualification_sources(root,mapping,identity,receipt_path)
                (root/'ci/issues-native/historical-source-map.json').write_text(json.dumps(policy))
                with self.assertRaisesRegex(ValueError,'Original qualification parser'):
                    recovery.verify_qualification_sources(root,{**mapping,'scripts/macho_stab_guard.py':'0'*64},identity,receipt_path)
                receipt_path.write_text('{}')
                with self.assertRaisesRegex(ValueError,'receipt hash'):
                    recovery.verify_qualification_sources(root,mapping,identity,receipt_path)
                receipt_path.write_text(json.dumps(receipt))
                bad={**receipt,'source_sha256':{'scripts/macho_stab_guard.py':'0'*64}}
                receipt_path.write_text(json.dumps(bad));saved_digest=pin['receipt_sha256'];pin['receipt_sha256']=recovery.digest(receipt_path)
                with self.assertRaisesRegex(ValueError,'Current parser not bound'):
                    recovery.verify_qualification_sources(root,mapping,identity,receipt_path)
                receipt_path.write_text(json.dumps(receipt));pin['receipt_sha256']=saved_digest
                fake.verify_host_local_qualification=lambda *args: (_ for _ in ()).throw(ValueError('full verifier rejected'))
                with self.assertRaisesRegex(ValueError,'full verifier rejected'):
                    recovery.verify_qualification_sources(root,mapping,identity,receipt_path)

    def test_explicit_recovery_and_original_normal_guard_remain(self):
        source=Path(__file__).with_name('package_candidate.py').read_text()
        self.assertIn('def main(*, recover_fixed_native=False, strip_unsigned_widget=False, strip_widget_true_locals=False, strip_openssl_true_locals=False, same_run_qualified=False, language_switch=False):', source)
        self.assertIn("if strip_unsigned_widget and owner == 'SideStore':", source)
        self.assertIn("elif sys.argv[1:] == ['--recover-fixed-native', '--strip-unsigned-widget']:", source)
        self.assertIn("sys.argv[1:] == ['--recover-fixed-native']", source)
        self.assertIn('native_identity = packaging_identity', source)
        self.assertEqual(source.count("**({'packaging_identity': packaging_identity} if recover_fixed_native else {})"), 2)
        self.assertNotIn('os.environ[\'GITHUB_SHA\'] =', source)
        self.assertIn('native_record.verify_generated_openssl_payload(embedded=True)', source)
        self.assertIn('verify_russian_ipa(output', source)
        self.assertIn("provenance['packaging_recovery'] = recovery_provenance", source)


if __name__ == '__main__': unittest.main()
