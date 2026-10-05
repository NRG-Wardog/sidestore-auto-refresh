"""Refuse direct Apple certificate changes while durable local state is unknown."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_v3_auth_recovery_audit import declaration

ROOT = Path(__file__).resolve().parents[1]


def template(name):
    return (ROOT / 'scripts/templates' / name).read_text()


class CertificateStorageAdmissionTests(unittest.TestCase):
    def test_production_refusal_precedes_reservation_and_preserves_existing_gates(self):
        service = template('v3_sidestore_service.swift')
        receive = declaration(service, 'private func receive(_ data: Data')
        storage = receive.index('if let failure = signingStorageFailure(operation: operation, id: id)')
        self.assertLess(receive.index('if mutation, directRecoveryRecord != nil, !directRecoveryControl'), storage)
        self.assertLess(receive.index('guard V3ServiceMutationAdmissionPolicy.admits('), storage)
        self.assertLess(storage, receive.index('V3DirectMutationRecoveryLifecycle.reserve('))
        self.assertLess(storage, receive.index('tasks[id] = Task'))
        refusal = declaration(receive, 'if let failure = signingStorageFailure(operation: operation, id: id)')
        self.assertIn('V3DirectMutationPreDispatchReplyPolicy.annotate', refusal)
        for mutation in ('clearPrepared', 'settleDirect', 'reserve(', 'markDirect'):
            self.assertNotIn(mutation, refusal)

    def test_after_suspension_rechecks_at_each_remote_mutation_boundary(self):
        service = template('v3_sidestore_service.swift')
        revoke = service[service.index('        case "certRevoke":'):service.index('        case "certCreate":')]
        self.assertLess(revoke.index('fetchCertificates(team: team)'), revoke.index('signingStorageFailure('))
        self.assertLess(revoke.index('signingStorageFailure('), revoke.index('revokeCertificate('))
        create = service[service.index('        case "certCreate":'):]
        self.assertLess(create.index('create: {'), create.index('self.signingStorageFailure('))
        self.assertLess(create.index('self.signingStorageFailure('), create.index('createCertificate('))
        self.assertIn('case "authReconcileStorage":', service)
        self.assertIn('try await V3HeadlessRuntime.shared.auth.reconcileStorage()', service)

    def test_production_gate_and_failure_round_trip_execute(self):
        swift = shutil.which('swiftc')
        if not swift:
            self.skipTest('Swift unavailable; production admission harness runs in macOS CI')
        service = template('v3_sidestore_service.swift')
        primitives = template('v3_behavioral_primitives.swift')
        gate = declaration(service, 'if let failure = signingStorageFailure(operation: operation, id: id)')
        helper = declaration(service, 'private func signingStorageFailure(operation: String, id: String)')
        dispatch_guard = declaration(service, 'if let failure = self.signingStorageFailure(operation: operation, id: id)')
        annotate = declaration(service, 'static func annotate(request: [String: Any], heldRequestID: String? = nil,')
        eligible = declaration(service, 'static func isEligible(request: [String: Any])')
        harness = (ROOT / 'tests/fixtures/v3_certificate_storage_admission_harness.swift').read_text()
        for marker, value in [('__PRODUCTION_GATE__', gate), ('__PRODUCTION_HELPER__', helper),
                              ('__PRODUCTION_DISPATCH_GUARD__', dispatch_guard),
                              ('__PRODUCTION_ANNOTATE__', annotate), ('__PRODUCTION_ELIGIBILITY__', eligible)]:
            harness = harness.replace(marker, value)
        source = (template('combined_failure.swift') + template('v3_wire_contract.swift') +
                  declaration(primitives, 'enum V3CertificateStorageAdmission {') + harness)
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / 'main.swift'
            binary = Path(temp) / 'admission'
            src.write_text(source)
            compiled = subprocess.run([swift, '-parse-as-library', str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('V3_CERTIFICATE_STORAGE_ADMISSION_PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
