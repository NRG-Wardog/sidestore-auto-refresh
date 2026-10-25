"""Rehearse a local, immutable upstream revision as data; never execute its shell."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from packaging_adapters import COMBINED_PACKAGING_RULES, apply_rules, configure_anchors, patch_surface

UPSTREAM_PATH = '.github/build_github.sh'


def rehearse(repository, commit, anchor_profile=None):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('An immutable full lowercase commit SHA is required')
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_TERMINAL_PROMPT='0')
    def git(*args):
        return subprocess.run(['git', '--no-pager', '-C', str(repository), *args],
                              env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15).stdout
    if git('cat-file', '-t', commit).strip() != b'commit':
        raise ValueError('Requested object is not a commit')
    blob = commit + ':' + UPSTREAM_PATH
    expected_bytes = int(git('cat-file', '-s', blob))
    if expected_bytes > 1024 * 1024:
        raise ValueError('Upstream packaging script exceeds review bound')
    original = git('cat-file', 'blob', blob)
    if len(original) != expected_bytes:
        raise ValueError('Upstream packaging blob size changed')
    result = rehearse_bytes(original, commit, anchor_profile)
    result['source_identity_verification'] = 'local_git_commit_and_blob'
    return result


def rehearse_bytes(original, commit, anchor_profile=None):
    """Transform acquired bytes; caller must separately establish source provenance."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or len(original) > 1024 * 1024:
        raise ValueError("Invalid bounded source identity")
    rules = COMBINED_PACKAGING_RULES
    profile_sha = None
    if anchor_profile is not None:
        required = {'schema_version', 'source_commit', 'source_sha256', 'anchor_overrides', 'rationale'}
        if not isinstance(anchor_profile, dict) or set(anchor_profile) != required:
            raise ValueError('Anchor profile schema differs')
        if (anchor_profile['schema_version'] != 1 or anchor_profile['source_commit'] != commit or
                anchor_profile['source_sha256'] != hashlib.sha256(original).hexdigest() or
                not isinstance(anchor_profile['rationale'], str) or not anchor_profile['rationale']):
            raise ValueError('Anchor profile provenance differs')
        rules = configure_anchors(rules, anchor_profile['anchor_overrides'])
        profile_sha = hashlib.sha256(json.dumps(anchor_profile, sort_keys=True).encode()).hexdigest()
    transformed = ('set -eu\n' + apply_rules(original.decode('utf-8'), rules)).encode('utf-8')
    return dict(schema_version=1, source_commit=commit, source_path=UPSTREAM_PATH,
                source_identity_verification='caller_supplied_acquired_bytes',
                source_sha256=hashlib.sha256(original).hexdigest(),
                adapted_sha256=hashlib.sha256(transformed).hexdigest(),
                source_bytes=len(original), adapted_bytes=len(transformed),
                rules=patch_surface(rules), anchor_profile_sha256=profile_sha,
                source_shell_executed=False, packaging_executed=False,
                native_compilation=False, physical_device_validation=False,
                result='ADAPTER_REHEARSAL_PASS_NOT_RUNTIME_VALIDATION')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--anchor-profile', type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Rehearsal output must be fresh')
    profile = None
    if args.anchor_profile is not None:
        if args.anchor_profile.stat().st_size > 65536:
            raise ValueError('Anchor profile exceeds review bound')
        profile = json.loads(args.anchor_profile.read_text())
    result = rehearse(args.repository, args.commit, profile)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
