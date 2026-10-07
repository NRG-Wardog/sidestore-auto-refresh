"""Execute transformed native boundaries using synthetic I/O and VM doubles.

No Apple libraries, Apple calls or real provisioning bytes are used. These tests
establish containment and failure behavior, not native ADI compatibility.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import re
import hashlib
import json
import stat

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('isolated_patch', ROOT / 'scripts/patch_anisette_isolated_otp.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def source(path):
    fixtures = ROOT / 'tests/fixtures/pinned_anisettekit'
    provenance = json.loads((fixtures / 'provenance.json').read_text())
    if provenance['revision'] != PATCH.PIN:
        raise AssertionError('Pinned source fixture revision drift')
    if os.environ.get('ANISETTEKIT_TEST_SOURCE'):
        data = subprocess.check_output(['git', '-C', os.environ['ANISETTEKIT_TEST_SOURCE'],
            'show', PATCH.PIN + ':' + path], env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
    else:
        data = (fixtures / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != provenance['sha256'][path]:
        raise AssertionError('Pinned source fixture hash drift: ' + path)
    return data.decode()


def declaration(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 0
    for end in range(opening, len(text)):
        if text[end] == '{': depth += 1
        elif text[end] == '}':
            depth -= 1
            if depth == 0: return text[start:end + 1]
    raise AssertionError(signature)


class IsolatedAnisetteOTPTests(unittest.TestCase):
    success_trace = ('arguments.ok,root.ok,uuid_dir.created,file.open.ok,file.stream.ok,'
        'file.write.ok,file.flush.ok,file.close.ok,file.read_open.ok,file.readback.ok,'
        'file.read_close.ok,file.rename.ok,vm.init.ok,setup.begin,library.load.ok,'
        'library.init.ok,uuid_dir.exists,provisioning_path.ok,android_id.ok,setup.ok,'
        'native.symbol.ok,native.otp.ok,native.output.ok,cleanup.ok').split(',')

    @classmethod
    def expected_fault_trace(cls, fault):
        before = lambda stage: cls.success_trace[:cls.success_trace.index(stage)]
        tail = ['cleanup.ok']
        if fault in ('ok', 'concurrent', 'mixed'): return cls.success_trace
        if fault == 'empty': return ['arguments.failed']
        if fault == 'rootpermissions': return ['arguments.ok', 'root.failed', 'cleanup.not_needed']
        if fault in ('mkdir', 'existing'):
            return ['arguments.ok', 'root.ok', 'uuid_dir.failed' if fault == 'mkdir' else 'uuid_dir.exists', 'cleanup.not_needed']
        failed_stages = {'open': 'file.open', 'fdopen': 'file.stream', 'write': 'file.write',
            'flush': 'file.flush', 'close': 'file.close', 'readopen': 'file.read_open',
            'read': 'file.readback', 'mismatch': 'file.readback', 'extra': 'file.readback',
            'readerror': 'file.readback', 'readclose': 'file.read_close', 'rename': 'file.rename',
            'construct': 'vm.init', 'load': 'library.load', 'setup': 'provisioning_path',
            'symbol': 'native.symbol', 'otp': 'native.otp', 'throw': 'native.otp',
            'length': 'native.output', 'outputread': 'native.output'}
        if fault == 'cleanup': return cls.success_trace[:-1] + ['cleanup.failed', 'cleanup.ok']
        if fault == 'alloc': return cls.success_trace + ['response.allocation.failed']
        stage = failed_stages[fault]
        after = []
        if fault in ('write',): after += ['file.flush.ok']
        if fault in ('write', 'flush', 'fdopen'): after += ['file.close.ok']
        if stage == 'file.readback': after += ['file.read_close.ok']
        if fault in ('load', 'setup'): after += ['setup.failed']
        return before(stage + '.ok') + [stage + '.failed'] + after + tail

    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('c++') or shutil.which('g++')
        if not compiler:
            raise unittest.SkipTest('C++ compiler required for synthetic native tests')
        cls.temporary = tempfile.TemporaryDirectory(prefix='isolated-adi-build-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.build = Path(cls.temporary.name)
        (cls.build / 'Loader').mkdir()
        (cls.build / 'Loader/elf_loader_emulator.h').write_text(
            (ROOT / 'tests/fixtures/isolated_anisette_vm_double.h').read_text())
        for name in ('Native/anisette_base.h', 'Native/anisette_base.cpp',
                     'Native/include/anisette_core.h', 'Native/anisette_core_uc.cpp'):
            text = source(name)
            if name in PATCH.PATHS:
                text = PATCH.transform(name, text)
            (cls.build / Path(name).name).write_text(text)
        (cls.build / 'main.cpp').write_text((ROOT / 'tests/fixtures/isolated_anisette_core_harness.cpp').read_text())
        cls.executable = cls.build / 'native-test'
        result = subprocess.run([compiler, '-std=c++17', '-pthread', '-I', str(cls.build),
            str(cls.build / 'main.cpp'), str(cls.build / 'anisette_base.cpp'), '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        if b'V3_ISOLATED_ANISETTE_OTP_V1' not in cls.executable.read_bytes():
            raise AssertionError('Compiled native probe marker absent')

    def test_actual_native_boundary_success_faults_and_concurrent_calls(self):
        for fault in ('ok', 'concurrent', 'mixed', 'mkdir', 'open', 'fdopen', 'write', 'flush', 'close',
                      'readopen', 'read', 'mismatch', 'rename', 'construct', 'load', 'setup',
                      'symbol', 'otp', 'throw', 'length', 'outputread', 'cleanup', 'existing',
                      'rootpermissions', 'empty', 'readclose', 'extra', 'readerror', 'alloc'):
            with self.subTest(fault=fault):
                result = subprocess.run([str(self.executable), fault], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('ISOLATED_NATIVE_OTP_PASS', result.stdout)
                self.assertIn('NORMAL_LOG_RETAINED', result.stdout)
                traces = [line.removeprefix('NATIVE_TRACE=').split(',') for line in result.stdout.splitlines()
                          if line.startswith('NATIVE_TRACE=')]
                expected = [self.expected_fault_trace(fault)]
                if fault == 'concurrent': expected *= 2
                if fault == 'mixed': expected += [['arguments.failed']]
                self.assertCountEqual(traces, expected)
                for trace in traces:
                    self.assertLessEqual(len(trace), 32)
                    self.assertLessEqual(len(','.join(trace).encode('ascii')), 1024)
                    self.assertTrue(set(trace) <= set(PATCH.native_trace_tokens()))
                for private in ('SYNTHETIC-EXISTING-BLOB', 'isolated-adi-test-',
                                '00010203-0405-0607-0809-0a0b0c0d0e0f', '0001020304050607'):
                    self.assertNotIn(private, result.stdout + result.stderr)

    def test_normal_native_trace_observes_unchecked_io_without_changing_failure_semantics(self):
        success = ('arguments.ok,setup.begin,vm.reused,library.cached,uuid_dir.created,'
            'provisioning_path.ok,android_id.ok,setup.ok,file.open.ok,file.write.ok,'
            'file.flush.not_checked,file.close.ok,file.readback.not_checked,native.symbol.ok,'
            'native.otp.ok,native.output.not_checked,cleanup.not_requested').split(',')
        cases = {'ok': success,
            'close': [event.replace('file.close.ok', 'file.close.failed') for event in success],
            'otp': success[:success.index('native.otp.ok')] + ['native.otp.failed', 'cleanup.not_requested'],
            'symbol': success[:success.index('native.symbol.ok')] + ['native.symbol.failed', 'cleanup.not_requested']}
        cases['write'] = [event.replace('file.write.ok', 'file.write.failed') for event in cases['otp']]
        cases['readopen'] = success[:success.index('file.open.ok')] + [
            'file.open.failed', 'file.readback.not_checked', 'native.symbol.ok', 'native.otp.failed', 'cleanup.not_requested']
        cases['mkdir'] = [event.replace('uuid_dir.created', 'uuid_dir.failed') for event in cases['readopen']]
        cases['cold'] = success[:2] + ['vm.init.ok', 'library.load.ok', 'library.init.ok'] + success[4:]
        for fault, expected in cases.items():
            with self.subTest(fault=fault):
                result = subprocess.run([str(self.executable), 'normal_' + fault], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('NATIVE_TRACE=' + ','.join(expected), result.stdout)
                self.assertIn('NORMAL_NATIVE_TRACE_PASS', result.stdout)
        result = subprocess.run([str(self.executable), 'normal_invalid'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('NORMAL_INVALID_ARGUMENT_PASS', result.stdout)

    def test_native_trace_is_bounded_and_can_be_disabled_without_changing_results(self):
        result = subprocess.run([str(self.executable), 'tracecap'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('NATIVE_TRACE=' + ','.join(['arguments.ok'] * 31 + ['trace.truncated']), result.stdout)
        from unittest.mock import patch
        core = self.build / 'anisette_core_uc.cpp'
        enabled = core.read_text()
        with patch.object(PATCH, 'temporary_trace_enabled', return_value=False):
            disabled = PATCH.transform(PATCH.PATHS[0], source(PATCH.PATHS[0]))
            self.assertIn('#define V3_TEMPORARY_ANISETTE_TRACE_ENABLED 0', disabled)
        binary = self.build / 'trace-disabled-test'
        try:
            core.write_text(disabled)
            compiled = subprocess.run([shutil.which('c++') or shutil.which('g++'), '-std=c++17', '-pthread',
                '-I', str(self.build), str(self.build / 'main.cpp'), str(self.build / 'anisette_base.cpp'),
                '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for fault in ('ok', 'otp', 'write', 'mixed', 'cleanup', 'normal_write', 'tracecap'):
                result = subprocess.run([str(binary), fault], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('NATIVE_TRACE=disabled', result.stdout)
                self.assertNotIn('NATIVE_TRACE=arguments', result.stdout)
        finally:
            core.write_text(enabled)

    def test_native_swift_allowlist_and_central_switch_fail_closed(self):
        from unittest.mock import patch
        swift = PATCH.transform(PATCH.PATHS[4], source(PATCH.PATHS[4]))
        parser = declaration(swift, '    func parseHeadersResponse(')
        self.assertIn('dict.removeValue(forKey: "v3_native_trace")', parser)
        self.assertLess(parser.index('dict.removeValue'), parser.index('AnisetteDataResponse(from: dict)'))
        self.assertIn('TemporaryAnisetteNativeTrace.suffix(temporaryTrace)', parser)
        self.assertEqual(len(PATCH.native_trace_tokens()), len(set(PATCH.native_trace_tokens())))
        central = declaration((ROOT / 'scripts/templates/combined_failure.swift').read_text(),
                              '    private enum NativeEvent:')
        central_tokens = re.findall(r'case \w+ = "([a-z_.]+)"', central)
        self.assertEqual(len(central_tokens), len(set(central_tokens)))
        self.assertEqual(set(central_tokens), set(PATCH.native_trace_tokens()))
        with patch.object(PATCH, 'temporary_trace_enabled', return_value=False):
            disabled = PATCH.transform(PATCH.PATHS[4], source(PATCH.PATHS[4]))
            self.assertIn('static let enabled = false', disabled)
            self.assertIn('guard enabled, let value,', disabled)
        with patch.object(PATCH.Path, 'read_text', return_value=''):
            with self.assertRaises(ValueError): PATCH.temporary_trace_enabled()

    def test_actual_package_swift_error_suffix_and_header_metadata_removal(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; native trace package harness runs in macOS CI')
        from unittest.mock import patch
        harness = (ROOT / 'tests/fixtures/anisette_native_trace_harness.swift').read_text()
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                with patch.object(PATCH, 'temporary_trace_enabled', return_value=enabled):
                    swift = PATCH.transform(PATCH.PATHS[4], source(PATCH.PATHS[4]))
                program = harness.replace('__PRODUCTION_RESPONSE_PARSER__', declaration(swift, '    func parseHeadersResponse('))
                program = program.replace('__PRODUCTION_JSON_PARSER__', declaration(swift, '    private func parseJSONString('))
                program = program.replace('__PRODUCTION_NATIVE_TRACE__', declaration(swift, 'private enum TemporaryAnisetteNativeTrace {'))
                path, binary = Path(directory) / 'main.swift', Path(directory) / 'native-trace-swift'
                path.write_text(program)
                result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                    capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), 'NATIVE_SWIFT_TRACE_PASS')

    def test_swift_probe_cannot_provision_or_change_derivation(self):
        swift = (ROOT / 'scripts/templates/isolated_anisette_otp.swift').read_text()
        self.assertIn('!existingBlob.isEmpty', swift)
        self.assertIn('storage: .memory(existingBlob: existingBlob)', swift)
        self.assertIn('guard result.newBlob == nil', swift)
        for method in ('    func startProvision(', '    func endProvision('):
            self.assertIn('throw AnisetteError.invalidArgument', declaration(swift, method))
        self.assertIn('mkdtemp(buffer.baseAddress!)', swift)
        self.assertIn('AnisetteClient(provisioningDir: ownedRoot,', swift)
        self.assertIn('return try parseHeadersResponse(', swift)
        self.assertNotIn('URLSession', swift)
        native = PATCH.transform(PATCH.PATHS[0], source(PATCH.PATHS[0]))
        self.assertIn('std::string android_id = get_android_id_string(identifier);', native)
        self.assertEqual(native.count('get_android_id_string(identifier)'), 1)
        self.assertNotIn('legacy', native.lower())

    def test_patch_all_files_derive_from_pin_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            originals = {path: source(path).encode() for path in PATCH.PATHS}
            for path, data in originals.items():
                (target / path).parent.mkdir(parents=True, exist_ok=True)
                (target / path).write_bytes(data)
            from unittest.mock import patch
            with patch.object(PATCH, 'expected_files', return_value={p: (v, None) for p, v in originals.items()}):
                self.assertEqual(PATCH.patch(target), 5)
                report = PATCH.evidence(target)
                self.assertEqual(report['native_symbol'], 'get_anisette_headers_isolated_uc')
                self.assertEqual(len(report['files']), 5)
                self.assertEqual(report, PATCH.expected_evidence())
                self.assertTrue(all(item['original_sha256'] != item['prepared_sha256'] for item in report['files']))
                self.assertEqual(PATCH.patch(target, verify=True), 5)
                self.assertEqual(PATCH.patch(target), 5)
                bad = target / PATCH.PATHS[-1]
                bad.write_text(bad.read_text() + '\n// drift\n')
                before = {(target / p): (target / p).read_bytes() for p in PATCH.PATHS}
                with self.assertRaises(ValueError):
                    PATCH.patch(target)
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_read_only_checkout_sources_are_atomically_replaced_with_original_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            originals = {path: source(path).encode() for path in PATCH.PATHS}
            modes = {path: (0o555 if index == 0 else 0o444) for index, path in enumerate(PATCH.PATHS)}
            for path, data in originals.items():
                (target / path).parent.mkdir(parents=True, exist_ok=True)
                (target / path).write_bytes(data)
                (target / path).chmod(modes[path])
            from unittest.mock import patch
            with patch.object(PATCH, 'expected_files', return_value={p: (v, None) for p, v in originals.items()}):
                # This makes the test meaningful even when run by root: direct
                # truncating writes are prohibited, independent of permissions.
                with patch.object(Path, 'write_bytes', side_effect=AssertionError('direct source write')):
                    self.assertEqual(PATCH.patch(target), 5)
                    self.assertEqual(PATCH.patch(target, verify=True), 5)
                    self.assertEqual(PATCH.patch(target), 5)
                self.assertEqual(PATCH.evidence(target), PATCH.expected_evidence())
                for path in PATCH.PATHS:
                    self.assertEqual(stat.S_IMODE((target / path).stat().st_mode), modes[path])
                self.assertEqual(list(target.rglob('.*.isolated-otp-*')), [])

    def test_failed_atomic_source_replace_preserves_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'source.swift'
            target.write_bytes(b'original')
            target.chmod(0o444)
            from unittest.mock import patch
            with patch.object(PATCH.os, 'replace', side_effect=PermissionError('injected replacement failure')):
                with self.assertRaises(PermissionError):
                    PATCH._replace_verified_source(target, b'prepared', b'original', target.stat())
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_actual_loader_lifetime_and_read_only_callbacks(self):
        compiler = shutil.which('c++') or shutil.which('g++')
        loader = PATCH.transform(PATCH.PATHS[2], source(PATCH.PATHS[2]))
        signatures = ['uint64_t PageAllocator::alloc(', 'EmulatorVM::EmulatorVM(',
                      'EmulatorVM::~EmulatorVM()', 'uint64_t EmulatorVM::write_bytes(',
                      'uint64_t EmulatorVM::write_string(', 'static bool deny_read_only_mutation(',
                      'static int linux_to_darwin_open_flags(', 'int32_t run_vm_procedure(',
                      'bool load_library_to_vm(']
        signatures += ['static void hook_' + name + '(' for name in ('open', 'close', 'write', 'ftruncate', 'mkdir', 'chmod', 'umask')]
        functions = '\n\n'.join(declaration(loader, name) for name in signatures)
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / 'Loader').mkdir()
            (build / 'unicorn').mkdir()
            (build / 'Loader/elf_loader_emulator.h').write_text(PATCH.transform(PATCH.PATHS[3], source(PATCH.PATHS[3])))
            for path in ('Native/anisette_base.h', 'Native/include/anisette_core.h', 'Native/anisette_base.cpp'):
                (build / Path(path).name).write_text(source(path))
            constants = sorted(set(re.findall(r'\bUC_[A-Z0-9_]+\b', functions)))
            # SIMD register constants must form the real contiguous range.
            constants = [x for x in constants if not x.startswith('UC_ARM64_REG_V')]
            constants += ['UC_ARM64_REG_V' + str(n) for n in range(32)]
            constants.remove('UC_ERR_OK')
            header = '#pragma once\n#include <cstdint>\n#include <cstddef>\nstruct uc_engine;\nusing uc_err=int;using uc_hook=uint64_t;\n'
            header += 'enum { UC_ERR_OK=0, ' + ', '.join(constants) + ' };\n'
            header += '''uc_err uc_open(int,int,uc_engine**);uc_err uc_close(uc_engine*);
uc_err uc_mem_map(uc_engine*,uint64_t,size_t,int);
uc_err uc_mem_write(uc_engine*,uint64_t,const void*,size_t);
uc_err uc_mem_read(uc_engine*,uint64_t,void*,size_t);
uc_err uc_reg_write(uc_engine*,int,const void*);uc_err uc_reg_read(uc_engine*,int,void*);
uc_err uc_hook_add(uc_engine*,uc_hook*,int,void*,void*,uint64_t,uint64_t);
uc_err uc_emu_start(uc_engine*,uint64_t,uint64_t,uint64_t,size_t);
const char* uc_strerror(uc_err);
'''
            (build / 'unicorn/unicorn.h').write_text(header)
            (build / 'loader_functions.inc').write_text(functions)
            (build / 'main.cpp').write_text((ROOT / 'tests/fixtures/isolated_anisette_loader_harness.cpp').read_text())
            binary = build / 'loader-test'
            compiled = subprocess.run([compiler, '-std=c++17', '-I', str(build), str(build / 'main.cpp'),
                str(build / 'anisette_base.cpp'), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('ISOLATED_LOADER_CONTAINMENT_PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
