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


def copy_size_reports(source, destination):
    """Only non-secret JSON/log evidence crosses from binary staging."""
    if not source.exists(): return
    if source.is_symlink() or not source.is_dir(): raise ValueError('Invalid size report source')
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob('*')):
        if path.suffix not in {'.json', '.log'}: continue
        if path.is_symlink() or not path.is_file(): raise ValueError('Unsafe size report file')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if sha(target) != sha(path): raise ValueError('Size report copy differs')


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
    initial_root = Path('packaging-size-inputs')
    if initial_root.exists() or initial_root.is_symlink(): raise ValueError('Size inputs must be fresh')
    initial_root.mkdir()
    initial = initial_root / 'initial-combined.ipa'
    if initial.exists() or initial.is_symlink(): raise ValueError('Initial IPA must be fresh')
    if output.exists() or output.is_symlink(): raise ValueError('IPA output must be fresh')
    subprocess.run([sys.executable, 'harness/scripts/package_livecontainer_combined.py', '--root', 'sources/LiveContainer',
                    '--host', str(root / 'LiveContainer.app'), '--side', str(root / 'SideStore.app'), '--output', str(initial)], check=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
    from optimize_native_candidate import optimize_same_run_ipa
    # Revalidate sealed generated inputs and exact unstripped native products before packaging-only stripping.
    import record as native_record
    generated_openssl = {}
    for embedded, key in ((False, 'OpenSSLHost'), (True, 'OpenSSLEmbedded')):
        native_record.verify_generated_openssl_payload(embedded=embedded)
        snapshot = (native_record.generated_embedded_snapshot if embedded else native_record.generated_openssl_snapshot)(required=True, sealed=True)
        generated_root = native_record.S / snapshot['relative_path']
        if embedded: generated_root = generated_root / 'GeneratedDependencies/OpenSSL35'
        framework = native_record.generated_device_framework(generated_root)
        generated_openssl[key] = {'binary': framework / 'OpenSSL',
            'dsym': framework.parent / 'dSYMs/OpenSSL.framework.dSYM',
            'payload_receipt': native_record.E / ('generated-embedded-openssl-payload.json' if embedded else 'generated-openssl-payload.json')}
    initial_hash = sha(initial)
    try:
        optimized, size_report = optimize_same_run_ipa(initial, expected_sha=initial_hash,
            transfer_roots={owner: Path(os.environ['RUNNER_TEMP']) / ('native-transfer-' + owner) for owner in expected},
            receipt_path=Path('evidence/receipt.json'), work=Path('packaging-size-work'),
            output=Path('packaging-size-products'), retained=Path('size-diagnostics'),
            run_identity={key: receipt[key] for key in ('run_id', 'run_attempt', 'workflow_commit')},
            generated_openssl=generated_openssl)
    finally:
        copy_size_reports(Path('packaging-size-products'), Path('package-evidence/size-reports'))
    native_record.verify_generated_openssl_payload()
    native_record.verify_generated_openssl_payload(embedded=True)
    # The transform never edits compiled products, receipts, or the initial IPA.
    for owner in expected:
        source = Path(os.environ['RUNNER_TEMP']) / ('native-' + owner) / 'Build/Products/Release-iphoneos' / (owner + '.app')
        if inventory(source)[0] != expected[owner]: raise ValueError('Compiled input changed during packaging: ' + owner)
    if sha(initial) != initial_hash: raise ValueError('Initial combined IPA changed')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(optimized, output)
    if sha(output) != size_report['final_ipa_sha256']: raise ValueError('Final optimized IPA copy differs')
    Path('diagnostic/size-optimization.json').write_text(json.dumps(size_report, indent=2))
    provenance = dict(schema_version=1, native_run_id=os.environ['GITHUB_RUN_ID'], native_run_attempt=os.environ['GITHUB_RUN_ATTEMPT'],
                      workflow_commit=os.environ['GITHUB_SHA'], source_graph=receipt['requested_graph'],
                      ipa_bytes=output.stat().st_size, ipa_sha256=sha(output), channel='experimental',
                      public_release_modified=False, physical_device_validation=False,
                      metadata_changes=records, initial_ipa_sha256=initial_hash,
                      size_optimization_receipt_sha256=sha(Path('diagnostic/size-optimization.json')),
                      retained_original_ipa_and_symbols_sha256=size_report['retained_debug_sidecar_sha256'],
                      signing_policy='reviewed Apple ad-hoc; original designated requirement replaced; installer resigning required')
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
