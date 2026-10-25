"""Keep generated app consumers on the real SideStoreSupport module boundary."""
from pathlib import Path
import platform
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


def file_imports(source, allowed_modules=None):
    # Preserve each generated file's own imports and their conditional guards.
    # Never inject a module import on behalf of a consumer under test.
    lines = []
    for line in source.splitlines():
        imported = re.fullmatch(r"import (\w+)", line)
        if re.match(r"^\s*#(?:if|elseif|else|endif)\b", line):
            lines.append(line)
        elif imported and (allowed_modules is None or imported[1] in allowed_modules):
            lines.append(line)
    return "\n".join(lines) + "\n"


def diagnostic_references(source):
    return {name for name in SHARED_DIAGNOSTICS if re.search(r"\b" + name + r"\b", source)}


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
    consumers = {}
    for path in live.rglob("*.swift"):
        if "SideStoreSupport" in path.relative_to(live).parts:
            continue
        source = path.read_text(encoding="utf-8")
        if diagnostic_references(source):
            consumers[path.relative_to(live).as_posix()] = source
    return support, shell, support[common_start:common_end] + support[wire_start:wire_end], shell[:primitive_end], consumers


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
            support, shell, framework, primitives, consumers = generated_sources(Path(directory))
        self.assertTrue({"LiveContainerSwiftUI/App/AppDelegate.swift",
                         "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
                         "LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift"}.issubset(consumers))
        for path, source in consumers.items():
            with self.subTest(path=path):
                self.assertRegex(source, r"(?m)^import SideStoreSupport$",
                                 "every Swift file must import the module it consumes")
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
            _, shell, framework, primitives, _ = generated_sources(root / "generated")
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
            host = file_imports(shell, {"Foundation", "CoreFoundation", "SideStoreSupport"}) + primitives
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

    def test_native_each_generated_consumer_requires_its_own_import(self):
        compiler = shutil.which("swiftc")
        xcrun = shutil.which("xcrun")
        if not compiler or not xcrun:
            self.skipTest("iOS SDK unavailable; per-file diagnostic imports execute in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, framework, _, consumers = generated_sources(root / "generated")
            sdk = subprocess.run([xcrun, "--sdk", "iphonesimulator", "--show-sdk-path"],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(sdk.returncode, 0, sdk.stderr)
            target = platform.machine() + "-apple-ios17.0-simulator"
            flags = ["-sdk", sdk.stdout.strip(), "-target", target]
            support_path = root / "SideStoreSupport.swift"
            support_path.write_text(framework, encoding="utf-8")
            compiled = subprocess.run([
                compiler, *flags, "-parse-as-library", "-emit-module", "-module-name", "SideStoreSupport",
                "-emit-module-path", str(root / "SideStoreSupport.swiftmodule"), str(support_path),
            ], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            reads = {
                "LCAnisettePairError": "_ = LCAnisettePairError.safeMessage; _ = LCAnisettePairError.recovery",
                "V3DiagnosticBuild": "_ = V3DiagnosticBuild.commit",
                "V3DiagnosticCopy": '_ = V3DiagnosticCopy.details(visibleMessage: "", technical: "")',
                "V3DiagnosticPresentation": '_ = V3DiagnosticPresentation.label("", context: .refresh)',
            }
            for relative, source in consumers.items():
                with self.subTest(path=relative):
                    references = diagnostic_references(source)
                    body = "func checkDiagnosticDependencies() {\n" + "\n".join(reads[name] for name in sorted(references)) + "\n}\n"
                    if relative.endswith("LCEmbeddedSideStoreRefreshView.swift"):
                        # Typecheck the exact generated Text/Button UI too. Only
                        # the surrounding View and sample state are scaffolding.
                        body += "@MainActor struct SettingsDiagnosticsProbe: View {\nlet lastError = \"Failed\"\n@ViewBuilder var body: some View {\n"
                        body += declaration(source, "                if !lastError.isEmpty {") + "\n}\n}\n"
                    imports = file_imports(source)
                    probe = root / Path(relative).name
                    probe.write_text(imports + body, encoding="utf-8")
                    command = [compiler, *flags, "-typecheck", "-I", str(root), str(probe)]
                    compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    self.assertEqual(compiled.returncode, 0, relative + "\n" + compiled.stderr)
                    # A sibling file's import must never rescue this one. The
                    # negative case uses identical code and only removes its
                    # own production import, with no umbrella/@testable import.
                    self.assertIn("import SideStoreSupport\n", imports)
                    probe.write_text(imports.replace("import SideStoreSupport\n", "") + body, encoding="utf-8")
                    rejected = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    self.assertNotEqual(rejected.returncode, 0, relative + " compiled without its dependency import")
                    for name in references:
                        self.assertIn("cannot find '" + name + "' in scope", rejected.stderr)
