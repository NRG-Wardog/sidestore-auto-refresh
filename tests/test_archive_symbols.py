#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_archive_symbols as proof

VALID = "archive.o:\n0000000000000010 T _lockdown_diag_rust_log\n0000000000000020 T _idevice_set_transport_log_callback\n"


class SymbolProofTests(unittest.TestCase):
    def test_exact_global_text_definitions_pass(self):
        self.assertEqual(proof.definitions(0, VALID), sorted(proof.REQUIRED))

    def test_observed_partial_output_with_reader_error_fails(self):
        with self.assertRaisesRegex(ValueError, "partial symbols"):
            proof.definitions(1, VALID)

    def test_missing_symbol_fails(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            proof.definitions(0, VALID.replace("_lockdown_diag_rust_log", "_unrelated"))

    def test_undefined_local_and_similar_names_are_not_definitions(self):
        for modified in (VALID.replace(" T ", " U "), VALID.replace(" T ", " t "),
                         VALID.replace("_lockdown_diag_rust_log", "_lockdown_diag_rust_logger")):
            with self.subTest(output=modified), self.assertRaises(ValueError):
                proof.definitions(0, modified)

    def test_bitcode_style_omitted_addresses_are_supported(self):
        self.assertEqual(proof.definitions(0, "        T _lockdown_diag_rust_log\n        T _idevice_set_transport_log_callback\n"), sorted(proof.REQUIRED))

    def test_matching_full_llvm_version_is_extracted(self):
        self.assertEqual(proof.llvm_version("LLVM version: 22.1.8"), proof.llvm_version("LLVM version 22.1.8-rust-1.98.1-stable"))
        self.assertNotEqual(proof.llvm_version("LLVM version 21.0.0"), proof.llvm_version("LLVM version: 22.1.8"))
        with self.assertRaises(ValueError):
            proof.llvm_version("Apple reader with no reported version")

    def test_component_is_selected_only_from_actual_host_listing(self):
        self.assertEqual(proof.component_name("llvm-tools-aarch64-apple-darwin (installed)\n", "aarch64-apple-darwin"), "llvm-tools")
        self.assertEqual(proof.component_name("llvm-tools-preview-aarch64-apple-darwin\n", "aarch64-apple-darwin"), "llvm-tools-preview")
        with self.assertRaises(ValueError):
            proof.component_name("llvm-tools-x86_64-apple-darwin", "aarch64-apple-darwin")

    def test_failed_reader_retains_stdout_stderr_command_and_status(self):
        with tempfile.TemporaryDirectory(prefix="symbol-proof-fixture-", dir=ROOT) as folder:
            output = Path(folder)
            def failed(command, stdout, stderr, check):
                stdout.write(VALID)
                stderr.write("Unknown attribute kind (105)\n")
                return subprocess.CompletedProcess(command, 1)
            with patch.object(proof.subprocess, "run", failed):
                code, text = proof.capture(["fixture-nm", "archive.a"], output, "idevice-symbols")
            self.assertEqual(code, 1)
            self.assertEqual(text, VALID)
            self.assertIn("Unknown attribute", (output / "idevice-symbols.stderr.txt").read_text())
            with self.assertRaises(ValueError):
                proof.definitions(code, text)

    def test_complete_wrapper_preserves_failed_proof_and_accepts_only_complete_read(self):
        for code, symbols, version, expected in ((1, VALID, "22.1.8", "FAIL"),
                (0, VALID.replace("_lockdown_diag_rust_log", "_missing"), "22.1.8", "FAIL"),
                (0, VALID, "21.0.0", "FAIL"), (0, VALID, "22.1.8", "PASS")):
            with self.subTest(code=code, version=version, expected=expected), tempfile.TemporaryDirectory(
                    prefix="reader-wrapper-fixture-", dir=ROOT) as folder:
                root = Path(folder)
                sysroot = root / "sysroot"
                reader = sysroot / "lib/rustlib/aarch64-apple-darwin/bin/llvm-nm"
                reader.parent.mkdir(parents=True)
                reader.write_text("Synthetic reader placeholder; never executed")
                reader.chmod(0o755)
                archive = root / "archive.a"
                archive.write_bytes(b"Synthetic archive placeholder")
                output = root / "proof"
                def fake_capture(command, directory, label):
                    values = {
                        "symbol-reader-rustc-version": "rustc 1.98.1 (fixture)\nhost: aarch64-apple-darwin\nLLVM version: 22.1.8\n",
                        "symbol-reader-sysroot": str(sysroot),
                        "symbol-reader-selected-sysroot": str(sysroot),
                        "symbol-reader-toolchain": "stable-aarch64-apple-darwin (default)",
                        "symbol-reader-components-available": "llvm-tools-aarch64-apple-darwin",
                        "symbol-reader-components-installed": "llvm-tools-aarch64-apple-darwin",
                        "symbol-reader-component-install": "",
                        "llvm-nm-version": "LLVM version " + version,
                        "idevice-symbols": symbols,
                    }
                    text = values[label]
                    (directory / (label + ".txt")).write_text(text)
                    (directory / (label + ".stderr.txt")).write_text("reader failure" if label == "idevice-symbols" and code else "")
                    return (code if label == "idevice-symbols" else 0), text
                with patch.object(proof, "capture", fake_capture):
                    if expected == "PASS":
                        proof.inspect(archive, output)
                    else:
                        with self.assertRaises(ValueError):
                            proof.inspect(archive, output)
                record = json.loads((output / "idevice-symbol-proof.json").read_text())
                self.assertEqual(record["status"], expected)
                if version == "22.1.8":
                    self.assertEqual(record["reader_exit_code"], code)
                    self.assertIn("--arch=arm64", record["commands"][-1]["command"])
                    self.assertEqual((output / "idevice-symbols.txt").read_text(), symbols)


if __name__ == "__main__":
    unittest.main(verbosity=2)
