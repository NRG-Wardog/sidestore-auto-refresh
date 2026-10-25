#!/usr/bin/env python3
"""Use this Rust toolchain's LLVM reader; partial archive output never passes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REQUIRED = {"_lockdown_diag_rust_log", "_idevice_set_transport_log_callback"}


def llvm_version(text):
    match = re.search(r"LLVM version:?\s+(\d+\.\d+\.\d+)", text)
    if not match:
        raise ValueError("LLVM version missing from tool provenance")
    return match[1]


def component_name(listing, host):
    available = {line.split()[0] for line in listing.splitlines() if line.split()}
    for name in ("llvm-tools", "llvm-tools-preview"):
        if name + "-" + host in available:
            return name
    raise ValueError("Official LLVM tools component unavailable for the active Rust host")


def definitions(returncode, stdout):
    if returncode != 0:
        raise ValueError(f"LLVM reader failed with exit {returncode}; partial symbols are not proof")
    found = set(re.findall(r"^\s*(?:[0-9a-fA-F]+\s+)?T\s+(\S+)\s*$", stdout, re.M))
    if not REQUIRED.issubset(found):
        raise ValueError("Required external text definitions missing: " + ", ".join(sorted(REQUIRED - found)))
    return sorted(REQUIRED)


def capture(command, output, label):
    with (output / (label + ".txt")).open("w") as stdout, (output / (label + ".stderr.txt")).open("w") as stderr:
        result = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    text = (output / (label + ".txt")).read_text()
    return result.returncode, text


def inspect(archive, output):
    output.mkdir(parents=True, exist_ok=True)
    record = {"status": "FAIL", "archive": str(archive), "architecture": "arm64", "commands": []}
    def checked(command, label):
        code, text = capture(command, output, label)
        record["commands"].append({"command": command, "exit_code": code, "stdout": label + ".txt", "stderr": label + ".stderr.txt"})
        if code:
            raise ValueError(label + " failed with exit " + str(code))
        return text
    try:
        rust = checked(["rustc", "--version", "--verbose"], "symbol-reader-rustc-version")
        host_match = re.search(r"^host: (\S+)$", rust, re.M)
        if not host_match or host_match[1] != "aarch64-apple-darwin" or not rust.startswith("rustc 1.98.1 "):
            raise ValueError("Expected the already-reviewed Rust 1.98.1 ARM64 host")
        host = host_match[1]
        sysroot = Path(checked(["rustc", "--print", "sysroot"], "symbol-reader-sysroot").strip()).resolve(strict=True)
        active = checked(["rustup", "show", "active-toolchain"], "symbol-reader-toolchain").split()[0]
        selected_sysroot = Path(checked(["rustup", "run", active, "rustc", "--print", "sysroot"], "symbol-reader-selected-sysroot").strip()).resolve(strict=True)
        if selected_sysroot != sysroot:
            raise ValueError("rustup active toolchain does not match the producing rustc")
        listing = checked(["rustup", "component", "list", "--toolchain", active], "symbol-reader-components-available")
        component = component_name(listing, host)
        checked(["rustup", "component", "add", "--toolchain", active, component], "symbol-reader-component-install")
        installed = checked(["rustup", "component", "list", "--toolchain", active, "--installed"], "symbol-reader-components-installed")
        component_name(installed, host)
        # rustc's official PGO documentation specifies this component layout.
        # Derive both sysroot and host from the producing compiler, never PATH.
        reader = (sysroot / "lib/rustlib" / host / "bin/llvm-nm").resolve(strict=True)
        if not reader.is_relative_to(sysroot) or not reader.is_file() or not os.access(reader, os.X_OK):
            raise ValueError("Matching component's llvm-nm is missing or escaped its sysroot")
        version = checked([str(reader), "--version"], "llvm-nm-version")
        if llvm_version(rust) != llvm_version(version):
            raise ValueError("Reader LLVM version differs from the producing Rust compiler")
        record.update(toolchain=active, component=component, reader=str(reader),
            llvm_version=llvm_version(version), reader_sha256=hashlib.sha256(reader.read_bytes()).hexdigest(),
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        command = [str(reader), "--arch=arm64", "--extern-only", "--defined-only", "--format=bsd", str(archive)]
        code, symbols = capture(command, output, "idevice-symbols")
        record["commands"].append({"command": command, "exit_code": code,
            "stdout": "idevice-symbols.txt", "stderr": "idevice-symbols.stderr.txt"})
        record["reader_exit_code"] = code
        record["definitions"] = definitions(code, symbols)
        record["status"] = "PASS"
        return record
    except Exception as error:
        record["error"] = str(error)
        raise
    finally:
        (output / "idevice-symbol-proof.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    inspect(a.archive, a.output)
