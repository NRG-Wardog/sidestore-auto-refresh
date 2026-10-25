import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import build
import build_ios
import embedded

class EmbeddedTests(unittest.TestCase):
    def test_exact_manifest_delta_reconstructs_upstream(self):
        original=(embedded.HERE/'fixtures/RemotePairingKit.Package.swift').read_bytes()
        self.assertEqual(embedded.object_hash('blob',original),embedded.MANIFEST_BLOB)
        after=embedded.patch_manifest(original)
        self.assertEqual(after.count(embedded.NEW.encode()),1)
        self.assertEqual(after.replace(embedded.NEW.encode(),embedded.OLD.encode()),original)
        with self.assertRaisesRegex(ValueError,'manifest differs'):embedded.patch_manifest(original+b'\n')

    def test_tree_reconstruction_matches_git_and_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'nested').mkdir();(root/'nested/a').write_bytes(b'a');(root/'top').write_bytes(b'b')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            expected=subprocess.check_output(['git','-C',str(root),'write-tree'],text=True).strip()
            entries={n:{'mode':'100644','git_blob':embedded.object_hash('blob',(root/n).read_bytes())} for n in ['nested/a','top']}
            self.assertEqual(embedded.tree_hash(entries),expected)
            entries['../escape']=entries['top']
            with self.assertRaisesRegex(ValueError,'Unsafe'):embedded.tree_hash(entries)

    def test_resolver_scaffold_is_presealed_empty_and_mode_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();embedded.prepare_resolver_scaffold(root)
            embedded.validate_resolver_scaffold(root)
            self.assertEqual((root/'.swiftpm').stat().st_mode&0o777,0o755)
            self.assertEqual((root/'.swiftpm/xcode').stat().st_mode&0o777,0o755)
            for location in [root/'.swiftpm',root/'.swiftpm/xcode']:
                location.chmod(0o777)
                with self.assertRaisesRegex(ValueError,'mode changed'):embedded.validate_resolver_scaffold(root)
                location.chmod(0o755)
            extra=root/'.swiftpm/xcode/unexpected';extra.mkdir()
            with self.assertRaisesRegex(ValueError,'contents changed'):embedded.validate_resolver_scaffold(root)
            extra.rmdir();extra.write_text('injected')
            with self.assertRaisesRegex(ValueError,'contents changed'):embedded.validate_resolver_scaffold(root)
            extra.unlink()
            (root/'.swiftpm/xcode').rmdir();(root/'outside').mkdir();(root/'.swiftpm/xcode').symlink_to(root/'outside',target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'linked'):embedded.validate_resolver_scaffold(root)

    def test_source_bound_overlay_stages_once_and_detects_source_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();upstream=root/'upstream';upstream.mkdir()
            subprocess.run(['git','init','-q',str(upstream)],check=True)
            (upstream/'Package.swift').write_bytes((embedded.HERE/'fixtures/RemotePairingKit.Package.swift').read_bytes())
            (upstream/'src').mkdir();(upstream/'src/fixture.c').write_text('/* synthetic source, not executed */\n')
            subprocess.run(['git','-C',str(upstream),'add','.'],check=True)
            subprocess.run(['git','-C',str(upstream),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
            pin=embedded.git(upstream,'rev-parse','HEAD').decode().strip()
            built=root/'built';generated=built/'GeneratedDependencies/OpenSSL35';generated.mkdir(parents=True)
            (generated/'fixture').write_bytes(b'synthetic library, not executed')
            receipt={'optimization':'Os','schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
                'source':json.loads((embedded.HERE/'source.json').read_text()),'recipe_sha256':build.digest(embedded.HERE/'build_ios.py'),
                'shared_recipe_sha256':build.digest(embedded.HERE/'build.py'),'device_executed':False,'simulator_executed':False,
                'production_admission':False,'old_gitlink_overwritten':False,'slices':{k:{} for k in build_ios.SLICES},
                'merged_simulator_module_probe_sha256':{'arm64':'0'*64,'x86_64':'0'*64},
                'xcframework_relative_path':'GeneratedDependencies/OpenSSL35/OpenSSL.xcframework','generated_inventory':build.inventory(generated)}
            (built/'ios-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
            mux=root/'minimuxer';(mux/'DeviceGateway').mkdir(parents=True)
            (mux/'DeviceGateway/Package.swift').write_text('.package(path: "../GeneratedDependencies/RemotePairingKit")')
            with patch.object(embedded,'PIN',pin),patch.object(embedded,'TREE',embedded.git(upstream,'rev-parse','HEAD^{tree}').decode().strip()):
                target=embedded.stage(built,upstream,mux)
                self.assertEqual(embedded.validate_embedded(mux)[0],target/'GeneratedDependencies/OpenSSL35')
                embedded.validate_resolver_scaffold(target)
                original_receipt=(target/'generated-provenance.json').read_bytes()
                (target/'.swiftpm/xcode').chmod(0o777)
                with self.assertRaisesRegex(ValueError,'mode changed'):embedded.validate_embedded(mux)
                self.assertEqual((target/'generated-provenance.json').read_bytes(),original_receipt)
                (target/'.swiftpm/xcode').chmod(0o755)
                with self.assertRaisesRegex(ValueError,'existing'):embedded.stage(built,upstream,mux)
                (target/'src/fixture.c').write_text('changed')
                with self.assertRaisesRegex(ValueError,'bytes differ'):embedded.validate_embedded(mux)

if __name__=='__main__':unittest.main()
