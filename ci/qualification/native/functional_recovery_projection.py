"""Exact mapping/prompt successor inverse, not behavioral equivalence or runtime proof.

Bind the current three owners, complete Git deltas and all changed raw inputs
before replaying immutable historical evidence. S15 is not part of this layer.
"""
import copy
import json
from pathlib import Path
from ci.qualification.native import preservation_evidence_owner_successor as previous
from ci.qualification.native import preservation_evidence_projection as evidence
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as git_proof
from ci.qualification.native import owner_fixes_projection as owner_fixes

MANIFEST = Path(__file__).with_name('functional-recovery-source-delta.json')
MANIFEST_SHA256 = 'cadefc4dfcd682db70948d20c47f4bec86b2ff7c05b032e373fdcd20ae44ee8f'
OWNER_COMMITS = {
    'LiveContainer': ('c82033f094a57bcc86166c14b0297f0607321c69', '68561eb6dbbc5b6e0bfe4591401af1cb4f500d04'),
    'SideStore': ('9ba862f47d8a32e0548924d4a087b0e1e853b0d0', '9bfb1822e9153c622ef8cda88cd4247038a8feeb'),
    git_proof.SIDESIGN: ('082571590a6019972045a41a886caf4f27e66683', '594d3426794d40c7bfde6ace0fba9d6309c214a6'),
}
LOCALIZATION = 'LiveContainer/LiveContainerSwiftUI/Utilities/V3Localization.swift'
require, sha, blob, canonical = evidence.require, evidence.sha, evidence.blob, evidence.canonical


def owner_path(path):
    owners = [owner for owner in OWNER_COMMITS if path.startswith(owner + '/')]
    require(bool(owners), 'Functional source owner differs: ' + path)
    owner = max(owners, key=len)
    return owner, path[len(owner) + 1:]


def specifications():
    require(MANIFEST.is_file() and not MANIFEST.is_symlink(), 'Missing/linked functional manifest')
    raw = MANIFEST.read_bytes()
    require(sha(raw) == MANIFEST_SHA256, 'Unreviewed functional manifest')
    value = json.loads(raw)
    require(set(value) == {'schema', 'owners', 'files'} and value['schema'] == 'reviewed-functional-recovery-source-delta-v1',
            'Functional schema differs')
    require(set(value['owners']) == set(OWNER_COMMITS), 'Functional owner closure differs')
    files = set()
    for owner, spec in value['owners'].items():
        require((spec['before_commit'], spec['after_commit']) == OWNER_COMMITS[owner], 'Functional owner pins differ')
        paths = []
        for row in spec['delta']:
            metadata, path = row.split('\t')
            old_mode, new_mode, old_blob, new_blob, status = metadata.split()
            old_mode = old_mode.removeprefix(':')
            require(path not in paths and path in spec['changes'], 'Functional delta path closure differs')
            paths.append(path)
            change = spec['changes'][path]
            require((old_mode, new_mode, old_blob, new_blob) == tuple(change[key] for key in
                    ('before_mode', 'after_mode', 'before_blob', 'after_blob')), 'Functional delta binding differs')
            require(status == ('A' if old_mode == '000000' else 'M'), 'Functional delta status differs')
            if new_mode == '160000':
                require(owner == 'SideStore' and path == git_proof.GITLINK and old_mode == '160000'
                        and old_blob == OWNER_COMMITS[git_proof.SIDESIGN][0]
                        and new_blob == OWNER_COMMITS[git_proof.SIDESIGN][1], 'Functional gitlink differs')
            else:
                require(new_mode in ('100644', '100755') and old_mode in ('000000', '100644', '100755'),
                        'Functional source mode differs')
                key = owner + '/' + path
                files.add(key)
                file = value['files'].get(key, {})
                require((file.get('before_commit'), file.get('after_commit')) == OWNER_COMMITS[owner]
                        and file.get('after_blob') == new_blob
                        and file.get('before_blob') == (blob(b'') if old_mode == '000000' else old_blob),
                        'Functional file provenance differs: ' + key)
        require(set(paths) == set(spec['changes']), 'Functional change closure differs')
    require(files == set(value['files']), 'Functional regular file closure differs')
    return value


def restore_bytes(path, data):
    spec = specifications()['files'][path]
    if sha(data) == spec['before_sha256']:
        require(blob(data) == spec['before_blob'], 'Functional predecessor blob differs: ' + path)
        return data
    require(sha(data) == spec['after_sha256'] and blob(data) == spec['after_blob'], 'Unreviewed functional source: ' + path)
    lines = data.decode().splitlines(keepends=True)
    last_end = 0
    for edit in spec['edits']:
        start, end = edit['after_lines']
        require(type(start) is int and type(end) is int and last_end <= start <= end <= len(lines),
                'Invalid functional delta span: ' + path)
        last_end = end
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Functional delta mismatch: ' + path)
        lines[start:end] = edit['before'].splitlines(keepends=True)
    restored = ''.join(lines).encode()
    require(sha(restored) == spec['before_sha256'] and blob(restored) == spec['before_blob'],
            'Functional inverse changed historical source: ' + path)
    return restored


def restore_if_successor(path, data):
    spec = specifications()['files'].get(path)
    return restore_bytes(path, data) if spec and sha(data) == spec['after_sha256'] else data


def normalize(contract, path):
    spec = specifications()['files'].get(path)
    if not spec or contract.get('input_sha256', {}).get(path) != spec['after_sha256']:
        return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][path] = spec['before_sha256']
    return result


def normalized_graph(graph):
    owners = specifications()['owners']
    current = graph.get('owners', {})
    if not any(current.get(owner) == ['NRG-Wardog/' + owner.split('/')[-1], spec['after_commit']]
               for owner, spec in owners.items()):
        previous.normalized_graph(graph)
        return graph
    result = copy.deepcopy(graph)
    for owner, spec in owners.items():
        require(current.get(owner) == ['NRG-Wardog/' + owner.split('/')[-1], spec['after_commit']],
                'Partial/unknown functional owner graph')
        delta = result['reviewed_deltas'][owner]
        require(delta['tree'] == spec['after_tree'], 'Functional graph tree differs: ' + owner)
        for path, change in spec['changes'].items():
            require(delta['files'].get(path) == change['after_blob'], 'Functional graph blob differs: ' + path)
            if change['before_graph_present']:
                delta['files'][path] = change['before_graph_blob']
            else:
                del delta['files'][path]
        delta['tree'] = spec['before_tree']
        require(sha(canonical(delta)) == spec['before_reviewed_delta_sha256'], 'Functional graph inverse changed unrelated bindings: ' + owner)
        result['owners'][owner][1] = spec['before_commit']
    previous.normalized_graph(result)
    return result


def evidence_commit(graph, owner):
    return previous.evidence_commit(normalized_graph(graph), owner)


def proof_commit(graph, owner):
    return previous.proof_commit(normalized_graph(graph), owner)


def historical_paths():
    return previous.committed_paths() | {git_proof.SIDESIGN + '/' + path for path in git_proof.TEST_PATHS}


def validate_sources(sources, graph=None):
    if graph is None:
        from ci.build.verify_candidate_graph import load_candidate_graph
        graph = load_candidate_graph()
    sources = Path(sources).resolve(strict=True)
    old_graph = normalized_graph(graph)
    if old_graph == graph:
        return previous.validate_sources(sources, graph)
    value = specifications()
    for owner, spec in value['owners'].items():
        root = git_proof.source_root(sources, owner)
        require(git_proof.git(root, 'rev-parse', 'HEAD').decode().strip() == spec['after_commit'], 'Functional checkout pin differs: ' + owner)
        expected = spec['after_commit']
        for node in spec['commit_chain']:
            require(node['commit'] == expected and len(node['parents']) == 1, 'Functional parent chain differs')
            require(git_proof.commit_info(root, node['commit']) == (node['tree'], node['parents']), 'Functional commit ancestry differs')
            expected = node['parents'][0]
        require(expected == spec['before_commit'] and spec['commit_chain'][0]['tree'] == spec['after_tree'],
                'Functional ancestor closure differs')
        require(git_proof.commit_info(root, spec['before_commit'])[0] == spec['before_tree'], 'Functional predecessor tree differs')
        delta = git_proof.git(root, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames', '-r',
                             spec['before_commit'], spec['after_commit']).decode().splitlines()
        require(delta == spec['delta'], 'Functional complete owner delta differs: ' + owner)
    restored, checked = {}, {}
    for path in sorted(historical_paths() | set(value['files'])):
        owner, relative = owner_path(path)
        spec = value['owners'][owner]
        root = git_proof.source_root(sources, owner)
        current = portal.read_source(sources, path)
        require(current == git_proof.git(root, 'cat-file', 'blob', spec['after_commit'] + ':' + relative),
                'Functional working/Git bytes differ: ' + path)
        data = restore_bytes(path, current) if path in value['files'] else current
        change = spec['changes'].get(relative, {})
        prior = b'' if change.get('before_mode') == '000000' else git_proof.git(root, 'cat-file', 'blob', spec['before_commit'] + ':' + relative)
        require(data == prior, 'Functional inverse differs from committed predecessor: ' + path)
        restored[path] = data
        checked[path] = {'sha256': sha(current), 'git_blob': blob(current), 'commit': spec['after_commit']}
    binding = previous.validate_committed_sources(sources, old_graph,
        {path: restored[path] for path in previous.committed_paths()},
        sidesign_proof_inputs={path: restored[git_proof.SIDESIGN + '/' + path] for path in git_proof.TEST_PATHS})
    result = copy.deepcopy(binding)
    result['schema'] = 'reviewed-functional-recovery-owner-binding-v1'
    result['historical_binding'] = binding
    result['current_owners'] = {owner: spec['after_commit'] for owner, spec in value['owners'].items()}
    result['functional_successor'] = {'manifest_sha256': MANIFEST_SHA256, 'current_inputs': checked,
                                    'behavioral_equivalence_claimed': False, 'native_execution': False}
    return result


def validate_owner_chain(sources):
    validate_sources(sources)
    result = {}
    for path in owner_fixes.specifications():
        data = restore_if_successor(path, portal.read_source(sources, path))
        if path in portal.specifications():
            data = portal.restore_if_successor(path, evidence.restore_if_successor(path, data))
        result[path] = sha(owner_fixes.restore_bytes(path, data))
    return result
