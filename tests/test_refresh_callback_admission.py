"""Admission identity remains exact in generated callback/dispatch contracts."""
import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('callback_admission', ROOT / 'scripts/patch_refresh_callback_admission.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

class CallbackAdmissionTests(unittest.TestCase):
    def test_canonical_host_is_idempotent(self):
        host = (ROOT / 'scripts/templates/combined_refresh_handler.swift').read_text()
        self.assertEqual(patcher.install_host(host), host)
        for bad in (host.replace('callbackAdmissionID: admissionID', 'callbackAdmissionID: other'),
                    host.replace('payload: ["state": terminalState, "admissionID": admissionID]',
                                 'payload: ["state": terminalState]')):
            with self.assertRaises(SystemExit): patcher.install_host(bad)

    def test_header_generation_and_drift_rejection(self):
        source = '- (void)onConnection:(NSXPCConnection*)connection;\n- (void)refreshAllAppsWithIdentifier:(NSString*)identifier mangledTypeName:(NSString *)mangledTypeName refreshRunID:(NSString* _Nullable)refreshRunID;'
        patched = patcher.install_header(source)
        self.assertEqual(patcher.install_header(patched), patched)
        with self.assertRaises(SystemExit):
            patcher.install_header(patched.replace('finishHostRefresh:', 'finishOtherRefresh:'))

    def test_wire_requires_canonical_admission(self):
        wire = (ROOT / 'scripts/templates/v3_wire_contract.swift').read_text()
        self.assertIn('Set(payload.keys) == Set(["state", "admissionID"]) &&\n                canonicalSecretToken(payload["admissionID"])', wire)

if __name__ == '__main__': unittest.main()
