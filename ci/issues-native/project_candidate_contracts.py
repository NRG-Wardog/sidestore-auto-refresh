"""Rebind only frozen current-input hashes to explicitly reviewed candidate blobs.

Historical baseline owners/hashes, fixture definitions, and behavioral expectations
remain byte-for-byte equivalent JSON values. This is not a test-results receipt.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from verify_candidate_graph import HERE, load_candidate_graph, require, valid_path, git

CONTRACTS = ('comprehensive-diagnostics-contract.json', 'setup-state-recovery-contract.json')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def project(contract, graph, sources):
    result = copy.deepcopy(contract)
    changes = {}
    for path, old_hash in contract['input_sha256'].items():
        require(valid_path(path), 'unsafe contract input: ' + path)
        matches = [name for name in graph['owners'] if path.startswith(name + '/')]
        require(bool(matches), 'unowned contract input: ' + path)
        owner = max(matches, key=len)
        relative = path[len(owner) + 1:]
        root = sources / owner
        commit = graph['owners'][owner][1]
        require(git(root, 'rev-parse', 'HEAD') == commit, 'candidate head drift: ' + owner)
        local = sources / path
        require(local.is_file() and not local.is_symlink() and local.resolve().is_relative_to(sources.resolve()), 'nonregular contract input: ' + path)
        data = local.read_bytes()
        new_hash = sha(data)
        if new_hash == old_hash:
            continue
        delta = graph['reviewed_deltas'].get(owner, {})
        blob = delta.get('files', {}).get(relative)
        require(blob is not None, 'changed input absent from reviewed delta: ' + path)
        require(git(root, 'rev-parse', commit + ':' + relative) == blob, 'candidate input blob differs: ' + path)
        actual_blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(actual_blob == blob, 'candidate input working bytes differ: ' + path)
        result['input_sha256'][path] = new_hash
        changes[path] = {'before_sha256': old_hash, 'after_sha256': new_hash, 'reviewed_blob': blob}
    return result, changes


def contracts(sources, output, verify=False):
    graph = load_candidate_graph()
    receipts = {}
    for name in CONTRACTS:
        original = HERE / name
        projected, changes = project(json.loads(original.read_text()), graph, sources)
        data = (json.dumps(projected, indent=2, sort_keys=True) + '\n').encode()
        target = output / name
        if verify:
            require(target.read_bytes() == data, 'candidate contract projection drift: ' + name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        receipts[name] = {'frozen_contract_sha256': sha(original.read_bytes()),
                          'candidate_contract_sha256': sha(data), 'rebound_inputs': changes}
    receipt = {'schema': 'reviewed-candidate-contract-projection-v1',
               'graph_sha256': sha((HERE / 'candidate-graph.json').read_bytes()),
               'behavioral_expectations_modified': False, 'contracts': receipts}
    target = output / 'projection.json'
    if verify:
        require(json.loads(target.read_text()) == receipt, 'projection receipt drift')
    else:
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


if __name__ == '__main__':
    contracts(Path('sources'), Path('evidence/candidate-contracts'), '--verify' in sys.argv)
