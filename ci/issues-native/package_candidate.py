"""Package only the exact same-run compiled products, never publish a release."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()



def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Product root must be a real directory')
    anchor = root.resolve(strict=True)
    shape = {'.': {'type': 'directory', 'mode': root.stat().st_mode & 0o7777}}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(anchor):
                raise ValueError('Product link escapes copied bundle: ' + name)
            shape[name] = {'link': target}
        elif path.is_file() or path.is_dir():
            shape[name] = {'mode': path.stat().st_mode & 0o7777,
                           'type': 'file' if path.is_file() else 'directory'}
        else:
            raise ValueError('Unsupported product entry: ' + name)
    files = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
    return files, shape


def main():
    receipt = json.loads(Path('evidence/receipt.json').read_text())
    for key, expected in [('status', 'COMPILE_ONLY_PASS_REQUIRES_REVIEW'),
                          ('workflow_commit', os.environ['GITHUB_SHA']),
                          ('run_id', os.environ['GITHUB_RUN_ID']),
                          ('run_attempt', os.environ['GITHUB_RUN_ATTEMPT'])]:
        if receipt[key] != expected: raise ValueError('Same-run compile receipt differs: ' + key)
    if receipt.get('evidence_sha256', {}).get('product-hashes.json') != sha(Path('evidence/product-hashes.json')):
        raise ValueError('Product manifest differs from compile receipt')
    from verify_candidate_graph import load_candidate_graph
    if receipt.get('requested_graph') != load_candidate_graph()['owners']:
        raise ValueError('Compile receipt graph differs from reviewed candidate')
    expected = json.loads(Path('evidence/product-hashes.json').read_text())
    if set(expected) != {'LiveContainer', 'SideStore'}: raise ValueError('Unexpected product set')
    root = Path('packaging-inputs')
    if root.exists() or root.is_symlink(): raise ValueError('Packaging inputs must be fresh')
    records = []
    for owner in expected:
        source = Path(os.environ['RUNNER_TEMP']) / ('native-' + owner) / 'Build/Products/Release-iphoneos' / (owner + '.app')
        observed, source_shape = inventory(source)
        if observed != expected[owner]: raise ValueError('Compiled product bytes changed: ' + owner)
        target = root / (owner + '.app')
        shutil.copytree(source, target, symlinks=True)
        copied, copied_shape = inventory(target)
        if copied_shape != source_shape: raise ValueError('Packaging copy modes/links differ: ' + owner)
        if copied != expected[owner]: raise ValueError('Packaging input copy differs: ' + owner)
        for path in [target / 'Info.plist', *sorted(target.glob('PlugIns/*.appex/Info.plist'))]:
            if path.is_symlink(): raise ValueError('Linked application metadata')
            before = path.read_bytes()
            value = plistlib.loads(before)
            value['LCBuilderCommit'] = os.environ['GITHUB_SHA']
            after = plistlib.dumps(value, fmt=plistlib.FMT_BINARY if before.startswith(b'bplist00') else plistlib.FMT_XML, sort_keys=False)
            path.write_bytes(after)
            records.append(dict(path=path.relative_to(root).as_posix(), before_sha256=hashlib.sha256(before).hexdigest(),
                                after_sha256=sha(path), metadata_change='LCBuilderCommit only'))
    Path('package-evidence/builder-stamping.json').write_text(json.dumps(records, indent=2))
    output = Path('diagnostic/LiveContainer-SideStore-issues-experimental.ipa')
    if output.exists() or output.is_symlink(): raise ValueError('IPA output must be fresh')
    subprocess.run([sys.executable, 'harness/scripts/package_livecontainer_combined.py', '--root', 'sources/LiveContainer',
                    '--host', str(root / 'LiveContainer.app'), '--side', str(root / 'SideStore.app'), '--output', str(output)], check=True)
    provenance = dict(schema_version=1, native_run_id=os.environ['GITHUB_RUN_ID'], native_run_attempt=os.environ['GITHUB_RUN_ATTEMPT'],
                      workflow_commit=os.environ['GITHUB_SHA'], source_graph=receipt['requested_graph'],
                      ipa_bytes=output.stat().st_size, ipa_sha256=sha(output), channel='experimental',
                      public_release_modified=False, physical_device_validation=False,
                      metadata_changes=records)
    Path('diagnostic/provenance.json').write_text(json.dumps(provenance, indent=2))
    Path('diagnostic/SHA256SUMS').write_text(sha(output) + '  ' + output.name + '\n')
    shutil.copyfile('evidence/receipt.json', 'diagnostic/native-receipt.json')
    if output.stat().st_size >= 524288000: raise ValueError('IPA exceeds supported artifact transfer size')


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / 'Product.app'
        root.mkdir()
        binary = root / 'binary'
        binary.write_bytes(b'not executable test fixture')
        binary.chmod(0o755)
        link = root / 'link'
        link.symlink_to('binary')
        files, shape = inventory(root)
        assert files['binary'] == files['link']
        copied = Path(temp) / 'Copy.app'
        shutil.copytree(root, copied, symlinks=True)
        assert inventory(copied) == (files, shape)
        (copied / 'binary').chmod(0o644)
        assert inventory(copied)[1] != shape
        for target in ('/etc/hosts', '../outside', 'missing'):
            link.unlink()
            link.symlink_to(target)
            try:
                inventory(root)
            except (ValueError, FileNotFoundError):
                pass
            else:
                raise AssertionError('unsafe link admitted: ' + target)
        root_link = Path(temp) / 'root-link'
        root_link.symlink_to(root)
        try:
            inventory(root_link)
        except ValueError:
            pass
        else:
            raise AssertionError('linked root admitted')
    print('PASS packaging inventory: copy bytes/modes, internal links, external/dangling/root-link rejection')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
    elif sys.argv[1:]:
        raise SystemExit('unknown arguments')
    else:
        main()
