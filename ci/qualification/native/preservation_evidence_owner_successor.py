"""One exact test-only successor layer over immutable evidence provenance.

Current Git objects, complete deltas, working test bytes and unchanged production
inputs must validate before historical committed inputs reach the prior proof.
This is source binding, never native execution or device/release qualification.
"""
import copy
import json
from pathlib import Path
from ci.qualification.native import preservation_evidence_projection as evidence
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as prior
from ci.qualification.native import owner_fixes_projection as owner_fixes

MANIFEST = Path(__file__).with_name('preservation-evidence-owner-successor.json')
MANIFEST_SHA256 = 'f223280b970613a887e87237715c05461ecb977e7b8d51a5fdfae36f61445c41'
OWNER_COMMITS = {
    'LiveContainer': ('f59be2462026a70a957d309963ef91a9f29d893b', 'c82033f094a57bcc86166c14b0297f0607321c69'),
    'SideStore': ('d2bf28820367a882fe0f1db93184a830db83c243', '9ba862f47d8a32e0548924d4a087b0e1e853b0d0'),
}
TEST_PATHS = {
    'LiveContainer': ('.ci/refresh-observations/test_refresh_observations.py', '.github/workflows/portal-identity-contract.yml'),
    'SideStore': ('.github/workflows/preservation-portal-contract.yml', 'tests/preservation_origin/generate_fixture.py',
                  'tests/preservation_origin/test_contract.py'),
}
require, sha, blob, canonical = evidence.require, evidence.sha, evidence.blob, evidence.canonical


def specifications():
    require(MANIFEST.is_file() and not MANIFEST.is_symlink(), 'Missing/linked evidence-owner successor manifest')
    raw = MANIFEST.read_bytes()
    require(sha(raw) == MANIFEST_SHA256, 'Unreviewed evidence-owner successor manifest')
    value = json.loads(raw)
    require(set(value) == {'schema', 'owners'} and value['schema'] == 'reviewed-preservation-evidence-test-successor-v1',
            'Evidence-owner successor schema differs')
    require(set(value['owners']) == set(OWNER_COMMITS), 'Evidence-owner successor closure differs')
    for owner, spec in value['owners'].items():
        require((spec['before_commit'], spec['after_commit']) == OWNER_COMMITS[owner], 'Evidence-owner successor pins differ')
        require(spec['before_commit'] == evidence.OWNER_COMMITS[owner][1], 'Evidence-owner immutable provenance differs')
        require(set(spec['changes']) == set(TEST_PATHS[owner]), 'Evidence-owner test path closure differs')
        expected = []
        for path in TEST_PATHS[owner]:
            change = spec['changes'][path]
            expected.append(':100644 100644 ' + change['before_blob'] + ' ' + change['after_blob'] + ' M\t' + path)
        require(spec['delta'] == expected, 'Evidence-owner successor is not the exact test-only delta')
    return value


def normalized_graph(graph):
    owners = specifications()['owners']
    current = {owner: graph.get('owners', {}).get(owner) for owner in owners}
    if not any(current[owner] == ['NRG-Wardog/' + owner, spec['after_commit']] for owner, spec in owners.items()):
        evidence.normalized_graph(graph)
        return graph
    result = copy.deepcopy(graph)
    for owner, spec in owners.items():
        require(current[owner] == ['NRG-Wardog/' + owner, spec['after_commit']], 'Partial/unknown evidence-owner successor graph')
        delta = result['reviewed_deltas'][owner]
        require(delta['tree'] == spec['after_tree'], 'Evidence-owner successor graph tree differs: ' + owner)
        for path, change in spec['changes'].items():
            require(delta['files'].get(path) == change['after_blob'], 'Evidence-owner successor graph blob differs: ' + path)
            delta['files'][path] = change['before_blob']
        delta['tree'] = spec['before_tree']
        require(sha(canonical(delta)) == spec['before_reviewed_delta_sha256'],
                'Evidence-owner successor inverse changed unrelated bindings: ' + owner)
        result['owners'][owner][1] = spec['before_commit']
    evidence.normalized_graph(result)
    return result


def evidence_commit(graph, owner):
    """Return the exact evidence proof owner, not the latest checkout pin."""
    return normalized_graph(graph)['owners'][owner][1]


def proof_commit(graph, owner):
    return evidence.proof_commit(normalized_graph(graph), owner)


def validate_sources(sources, graph=None):
    if graph is None:
        from ci.build.verify_candidate_graph import load_candidate_graph
        graph = load_candidate_graph()
    sources = Path(sources).resolve(strict=True)
    previous = normalized_graph(graph)
    if previous == graph:
        return evidence.validate_sources(sources, graph)
    for owner, spec in specifications()['owners'].items():
        root = prior.source_root(sources, owner)
        require(prior.git(root, 'rev-parse', 'HEAD').decode().strip() == spec['after_commit'],
                'Evidence-owner successor checkout differs: ' + owner)
    inputs = {path: portal.read_source(sources, path) for path in committed_paths()}
    return validate_committed_sources(sources, graph, inputs)


def committed_paths():
    return {owner + '/' + path for owner, spec in specifications()['owners'].items()
            for path in set(spec['changes']) | set(evidence.specifications()['owners'][owner]['changes'])}


def validate_committed_sources(sources, graph, current_inputs, *, sidesign_proof_inputs=None):
    """Replay explicit predecessor bytes; callers must separately bind current HEAD."""
    sources = Path(sources).resolve(strict=True)
    previous = normalized_graph(graph)
    require(previous != graph, 'Committed evidence-owner graph differs')
    require(set(current_inputs) == committed_paths() and all(type(data) is bytes for data in current_inputs.values()),
            'Committed evidence-owner input closure differs')
    value = specifications()
    old = evidence.specifications()
    proof_inputs, checked = {}, {}
    for owner, spec in value['owners'].items():
        root = prior.source_root(sources, owner)
        require(prior.commit_info(root, spec['after_commit']) == (spec['after_tree'], [spec['before_commit']]),
                'Evidence-owner successor parent/tree differs: ' + owner)
        require(prior.commit_info(root, spec['before_commit'])[0] == spec['before_tree'],
                'Evidence-owner predecessor tree differs: ' + owner)
        delta = prior.git(root, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames',
                          '-r', spec['before_commit'], spec['after_commit']).decode().splitlines()
        require(delta == spec['delta'], 'Evidence-owner successor complete test-only delta differs: ' + owner)
        paths = []
        for row in delta:
            metadata, path = row.split('\t')
            old_mode, new_mode, old_blob, new_blob, status = metadata.split()
            require((old_mode, new_mode, status) == (':100644', '100644', 'M')
                    and path in TEST_PATHS[owner] and path not in paths,
                    'Evidence-owner successor changed a non-test path or mode: ' + owner)
            paths.append(path)
        require(paths == list(TEST_PATHS[owner]), 'Evidence-owner successor test path closure differs: ' + owner)
        published = old['owners'][owner]['changes']
        for path in sorted(set(published) | set(spec['changes'])):
            relative = owner + '/' + path
            current = current_inputs[relative]
            binding = spec['changes'].get(path, published.get(path))
            require(sha(current) == binding['after_sha256'] and blob(current) == binding['after_blob']
                    and current == prior.git(root, 'cat-file', 'blob', spec['after_commit'] + ':' + path),
                    'Evidence-owner successor working/Git bytes differ: ' + relative)
            historical = prior.git(root, 'cat-file', 'blob', spec['before_commit'] + ':' + path)
            if path in spec['changes']:
                change = spec['changes'][path]
                require(sha(historical) == change['before_sha256'] and blob(historical) == change['before_blob'],
                        'Evidence-owner predecessor test bytes differ: ' + relative)
            else:
                require(current == historical, 'Evidence-owner changed a published proof input: ' + relative)
            if path in published:
                proof_inputs[relative] = historical
            checked[relative] = {'sha256': sha(current), 'git_blob': blob(current), 'commit': spec['after_commit']}
    binding = evidence.validate_committed_sources(sources, previous, proof_inputs,
                                                  sidesign_proof_inputs=sidesign_proof_inputs)
    result = copy.deepcopy(binding)
    result['schema'] = 'reviewed-preservation-evidence-test-owner-binding-v1'
    result['evidence_binding'] = binding
    result['evidence_owners'] = {owner: spec['before_commit'] for owner, spec in value['owners'].items()}
    result['current_owners'] = {owner: spec['after_commit'] for owner, spec in value['owners'].items()}
    result['test_owner_successor'] = {'manifest_sha256': MANIFEST_SHA256, 'current_inputs': checked}
    return result


def validate_owner_chain(sources):
    """Strict current binding first, then replay unchanged production inverses."""
    validate_sources(sources)
    return {path: sha(owner_fixes.restore_bytes(path,
                portal.restore_if_successor(path, evidence.restore_if_successor(path, portal.read_source(sources, path)))
                if path in portal.specifications() else portal.read_source(sources, path)))
            for path in owner_fixes.specifications()}
