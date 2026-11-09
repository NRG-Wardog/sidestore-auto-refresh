#!/usr/bin/env python3
"""Read-only compatibility evidence for checked Anisette staging.

This immutable manifest preserves the accepted artifact contract. It is not a
source generator and does not select current production dependency versions.
Changing the accepted contract requires reviewed hashes, not regeneration from
mutable templates. The exact manifest was captured from the retired builder.
"""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import stat
import subprocess

_MANIFEST = {'schema_version': 1,
 'anisettekit_revision': '1f5a7e36553cc865b873f222b87a6486c0bcc7bf',
 'marker': 'V3_CHECKED_ANISETTE_STAGING_V1',
 'native_symbol': 'get_anisette_headers_uc',
 'compiled_literal': 'Checked OTP staging failed',
 'swift_api': 'UnicornAnisetteDataProvider.getAnisetteHeaders',
 'dormant_native_symbol': 'get_anisette_headers_isolated_uc',
 'dormant_swift_api': 'IsolatedAnisetteOTPProvider.getExistingHeaders',
 'files': [{'path': 'Native/anisette_core_uc.cpp',
            'original_sha256': 'abee10baeca21b7d175872f1f05bd4c9b36f62dd563404e6e2a17b8a7c7c2d60',
            'prepared_sha256': '2c443523819368c8a48ada0eadb9dbff1d6657b73454db44d45c3cc626e40ab7'},
           {'path': 'Native/include/anisette_core.h',
            'original_sha256': '887326b1b3d78426f39472654b58e75876d2e57f91ce7b0ff1bc3e0343ba0672',
            'prepared_sha256': 'ca1ea4005f77dcca17c68965ba2dd1f65596e282a57adb4277f1d90d8bd11f02'},
           {'path': 'Native/Loader/elf_loader_emulator.cpp',
            'original_sha256': '9d2d00012d1019bf7e99c2b16a9af6570eec30f75a7a01cffa15be05c5b4384c',
            'prepared_sha256': '840b7f4ca30a12da86e0b0ae08a453ebb88e1cd74327f94a303b362b94e8604f'},
           {'path': 'Native/Loader/elf_loader_emulator.h',
            'original_sha256': '83b76029f2360edb70cef85abc09ec8e2f6c7ee122fb4f16b2ac7458f48a9c7a',
            'prepared_sha256': 'aacfd99cf5049d2208bc9d6af471411ca3acd277828ebcd1f27914f552f2f62c'},
           {'path': 'Sources/AnisetteDataProvider.swift',
            'original_sha256': 'f501ed0ee8ec218aa8e8c0237fca00ecc916193f6b8b14b77bf1b949850680fc',
            'prepared_sha256': '96a3af11c76ca7476162d25ca345b8e3aeb9f3343fc3e77b03481a61d6c79020'}]}
PATHS = tuple(entry['path'] for entry in _MANIFEST['files'])


def expected_evidence():
    """Return an independent copy of the accepted artifact evidence contract."""
    return copy.deepcopy(_MANIFEST)


def verify_prepared_checkout(root):
    """Verify the legacy no-pins collector boundary without modifying any file."""
    root = Path(root)
    expected = expected_evidence()
    pin = expected['anisettekit_revision']
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], env=env)
    if git('rev-parse', 'HEAD').decode().strip() != pin:
        raise ValueError('Anisette evidence requires the exact pinned revision')
    for entry in expected['files']:
        name = entry['path']
        target = root / name
        cursor = target.parent
        while True:
            if cursor.is_symlink():
                raise ValueError('Substituted Anisette source directory')
            if cursor == root:
                break
            cursor = cursor.parent
        details = target.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise ValueError('Missing or substituted Anisette source: ' + name)
        if hasattr(os, 'geteuid') and details.st_uid != os.geteuid():
            raise ValueError('Anisette source is not owned by the current user: ' + name)
        original = git('show', pin + ':' + name)
        if hashlib.sha256(original).hexdigest() != entry['original_sha256']:
            raise ValueError('Pinned Anisette source hash drift: ' + name)
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry['prepared_sha256']:
            raise ValueError('Anisette prepared-source hash mismatch: ' + name)
    return len(expected['files'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('anisette_root', type=Path)
    args = parser.parse_args()
    print('ANISETTE_EVIDENCE_PASS files=' + str(verify_prepared_checkout(args.anisette_root)))
