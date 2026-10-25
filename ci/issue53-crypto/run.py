#!/usr/bin/env python3
"""Hash-bound single-library synthetic probe. Not app/device qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(args):
    if args.output.exists():
        raise ValueError('Refusing to reuse an existing receipt path')
    if getattr(args, 'provider_module', None) is not None and getattr(args, 'builtin_providers', False):
        raise ValueError('Provider module and built-in-only mode are mutually exclusive')
    library = args.library.resolve(strict=True)
    if digest(library) != args.sha256:
        raise ValueError('Supplied library hash does not match')
    framework = next((p for p in library.parents if p.suffix == '.framework'), None)
    if framework:
        plist = library.parent / 'Resources/Info.plist'
        if not plist.is_file():
            plist = framework / 'Info.plist'
        info = plistlib.loads(plist.read_bytes())
        if platform.system() != 'Darwin' or info.get('DTPlatformName') != 'macosx':
            raise ValueError('Framework is not a verified macOS slice; iOS/simulator needs its own app runner')
    if platform.system() not in ('Linux', 'Darwin'):
        raise ValueError('Unsupported native executable platform')
    # No inherited provider search override or dynamic-loader injection.
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('LD_', 'DYLD_', 'OPENSSL_')):
            env.pop(key)
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        modules = work / 'providers'
        modules.mkdir()
        env['OPENSSL_MODULES'] = str(modules)
        module_hash = None
        if args.provider_module is not None:
            module = args.provider_module.resolve(strict=True)
            if module.name not in ('legacy.so', 'legacy.dylib') or digest(module) != args.provider_module_sha256:
                raise ValueError('Legacy provider module name/hash mismatch')
            module_hash = digest(module)
            copied = modules / module.name
            shutil.copyfile(module, copied)
            if digest(copied) != args.provider_module_sha256:
                raise ValueError('Copied provider module hash mismatch')
        elif not args.builtin_providers:
            raise ValueError('Require explicitly hash-bound legacy module or built-in-only provider mode')
        platform_output = None
        if framework:
            inspected = subprocess.run(['xcrun', 'vtool', '-show-build', str(library)], env=env,
                                       check=True, capture_output=True, text=True, timeout=15)
            platform_output = inspected.stdout
            platforms = re.findall(r'^\s*platform\s+(\S+)\s*$', platform_output, re.MULTILINE)
            if not platforms or any(p != 'MACOS' for p in platforms):
                raise ValueError('Actual Mach-O slice platform is not exclusively MACOS')
        headers_root = args.headers.resolve(strict=True)
        header_hashes = {}
        if getattr(args, 'header_inventory', None):
            for path in sorted(headers_root.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Symlink inside reviewed header inventory')
                if path.is_file():
                    header_hashes[str(path.relative_to(headers_root))] = digest(path)
            expected_headers = json.loads(args.header_inventory.read_text())
            if not header_hashes or header_hashes != expected_headers:
                raise ValueError('OpenSSL header inventory differs from reviewed bytes')
        include = headers_root
        if (headers_root / 'crypto.h').is_file():
            include = work / 'include'
            include.mkdir()
            (include / 'openssl').symlink_to(headers_root, target_is_directory=True)
        binary = work / 'probe'
        rpath = framework.parent if framework else library.parent
        command = [args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(include),
                   str(HERE / 'probe.c'), str(library), '-Wl,-rpath,' + str(rpath), '-o', str(binary)]
        if platform.system() == 'Linux':
            command += ['-ldl']
        subprocess.run(command, env=env, check=True, timeout=60)
        result = subprocess.run([str(binary)], cwd=work, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'Probe exit {result.returncode}\n{result.stdout[-8000:]}\n{result.stderr[-8000:]}')
        rows = result.stdout.splitlines()
        images = [row[6:] for row in rows if row.startswith('IMAGE ')]
        headers = [row[7:] for row in rows if row.startswith('HEADER ')]
        versions = [row[8:] for row in rows if row.startswith('VERSION ')]
        if len(images) != 1 or len(versions) != 1:
            raise ValueError('Missing or ambiguous loaded-library evidence')
        loaded = Path(images[0]).resolve(strict=True)
        if loaded != library or digest(loaded) != args.sha256:
            raise ValueError('Actually loaded image differs from the requested library')
        if headers != [args.expected_version]:
            raise ValueError('Compile-time headers differ from expected exact version')
        if versions[0] != args.expected_version:
            raise ValueError('Actual runtime version differs from expected exact version')
        provider_rows = [row.split(' ', 2)[1:] for row in rows if row.startswith('PROVIDER_IMAGE ')]
        if len(provider_rows) != 2 or {r[0] for r in provider_rows} != {'default', 'legacy'}:
            raise ValueError('Missing or ambiguous provider dispatch images')
        provider_images = {}
        for name, path in provider_rows:
            actual = Path(path).resolve(strict=True)
            expected_image = library if name == 'default' or args.builtin_providers else copied
            expected_digest = args.sha256 if expected_image == library else args.provider_module_sha256
            if actual != expected_image or digest(actual) != expected_digest:
                raise ValueError('Provider dispatch image differs from reviewed input')
            provider_images[name] = {'path': str(actual), 'sha256': digest(actual)}
        expected = {'explicit-default-and-legacy-provider-activation',
                    'synthetic-PEM-and-DER-key-certificate-roundtrip',
                    'modern-pkcs12-malformed-truncated', 'legacy-pkcs12-malformed-truncated',
                    'modern-pkcs12-roundtrip-wrong-password-tampered-mac',
                    'legacy-pkcs12-roundtrip-wrong-password-tampered-mac', 'synthetic-signature-produced'}
        cases = [row[5:] for row in rows if row.startswith('PASS ')]
        if set(cases) != expected or len(cases) != len(expected):
            raise ValueError('Probe did not execute the complete exact case set')
        # Independent consumer of public material, not the probe's verification API.
        # This does not claim a wholly independent cryptographic implementation.
        if platform.system() == 'Darwin':
            verifier = work / 'verify'
            subprocess.run(['xcrun', 'swiftc', str(HERE / 'verify.swift'), '-o', str(verifier)],
                           env=env, check=True, timeout=60)
            verified = subprocess.run([str(verifier)], cwd=work, env=env, check=True,
                                      capture_output=True, text=True, timeout=10)
            if verified.stdout.strip() != 'PASS Apple-Security-signature-and-tamper':
                raise ValueError('Independent Security verification did not complete')
            verifier_identity = 'Apple Security SecKeyVerifySignature'
        else:
            import cryptography
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            public = serialization.load_pem_public_key((work / 'public.pem').read_bytes())
            signature, message = (work / 'signature.bin').read_bytes(), (work / 'message.bin').read_bytes()
            public.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
            try:
                public.verify(signature, message + b'changed', padding.PKCS1v15(), hashes.SHA256())
            except InvalidSignature:
                pass
            else:
                raise ValueError('Independent consumer accepted altered message')
            verifier_identity = 'cryptography ' + cryptography.__version__
        receipt = {'schema': 'issue53-single-library-crypto-v1', 'status': 'PASS_SYNTHETIC_ONLY',
                   'system_smoke': args.system_smoke, 'shipped_app_runtime_qualified': False,
                   'device_executed': False, 'coexisting_images_tested': False,
                   'legacy_module_input_sha256': module_hash, 'builtin_providers_only': args.builtin_providers,
                   'openssl_header_inventory_bound': bool(header_hashes), 'header_inventory': header_hashes,
                   'macho_platform_qualified': platform_output is not None, 'macho_build_commands': platform_output,
                   'header_version': headers[0], 'provider_module_provenance_qualified': False,
                   'provider_dispatch_images_bound': True, 'provider_images': provider_images,
                   'library_sha256': args.sha256, 'loaded_image': str(loaded), 'version': versions[0],
                   'source_sha256': digest(HERE / 'probe.c'), 'runner_sha256': digest(Path(__file__)),
                   'platform': platform.platform(), 'cases': cases + ['external-consumer-signature-and-tamper'],
                   'signature_verifier': verifier_identity,
                   'signature_verifier_source_sha256': digest(HERE / 'verify.swift') if platform.system() == 'Darwin' else None,
                   'limits': ['Synthetic in-memory credentials only; no user keychain or account.',
                              'Provider dispatch images are hash-bound; transitive system-library provenance is not independently qualified.',
                              'No Apple CMS/Mach-O signing or full application consumer execution.',
                              'No claim that both shipped OpenSSL images can coexist or load correctly.']}
        args.output.write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--headers', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--header-inventory', type=Path)
    parser.add_argument('--expected-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument('--provider-module', type=Path)
    provider.add_argument('--builtin-providers', action='store_true')
    parser.add_argument('--provider-module-sha256')
    parser.add_argument('--system-smoke', action='store_true')
    args = parser.parse_args()
    if args.provider_module is not None and not args.provider_module_sha256:
        parser.error('--provider-module requires --provider-module-sha256')
    run(args)
