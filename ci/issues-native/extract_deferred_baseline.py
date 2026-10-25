"""Read six frozen historical source blobs from existing local Git objects only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from verify_comprehensive_diagnostics import load_contract, regular, require, digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    contract = load_contract(args.contract)['deferred_baseline']
    require(not args.sources.is_symlink(), 'Symlink source root')
    sources = args.sources.resolve(strict=True)
    require(not args.output.exists() and not args.output.is_symlink() and
            not args.receipt.exists() and not args.receipt.is_symlink(), 'Fresh baseline output required')
    require(not args.output.resolve().is_relative_to(sources), 'Baseline output inside sources')
    environment = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                       GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    def git(owner, *arguments):
        checkout = sources / owner
        require(checkout.is_dir() and not checkout.is_symlink() and (checkout / '.git').exists(),
                'Missing exact local Git checkout')
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(checkout), *arguments],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, env=environment, timeout=60)
        return result.stdout
    for owner, value in contract['owners'].items():
        commit = value['commit']
        require(git(owner, 'cat-file', '-t', commit).decode().strip() == 'commit', 'Baseline commit absent')
        require(git(owner, 'rev-parse', commit + '^{tree}').decode().strip() == value['tree'],
                'Baseline source tree differs')
    contents = {}
    for name, expected in contract['input_sha256'].items():
        owner, relative = name.split('/', 1)
        commit = contract['owners'][owner]['commit']
        raw = git(owner, 'show', commit + ':' + relative)
        require(digest(raw) == expected, 'Historical baseline blob differs: ' + name)
        contents[name] = raw
    args.output.mkdir(parents=True)
    for name, raw in contents.items():
        path = args.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        require(digest(regular(args.output, name).read_bytes()) == contract['input_sha256'][name],
                'Baseline output changed')
    write_json(args.receipt, {'schema': 'deferred-prior-pem-sources-v1', 'origin': 'exact-local-git-objects',
        'owners': contract['owners'], 'input_sha256': contract['input_sha256'],
        'network': False, 'target_execution': False})
    print('SIX_EXACT_DELIVERED_BASELINE_SOURCE_BLOBS_EXTRACTED_NO_NETWORK')


if __name__ == '__main__':
    main()
