"""Exercise the generated ODA catch/recovery path with only native OTP doubled."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_anisette_pair_precondition as pair
from test_v3_account_diagnostics import diagnostic_sources, declaration

ROOT = Path(__file__).resolve().parents[1]


def integration_source():
    source = pair.program().split('@main struct AnisettePairTests', 1)[0]
    source = source.replace('func SecItemCopyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<CFTypeRef?>) -> Int {',
        'func SecItemCopyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<CFTypeRef?>) -> Int {\n    RecoveryIntegrationQueries.count += 1', 1)
    source = source.replace('            let value = Store.data[group]?[key]',
        '            if key == "identifier" && RecoveryIntegrationQueries.cancelOnSnapshotRead {\n'
        '                RecoveryIntegrationQueries.cancelOnSnapshotRead = false\n'
        '                withUnsafeCurrentTask { $0?.cancel() }\n'
        '            }\n            let value = Store.data[group]?[key]', 1)
    return source + '\nenum RecoveryIntegrationQueries { static var count = 0; static var cancelOnSnapshotRead = false }\n'  + (ROOT / 'tests/fixtures/anisette_recovery_integration_harness.swift').read_text()


class RecoveryIntegrationTests(unittest.TestCase):
    def test_native_owned_temporary_probe_lifecycle(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; owned temporary probe harness runs in macOS CI')
        support = (ROOT / 'scripts/templates/anisette_legacy_recovery.swift').read_text()
        source = (ROOT / 'tests/fixtures/anisette_recovery_temporary_harness.swift').read_text().replace(
            '__PRODUCTION_TEMPORARY_PROBE__', declaration(support, 'private enum LCAnisetteIsolatedProbe {'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'
            binary = Path(directory) / 'temporary'
            path.write_text(source)
            result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('TEMPORARY_PROBE_PASS', result.stdout)

    def test_native_recovery_context_wire_and_cancellation(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; recovery wire harness runs in macOS CI')
        source = diagnostic_sources() + (ROOT / 'tests/fixtures/anisette_recovery_wire_harness.swift').read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'
            binary = Path(directory) / 'wire'
            path.write_text(source)
            result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('RECOVERY_WIRE_PASS', result.stdout)

    def test_generated_recovery_source_closure(self):
        source = integration_source()
        self.assertNotIn('__PRODUCTION_', source)
        self.assertIn('return try await recoverVerifiedLegacyIdentity(', source)
        self.assertIn('extension OnDeviceAnisetteManager {', source)
        self.assertIn('func commitAnisetteRecovery(', source)

    def test_native_generated_oda_recovery(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; actual ODA recovery harness runs in macOS CI')
        source = integration_source()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'
            binary = Path(directory) / 'recovery'
            cases = ('success', 'current_success', 'no_candidate', 'fresh', 'wrong_operation', 'wrong_code',
                         'rejected', 'malformed', 'changed', 'temporary', 'cancelled',
                         'current_changed', 'current_cancel', 'control_wrong_code', 'control_wrong_phase',
                         'keychain_read_failure', 'orphaned_pair', 'fresh_commit_failure', 'fresh_commit_state_changed')
            flag = 'public static let temporaryAnisetteTraceEnabled = true'
            self.assertEqual(source.count(flag), 1)
            for enabled in (True, False):
                program = source if enabled else source.replace(flag, flag.replace('true', 'false'), 1)
                path.write_text(program)
                result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                                        capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                for case in cases:
                    with self.subTest(trace_enabled=enabled, case=case):
                        result = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn('RECOVERY_INTEGRATION_PASS ' + case, result.stdout)

    def test_isolated_probe_keeps_existing_data_and_cleans_owned_root(self):
        source = (ROOT / 'scripts/templates/anisette_legacy_recovery.swift').read_text()
        self.assertIn('mkdtemp(pointer)', source)
        self.assertEqual(source.count('IsolatedAnisetteOTPProvider.getExistingHeaders('), 1)
        self.assertEqual(source.count('FileManager.default.removeItem(at: root)'), 1)
        self.assertIn('validateAndCreateAnisetteData(from: raw)', source)
        self.assertLess(source.index('removeItem(at: root)'), source.index('let (data, otp, mid)'))
        self.assertNotIn('startProvision', source)
        self.assertNotIn('endProvision', source)
        self.assertNotIn('URLSession', source)
        self.assertNotIn('signOut', source)
