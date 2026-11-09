"""Verify v3 delegates IPA/app metadata parsing to pinned SideStore code."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.owner_source import owner_source
from tests.support.product_sources import ProductSource

RUNTIME = ProductSource("v3_headless_runtime.swift")
HARNESS = ROOT / "tests/fixtures/v3_install_metadata_parser_harness.swift"
SWIFTC = shutil.which("swiftc")


def pinned_app_manager():
    return owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")


def pinned_archive_reader():
    return owner_source("Sources/Archiver/Archive.swift", "SideStore/Dependencies/SideSign")


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
    def test_current_parser_is_available_to_backend_without_private_adapter(self):
        source = pinned_app_manager()
        signature = "static func readAppMetadata(from url: URL, packageType: PackageType)"
        self.assertEqual(source.count(signature), 1)
        self.assertNotIn("private " + signature, source)

    def test_v3_resolution_uses_upstream_parser_for_both_ipa_inputs(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertEqual(runtime.count("AppManager.readAppMetadata(from:"), 2)
        self.assertNotIn("static func readAppMetadata(from url:", runtime)
        self.assertIn("V3IPAStaging.inspect(token: token", runtime)
        self.assertIn("return try await ipaTarget(url: url, scoped: false, sessionID: id)", runtime)

    def test_archive_reader_harness_stub_matches_pinned_sidesign_api(self):
        archive = pinned_archive_reader()
        next_file = swift_function(archive, "public func goToNextFile() -> Bool")
        self.assertIn("let nextIndex = currentIndex + 1", next_file)
        self.assertIn("if hasNext {\n                currentIndex = nextIndex", next_file)
        self.assertIn("return hasNext", next_file)
        fixture = HARNESS.read_text(encoding="utf-8")
        self.assertIn("func goToNextFile() -> Bool { false }", fixture)
        self.assertNotIn("func goToNextFile() throws", fixture)
        parser = swift_function(
            pinned_app_manager(),
            "static func readAppMetadata(from url: URL, packageType: PackageType)")
        self.assertIn("} while reader.goToNextFile()", parser)
        self.assertNotIn("try reader.goToNextFile()", parser)

    def test_pinned_appmanager_parser_executes_for_app_bundle_fixtures(self):
        if not SWIFTC:
            self.skipTest("Swift executable harness runs in macOS CI")
        parser = swift_function(
            pinned_app_manager(),
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
