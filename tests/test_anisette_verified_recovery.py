"""Production-extracted, in-memory-only Anisette legacy recovery transactions."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest

import test_embedded_keychain as keychain
from test_v3_account_diagnostics import declaration

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'


def recovery_sources():
    doubles = keychain.DOUBLES
    doubles = doubles.replace('    static var writes = 0', '''    static var writes = 0
    static var afterSet: ((String, String, Data) throws -> Void)?
    static var recoveryQueries: [String] = []
    static var malformedLegacyRow = false''')
    doubles = doubles.replace('Store.data[group, default: [:]][key] = value; Store.writes += 1',
        'Store.data[group, default: [:]][key] = value; Store.writes += 1\n            try Store.afterSet?(group, key, value)')
    doubles = doubles.replace('    let visible = [Store.processGroup, Store.keychainGroup]', '''    if let key = query[kSecAttrAccount] as? String { Store.recoveryQueries.append(key) }
    let visible = Array(Set([Store.processGroup, Store.keychainGroup] + Array(Store.data.keys)))''')
    doubles = doubles.replace('    if rows.isEmpty { return errSecItemNotFound }',
        '    if Store.malformedLegacyRow && query[kSecAttrAccount] != nil { rows.append([kSecAttrAccount: "identifier"]) }\n    if rows.isEmpty { return errSecItemNotFound }')
    return doubles + (ROOT / 'scripts/templates/embedded_shared_keychain.swift').read_text() + keychain.module.KEYCHAIN_ACCESS_ADAPTER


class AnisetteVerifiedRecoveryTests(unittest.TestCase):
    def test_executable_production_policy_and_journal(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; verified Anisette recovery executes in required macOS CI')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'Recovery.swift'
            executable = Path(directory) / 'recovery'
            source.write_text(recovery_sources() + (FIXTURES / 'anisette_verified_recovery_harness.swift').read_text())
            built = subprocess.run([compiler, '-swift-version', '5', '-parse-as-library', '-O', str(source), '-o', str(executable)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('VERIFIED_ANISETTE_RECOVERY_PASS', result.stdout)

    def test_production_boundary_is_scoped_and_proof_only(self):
        source = (ROOT / 'scripts/templates/embedded_shared_keychain.swift').read_text()
        candidate = declaration(source, '    static func anisetteRecoveryCandidate(')
        self.assertNotIn('.set(', candidate)
        self.assertNotIn('.remove(', candidate)
        self.assertNotIn('reconcileAnisetteRecoveryLocked', candidate)
        queries = declaration(source, '    private static func legacyAnisetteItems(')
        self.assertIn('["identifier", "adiPb"]', queries)
        self.assertIn('legacyItems(service: service, key: key)', queries)
        security = declaration(source, '    private static func legacyItems(')
        self.assertIn('if let key { query[kSecAttrAccount as String] = key }', security)
        self.assertIn('kSecAttrService as String: service', security)
        proof = declaration(source, 'struct LCAnisetteRecoveryProof:')
        self.assertIn('fileprivate init(candidate:', proof)
        commit = declaration(source, '    static func commitAnisetteRecovery(')
        self.assertIn('saveTransactionJournal(', commit)
        self.assertIn('try verifyReceipt()', commit)
        self.assertIn('try Task.checkCancellation()', commit)
        self.assertNotIn('key: "adiPb"', commit)
        self.assertNotIn('authKeys', commit)
        self.assertNotIn('Certificate', commit)
        reconcile = declaration(source, '    private static func reconcileAnisetteRecoveryLocked(')
        self.assertNotIn('.set(', reconcile)
        self.assertIn('observed == priorPair || observed == intendedPair', reconcile)

    def test_historical_slices_are_hash_pinned_and_minimal(self):
        directory = FIXTURES / 'anisette_upgrade_history'
        manifest = json.loads((directory / 'manifest.json').read_text())
        for item in manifest:
            source = (directory / item['file']).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), item['sha256'])
            self.assertLess(len(source), 6500)
            self.assertEqual(item['provenance'], 'verbatim production declaration')

    def test_historical_upgrade_executes_real_read_write_and_resolver(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; historical Anisette upgrade executes in required macOS CI')
        with tempfile.TemporaryDirectory() as directory:
            fixture = FIXTURES / 'anisette_upgrade_history'
            extra = (fixture / 'harness.swift').read_text()
            for name in ('legacy_read', 'legacy_read_string', 'legacy_write', 'mid_read', 'mid_write', 'mid_write_one', 'resolver'):
                extra = extra.replace('// INSERT_' + name.upper(), (fixture / (name + '.swift')).read_text())
            source = Path(directory) / 'Upgrade.swift'
            executable = Path(directory) / 'upgrade'
            source.write_text(recovery_sources() + extra)
            built = subprocess.run([compiler, '-swift-version', '5', '-parse-as-library', '-O', str(source), '-o', str(executable)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('HISTORICAL_ANISETTE_UPGRADE_PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
