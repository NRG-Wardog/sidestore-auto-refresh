"""Run maintained provisioning plus a child-identity mutant against DB/portal doubles."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = "SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift"
HARNESS_PATH = ROOT / "tests/fixtures/v3_provisioning_extension_identity_harness.swift"


import sys
sys.path.insert(0, str(ROOT / "tests"))
from owner_source import owner_source
from support.product_sources import declaration_end


def swift_declaration(source: str, signature: str) -> str:
    if source.count(signature) != 1:
        raise AssertionError(f"Expected one production declaration: {signature}")
    start = source.index(signature)
    return source[start:declaration_end(source, start)]


def harness_source() -> tuple[str, str, str]:
    source = owner_source(SOURCE_PATH, "SideStore")
    production = swift_declaration(source, "    private func provisionAndFetchProfile(")
    preferred_lookup = swift_declaration(source, "    private func getPreferredBundleID(")
    # Explicit negative control: restore the preferred-parent child-ID bug in
    # a copy of the current method. No historical generated implementation.
    anchor = "bundleID = parentID + suffix"
    if production.count(anchor) != 1:
        raise AssertionError("Current child identity mutation anchor changed")
    mutant = production.replace(anchor, "bundleID = preferredBundleID ?? (parentID + suffix)")
    pinned_method = mutant.replace("private func provisionAndFetchProfile(", "func provisionAndFetchProfilePinned(", 1)
    generated_method = production.replace("private func provisionAndFetchProfile(", "func provisionAndFetchProfileGenerated(", 1)
    fixture = HARNESS_PATH.read_text(encoding="utf-8")
    program = fixture.replace("// {{PINNED_PROVISION_AND_FETCH_PROFILE}}", pinned_method)
    program = program.replace("// {{GENERATED_PROVISION_AND_FETCH_PROFILE}}", generated_method)
    program = program.replace("// {{GENERATED_PREFERRED_BUNDLE_LOOKUP}}", preferred_lookup)
    if "{{PINNED_" in program or "{{GENERATED_" in program:
        raise AssertionError("A production source declaration was not inserted into the harness")
    return program, pinned_method, generated_method


class ProvisioningExtensionIdentityTests(unittest.TestCase):
    def test_current_method_and_explicit_identity_mutant_are_complete(self):
        program, pinned_method, generated_method = harness_source()
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
        program, _, _ = harness_source()

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
