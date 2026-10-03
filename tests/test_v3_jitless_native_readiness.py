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


def generated_harness(shell: str) -> str:
    behavior = (ROOT / PRIMITIVES_PATH).read_text(encoding="utf-8")
    fixture = HARNESS_PATH.read_text(encoding="utf-8")
    anchor = "// {{PRODUCTION_READINESS_SLICES}}"
    if fixture.count(anchor) != 1:
        raise AssertionError("production readiness slice anchor changed")
    return fixture.replace(anchor, "\n\n".join(production_slices(shell, behavior)))


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
        self.assertNotIn("SecPKCS12Import", source)

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


if __name__ == "__main__":
    unittest.main()
