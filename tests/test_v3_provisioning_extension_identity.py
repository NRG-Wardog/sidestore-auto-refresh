"""Run the generated pinned provisioning method against mocked DB/portal edges."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift"
HARNESS_PATH = ROOT / "tests/fixtures/v3_provisioning_extension_identity_harness.swift"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load production patch module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def startup_patcher():
    local = ROOT / "scripts/patch_combined_service_startup.py"
    module = load_module(local, "patch_combined_service_startup")
    if hasattr(module, "patch_provisioning_profile_requests"):
        return module

    # During isolated development the test-only worktree can precede the
    # production helper worktree. CI and the merged branch use the local module.
    development_copy = ROOT.parents[0] / "v3r77-provisioning/scripts/patch_combined_service_startup.py"
    if development_copy.is_file():
        candidate = load_module(development_copy, "patch_combined_service_startup_development")
        if hasattr(candidate, "patch_provisioning_profile_requests"):
            return candidate
    raise AssertionError("production patch_provisioning_profile_requests helper is missing")


def source_checkout() -> Path:
    configured = os.environ.get("SIDESTORE_TEST_SOURCE")
    if configured:
        return Path(configured)
    return ROOT.parents[0] / "v3r76-test-side"


def pinned_source(checkout: Path, reference: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(checkout), "show", f"{reference}:{SOURCE_PATH}"],
        text=True,
        encoding="utf-8",
    )


def swift_declaration(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"Generated/pinned Swift declaration missing: {signature}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"Swift declaration has no body: {signature}")

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
    raise AssertionError(f"Unterminated Swift declaration: {signature}")


def harness_source(checkout: Path) -> tuple[str, str, str]:
    patcher = startup_patcher()
    reference = patcher.PINS[1]
    pinned = pinned_source(checkout, reference)
    generated = patcher.patch_provisioning_profile_requests(pinned)
    pinned_method = swift_declaration(pinned, "    private func provisionAndFetchProfile(")
    generated_method = swift_declaration(generated, "    private func provisionAndFetchProfile(")
    preferred_lookup = swift_declaration(generated, "    private func getPreferredBundleID(")

    # Preserve the complete production method body; only rename the two method
    # declarations so old and generated implementations can run side by side.
    pinned_method = pinned_method.replace(
        "private func provisionAndFetchProfile(",
        "func provisionAndFetchProfilePinned(", 1,
    )
    generated_method = generated_method.replace(
        "private func provisionAndFetchProfile(",
        "func provisionAndFetchProfileGenerated(", 1,
    )
    fixture = HARNESS_PATH.read_text(encoding="utf-8")
    program = fixture.replace("// {{PINNED_PROVISION_AND_FETCH_PROFILE}}", pinned_method)
    program = program.replace("// {{GENERATED_PROVISION_AND_FETCH_PROFILE}}", generated_method)
    program = program.replace("// {{GENERATED_PREFERRED_BUNDLE_LOOKUP}}", preferred_lookup)
    if "{{PINNED_" in program or "{{GENERATED_" in program:
        raise AssertionError("A production source declaration was not inserted into the harness")
    return program, pinned_method, generated_method


class ProvisioningExtensionIdentityTests(unittest.TestCase):
    def test_production_generator_extracts_full_pinned_and_generated_methods(self):
        checkout = source_checkout()
        if not checkout.is_dir():
            self.skipTest("Pinned SideStore checkout unavailable; set SIDESTORE_TEST_SOURCE in macOS CI")
        patcher = startup_patcher()
        revision = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        self.assertEqual(patcher.PINS[1], revision,
                         "the generated method must come from the exact pinned SideStore source")

        program, pinned_method, generated_method = harness_source(checkout)
        self.assertIn("let preferredBundleID = await self.getPreferredBundleID", pinned_method)
        self.assertIn("parentID + suffix", generated_method)
        self.assertIn("targetAppBundle.bundleIdentifier.hasPrefix(parentAppBundle.bundleIdentifier + \".\")",
                      generated_method)
        self.assertIn("lcProvisioningBundleRequest(", generated_method)
        self.assertIn("NSPredicate(", program)
        self.assertIn("DeveloperPortalProxy.shared.downloadProvisioningProfile", generated_method)

    def test_pinned_and_generated_profile_identity_paths_execute(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; execute production Swift harness in macOS CI")
        checkout = source_checkout()
        if not checkout.is_dir():
            self.skipTest("Pinned SideStore checkout unavailable; set SIDESTORE_TEST_SOURCE in macOS CI")

        patcher = startup_patcher()
        revision = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        self.assertEqual(patcher.PINS[1], revision,
                         "the executable harness must use the exact pinned SideStore source")
        program, _, _ = harness_source(checkout)

        with tempfile.TemporaryDirectory() as temporary:
            swift = Path(temporary) / "ProvisioningExtensionIdentityHarness.swift"
            executable = Path(temporary) / "provisioning-extension-identity"
            swift.write_text(program, encoding="utf-8")
            compiled = subprocess.run(
                [compiler, "-swift-version", "5", "-parse-as-library", str(swift), "-o", str(executable)],
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("V3_PROVISIONING_EXTENSION_IDENTITY_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
