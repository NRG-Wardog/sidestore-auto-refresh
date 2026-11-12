"""Finite diagnostic-only source inverse over immutable portal proof provenance.

Current source, complete reviewed owner deltas and parent chains are bound before
restored bytes enter the original committed-source validator. No runtime, device
success or equivalence of newly added diagnostic behavior is claimed.
"""
import copy
import hashlib
import json
from pathlib import Path
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as prior
from ci.qualification.native import owner_fixes_projection as owner_fixes

MANIFEST = Path(__file__).with_name('preservation-evidence-source-delta.json')
MANIFEST_SHA256 = '009ecb09fbed59f728e2d05d021e59a3c382f0e1b4439edb62aa691b31dbb424'
OWNER_COMMITS = {
    'LiveContainer': ('511e25b647c80bd070bef31e80b1020cfd4a5b7b', 'f59be2462026a70a957d309963ef91a9f29d893b'),
    'SideStore': ('b61990276f574c0f5a3dee95fd816ba957d50c6c', 'd2bf28820367a882fe0f1db93184a830db83c243'),
}
FETCH = 'SideStore/SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift'
SELECTION_KEY = 'reviewed_preservation_evidence_selection_dependency'


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def specifications():
    require(MANIFEST.is_file() and not MANIFEST.is_symlink(), 'Missing/linked preservation-evidence manifest')
    raw = MANIFEST.read_bytes()
    require(sha(raw) == MANIFEST_SHA256, 'Unreviewed preservation-evidence manifest')
    value = json.loads(raw)
    require(set(value) == {'schema', 'owners', 'files'}
            and value['schema'] == 'reviewed-preservation-evidence-source-delta-v1', 'Preservation-evidence schema differs')
    require(set(value['owners']) == set(OWNER_COMMITS)
            and set(value['files']) == {portal.HOST, portal.SERVICE, portal.SELECTION, FETCH},
            'Preservation-evidence input closure differs')
    for owner, spec in value['owners'].items():
        require((spec['before_commit'], spec['after_commit']) == OWNER_COMMITS[owner], 'Preservation-evidence owner commits differ')
    for path, spec in value['files'].items():
        require((spec['before_commit'], spec['after_commit']) == OWNER_COMMITS[path.split('/', 1)[0]],
                'Preservation-evidence source commits differ: ' + path)
        if path in portal.specifications():
            old = portal.specifications()[path]
            require(all(spec['before_' + key] == old['after_' + key] for key in ('sha256', 'blob')),
                    'Preservation-evidence portal predecessor differs: ' + path)
    return value


def restore_bytes(path, data):
    spec = specifications()['files'][path]
    if sha(data) == spec['before_sha256']:
        require(blob(data) == spec['before_blob'], 'Preservation-evidence predecessor blob differs: ' + path)
        return data
    require(sha(data) == spec['after_sha256'] and blob(data) == spec['after_blob'],
            'Unreviewed preservation-evidence source: ' + path)
    lines = data.decode().splitlines(keepends=True)
    last_end = 0
    for edit in spec['edits']:
        start, end = edit['after_lines']
        require(type(start) is int and type(end) is int and last_end <= start <= end <= len(lines),
                'Invalid preservation-evidence delta span: ' + path)
        last_end = end
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Preservation-evidence delta mismatch: ' + path)
        lines[start:end] = edit['before'].splitlines(keepends=True)
    restored = ''.join(lines).encode()
    require(sha(restored) == spec['before_sha256'] and blob(restored) == spec['before_blob'],
            'Preservation-evidence inverse changed historical source: ' + path)
    return restored


def restore_if_successor(path, data):
    spec = specifications()['files'].get(path)
    return restore_bytes(path, data) if spec is not None and sha(data) == spec['after_sha256'] else data


def normalize(contract, path):
    spec = specifications()['files'].get(path)
    if spec is None or contract.get('input_sha256', {}).get(path) != spec['after_sha256']:
        return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][path] = spec['before_sha256']
    return result


def normalized_graph(graph):
    owners = specifications()['owners']
    current = {owner: graph.get('owners', {}).get(owner) for owner in owners}
    if not any(current[owner] == ['NRG-Wardog/' + owner, spec['after_commit']] for owner, spec in owners.items()):
        for owner in owners:
            prior.proof_commit(graph, owner)
        return graph
    result = copy.deepcopy(graph)
    for owner, spec in owners.items():
        require(current[owner] == ['NRG-Wardog/' + owner, spec['after_commit']], 'Partial/unknown preservation-evidence owner graph')
        delta = result['reviewed_deltas'][owner]
        require(delta['tree'] == spec['after_tree'], 'Preservation-evidence graph tree differs: ' + owner)
        for path, change in spec['changes'].items():
            require(delta['files'].get(path) == change['after_blob'], 'Preservation-evidence graph blob differs: ' + path)
            delta['files'][path] = change['before_blob']
        delta['tree'] = spec['before_tree']
        require(sha(canonical(delta)) == spec['before_reviewed_delta_sha256'],
                'Preservation-evidence graph inverse changed unrelated historical bindings: ' + owner)
        result['owners'][owner][1] = spec['before_commit']
    for owner in owners:
        prior.proof_commit(result, owner)
    return result


def proof_commit(graph, owner):
    return prior.proof_commit(normalized_graph(graph), owner)


def validate_sources(sources, graph=None):
    if graph is None:
        from ci.build.verify_candidate_graph import load_candidate_graph
        graph = load_candidate_graph()
    sources = Path(sources).resolve(strict=True)
    previous = normalized_graph(graph)
    if previous == graph:
        return prior.validate_sources(sources, graph)
    value = specifications()
    for owner, spec in value['owners'].items():
        root = prior.source_root(sources, owner)
        require(prior.git(root, 'rev-parse', 'HEAD').decode().strip() == spec['after_commit'], 'Preservation-evidence checkout pin differs: ' + owner)
        expected_commit = spec['after_commit']
        for node in spec['commit_chain']:
            require(node['commit'] == expected_commit and len(node['parents']) == 1, 'Preservation-evidence parent chain differs')
            require(prior.commit_info(root, node['commit']) == (node['tree'], node['parents']), 'Preservation-evidence commit ancestry differs')
            expected_commit = node['parents'][0]
        require(expected_commit == spec['before_commit'] and spec['commit_chain'][0]['tree'] == spec['after_tree'],
                'Preservation-evidence ancestor closure differs')
        changes = prior.git(root, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames',
                            '-r', spec['before_commit'], spec['after_commit']).decode().splitlines()
        require(changes == spec['delta'], 'Preservation-evidence complete owner delta differs: ' + owner)
        for path, change in spec['changes'].items():
            data = portal.read_source(sources, owner + '/' + path)
            require(blob(data) == change['after_blob'] and sha(data) == change['after_sha256']
                    and data == prior.git(root, 'cat-file', 'blob', spec['after_commit'] + ':' + path),
                    'Preservation-evidence owner working bytes differ: ' + owner + '/' + path)
    restored = {}
    for path, spec in value['files'].items():
        data = portal.read_source(sources, path)
        restored[path] = restore_bytes(path, data)
        owner, relative = path.split('/', 1)
        require(restored[path] == prior.git(sources / owner, 'cat-file', 'blob', spec['before_commit'] + ':' + relative),
                'Preservation-evidence inverse differs from committed predecessor: ' + path)
    binding = prior.validate_committed_sources(sources, previous, {path: restored[path] for path in portal.specifications()})
    result = copy.deepcopy(binding)
    result['schema'] = 'reviewed-preservation-evidence-owner-binding-v1'
    result['prior_binding'] = binding
    result['current_owners'] = {owner: graph['owners'][owner][1] for owner in value['owners']}
    result['preservation_evidence_successor'] = {'manifest_sha256': MANIFEST_SHA256, 'owners': {owner: list(commits) for owner, commits in OWNER_COMMITS.items()}}
    for path, spec in value['files'].items():
        result['inputs'][path] = {'sha256': spec['after_sha256'], 'git_blob': spec['after_blob'],
                                 'predecessor_sha256': spec['before_sha256'], 'predecessor_commit': spec['before_commit']}
    return result


def validate_owner_chain(sources):
    """Bind every current input before replaying the immutable owner inverse."""
    validate_sources(sources)
    return {path: sha(owner_fixes.restore_bytes(path,
                portal.restore_if_successor(path, restore_if_successor(path, portal.read_source(sources, path)))
                if path in portal.specifications() else portal.read_source(sources, path)))
            for path in owner_fixes.specifications()}


def selection_descriptor():
    spec = specifications()['files'][portal.SELECTION]
    return {'path': portal.SELECTION, 'prior_sha256': spec['before_sha256'], 'sha256': spec['after_sha256'],
            'git_blob': spec['after_blob'], 'manifest_sha256': MANIFEST_SHA256}


def selection_normalized(contract):
    spec = specifications()['files'][portal.SELECTION]
    current = contract.get('input_sha256', {}).get(portal.SELECTION)
    if current != spec['after_sha256'] and SELECTION_KEY not in contract:
        return contract
    require(contract.get(SELECTION_KEY) == selection_descriptor(), 'Unknown/missing preservation-evidence selection proof')
    require(current == spec['after_sha256'], 'Unbound preservation-evidence selection successor')
    result = normalize(contract, portal.SELECTION)
    result.pop(SELECTION_KEY)
    return result
