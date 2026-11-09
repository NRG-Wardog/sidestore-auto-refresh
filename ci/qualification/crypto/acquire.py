#!/usr/bin/env python3
"""Acquire only reviewed macOS slices; never execute repository/build scripts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
PREFIX = 'OpenSSL.xcframework/macos-arm64_x86_64/OpenSSL.framework/Versions/A/'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def materialize(owner, output, read):
    pins = json.loads((HERE / 'pinned-inputs.json').read_text())[owner]
    inventory_file = HERE / (owner + '-headers.json')
    if sha(inventory_file.read_bytes()) != pins['header_inventory_sha256']:
        raise ValueError('Reviewed header inventory changed')
    expected = {'OpenSSL': pins['binary_sha256'], 'Resources/Info.plist': pins['info_sha256']}
    expected.update({'Headers/' + path: digest for path, digest in json.loads(inventory_file.read_text()).items()})
    base = output / owner / 'OpenSSL.framework/Versions/A'
    if base.exists():
        raise ValueError('Refusing pre-existing framework output')
    for relative, digest in expected.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid reviewed input path')
        data = read(relative)
        if sha(data) != digest:
            raise ValueError('Pinned framework input mismatch: ' + owner + '/' + relative)
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (base / 'OpenSSL').chmod(0o755)
    return expected

def acquire(output):
    pins = json.loads((HERE/'pinned-inputs.json').read_text())
    if output.exists():
        raise ValueError('Acquisition output must not exist')
    output.mkdir(parents=True)
    repo = output/'host-source'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True, timeout=120).strip()
    git('remote', 'add', 'origin', 'https://github.com/krzyzanowskim/OpenSSL.git')
    git('fetch', '--depth=1', '--filter=blob:none', 'origin', pins['host']['commit'])
    if git('rev-parse', 'FETCH_HEAD^{tree}') != pins['host']['tree']:
        raise ValueError('Pinned source tree differs')
    git('sparse-checkout', 'init', '--no-cone')
    (repo/'.git/info/sparse-checkout').write_text('/Frameworks/OpenSSL.xcframework/macos-arm64_x86_64/\n')
    git('checkout', '--detach', pins['host']['commit'])
    host = repo/'Frameworks'/PREFIX
    # Read only regular reviewed files, not checkout symlink aliases.
    def read_host(relative):
        file = host/relative
        if not file.is_file() or file.is_symlink():
            raise ValueError('Nonregular host input')
        return file.read_bytes()
    receipts = {'host': materialize('host', output, read_host)}
    archive = output/'embedded.zip'
    subprocess.run(['curl', '--fail', '--location', '--max-time', '180', '--retry', '0',
                    pins['embedded']['archive_url'], '--output', str(archive)], check=True, timeout=190)
    if sha(archive.read_bytes()) != pins['embedded']['archive_sha256']:
        raise ValueError('Embedded release archive checksum mismatch')
    with zipfile.ZipFile(archive) as zipped:
        if len(zipped.namelist()) != len(set(zipped.namelist())):
            raise ValueError('Duplicate release archive paths')
        # No general archive extraction or symlink materialization.
        receipts['embedded'] = materialize('embedded', output, lambda p: zipped.read(PREFIX+p))
    (output/'acquisition.json').write_text(json.dumps({'pins': pins, 'verified_files': receipts}, indent=2)+'\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    acquire(parser.parse_args().output)
