import argparse
import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('crypto_probe_runner', HERE / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class Controls(unittest.TestCase):
    def test_wrong_binary_hash_never_compiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); binary = root / 'libcrypto'; binary.write_bytes(b'fixture')
            args = argparse.Namespace(library=binary, output=root/'receipt.json', sha256='0'*64)
            with patch.object(runner.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'hash'):
                    runner.run(args)
                process.assert_not_called()
                self.assertFalse(args.output.exists())

    def test_unverified_provider_module_never_compiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); library = root/'libcrypto'; library.write_bytes(b'crypto')
            module = root/'legacy.so'; module.write_bytes(b'provider')
            args = argparse.Namespace(library=library, output=root/'receipt.json', sha256=runner.digest(library),
                                      provider_module=module, provider_module_sha256='0'*64, builtin_providers=False)
            with patch.object(runner.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'module name/hash'):
                    runner.run(args)
                process.assert_not_called()
                self.assertFalse(args.output.exists())

    def test_stale_receipt_never_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp)/'receipt.json'; receipt.write_text('old')
            with self.assertRaisesRegex(ValueError, 'existing receipt'):
                runner.run(argparse.Namespace(output=receipt))
            self.assertEqual(receipt.read_text(), 'old')

    def test_loaded_image_version_headers_and_case_closure_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); library = root/'libcrypto'; library.write_bytes(b'expected')
            other = root/'wrong'; other.write_bytes(b'wrong')
            args = argparse.Namespace(library=library, output=root/'receipt.json', sha256=runner.digest(library),
                                      headers=root, cc='cc', expected_version='OpenSSL fixture', system_smoke=True,
                                      provider_module=None, builtin_providers=True)
            rows = f'HEADER OpenSSL fixture\nIMAGE {library}\nVERSION OpenSSL fixture\nPROVIDER_IMAGE default {library}\nPROVIDER_IMAGE legacy {library}\n'
            variants = [(rows.replace(str(library), str(other)), 'loaded image'),
                        (rows.replace('VERSION OpenSSL fixture', 'VERSION wrong'), 'runtime version'),
                        (rows.replace('HEADER OpenSSL fixture', 'HEADER wrong'), 'headers'),
                        (rows, 'complete exact case set')]
            for output, expected in variants:
                result = runner.subprocess.CompletedProcess([], 0, stdout=output, stderr='')
                with patch.object(runner.subprocess, 'run', return_value=result):
                    with self.assertRaisesRegex(ValueError, expected):
                        runner.run(args)
                self.assertFalse(args.output.exists())

    def test_versioned_framework_checks_actual_macho_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); base = root/'OpenSSL.framework/Versions/A'; base.mkdir(parents=True)
            (base/'Resources').mkdir()
            (base/'Resources/Info.plist').write_bytes(plistlib.dumps({'DTPlatformName': 'macosx'}))
            library = base/'OpenSSL'; library.write_bytes(b'fixture')
            args = argparse.Namespace(library=library, output=root/'receipt.json', sha256=runner.digest(library),
                                      provider_module=None, builtin_providers=True)
            result = runner.subprocess.CompletedProcess([], 0, stdout='platform IOS\n', stderr='')
            with patch.object(runner.platform, 'system', return_value='Darwin'), patch.object(runner.subprocess, 'run', return_value=result) as process:
                with self.assertRaisesRegex(ValueError, 'Mach-O slice platform'):
                    runner.run(args)
                self.assertEqual(process.call_count, 1)
                self.assertEqual(process.call_args.args[0][:3], ['xcrun', 'vtool', '-show-build'])

    def test_pinned_inventories_and_materializer_fail_closed(self):
        import json
        spec = importlib.util.spec_from_file_location('acquire', HERE/'acquire.py')
        acquire = importlib.util.module_from_spec(spec); spec.loader.exec_module(acquire)
        pins = json.loads((HERE/'pinned-inputs.json').read_text())
        for owner, count in [('host', 140), ('embedded', 144)]:
            inventory = HERE/(owner+'-headers.json')
            self.assertEqual(runner.digest(inventory), pins[owner]['header_inventory_sha256'])
            self.assertEqual(len(json.loads(inventory.read_text())), count)
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, 'Pinned framework input mismatch'):
                    acquire.materialize(owner, Path(temp), lambda p: b'incorrect')

    def test_ios_and_simulator_are_not_native_mac_executables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); framework = root/'OpenSSL.framework'; framework.mkdir()
            binary = framework/'OpenSSL'; binary.write_bytes(b'fixture')
            for platform in ('iphoneos', 'iphonesimulator'):
                (framework/'Info.plist').write_bytes(plistlib.dumps({'DTPlatformName': platform}))
                args = argparse.Namespace(library=binary, output=root/'receipt.json', sha256=runner.digest(binary))
                with patch.object(runner.platform, 'system', return_value='Darwin'), patch.object(runner.subprocess, 'run') as process:
                    with self.assertRaisesRegex(ValueError, 'macOS slice'):
                        runner.run(args)
                    process.assert_not_called()

if __name__ == '__main__':
    unittest.main()
