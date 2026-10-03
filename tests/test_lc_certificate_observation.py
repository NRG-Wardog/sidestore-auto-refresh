"""Exercise the pinned LiveContainer certificate-observation transform."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
PATCH_PATH = Path(os.environ.get("LC_CERTIFICATE_OBSERVATION_PATCHER",
                                ROOT / "scripts/patch_lc_certificate_observation.py"))
FIXTURE_ROOT = ROOT / "tests/fixtures/lc_certificate_observation"


def load_patcher():
    if not PATCH_PATH.is_file():
        raise FileNotFoundError(f"certificate observation patcher not found: {PATCH_PATH}")
    spec = importlib.util.spec_from_file_location("patch_lc_certificate_observation", PATCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned_sources(patcher):
    live = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
    sources = {}
    for relative in patcher.PATHS:
        if live:
            data = subprocess.check_output(["git", "-C", live, "show", f"{PIN}:{relative}"], text=True)
        else:
            fixture = FIXTURE_ROOT / Path(relative).name
            data = fixture.read_text(encoding="utf-8")
        sources[relative] = data
    return sources


def matching_swift_or_objc_brace(text: str, open_brace: int) -> int:
    depth = 0
    state = "code"
    comment_depth = 0
    index = open_brace
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if char == "\n": state = "code"
        elif state == "block_comment":
            if char == "/" and next_char == "*": comment_depth += 1; index += 1
            elif char == "*" and next_char == "/":
                comment_depth -= 1; index += 1
                if comment_depth == 0: state = "code"
        elif state == "string":
            if char == "\\": index += 1
            elif char == '"': state = "code"
        else:
            if char == "/" and next_char == "/": state = "line_comment"; index += 1
            elif char == "/" and next_char == "*": state = "block_comment"; comment_depth = 1; index += 1
            elif char == '"': state = "string"
            elif char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0: return index + 1
        index += 1
    raise AssertionError("unbalanced native source method")


def method_after_marker(text: str, marker: str, signature: str) -> str:
    marker_start = text.index(marker)
    start = text.index(signature, marker_start)
    opening = text.index("{", start)
    return text[start:matching_swift_or_objc_brace(text, opening)]


class CertificateObservationTransformTests(unittest.TestCase):
    def setUp(self):
        try:
            self.patcher = load_patcher()
        except FileNotFoundError as error:
            self.skipTest(str(error))
        self.sources = pinned_sources(self.patcher)

    def test_pinned_transform_is_idempotent_and_observation_only(self):
        original = dict(self.sources)
        generated = self.patcher.transform(self.sources)
        self.assertEqual(self.sources, original, "transform must not mutate its input map")
        self.assertEqual(self.patcher.transform(generated), generated)
        signer = generated["ZSign/zsign.mm"]
        lcutils = generated["LiveContainerSwiftUI/Utilities/LCUtils.m"]
        self.assertEqual(signer.count("V3_CANONICAL_CERTIFICATE_FACTS_V1"), 1)
        self.assertIn("asset.InitSimple(cert.bytes, (int)cert.length, nil, 0, string(passwordBytes))", signer)
        self.assertIn("X509_digest((X509 *)asset.m_x509Cert, EVP_sha256(), digest, &length)", signer)
        self.assertIn('@"teamIdentifier": team', signer)
        self.assertIn('@"identitySHA256": fingerprint', signer)
        self.assertNotIn('@"p12Data"', signer)
        self.assertNotIn('@"password"', signer)
        self.assertIn("completionHandler(2, nil, nil, @\"LiveContainer's certificate validator is unavailable.\")", lcutils)
        self.assertIn("NSString *password = [LCSharedUtils certificatePassword];", lcutils)
        self.assertIn("return [signer checkCert:certData pass:password completionHandler:completionHandler];", lcutils)

    def test_incomplete_sources_fail_closed_without_partial_generated_outputs(self):
        incomplete = dict(self.sources)
        incomplete["ZSign/zsign.mm"] = incomplete["ZSign/zsign.mm"].replace(
            "+ (NSString*)getTeamIdWithCert:", "+ (NSString*)getTeamIdentifierWithCert:", 1)
        before = dict(incomplete)
        with self.assertRaises(ValueError):
            self.patcher.transform(incomplete)
        self.assertEqual(incomplete, before)

        partial = dict(self.sources)
        partial["ZSign/zsign.mm"] += "\n// certificateFactsWith partial marker\n"
        with self.assertRaises(ValueError):
            self.patcher.transform(partial)

        validation = dict(self.sources)
        validation_path = "LiveContainerSwiftUI/Utilities/LCUtils.m"
        validation[validation_path] = validation[validation_path].replace("return -6;", "return -7;", 1)
        with self.assertRaises(ValueError):
            self.patcher.transform(validation)


class NativeCertificateObservationTests(unittest.TestCase):
    @property
    def native_required(self):
        return os.environ.get("REQUIRE_LC_CERTIFICATE_NATIVE_TESTS") == "1"

    def native_missing(self, reason):
        if self.native_required:
            self.fail(reason)
        self.skipTest(reason)

    def setUp(self):
        try:
            self.patcher = load_patcher()
        except FileNotFoundError as error:
            self.native_missing(str(error))
        live = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
        if not live:
            self.native_missing("LIVE_CONTAINER_TEST_SOURCE pinned checkout is supplied by macOS CI")
        actual = subprocess.check_output(["git", "-C", live, "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(actual, PIN, "native helper test must use the pinned LiveContainer checkout")
        self.live = Path(live)
        self.sources = pinned_sources(self.patcher)

    def _openssl3(self):
        if sys.platform != "darwin":
            self.native_missing("ZSign Objective-C++ integration requires macOS")
        brew = shutil.which("brew")
        if not brew:
            self.native_missing("brew OpenSSL 3 unavailable; CI needs `brew install openssl@3`")
        try:
            prefix = Path(subprocess.check_output([brew, "--prefix", "openssl@3"], text=True).strip())
        except subprocess.CalledProcessError:
            self.native_missing("brew OpenSSL 3 unavailable; CI needs `brew install openssl@3`")
        executable = prefix / "bin/openssl"
        if not executable.exists():
            self.native_missing("brew OpenSSL 3 binary missing; CI needs `brew install openssl@3`")
        version = subprocess.check_output([str(executable), "version"], text=True).strip()
        if not version.startswith("OpenSSL 3."):
            self.native_missing(f"native test requires OpenSSL 3, found {version}")
        return prefix, executable

    def test_native_init_simple_valid_wrong_password_malformed_multiple_and_validation_callback(self):
        compiler = shutil.which("clang++")
        if not compiler:
            self.native_missing("clang++ unavailable; Xcode command line tools are required for native ZSign test")
        openssl_prefix, openssl = self._openssl3()
        generated = self.patcher.transform(self.sources)
        signer_method = method_after_marker(generated["ZSign/zsign.mm"],
            "V3_CANONICAL_CERTIFICATE_FACTS_V1", "+ (NSDictionary<NSString *, NSString *> *)certificateFactsWithCert:")
        utils_method = method_after_marker(generated["LiveContainerSwiftUI/Utilities/LCUtils.m"],
            "V3_CANONICAL_CERTIFICATE_FACTS_V1", "+ (NSDictionary<NSString *, NSString *> *)certificateFactsWithKeyData:")
        validation_method = method_after_marker(generated["LiveContainerSwiftUI/Utilities/LCUtils.m"],
            "V3_CANONICAL_CERTIFICATE_VALIDATION_CALLBACK_V1", "+ (int)validateCertificateWithCompletionHandler:")
        signer_header = generated["ZSign/zsigner.h"]
        lcutils_header = generated["LiveContainerSwiftUI/Utilities/LCUtils.h"]
        signer_facts_declaration = next(line.strip() for line in signer_header.splitlines()
            if "certificateFactsWithCert:" in line)
        signer_check_declaration = next(line.strip() for line in signer_header.splitlines()
            if "checkCert:(NSData *)cert" in line)
        utils_facts_declaration = next(line.strip() for line in lcutils_header.splitlines()
            if "certificateFactsWithKeyData:" in line)
        validation_declaration = next(line.strip() for line in lcutils_header.splitlines()
            if "validateCertificateWithCompletionHandler:" in line)

        with tempfile.TemporaryDirectory() as native_directory:
            temp = Path(native_directory)
            # Compile the real, pristine ZSignAsset implementation using immutable
            # git-show files from the pin, never a builder-mutated source checkout.
            zsign = temp / "ZSign"
            tracked_zsign = subprocess.check_output(["git", "-C", str(self.live), "ls-tree", "-r",
                "--name-only", PIN, "--", "ZSign"], text=True).splitlines()
            for relative in tracked_zsign:
                contents = subprocess.check_output(["git", "-C", str(self.live), "show", f"{PIN}:{relative}"])
                destination = temp / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(contents)
            source_files = [zsign / "openssl.cpp",
                            *sorted((zsign / "common").glob("*.cpp"))]
            fixture = (ROOT / "tests/fixtures/lc_certificate_native_harness.mm").read_text(encoding="utf-8")
            values = {
                "$SIGNER_FACTS_DECLARATION$": signer_facts_declaration,
                "$CHECK_CERT_DECLARATION$": signer_check_declaration,
                "$LCUTILS_FACTS_DECLARATION$": utils_facts_declaration,
                "$VALIDATION_DECLARATION$": validation_declaration,
                "$SIGNER_FACTS_METHOD$": signer_method,
                "$LCUTILS_FACTS_METHOD$": utils_method,
                "$LCUTILS_VALIDATION_METHOD$": validation_method,
            }
            for marker, value in values.items():
                self.assertEqual(fixture.count(marker), 1)
                fixture = fixture.replace(marker, value, 1)
            harness_source = temp / "certificate_facts.mm"
            harness_source.write_text(fixture, encoding="utf-8")

            team = "TEAM123456"
            paths = []
            for ordinal in (1, 2):
                key = temp / f"key{ordinal}.pem"
                cert = temp / f"cert{ordinal}.pem"
                p12 = temp / f"valid{ordinal}.p12"
                subprocess.run([str(openssl), "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-sha256", "-keyout", str(key), "-out", str(cert), "-days", "2",
                    "-subj", f"/C=US/O=Observation Test/OU={team}/CN=Test Developer {ordinal}"],
                    check=True, capture_output=True, text=True)
                subprocess.run([str(openssl), "pkcs12", "-export", "-inkey", str(key), "-in", str(cert),
                    "-out", str(p12), "-passout", "pass:native-fixture-pass"],
                    check=True, capture_output=True, text=True)
                extracted = temp / f"extracted{ordinal}.pem"
                der = temp / f"cert{ordinal}.der"
                subprocess.run([str(openssl), "pkcs12", "-in", str(p12), "-clcerts", "-nokeys",
                    "-passin", "pass:native-fixture-pass", "-out", str(extracted)],
                    check=True, capture_output=True, text=True)
                subprocess.run([str(openssl), "x509", "-in", str(extracted), "-outform", "DER", "-out", str(der)],
                    check=True, capture_output=True, text=True)
                paths.append((p12, hashlib.sha256(der.read_bytes()).hexdigest()))
            malformed = temp / "malformed.p12"
            malformed.write_bytes(b"not a PKCS12 object\x00\x01")

            executable = temp / "certificate-facts-native"
            object_files = []
            for index, native_source in enumerate([harness_source, *source_files]):
                object_file = temp / f"native-{index}.o"
                compile_command = [compiler, "-std=c++17", "-ffunction-sections", "-fdata-sections",
                    "-Wno-deprecated-declarations", "-I", str(zsign), "-I", str(zsign / "common"),
                    "-I", str(openssl_prefix / "include")]
                if native_source.suffix == ".mm":
                    compile_command += ["-fobjc-arc", "-fblocks"]
                compile_command += ["-c", str(native_source), "-o", str(object_file)]
                compiled_object = subprocess.run(compile_command, capture_output=True, text=True)
                self.assertEqual(compiled_object.returncode, 0,
                    f"native source failed to compile ({native_source.name}):\n{compiled_object.stderr}")
                object_files.append(object_file)

            link_command = [compiler, *map(str, object_files), "-L", str(openssl_prefix / "lib"),
                "-Wl,-dead_strip", f"-Wl,-rpath,{openssl_prefix / 'lib'}",
                "-framework", "Foundation", "-lcrypto", "-lssl", "-lz",
                "-o", str(executable)]
            built = subprocess.run(link_command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable), str(paths[0][0]), str(paths[1][0]),
                str(malformed), team, paths[0][1], paths[1][1]],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("LC_CERTIFICATE_FACTS_NATIVE_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
