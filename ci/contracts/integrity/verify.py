"""Validate reviewed current source bytes, without replaying historical Git layouts.

This file and manifest changes require review together. The manifest is deliberately
not generated at validation time. Runtime receipts and source-owner pins remain in
the native build, independently of this repository maintenance check.
"""
from pathlib import Path as _CIPath
from pathlib import Path, PurePosixPath
import hashlib
import json
import re

ROOT = (_CIPath(__file__).resolve().parents[3])
MANIFEST_SHA256 = '14a805d139641cb067ab21723b52bb2f333b6181aa92800a54d8dddef45e4b0d'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value, 'Duplicate manifest key')
        value[key] = item
    return value


def read(root, name):
    path = PurePosixPath(name)
    require(isinstance(name, str) and str(path) == name and not path.is_absolute()
            and '..' not in path.parts and bool(path.parts), 'Invalid source path')
    target = root.joinpath(*path.parts)
    require(not any(p.is_symlink() for p in (target, *target.parents)), 'Linked source path: '+name)
    require(target.is_file(), 'Missing source: '+name)
    return target.read_bytes()


def verify(root=ROOT, *, expected_manifest_sha256=MANIFEST_SHA256):
    root = Path(root).absolute()
    raw = read(root, 'ci/contracts/integrity/manifest.json')
    require(sha(raw) == expected_manifest_sha256, 'Reviewed manifest digest differs')
    m = json.loads(raw, object_pairs_hook=pairs)
    require(set(m) == {'schema', 'basis', 'files'}, 'Manifest fields differ')
    require(m['schema'] == 'current-source-integrity-v1', 'Manifest schema differs')
    require(re.fullmatch(r'[0-9a-f]{40}', m['basis']) is not None, 'Invalid review basis')
    require(isinstance(m['files'], dict) and bool(m['files']), 'Empty source scope')
    for name, expected in m['files'].items():
        require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected) is not None,
                'Invalid source digest')
        require(sha(read(root, name)) == expected, 'Reviewed source bytes differ: '+name)
    workflows = {p.relative_to(root).as_posix() for p in (root/'.github/workflows').iterdir()
                 if p.suffix in ('.yml', '.yaml')}
    require(workflows == {p for p in m['files'] if p.startswith('.github/workflows/')},
            'Unreviewed workflow inventory')
    for retired in ('scripts/patch_cf_bundle_scan.py', 'scripts/patch_embedded_sidestore_startup.py', 'scripts/templates/cf_bundle_scan.h', 'scripts/patch_sidesign_privacy.py', 'scripts/patch_sidesign_2fa_state.py', 'scripts/patch_app_layout.py', 'scripts/patch_multitask_dock.py', 'scripts/templates/multitask_dock_session_state.swift', 'scripts/patch_coredevice_idevice.py', 'scripts/patch_local_idevice_package.py', 'scripts/patch_lc_certificate_observation.py', 'tests/fixtures/lc_certificate_observation', 'scripts/patch_dead10cc_fix.py', 'ci/issue55-zopfli', 'ci/issue56-zopfli', 'ci/language-switch-zopfli', 'ci/maintained-build', '.github/workflows/build-current.yml', '.github/workflows/livecontainer-build.yml', '.github/workflows/release-dispatch.yml', '.github/workflows/maintained-entry-contract.yml', '.github/workflows/livecontainer-source-audit.yml', 'migration/diagnostics', 'migration/p0-native-basis-v1', 'migration/p1-native-basis-v1', 'migration/p0-constructor-diagnostics-v1', 'migration/p0-elf-metadata-bounds-v1', 'migration/p0-cxx-allocation-v1', 'migration/p2-composed-host-v1', 'migration/p0-full-login-diagnostics-v1', 'migration/historical', 'migration/local_replay_tests', '.ci', 'ci/validation-history', '.github/workflow-archive',
                    'ci/branch-cleanup', 'ci/branch-forwardports', 'ci/branch-semantic-review',
                    'ci/release-v6.0.0', 'ci/release-notes-v6.0.0-20261024'):
        path = root/retired
        require(not path.exists() and not path.is_symlink(), 'Retired history path: '+retired)
    return m


if __name__ == '__main__':
    import sys
    require(not sys.argv[1:], 'Unknown verification arguments')
    manifest = verify()
    print(f"PASS: {len(manifest['files'])} reviewed current files and exact workflow inventory verified")
