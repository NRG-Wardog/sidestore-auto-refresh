"""Execute generated V3 JIT-Less reader/policy with a mocked native LCUtils API."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LEGACY_READER_COMMIT = "4ef8f978af2e16b8a41f611f48f99a17aa5a066a"
SHELL_PATH = "scripts/templates/v3_unified_shell.swift"
PRIMITIVES_PATH = "scripts/templates/v3_behavioral_primitives.swift"
HARNESS_PATH = ROOT / "tests/fixtures/v3_jitless_native_readiness_harness.swift"
SWIFTC = shutil.which("swiftc")


def current_shell() -> str:
    local = (ROOT / SHELL_PATH).read_text(encoding="utf-8")
    if "LCUtils.certificateFacts(withKeyData:" in local:
        return local

    # The isolated test worktree is based before the in-flight production
    # template edit. Local integration can use the sibling production worktree;
    # merged CI reads the repository-local template above.
    development_copy = ROOT.parents[0] / "v3r77-provisioning" / SHELL_PATH
    if development_copy.is_file():
        newer = development_copy.read_text(encoding="utf-8")
        if "LCUtils.certificateFacts(withKeyData:" in newer:
            return newer
    raise AssertionError("the native-facts V3JITLessStatusReader is missing")


def legacy_shell() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{LEGACY_READER_COMMIT}:{SHELL_PATH}"],
        text=True,
        encoding="utf-8",
    )


def swift_declaration(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"production Swift declaration missing: {signature}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"production Swift declaration has no body: {signature}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated production Swift declaration: {signature}")


def swift_closure_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"production root notification closure missing: {signature}")
    opening = source.find("{ _ in", start)
    if opening < 0:
        raise AssertionError("root notification closure signature changed")

    depth = 0
    in_string = False
    escaped = False
    index = opening
    while index < len(source):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                body = source[opening + len("{ _ in"):index]
                return body
        index += 1
    raise AssertionError("unterminated production root notification closure")


ROOT_CERT_EVENT = '.onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in'


def production_slices(shell: str, behavior: str) -> list[str]:
    slices = [
        swift_declaration(behavior, "enum V3StatusSeverity:"),
        swift_declaration(behavior, "enum V3JITLessReadiness:"),
        swift_declaration(behavior, "enum V3JITLessReadinessPolicy"),
        swift_declaration(behavior, "struct V3JITLessPresentation"),
        swift_declaration(behavior, "enum V3JITLessCompletionPolicy"),
        swift_declaration(shell, "private struct V3PKCS12CertificateFacts"),
        swift_declaration(shell, "private struct V3JITLessStatusResult"),
        swift_declaration(shell, "private enum V3JITLessStatusReader"),
    ]

    # Control only the device OS fact. The reader body, copy state, parser call,
    # callback flow, identity comparison and production policy remain extracted
    # verbatim from the shell.
    reader_index = len(slices) - 1
    reader = slices[reader_index]
    system_os_query = "ProcessInfo.processInfo.operatingSystemVersion.majorVersion"
    if reader.count(system_os_query) != 1:
        raise AssertionError("production reader OS query shape changed")
    slices[reader_index] = reader.replace(system_os_query,
        "V3JITLessTestEnvironment.osMajor", 1)
    return slices


def setup_revision_slices(shell: str, behavior: str) -> tuple[list[str], list[str], str]:
    facts = [
        swift_declaration(shell, "struct V3SetupReadinessObservation: Equatable"),
        swift_declaration(shell, "enum V3SetupReadinessObservationPolicy"),
        swift_declaration(behavior, "enum V3SetupSnapshotOutcome:"),
        swift_declaration(behavior, "enum V3SetupReloadRecomputePolicy"),
    ]
    methods = [
        swift_declaration(shell, "    func beginSetupFactObservation() -> UInt64"),
        swift_declaration(shell, "    func isSetupFactRevisionCurrent(_ revision: UInt64) -> Bool"),
        swift_declaration(shell, "    func invalidateSetupFacts()"),
        swift_declaration(shell, "    func recordJITLessReadiness(_ readiness: V3JITLessReadiness"),
    ]
    handler = swift_closure_body(shell, ROOT_CERT_EVENT)
    return facts, methods, handler


def generated_harness(shell: str, handler_shell: str | None = None) -> str:
    behavior = (ROOT / PRIMITIVES_PATH).read_text(encoding="utf-8")
    fixture = HARNESS_PATH.read_text(encoding="utf-8")
    replacements = {
        "// {{PRODUCTION_READINESS_SLICES}}": "\n\n".join(production_slices(shell, behavior)),
        "// {{PRODUCTION_SETUP_REVISION_SLICES}}": "\n\n".join(
            setup_revision_slices(shell, behavior)[0]),
        "// {{PRODUCTION_SETUP_REVISION_METHODS}}": "\n\n".join(
            setup_revision_slices(shell, behavior)[1]),
        "// {{PRODUCTION_CERTIFICATE_UPDATE_HANDLER}}": setup_revision_slices(
            handler_shell or shell, behavior)[2],
    }
    for anchor, replacement in replacements.items():
        if fixture.count(anchor) != 1:
            raise AssertionError(f"harness production slice anchor changed: {anchor}")
        fixture = fixture.replace(anchor, replacement)
    if "{{PRODUCTION_" in fixture:
        raise AssertionError("a production shell declaration was not inserted into the harness")
    return fixture


def compile_harness(source: str, directory: Path, name: str) -> tuple[Path, subprocess.CompletedProcess[str]]:
    swift = directory / f"{name}.swift"
    executable = directory / name
    swift.write_text(source, encoding="utf-8")
    compiled = subprocess.run(
        [SWIFTC, "-swift-version", "5", "-parse-as-library", str(swift), "-o", str(executable)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return executable, compiled


class JITLessNativeReadinessTests(unittest.TestCase):
    def test_production_types_policy_and_reader_are_extracted(self):
        source = generated_harness(current_shell())
        self.assertIn("LCUtils.certificateFacts(withKeyData: data, password: password)", source)
        self.assertIn("LCUtils.validateCertificate", source)
        self.assertIn("V3JITLessReadinessPolicy.evaluate", source)
        self.assertIn("V3JITLessPresentation.present", source)
        self.assertIn("V3SetupReadinessObservationPolicy.mayApplyFreshObservation", source)
        self.assertIn("Task {", source)
        self.assertNotIn("SecPKCS12Import", source)
        handler = setup_revision_slices(current_shell(),
            (ROOT / PRIMITIVES_PATH).read_text(encoding="utf-8"))[2]
        self.assertLess(handler.index("status.invalidateSetupFacts()"), handler.index("Task {"))

    def test_native_facts_cases_pass_and_old_security_reader_fails_behaviorally(self):
        if not SWIFTC:
            self.skipTest("swiftc unavailable; generated readiness harness runs in macOS CI")
        current = generated_harness(current_shell())
        old = generated_harness(legacy_shell())
        self.assertIn("LCUtils.certificateFacts(withKeyData: data, password: password)", current)
        self.assertNotIn("SecPKCS12Import", current)
        self.assertIn("SecPKCS12Import", old)
        self.assertIn("LCUtils.getCertTeamId(withKeyData: data, password: password)", old)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            current_binary, current_compile = compile_harness(current, directory, "native-reader")
            self.assertEqual(0, current_compile.returncode,
                             current_compile.stdout + current_compile.stderr)
            current_run = subprocess.run([str(current_binary)], capture_output=True,
                                         text=True, timeout=30)
            self.assertEqual(0, current_run.returncode,
                             current_run.stdout + current_run.stderr)
            self.assertIn("V3_JITLESS_NATIVE_READINESS_PASS", current_run.stdout)

            old_binary, old_compile = compile_harness(old, directory, "security-reader")
            # The legacy reader must compile; the expected failure is behavioral.
            self.assertEqual(0, old_compile.returncode,
                             old_compile.stdout + old_compile.stderr)
            old_run = subprocess.run([str(old_binary)], capture_output=True,
                                     text=True, timeout=30)
            self.assertNotEqual(0, old_run.returncode,
                                "the Security-based reader unexpectedly accepted native facts")
            self.assertIn("NATIVE_FACTS_MATCH_DID_NOT_REACH_READY", old_run.stderr)

    def test_root_certificate_notification_invalidates_before_its_reload_task(self):
        if not SWIFTC:
            self.skipTest("swiftc unavailable; root race harness runs in macOS CI")
        current = generated_harness(current_shell())
        old_handler = generated_harness(current_shell(), handler_shell=legacy_shell())
        self.assertIn("status.invalidateSetupFacts()", current)
        handler = setup_revision_slices(current_shell(),
            (ROOT / PRIMITIVES_PATH).read_text(encoding="utf-8"))[2]
        self.assertLess(handler.index("status.invalidateSetupFacts()"), handler.index("Task {"))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            current_file = directory / "current-root-handler.swift"
            current_binary = directory / "current-root-handler"
            current_file.write_text(current, encoding="utf-8")
            compiled = subprocess.run(
                [SWIFTC, "-swift-version", "5", "-D", "V3_ROOT_HANDLER_ONLY",
                 "-parse-as-library", str(current_file), "-o", str(current_binary)],
                capture_output=True, text=True, timeout=90)
            self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(current_binary)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("V3_JITLESS_ROOT_INVALIDATION_PASS", result.stdout)

            old_file = directory / "old-root-handler.swift"
            old_binary = directory / "old-root-handler"
            old_file.write_text(old_handler, encoding="utf-8")
            old_compiled = subprocess.run(
                [SWIFTC, "-swift-version", "5", "-D", "V3_ROOT_HANDLER_ONLY",
                 "-parse-as-library", str(old_file), "-o", str(old_binary)],
                capture_output=True, text=True, timeout=90)
            self.assertEqual(0, old_compiled.returncode,
                             old_compiled.stdout + old_compiled.stderr)
            old_result = subprocess.run([str(old_binary)], capture_output=True,
                                        text=True, timeout=30)
            self.assertNotEqual(0, old_result.returncode,
                                "the pre-fix handler unexpectedly invalidated synchronously")
            self.assertIn("certificate notification must invalidate before scheduling its reload task",
                          old_result.stderr)


if __name__ == "__main__":
    unittest.main()
