"""Keep generated app consumers on the real SideStoreSupport module boundary."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock

import test_combined_service_startup as startup_tests
import test_v3_service as service_tests

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates"
SHARED_DIAGNOSTICS = ("LCAnisettePairError", "V3DiagnosticBuild", "V3DiagnosticCopy",
                      "V3DiagnosticPresentation")
HOST_TEMPLATES = ("v3_unified_shell.swift", "v3_behavioral_primitives.swift",
                  "livecontainer_refresh_scheduler.swift", "livecontainer_refresh_settings.swift",
                  "v3_ipa_staging.swift", "v3_secret_handoff.swift", "v3_setup_intent.swift")


def declaration(source, signature):
    start = source.index(signature)
    return source[start:startup_tests._matching_swift_brace(source, source.index("{", start))]


def generated_sources(directory):
    # Read exact pinned Git blobs and run the same shell/service/startup adapters
    # as the workflow. Do not give the app a copy of combined_failure.swift: that
    # masks internal-vs-public errors even though a single-module test compiles.
    fixture = service_tests.ServicePatchTests()
    live, side = fixture.fixture(directory)
    fixture.apply((live, side))
    startup = service_tests.module("patch_combined_service_startup")
    with mock.object(startup.subprocess, "check_output", side_effect=lambda args, **kw:
                     startup.PINS[0 if args[2] == str(live) else 1]):
        startup.patch(live, side, "v3")
    support = (live / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
    shell = (live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift").read_text(encoding="utf-8")
    # Retain whole generated Foundation-only units, including their real access
    # modifiers and dependency closure, while excluding platform UI/XPC glue.
    common_start = support.index("import Foundation\nimport CoreFoundation\n\npublic struct CombinedRefreshTargetPlan:")
    common_end = support.index("import Foundation\n#if canImport(Darwin)", common_start)
    wire_start = support.index("import Foundation\nimport CoreFoundation\nimport CryptoKit")
    wire_end = support.index("import Foundation\n\nenum V3SetupSnapshotOutcome:", wire_start)
    primitive_end = shell.index("import Foundation\nimport Security")
    return support, shell, support[common_start:common_end] + support[wire_start:wire_end], shell[:primitive_end]


class HostDiagnosticScopeTests(unittest.TestCase):
    def test_host_consumed_common_types_are_exported(self):
        common = (TEMPLATES / "combined_failure.swift").read_text(encoding="utf-8")
        host = "\n".join((TEMPLATES / name).read_text(encoding="utf-8") for name in HOST_TEMPLATES)
        missing = []
        for access, name in re.findall(r"^(?:(public|private|fileprivate) )?(?:struct|enum|class|func) (\w+)", common, re.M):
            if access != "public" and re.search(r"\b" + name + r"\b", host):
                missing.append(name)
        self.assertEqual(missing, [], "host templates consume internal SideStoreSupport declarations")
        for name in SHARED_DIAGNOSTICS:
            self.assertEqual(len(re.findall(r"^public enum " + name + r"\b", common, re.M)), 1)
            self.assertNotRegex(host, r"\benum " + name + r"\b")
        # Display/copy access must not expose the authentication or storage APIs.
        for name in ("V3AccountOperationError", "V3AuthenticationPhaseError",
                     "V3AccountDatabaseRecovery", "V3PostMutationPersistenceError"):
            self.assertNotRegex(common, r"\bpublic (?:struct|enum) " + name + r"\b")
        self.assertNotIn("embedded_shared_keychain.swift", host)

    def test_generated_support_owns_helpers_once_and_host_uses_import(self):
        with tempfile.TemporaryDirectory() as directory:
            support, shell, framework, primitives = generated_sources(Path(directory))
        self.assertIn("import SideStoreSupport\n", shell)
        self.assertTrue(framework.startswith((TEMPLATES / "combined_failure.swift").read_text(encoding="utf-8")))
        self.assertEqual(primitives, (TEMPLATES / "v3_behavioral_primitives.swift").read_text(encoding="utf-8") + "\n")
        for name in SHARED_DIAGNOSTICS:
            self.assertEqual(len(re.findall(r"^public enum " + name + r"\b", support, re.M)), 1)
            self.assertNotRegex(shell, r"\benum " + name + r"\b")
        recovery = declaration(shell, "    var recoveryStorageDiagnosticCode: String {")
        self.assertIn("recoveryStorageKind.flatMap { causes[$0] }", recovery)
        self.assertNotIn("causes[recoveryStorageKind]", recovery)

    def test_native_generated_framework_and_host_diagnostics(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated module boundary executes in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, shell, framework, primitives = generated_sources(root / "generated")
            support_path = root / "SideStoreSupport.swift"
            support_path.write_text(framework, encoding="utf-8")
            support_object = root / "SideStoreSupport.o"
            compiled = subprocess.run([
                compiler, "-parse-as-library", "-emit-module", "-emit-object",
                "-module-name", "SideStoreSupport", "-emit-module-path", str(root / "SideStoreSupport.swiftmodule"),
                str(support_path), "-o", str(support_object),
            ], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            # All primitives and these host methods come from generated output;
            # no declarations from the framework are re-injected into this file.
            host = "import Foundation\nimport SideStoreSupport\n" + primitives
            host += "\nstruct RecoveryProbe {\n    var recoveryStorageKind: String?\n"
            host += declaration(shell, "    var recoveryStorageDiagnosticCode: String {") + "\n}\n"
            host += "\nenum AuthProbe {\n" + declaration(shell, "    static func failureMessage(from failure: [String: Any]) -> String {") + "\n}\n"
            host += r'''
@main enum GeneratedHostDiagnosticProbe {
    static func main() {
        let unknown: [String?] = [nil, "", "futureUnknownKind"]
        for kind in unknown {
            precondition(RecoveryProbe(recoveryStorageKind: kind).recoveryStorageDiagnosticCode == "SS-SAVE-C11",
                "missing or unknown kinds must retain generic persistence diagnostics")
        }
        let known = [("malformedRecord", "F59"), ("incompatibleRecord", "F60"),
                     ("storageUnavailable", "F61"), ("lockUnavailable", "F62"),
                     ("readFailure", "F63"), ("deleteFailure", "F64")]
        for (kind, token) in known {
            precondition(RecoveryProbe(recoveryStorageKind: kind).recoveryStorageDiagnosticCode == "SS-SAVE-C11-" + token)
        }
        let auth = AuthProbe.failureMessage(from: ["kind": "anisetteIdentityStateInvalid"])
        precondition(auth.hasPrefix(LCAnisettePairError.safeMessage + "\nError ID: "))
        precondition(V3AuthTerminalFailureActionPolicy.guidance(kind: "anisetteIdentityStateInvalid", retryable: false)
            == LCAnisettePairError.recovery)
        let shown = V3DiagnosticPresentation.label("Refresh failed.", context: .refresh)
        precondition(shown == "Refresh failed.\nError ID: SS-REFRESH-UNKNOWN")
        precondition(V3DiagnosticPresentation.label(shown, context: .refresh) == shown)
        precondition(V3DiagnosticPresentation.label("Failed.", context: .operation).hasSuffix("SS-OPERATION-UNKNOWN"))
        precondition(V3DiagnosticPresentation.label("Failed.", context: .global).hasSuffix("SS-UI-UNKNOWN"))
        let copy = V3DiagnosticCopy.details(visibleMessage: shown, technical: "underlying_domain=redacted")
        precondition(copy.contains("visible_error_id=SS-REFRESH-UNKNOWN"))
        precondition(copy.contains("builder_commit=" + V3DiagnosticBuild.commit))
        let opaque = "SECRET-account@example.invalid"
        precondition(!V3DiagnosticCopy.details(visibleMessage: "Error ID: " + opaque, technical: "").contains(opaque))
        print("GENERATED_HOST_DIAGNOSTIC_SCOPE_PASS")
    }
}
'''
            host_path = root / "Host.swift"
            host_path.write_text(host, encoding="utf-8")
            executable = root / "host-diagnostics"
            compiled = subprocess.run([
                compiler, "-parse-as-library", "-I", str(root), str(host_path), str(support_object), "-o", str(executable),
            ], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("GENERATED_HOST_DIAGNOSTIC_SCOPE_PASS", ran.stdout)
