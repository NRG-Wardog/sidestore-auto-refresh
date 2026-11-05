#!/usr/bin/env python3
"""Inspect built archives/headers only; never link or execute target-native code."""
import argparse, hashlib, json, os, plistlib, re, subprocess, sys, tomllib
from pathlib import Path

RUST_TOOLCHAIN = '1.98.1'
RUST_COMMIT = '48a229ceaefd4985c50990b14116b6d856af0985'
RUST_HOST = 'aarch64-apple-darwin'
LLVM_VERSION = '22.1.8'
COMPONENT_VERSION = '1.98.1 (48a229cea 2026-09-01)'
COMPONENT_DATE = '2026-09-03'
COMPONENT_URL = 'https://static.rust-lang.org/dist/2026-09-03/llvm-tools-1.98.1-aarch64-apple-darwin.tar.xz'
COMPONENT_SHA256 = '9b68ac31be7c6d25a08ce34dbd2c1d026b8006905e74415b6d9576678379ecbc'
NM_FLAGS = ['--arch=arm64', '--extern-only', '--defined-only', '--format=just-symbols']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_checked(command):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, file=sys.stderr, end='')
        raise


def resolve_pinned_nm():
    """Admit only the official host component in the already pinned Rust sysroot."""
    version = run_checked(['rustc', '+' + RUST_TOOLCHAIN, '--version', '--verbose']).stdout
    fields = dict(line.split(': ', 1) for line in version.splitlines() if ': ' in line)
    require(version.splitlines()[0] == 'rustc ' + COMPONENT_VERSION, 'Unexpected rustc version')
    for key, expected in [('release', RUST_TOOLCHAIN), ('commit-hash', RUST_COMMIT),
                          ('host', RUST_HOST), ('LLVM version', LLVM_VERSION)]:
        require(fields.get(key) == expected, 'Unexpected rustc ' + key)
    sysroot_text = run_checked(['rustc', '+' + RUST_TOOLCHAIN, '--print', 'sysroot']).stdout.strip()
    require(bool(sysroot_text) and '\n' not in sysroot_text and Path(sysroot_text).is_absolute(), 'Invalid Rust sysroot')
    sysroot = Path(sysroot_text).resolve(strict=True)
    component_output = run_checked(['rustup', 'component', 'list', '--toolchain', RUST_TOOLCHAIN, '--installed']).stdout
    components = {line.split()[0] for line in component_output.splitlines() if line.split()}
    require(bool(components & {'llvm-tools-' + RUST_HOST, 'llvm-tools-preview-' + RUST_HOST}), 'Pinned llvm-tools host component is not installed')
    installed_manifest = sysroot / 'lib/rustlib/multirust-channel-manifest.toml'
    require(installed_manifest.resolve(strict=True).is_relative_to(sysroot), 'Installed toolchain manifest escapes sysroot')
    manifest = tomllib.loads(installed_manifest.read_text())
    require(manifest.get('date') == COMPONENT_DATE, 'Unexpected Rust distribution date')
    package = manifest.get('pkg', {}).get('llvm-tools-preview', {})
    require(package.get('version') == COMPONENT_VERSION, 'Unexpected llvm-tools release')
    target = package.get('target', {}).get(RUST_HOST, {})
    require(target.get('available') is True and target.get('xz_url') == COMPONENT_URL
            and target.get('xz_hash') == COMPONENT_SHA256, 'Official pinned llvm-tools package metadata mismatch')
    expected_nm = sysroot / 'lib/rustlib' / RUST_HOST / 'bin/llvm-nm'
    nm = expected_nm.resolve(strict=True)
    require(nm.is_relative_to(sysroot) and nm == expected_nm, 'llvm-nm path escaped or aliased outside its exact host component path')
    require(nm.is_file() and os.access(nm, os.X_OK), 'Pinned llvm-nm is not executable')
    nm_version = run_checked([str(nm), '--version']).stdout
    versions = re.findall(r'\bLLVM version\s+(\d+\.\d+\.\d+)(?=[\s-]|$)', nm_version)
    require(versions == [LLVM_VERSION], 'llvm-nm LLVM version does not match pinned rustc')
    return nm, {
        'rust_toolchain': RUST_TOOLCHAIN, 'rustc_verbose_version': version,
        'host': RUST_HOST, 'sysroot': str(sysroot), 'installed_components': component_output,
        'installed_distribution_manifest_sha256': digest(installed_manifest),
        'component_url': COMPONENT_URL, 'component_sha256': COMPONENT_SHA256,
        'path': str(nm), 'sha256': digest(nm), 'version': nm_version,
        'provenance': 'Official rustup host component for the exact Rust release; installed manifest package URL/hash verified before execution. Executable hash is recorded evidence, not a substitute for distribution provenance.',
    }


def required_symbols():
    return json.loads((Path(__file__).parent / 'required-symbols.json').read_text())['symbols']


def inspect_symbols(nm, archive, symbols):
    command = [str(nm), *NM_FLAGS, str(archive)]
    completed = run_checked(command)
    defined = set(completed.stdout.splitlines())
    for symbol in symbols:
        require(symbol['mach_o_symbol'] in defined, 'Missing defined external symbol: ' + symbol['name'])
    return command, completed.stdout, completed.stderr


def selected_slice(root):
    info = plistlib.loads((root / 'Info.plist').read_bytes())
    libs = [x for x in info['AvailableLibraries'] if x['SupportedPlatform'] == 'ios' and not x.get('SupportedPlatformVariant') and x['SupportedArchitectures'] == ['arm64']]
    require(len(libs) == 1, 'Expected exactly one non-simulator iOS arm64 archive slice')
    lib = libs[0]
    folder = root / lib['LibraryIdentifier']
    archive = folder / lib['LibraryPath']
    headers = folder / lib['HeadersPath']
    for path in (archive, headers):
        require(path.resolve().is_relative_to(root.resolve()), 'Framework slice escapes its root')
    require(archive.is_file() and archive.suffix == '.a', 'Framework must contain the generated static archive')
    require((headers / 'idevice.h').is_file(), 'Framework lacks generated idevice.h')
    return archive, headers


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--framework', type=Path, required=True)
    p.add_argument('--staged-framework', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    symbols = required_symbols()
    archive = args.source / 'target/aarch64-apple-ios/release/libidevice_ffi.a'
    header = args.source / 'ffi/idevice.h'
    require(archive.is_file() and header.is_file(), 'Native build outputs are missing')
    nm, tool = resolve_pinned_nm()
    sdk = run_checked(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path']).stdout.strip()
    archive_sha256, header_sha256 = digest(archive), digest(header)
    artifacts = [('cargo', archive, args.source / 'ffi')]
    for name, root in [('framework', args.framework), ('staged', args.staged_framework)]:
        a, h = selected_slice(root)
        require(digest(a) == archive_sha256, 'Archive bytes changed in ' + name)
        require(digest(h / 'idevice.h') == header_sha256, 'Generated header bytes changed in ' + name)
        artifacts.append((name, a, h))
    # Symbol membership depends on archive bytes, not its staging path. Inspect
    # the original once, after every copy has passed byte-identity admission.
    nm_cmd, output, diagnostics = inspect_symbols(nm, archive, symbols)
    require(digest(archive) == archive_sha256, 'Cargo archive changed during symbol inspection')
    result = {'status': 'COMPILE_ONLY_ABI_INSPECTION_PASS_NOT_RUNTIME_VALIDATION', 'target_executed': False,
              'archive_sha256': archive_sha256, 'generated_header_sha256': header_sha256,
              'required_symbols': [x['name'] for x in symbols], 'symbol_reader': tool, 'artifacts': []}
    for name, a, headers in artifacts:
        # Keep all three compiler contexts: equal idevice.h alone does not prove
        # equivalent resolution of quoted/transitive includes in each directory.
        clang_cmd = ['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios15.0', '-isysroot', sdk, '-std=c11', '-Werror', '-fsyntax-only', '-I', str(headers), str(Path(__file__).parent / 'assert_header.c')]
        run_checked(clang_cmd)
        require(digest(a) == archive_sha256, 'Archive bytes changed before validation in ' + name)
        require(digest(headers / 'idevice.h') == header_sha256, 'Generated header bytes changed before validation in ' + name)
        row = {'name': name, 'archive': str(a), 'header': str(headers / 'idevice.h'),
               'archive_sha256': archive_sha256, 'clang_command': clang_cmd,
               'required_symbols_defined': True, 'header_signature_and_layout_checks': True}
        if name == 'cargo':
            row.update(symbol_validation='inspected_original', nm_command=nm_cmd,
                       nm_stdout_sha256=hashlib.sha256(output.encode()).hexdigest(), nm_stderr=diagnostics)
        else:
            row.update(symbol_validation='validated_by_byte_identity', symbol_validation_source='cargo')
        result['artifacts'].append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
