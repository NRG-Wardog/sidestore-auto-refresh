"""Read-only #53 admission using hash-reviewed native validators, never artifact code.

This adds no device, authentication or native execution. The exact successful
run/archive must first be acquired and projected by the existing source-bound
pipeline. A source-only draft cannot admit a previous builder's receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

import initial_rejection_package_admission as projection

safe = projection.safe
FILES = ('idevice-issue-tests.json', 'idevice-tests.log', 'idevice-ffi-tests.log',
         'idevice-listener-generated-abi.log')
REPORT = 'provider-ownership-package-admission.json'


def rust_counts(helper, package, output):
    # Reuse the exact native checks for every required real named Rust test,
    # zero ignored/filtered/failed cases, and exactly one passing summary.
    helper.validate_rust_output(package, output)
    summaries = re.findall(r'^test result: ok\. (\d+) passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;', output, re.M)
    starts = re.findall(r'^running (\d+) tests?$', output, re.M)
    rows = re.findall(r'^test ([^\n ]+) \.\.\. ([^\n]+)$', output, re.M)
    safe.require(len(summaries) == len(starts) == 1 and int(summaries[0]) == int(starts[0]) == len(rows),
                 'Ownership Rust executed test count differs: ' + package)
    safe.require(len(rows) == len({name for name, _ in rows}) and all(status == 'ok' for _, status in rows),
                 'Ownership Rust test duplicate or nonpassing record: ' + package)
    return {'required_named_tests': len(helper.REQUIRED_RUST_TESTS[package]), 'actual_passed_tests': len(rows)}


def verify(root, manifest, projector):
    root = Path(root).resolve(strict=True)
    contract, contract_sha, _, helper, graph = projection.trusted()
    safe.require({name: len(tests) for name, tests in helper.REQUIRED_RUST_TESTS.items()} ==
                 {'idevice': 4, 'idevice-ffi': 25}, 'Ownership reviewed named-test inventory differs')
    raw, binding, native = projection.projected_evidence(root, manifest, projector, contract, graph, FILES)
    for flag in ('physical_device_execution', 'authentication_executed', 'live_authentication_executed'):
        safe.require(native.get(flag) is False, 'Ownership aggregate execution claim differs: ' + flag)
    # Reject duplicate JSON keys before invoking the original pure verifier.
    safe.decode(raw[FILES[0]])
    with tempfile.TemporaryDirectory(prefix='provider-ownership-package-') as directory:
        receipt = Path(directory) / FILES[0]
        receipt.write_bytes(raw[FILES[0]])
        helper.verify_native_receipt(receipt)
    counts = {package: rust_counts(helper, package, raw[package + '-tests.log'].decode('utf-8'))
              for package in helper.REQUIRED_RUST_TESTS}
    helper.validate_generated_provider_output(raw[FILES[3]].decode('utf-8'))
    safe.require(type(manifest.get('ipa_bytes')) is int and manifest['ipa_bytes'] > 0 and
                 safe.digest(manifest.get('ipa_sha256')), 'Ownership input IPA identity missing')
    original = safe.regular(root/'original.ipa', 550*1024*1024)
    digest = hashlib.sha256()
    with original.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    safe.require(original.stat().st_size == manifest['ipa_bytes'] and digest.hexdigest() == manifest['ipa_sha256'],
                 'Ownership actual input IPA bytes differ')
    return {'schema': 'provider-ownership-package-admission-v1',
            'status': 'PASS_EXACT_RUST_AND_GENERATED_ABI_NOT_DEVICE',
            **binding, 'trusted_contract_sha256': contract_sha,
            'trusted_native_helper_sha256': contract['trusted_files_sha256']['run_issue_tests.py'],
            'trusted_graph_sha256': contract['trusted_files_sha256']['candidate-graph.json'],
            'idevice_source': {'commit': graph['owners']['idevice'][1],
                               'tree': graph['reviewed_deltas']['idevice']['tree']},
            'required_tests': {name: list(tests) for name, tests in helper.REQUIRED_RUST_TESTS.items()},
            'test_counts': counts, 'required_markers': list(helper.GENERATED_PROVIDER_MARKERS),
            'input_ipa_bytes': manifest['ipa_bytes'], 'input_ipa_sha256': digest.hexdigest(),
            'device_executed': False, 'live_authentication_executed': False, 'adapter_native_execution': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--projector', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.root, safe.decode(safe.read(args.root/'input-manifest.json')), args.projector)
    (args.root/REPORT).write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print('PASS exact Rust and generated-header ownership evidence; no device/native execution by adapter')


if __name__ == '__main__':
    main()
