"""Project reviewed candidate input blobs and finite additive coverage.

Historical baseline owners/hashes and every existing ordered case are retained.
Only explicitly allowlisted new production inputs and five reviewed host guard
cases may be added. This projection is not proof that any test executed.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from verify_candidate_graph import HERE, load_candidate_graph, require, valid_path, git

CONTRACTS = ('comprehensive-diagnostics-contract.json', 'setup-state-recovery-contract.json')
# Finite reviewed production dependencies extracted by candidate generators.
# Frozen JSON stays untouched; existing ordered cases are preserved.
ADDITIONAL_INPUTS = {
    'comprehensive-diagnostics-contract.json': (
        'SideStore/SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift',
        'SideStore/Dependencies/SideSign/Sources/Anisette/ILoaderRemoteHTTPTransport.swift',
        'SideStore/SideStore/Core/Operations/PipelineExecutionHandler.swift'),
    'setup-state-recovery-contract.json': (
        'SideStore/SideStore/Core/Operations/OperationContexts.swift',
        'SideStore/SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift',
        'SideStore/tests/cache_compatibility/test_host_preservation.py'),
}

HOST_ADDITIONAL_CASES = (
    'integration_legacy_bool_does_not_enroll_validated_strategy',
    'integration_unregistered_payload_nonce_does_not_opt_in',
    'integration_registered_payload_restores_validated_strategy',
    'integration_opt_out_bool_overrides_matching_registry',
    'integration_known_association_nonce_mismatch_fails_before_pipeline',
)
HOST_CONTEXT_INPUT = 'SideStore/SideStore/Core/Operations/OperationContexts.swift'
IDENTITY_CANDIDATE_INPUT = 'SideStore/SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift'


def add_reviewed_host_coverage(contract):
    result = copy.deepcopy(contract)
    frozen = json.loads((HERE / 'setup-state-recovery-contract.json').read_text())
    original_cases = frozen['case_names']['host-refresh-bundle']
    require(result['case_names']['host-refresh-bundle'] == original_cases,
            'original host cases must be retained before additions')
    require(len(original_cases) == 30 and original_cases[-1] == 'all_terminal_paths_preserve_source_and_cleanup',
            'unexpected original host case closure')
    result['case_names']['host-refresh-bundle'] = original_cases[:-1] + list(HOST_ADDITIONAL_CASES) + original_cases[-1:]
    paths = result['additive_suite_inputs']['host-refresh-bundle']['production']
    require(paths == frozen['additive_suite_inputs']['host-refresh-bundle']['production'],
            'original host production input closure differs')
    require(HOST_CONTEXT_INPUT in result['input_sha256'], 'new host context input not blob-bound')
    paths.append(HOST_CONTEXT_INPUT)
    require(IDENTITY_CANDIDATE_INPUT in result['input_sha256'], 'new identity policy input not blob-bound')
    require('candidate_only_inputs' not in result, 'candidate-only inventory already declared')
    result['candidate_only_inputs'] = {'remote-identity-reuse': [IDENTITY_CANDIDATE_INPUT]}
    host = 'SideStore/SideStore/Core/Operations/PipelineRunner.swift'
    result['reviewed_cache_host_change'] = {
        'original_sha256': frozen['input_sha256'][host],
        'candidate_sha256': result['input_sha256'][host],
        'insertion_sha256': 'cd11ece003588f8aeac44bab28c1d3797c11dfb5e23e9facd7a947c8a7057af6'}
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def project(contract, graph, sources, additional_inputs=()):
    result = copy.deepcopy(contract)
    changes = {}
    allowed = set().union(*map(set, ADDITIONAL_INPUTS.values()))
    require(set(additional_inputs) <= allowed, 'unreviewed additive contract input')
    inputs = dict(contract['input_sha256'])
    for path in additional_inputs:
        require(path not in inputs, 'additive input already in frozen contract')
        inputs[path] = None
    for path, old_hash in inputs.items():
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
        projected, changes = project(json.loads(original.read_text()), graph, sources, ADDITIONAL_INPUTS[name])
        if name == 'setup-state-recovery-contract.json':
            projected = add_reviewed_host_coverage(projected)
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
               'existing_behavioral_expectations_preserved': True,
               'additive_case_expectations': {'host-refresh-bundle': list(HOST_ADDITIONAL_CASES)},
               'additive_input_paths': {name: list(paths) for name, paths in ADDITIONAL_INPUTS.items()},
               'contracts': receipts}
    target = output / 'projection.json'
    if verify:
        require(json.loads(target.read_text()) == receipt, 'projection receipt drift')
    else:
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


if __name__ == '__main__':
    contracts(Path('sources'), Path('evidence/candidate-contracts'), '--verify' in sys.argv)
