"""Executable and integration coverage for authoritative sign-out reporting."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SignOutPostconditionTests(unittest.TestCase):
    def test_run_mutation_checks_only_signout_snapshot_before_success_copy(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("private func runMutation(")
        end = shell.index("\n    func signOut()", start)
        mutation = shell[start:end]

        self.assertIn("V3ServiceBridge.shared.request(operation: operation, target: target)", mutation)
        self.assertIn('operation == "signOut"', mutation)
        self.assertIn("V3SignOutOutcomePolicy.resolve(snapshot: snapshot)", mutation)
        self.assertIn("V3SignOutOutcomePolicy.successNotice(for: snapshot)", mutation)
        self.assertIn("presentUnconfirmedSignOut(signOutOutcome)", mutation)
        self.assertLess(mutation.index("accept(snapshot)"),
                        mutation.index("V3SignOutOutcomePolicy.resolve(snapshot: snapshot)"))
        self.assertIn('func signOut() { runMutation("signOut", successNotice: "Signed out successfully.") }', shell)

        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        service_start = service.index('case "signOut":')
        service_end = service.index('\n        case "syncAppIDs":', service_start)
        signout = service[service_start:service_end]
        self.assertIn("try V3BackendCommands.prepareSignOut()", signout)
        self.assertIn("AuthManager.shared.signOut(keepCertificate: true, keepAnisetteData: true)", signout)
        self.assertIn("return try snapshot()", signout)

        action = shell[shell.index("func performPrimaryIssueAction()"):
                       shell.index("@Published var presentation:", shell.index("func performPrimaryIssueAction()"))]
        self.assertIn("case .reloadStatus:", action)
        self.assertIn("reload()", action)

    def test_production_policy_executes_signout_postcondition_matrix(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; executable policy harness runs in macOS CI")

        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        fixture = (ROOT / "tests/fixtures/v3_signout_postcondition_harness.swift").read_text(encoding="utf-8")
        bool_start = wire.index("    static func strictBool(_ value: Any?) -> Bool? {")
        bool_end = wire.index("\n    static func strictInt", bool_start)
        production_bool_decoder = (
            "enum V3WireContract {\n" + wire[bool_start:bool_end] + "\n}"
        )
        policy_start = primitives.index("enum V3SignOutOutcome: Equatable {")
        policy_end = primitives.index("\nenum V3AnisetteFailureGuidance {", policy_start)
        production_signout_policy = primitives[policy_start:policy_end]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "signout-postcondition"
            source.write_text("import Foundation\nimport CoreFoundation\n" + production_bool_decoder + "\n" +
                              production_signout_policy + "\n" + fixture,
                              encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_SIGNOUT_POSTCONDITION_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
