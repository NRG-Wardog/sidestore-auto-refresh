#!/usr/bin/env python3
"""Source-owned fake regression tests; no compiler, nm, library or target executes."""
import contextlib
import hashlib
import io
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verify_native_abi as gate


class NativeABIContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fake-native-abi-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.archive = self.source / 'target/aarch64-apple-ios/release/libidevice_ffi.a'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'source-owned fake archive, not target code')
        self.header = self.source / 'ffi/idevice.h'
        self.header.parent.mkdir(parents=True)
        self.header.write_bytes(b'/* source-owned fake header */\n')
        self.framework = self.make_framework('framework')
        self.staged = self.make_framework('staged')
        self.output = self.root / 'result.json'
        self.sysroot = self.root / 'pinned-toolchain'
        self.nm = self.sysroot / 'lib/rustlib' / gate.RUST_HOST / 'bin/llvm-nm'
        self.nm.parent.mkdir(parents=True)
        self.nm.write_bytes(b'source-owned fake tool; never executed')
        self.nm.chmod(0o755)
        self.manifest = self.sysroot / 'lib/rustlib/multirust-channel-manifest.toml'
        self.manifest.write_text(
            'date = "' + gate.COMPONENT_DATE + '"\n'
            '[pkg.llvm-tools-preview]\nversion = "' + gate.COMPONENT_VERSION + '"\n'
            '[pkg.llvm-tools-preview.target.aarch64-apple-darwin]\navailable = true\n'
            'xz_url = "' + gate.COMPONENT_URL + '"\n'
            'xz_hash = "' + gate.COMPONENT_SHA256 + '"\n')
        self.verbose = ('rustc ' + gate.COMPONENT_VERSION + '\nrelease: ' + gate.RUST_TOOLCHAIN
                        + '\ncommit-hash: ' + gate.RUST_COMMIT + '\nhost: ' + gate.RUST_HOST
                        + '\nLLVM version: ' + gate.LLVM_VERSION + '\n')
        self.components = 'rustc-' + gate.RUST_HOST + '\nllvm-tools-' + gate.RUST_HOST + '\n'
        self.nm_version = 'llvm-nm, compatible with GNU nm\nLLVM (http://llvm.org/):\n  LLVM version 22.1.8-rust-1.98.1-stable\n'
        self.symbols = gate.required_symbols()
        self.nm_output = '\n'.join(x['mach_o_symbol'] for x in self.symbols) + '\n'
        self.nm_stderr = ''
        self.fail_nm = False
        self.fail_clang = False
        self.mutate_during_nm = None
        self.fail_clang_headers = None
        self.commands = []

    def make_framework(self, name):
        root = self.root / name
        folder = root / 'ios-arm64'
        headers = folder / 'Headers'
        headers.mkdir(parents=True)
        (folder / 'libidevice_ffi.a').write_bytes(self.archive.read_bytes())
        (headers / 'idevice.h').write_bytes(self.header.read_bytes())
        (root / 'Info.plist').write_bytes(plistlib.dumps({'AvailableLibraries': [{
            'LibraryIdentifier': 'ios-arm64', 'LibraryPath': 'libidevice_ffi.a',
            'HeadersPath': 'Headers', 'SupportedPlatform': 'ios', 'SupportedArchitectures': ['arm64']}]}))
        return root

    def fake_run(self, command, **kwargs):
        self.assertEqual(kwargs, {'check': True, 'capture_output': True, 'text': True})
        self.commands.append(command)
        stderr = ''
        if command == ['rustc', '+1.98.1', '--version', '--verbose']:
            stdout = self.verbose
        elif command == ['rustc', '+1.98.1', '--print', 'sysroot']:
            stdout = str(self.sysroot) + '\n'
        elif command == ['rustup', 'component', 'list', '--toolchain', '1.98.1', '--installed']:
            stdout = self.components
        elif command == [str(self.nm), '--version']:
            stdout = self.nm_version
        elif command[0] == str(self.nm):
            self.assertEqual(command[1:-1], ['--arch=arm64', '--extern-only', '--defined-only', '--format=just-symbols'])
            stdout, stderr = self.nm_output, self.nm_stderr
            if self.fail_nm:
                raise subprocess.CalledProcessError(1, command, output=stdout, stderr='fixture: incompatible bitcode\n')
            if self.mutate_during_nm is not None:
                self.mutate_during_nm.write_bytes(b'changed during synthetic symbol scan')
        elif command == ['xcrun', '--sdk', 'iphoneos', '--show-sdk-path']:
            stdout = '/source-owned/fake-sdk\n'
        elif command[:4] == ['xcrun', '--sdk', 'iphoneos', 'clang']:
            self.assertIn('-fsyntax-only', command)
            self.assertIn('-Werror', command)
            self.assertNotIn('-o', command)
            if self.fail_clang or (self.fail_clang_headers is not None and
                                   command[command.index('-I') + 1] == str(self.fail_clang_headers)):
                raise subprocess.CalledProcessError(1, command, stderr='fixture: ABI layout mismatch\n')
            stdout = ''
        else:
            self.fail('Unexpected external command: ' + repr(command))
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)

    def invoke(self):
        argv = ['verify_native_abi.py', '--source', str(self.source), '--framework', str(self.framework),
                '--staged-framework', str(self.staged), '--output', str(self.output)]
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(gate.subprocess, 'run', side_effect=self.fake_run), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            gate.main()
        return json.loads(self.output.read_text())

    def reject(self, exception=RuntimeError):
        with self.assertRaises(exception):
            self.invoke()
        self.assertFalse(self.output.exists(), 'Failure wrote a passing result')

    def test_pass_keeps_three_archives_six_symbols_headers_and_provenance(self):
        self.nm_stderr = 'fixture: benign archive member has no symbols\n'
        result = self.invoke()
        self.assertEqual(len(result['artifacts']), 3)
        self.assertEqual(len(result['required_symbols']), 6)
        self.assertFalse(result['target_executed'])
        self.assertEqual(result['symbol_reader']['sha256'], gate.digest(self.nm))
        self.assertEqual(result['symbol_reader']['component_sha256'], gate.COMPONENT_SHA256)
        original, *copies = result['artifacts']
        self.assertEqual(original['symbol_validation'], 'inspected_original')
        self.assertEqual(original['nm_stderr'], self.nm_stderr)
        self.assertTrue(all(x['header_signature_and_layout_checks'] for x in result['artifacts']))
        self.assertTrue(all(x['symbol_validation'] == 'validated_by_byte_identity' and
                            x['symbol_validation_source'] == 'cargo' and
                            x['archive_sha256'] == result['archive_sha256'] and
                            not any(k.startswith('nm_') for k in x) for x in copies))
        scans = [c for c in self.commands if c[0] == str(self.nm) and c[-1] != '--version']
        self.assertEqual(scans, [[str(self.nm), *gate.NM_FLAGS, str(self.archive)]])
        clang = [c for c in self.commands if c[:4] == ['xcrun', '--sdk', 'iphoneos', 'clang']]
        self.assertEqual([c[c.index('-I') + 1] for c in clang],
                         [str(self.source / 'ffi'), str(self.framework / 'ios-arm64/Headers'),
                          str(self.staged / 'ios-arm64/Headers')])
        self.assertFalse(any(command[:2] == ['xcrun', 'nm'] for command in self.commands))
        self.assertEqual(hashlib.sha256((Path(__file__).parent / 'assert_header.c').read_bytes()).hexdigest(), 'ebf680f7cdf8b373e081a8eebc6ff9f6e31f0190aa3c881014df60778dc7df08')

    def test_nm_error_rejected_even_with_all_six_symbols(self):
        self.fail_nm = True
        self.reject(subprocess.CalledProcessError)

    def test_missing_symbol_rejected(self):
        self.nm_output = '\n'.join(x['mach_o_symbol'] for x in self.symbols[1:])
        self.reject()

    def test_undefined_only_or_decorated_output_cannot_satisfy_exact_names(self):
        self.nm_output = '\n'.join('U ' + x['mach_o_symbol'] for x in self.symbols)
        self.reject()

    def test_wrong_rust_commit_rejected(self):
        self.verbose = self.verbose.replace(gate.RUST_COMMIT, '0' * 40)
        self.reject()

    def test_wrong_host_rejected(self):
        self.verbose = self.verbose.replace(gate.RUST_HOST, 'x86_64-apple-darwin')
        self.reject()

    def test_wrong_rust_llvm_version_rejected(self):
        self.verbose = self.verbose.replace(gate.LLVM_VERSION, '21.0.0')
        self.reject()

    def test_wrong_nm_llvm_version_rejected(self):
        self.nm_version = self.nm_version.replace(gate.LLVM_VERSION, '21.0.0')
        self.reject()

    def test_missing_component_rejected(self):
        self.components = 'rustc-' + gate.RUST_HOST + '\n'
        self.reject()

    def test_changed_component_digest_rejected_before_nm_execution(self):
        self.manifest.write_text(self.manifest.read_text().replace(gate.COMPONENT_SHA256, '0' * 64))
        self.reject()
        self.assertFalse(any(command[0] == str(self.nm) for command in self.commands))

    def test_unofficial_component_url_rejected(self):
        self.manifest.write_text(self.manifest.read_text().replace('https://static.rust-lang.org', 'https://invalid.example'))
        self.reject()

    def test_escaping_tool_symlink_rejected_before_execution(self):
        outside = self.root / 'outside-nm'
        outside.write_bytes(self.nm.read_bytes())
        outside.chmod(0o755)
        self.nm.unlink()
        self.nm.symlink_to(outside)
        self.reject()
        self.assertFalse(any(command[0] in [str(self.nm), str(outside)] for command in self.commands))

    def test_missing_tool_rejected(self):
        self.nm.unlink()
        self.reject(FileNotFoundError)

    def test_changed_framework_archive_rejected(self):
        (self.framework / 'ios-arm64/libidevice_ffi.a').write_bytes(b'changed fake archive')
        self.reject()
        self.assertFalse(any(c[0] == str(self.nm) and c[-1] != '--version' for c in self.commands))

    def test_changed_staged_archive_cannot_inherit_validation(self):
        (self.staged / 'ios-arm64/libidevice_ffi.a').write_bytes(b'changed fake staged archive')
        self.reject()
        self.assertFalse(any(c[0] == str(self.nm) and c[-1] != '--version' for c in self.commands))

    def test_copy_changed_during_original_inspection_cannot_inherit_validation(self):
        self.mutate_during_nm = self.staged / 'ios-arm64/libidevice_ffi.a'
        self.reject()

    def test_original_changed_during_inspection_rejected(self):
        self.mutate_during_nm = self.archive
        self.reject()

    def test_changed_staged_header_rejected(self):
        (self.staged / 'ios-arm64/Headers/idevice.h').write_bytes(b'changed fake header')
        self.reject()

    def test_clang_layout_failure_rejected(self):
        self.fail_clang = True
        self.reject(subprocess.CalledProcessError)

    def test_staged_clang_context_failure_rejected_after_original_passes(self):
        self.fail_clang_headers = self.staged / 'ios-arm64/Headers'
        self.reject(subprocess.CalledProcessError)


if __name__ == '__main__':
    unittest.main(verbosity=2)
