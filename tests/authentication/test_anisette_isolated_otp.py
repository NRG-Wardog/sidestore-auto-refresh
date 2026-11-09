"""Current owner native fault gates plus cross-owner Swift metadata contracts.

No historical patch generator or duplicated VM implementation is executed.
Native scripts and their inputs run from an immutable pinned Git snapshot.
"""
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from tests.owner_source import owner_source, owner_pin, owner_root
from tests.support.product_sources import read_product_source

ROOT = Path(__file__).resolve().parents[2]
PATHS = ("Native/anisette_core_uc.cpp", "Native/include/anisette_core.h",
         "Native/Loader/elf_loader_emulator.cpp", "Native/Loader/elf_loader_emulator.h",
         "Sources/AnisetteDataProvider.swift")

def source(path):
    return owner_source(path, "AnisetteKit")


def native_trace_tokens():
    text = source(PATHS[0])
    block = text.split('static const char *const tokens[] = {', 1)[1].split('};', 1)[0]
    return re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', block)


def trace_variant(text, enabled, *, native=False):
    before = '#define V3_TEMPORARY_ANISETTE_TRACE_ENABLED 1' if native else 'static let enabled = true'
    after = '#define V3_TEMPORARY_ANISETTE_TRACE_ENABLED 0' if native else 'static let enabled = false'
    if text.count(before) != 1:
        raise AssertionError('Current trace switch is missing or ambiguous')
    return text if enabled else text.replace(before, after, 1)


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
    def test_current_owner_native_fault_and_logging_suites(self):
        # owner_source checks HEAD and the runner's working bytes. The entire
        # execution tree then comes from pinned Git blobs, never dirty helpers.
        scripts = ('.ci/native-tests/run_tests.py',
                   '.ci/native-logging/test_native_logging_contract.py')
        for script in scripts:
            owner_source(script, 'AnisetteKit')
        archive = subprocess.check_output([
            'git', '-C', str(owner_root('AnisetteKit')), 'archive',
            owner_pin('AnisetteKit'), 'Native', 'Sources', '.ci', '.github', 'Package.swift'])
        with tempfile.TemporaryDirectory(prefix='pinned-anisette-tests-') as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(archive)) as files:
                for entry in files:
                    path = Path(entry.name)
                    if path.is_absolute() or '..' in path.parts:
                        raise ValueError('Unsafe owner snapshot path')
                    destination = root / path
                    if entry.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    elif entry.isfile():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(files.extractfile(entry).read())
                    else:
                        raise ValueError('Non-regular owner snapshot entry')
            for script in scripts:
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(root / script)],
                        cwd=root, capture_output=True, text=True, timeout=240)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('Ran ', result.stderr)
                    self.assertIn('OK', result.stderr)

    def test_native_swift_allowlist_and_central_switch_fail_closed(self):
        swift = source(PATHS[4])
        parser = declaration(swift, '    func parseHeadersResponse(')
        self.assertIn('dict.removeValue(forKey: "v3_native_trace")', parser)
        self.assertLess(parser.index('dict.removeValue'), parser.index('AnisetteDataResponse(from: dict)'))
        self.assertIn('TemporaryAnisetteNativeTrace.suffix(temporaryTrace)', parser)
        self.assertEqual(len(native_trace_tokens()), len(set(native_trace_tokens())))
        central = declaration(read_product_source('combined_failure.swift'),
                              '    private enum NativeEvent:')
        central_tokens = re.findall(r'case \w+ = "([a-z_.]+)"', central)
        self.assertEqual(len(central_tokens), len(set(central_tokens)))
        self.assertEqual(set(central_tokens), set(native_trace_tokens()))
        disabled = trace_variant(source(PATHS[4]), False)
        self.assertIn('static let enabled = false', disabled)
        self.assertIn('guard enabled, let value,', disabled)
        with self.assertRaises(AssertionError): trace_variant('', False)

    def test_actual_package_swift_error_suffix_and_header_metadata_removal(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; native trace package harness runs in macOS CI')
        harness = (ROOT / 'tests/fixtures/anisette_native_trace_harness.swift').read_text()
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                swift = trace_variant(source(PATHS[4]), enabled)
                program = harness.replace('__PRODUCTION_RESPONSE_PARSER__', declaration(swift, '    func parseHeadersResponse('))
                program = program.replace('__PRODUCTION_JSON_PARSER__', declaration(swift, '    private func parseJSONString('))
                program = program.replace('__PRODUCTION_NATIVE_TRACE__', declaration(swift, 'private enum TemporaryAnisetteNativeTrace {'))
                program = '\n'.join((source('Sources/LoginDiagnosticTrace.swift'),
                    declaration(swift, 'private enum TemporaryADIConsumptionTrace {'),
                    declaration(swift, 'private enum TemporaryConstructorFaultTrace {'), program))
                path, binary = Path(directory) / 'main.swift', Path(directory) / 'native-trace-swift'
                path.write_text(program)
                result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                    capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), 'NATIVE_SWIFT_TRACE_PASS')

    def test_swift_probe_cannot_provision_or_change_derivation(self):
        swift = source(PATHS[4])
        self.assertIn('!existingBlob.isEmpty', swift)
        self.assertIn('storage: .memory(existingBlob: existingBlob)', swift)
        self.assertIn('guard result.newBlob == nil', swift)
        provider = declaration(swift, 'private struct IsolatedExistingBlobProvider:')
        for method in ('    func startProvision(', '    func endProvision('):
            self.assertIn('throw AnisetteError.invalidArgument', declaration(provider, method))
        self.assertIn('mkdtemp(buffer.baseAddress!)', swift)
        self.assertIn('AnisetteClient(provisioningDir: ownedRoot,', swift)
        self.assertIn('return try parseHeadersResponse(', swift)
        self.assertNotIn('URLSession', swift)
        native = source(PATHS[0])
        self.assertIn('std::string android_id = get_android_id_string(identifier);', native)
        self.assertEqual(native.count('get_android_id_string(identifier)'), 1)
