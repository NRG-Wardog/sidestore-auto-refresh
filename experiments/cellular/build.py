"""Build only the standalone UIKit probe against the pinned patched Rust archive."""
import argparse
import hashlib
import json
import plistlib
from pathlib import Path
import re
import shlex
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent


def verify(ipa):
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        info_paths = [n for n in names if n.endswith('/Info.plist')]
        assert info_paths == ['Payload/CellularProbe.app/Info.plist'], info_paths
        assert not any('.appex/' in n or '.framework/' in n for n in names)
        assert not any('pairing' in n.lower() or 'mobileprovision' in n for n in names)
        info = plistlib.loads(archive.read(info_paths[0]))
        assert info['CFBundleIdentifier'] == 'com.nrgwardog.CellularProbe'
        assert info['CFBundleExecutable'] == 'CellularProbe'
        assert info['MinimumOSVersion'] == '15.0'
        assert not any(k in info for k in ('UIBackgroundModes', 'ALTAppGroups', 'BGTaskSchedulerPermittedIdentifiers'))
        code = archive.read('Payload/CellularProbe.app/CellularProbe')
        magic, cpu, _, kind = struct.unpack_from('<IIII', code)
        assert magic == 0xfeedfacf and cpu == 0x100000c and kind == 2, 'Expected arm64 Mach-O executable'
        for marker in (b'PROBE_BEGIN', b'COREDEVICE_RSD_BEGIN', b'BROWSE_BEGIN', b'RESOURCES_RELEASED', b'PATH_BEFORE', b'PATH_AFTER'):
            assert marker in code, marker
        return {'builder_commit': info['ProbeBuilderCommit'], 'app_bundles': 1,
                'extensions': 0, 'embedded_sidestore': False, 'device_test': 'NOT_RUN',
                'sha256': hashlib.sha256(Path(ipa).read_bytes()).hexdigest()}


def build(idevice, output, commit, native_log):
    assert re.fullmatch(r'[0-9a-f]{40}', commit)
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'Payload/CellularProbe.app'
    app.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    native = re.findall(r'native-static-libs: (.+)', native_log.read_text())
    if not native:
        raise RuntimeError('Rust did not report required native linker libraries')
    command = ['xcrun', '--sdk', 'iphoneos', 'clang', '-arch', 'arm64', '-miphoneos-version-min=15.0',
               '-isysroot', sdk, '-fobjc-arc', '-fmodules', '-O2', '-Wall',
               '-Werror=implicit-function-declaration', '-Werror=objc-method-access',
               '-I' + str(idevice / 'ffi'), str(ROOT / 'app/main.m'),
               str(idevice / 'target/aarch64-apple-ios/release/libidevice_ffi.a')]
    for framework in ('UIKit', 'Foundation', 'Network', 'UniformTypeIdentifiers'):
        command += ['-framework', framework]
    command += shlex.split(native[-1]) + ['-o', str(app / 'CellularProbe')]
    subprocess.run(command, check=True)
    info = plistlib.loads((ROOT / 'app/Info.plist').read_bytes())
    info['ProbeBuilderCommit'] = commit
    (app / 'Info.plist').write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(app)], check=True)
    subprocess.run(['xcrun', 'otool', '-L', str(app / 'CellularProbe')], check=True)
    ipa = output / 'CellularProbe.ipa'
    with zipfile.ZipFile(ipa, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
    report = verify(ipa)
    (output / 'CellularProbe.verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--idevice', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--commit')
    parser.add_argument('--native-log', type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.verify), indent=2))
    else:
        build(args.idevice.resolve(), args.output.resolve(), args.commit, args.native_log)
