"""Verify v3 delegates IPA/app metadata parsing to pinned SideStore code."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_v3_service

RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
HARNESS = ROOT / "tests/fixtures/v3_install_metadata_parser_harness.swift"
SWIFTC = shutil.which("swiftc")


def pinned_source_root():
    override = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
    candidates = [Path(override)] if override else [
        ROOT.parent / "upstream/SideStore",
        ROOT / "work/EmbeddedSideStore",
        ROOT.parent.parent / "work/EmbeddedSideStore",
        ROOT.parents[3] / ".audit/upstream/SideStore",
    ]
    for candidate in candidates:
        if not (candidate / "AltStore/Managing Apps/AppManager.swift").is_file():
            continue
        check = subprocess.run(
            ["git", "-C", str(candidate), "cat-file", "-e",
             f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
            capture_output=True, text=True)
        if check.returncode == 0:
            return candidate
        if override:
            raise AssertionError(f"SideStore source must include pin {patch_v3_service.PINS[1]}")
    raise unittest.SkipTest("exact pinned SideStore source unavailable")


def pinned_app_manager():
    source_root = pinned_source_root()
    return subprocess.check_output(
        ["git", "-C", str(source_root), "show",
         f"{patch_v3_service.PINS[1]}:AltStore/Managing Apps/AppManager.swift"],
        text=True, encoding="utf-8")


def swift_function(source, signature):
    if source.count(signature) != 1:
        raise AssertionError(f"expected exactly one function signature: {signature}")
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced function: {signature}")


class V3InstallMetadataAdapterTests(unittest.TestCase):
    def test_generated_appmanager_parser_matches_pinned_body_except_visibility(self):
        original = pinned_app_manager()
        generated = patch_v3_service.headless_app_manager_metadata_parser(original)
        self.assertEqual(
            generated,
            patch_v3_service.headless_app_manager_metadata_parser(generated),
            "metadata visibility extraction must be idempotent")
        upstream_parser = swift_function(
            original,
            "private static func readAppMetadata(from url: URL, packageType: PackageType)")
        generated_parser = swift_function(
            generated,
            "static func readAppMetadata(from url: URL, packageType: PackageType)")
        self.assertEqual(
            generated_parser,
            upstream_parser.replace("private static func", "static func", 1),
            "generated production parser must differ from pinned source only in access level")

    def test_v3_resolution_uses_upstream_parser_for_both_ipa_inputs(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertEqual(runtime.count("AppManager.readAppMetadata(from:"), 2)
        self.assertNotIn("static func readAppMetadata(from url:", runtime)
        self.assertIn("V3IPAStaging.inspect(token: token", runtime)
        self.assertIn("return try await ipaTarget(url: url, scoped: false, sessionID: id)", runtime)

    def test_pinned_appmanager_parser_executes_for_app_bundle_fixtures(self):
        if not SWIFTC:
            self.skipTest("Swift executable harness runs in macOS CI")
        parser = swift_function(
            patch_v3_service.headless_app_manager_metadata_parser(pinned_app_manager()),
            "static func readAppMetadata(from url: URL, packageType: PackageType)")
        fixture = HARNESS.read_text(encoding="utf-8")
        self.assertEqual(fixture.count("__PRODUCTION_METADATA_PARSER__"), 1)
        source = fixture.replace("__PRODUCTION_METADATA_PARSER__", parser)
        with tempfile.TemporaryDirectory(prefix="v3-install-metadata-") as temporary:
            temp = Path(temporary)
            swift_file = temp / "main.swift"
            executable = temp / "metadata-parser"
            swift_file.write_text(source, encoding="utf-8")
            compiled = subprocess.run(
                [SWIFTC, "-parse-as-library", str(swift_file), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable), str(temp)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 PRODUCTION APPMANAGER METADATA PARSER PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
