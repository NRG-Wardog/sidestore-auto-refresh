"""Admit one exact test-only dependency successor without rewriting proof history.

The portal inverse continues to identify its original owner commits. A newer
source graph may use only the separately reviewed gitlink/test-only commits
below, after their exact objects, parent edges, deltas and raw inputs validate.
This source binding is not native execution or release qualification.
"""
import hashlib
import subprocess
from pathlib import Path
from ci.qualification.native import portal_identity_projection as portal

SIDESIGN = 'SideStore/Dependencies/SideSign'
GITLINK = 'Dependencies/SideSign'
TEST_PATHS = (
    '.ci/owned-oda/strict-appid-portal-projection.json',
    '.ci/owned-oda/test_sources.py',
    '.github/workflows/appid-lookup-qualification.yml',
)


def specification():
    return {
        'commit': 'b61990276f574c0f5a3dee95fd816ba957d50c6c',
        'parent': '0eba6008c724c9f86a287b696748a74aef25f289',
        'tree': '5d93215f7bb4bb0cdc6bda9f359727ad18d948af',
        'sidesign_commit': '082571590a6019972045a41a886caf4f27e66683',
        'sidesign_parent': 'f4a1f10f1cfc404d241d563a06b21f0de07f7505',
        'sidesign_tree': '24df8b96c0ea06c9f454bd04a3321968e7b960f4',
    }


def require(ok, why):
    if not ok:
        raise ValueError(why)


def git(root, *args):
    return subprocess.check_output(['git', '--no-replace-objects', '--literal-pathspecs',
        '-C', str(root), '-c', 'core.hooksPath=/dev/null', '-c', 'core.quotePath=false', *args])


def proof_commit(graph, owner):
    """Map a current graph pin to immutable proof provenance, never any descendant."""
    row = graph.get('owners', {}).get(owner)
    require(isinstance(row, list) and len(row) == 2 and row[0] == 'NRG-Wardog/' + owner,
            'Unexpected portal owner repository: ' + owner)
    current = row[1]
    spec = specification()
    if current in portal.OWNER_COMMITS[owner]:
        require(owner != 'SideStore' or graph['owners'].get(SIDESIGN) != ['NRG-Wardog/SideSign', spec['sidesign_commit']],
                'Partial portal owner dependency successor')
        return current
    require(owner == 'SideStore' and current == spec['commit'], 'Unreviewed portal owner pin: ' + owner)
    require(spec['parent'] == portal.OWNER_COMMITS['SideStore'][1], 'Portal owner proof predecessor differs')
    require(graph['owners'].get(SIDESIGN) == ['NRG-Wardog/SideSign', spec['sidesign_commit']],
            'Portal owner SideSign pin differs')
    delta = graph.get('reviewed_deltas', {}).get('SideStore', {})
    require(delta.get('tree') == spec['tree'] and delta.get('files', {}).get(GITLINK) == spec['sidesign_commit'],
            'Portal owner tree/gitlink binding differs')
    require(graph.get('reviewed_deltas', {}).get(SIDESIGN, {}).get('tree') == spec['sidesign_tree'],
            'Portal owner SideSign tree binding differs')
    return spec['parent']


def commit_info(root, commit):
    raw = git(root, 'cat-file', 'commit', commit)
    actual = hashlib.sha1(b'commit ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    require(actual == commit, 'Portal owner commit object differs')
    headers = raw.split(b'\n\n', 1)[0].splitlines()
    trees = [line[5:].decode() for line in headers if line.startswith(b'tree ')]
    parents = [line[7:].decode() for line in headers if line.startswith(b'parent ')]
    require(len(trees) == 1, 'Portal owner commit tree closure differs')
    return trees[0], parents


def source_root(sources, owner):
    root = sources / owner
    require(root.is_dir() and not any((sources / part).is_symlink()
            for part in (Path(owner), *Path(owner).parents)), 'Missing/linked portal owner: ' + owner)
    return root


def validate_sources(sources, graph=None):
    """Bind current graph, exact commit ancestry and raw proof bytes together."""
    if graph is None:
        from ci.build.verify_candidate_graph import load_candidate_graph
        graph = load_candidate_graph()
    sources = Path(sources).resolve(strict=True)
    for owner in portal.OWNER_COMMITS:
        proof_commit(graph, owner)
        root = source_root(sources, owner)
        require(git(root, 'rev-parse', 'HEAD').decode().strip() == graph['owners'][owner][1],
                'Portal owner checkout pin differs: ' + owner)
    inputs = {path: portal.read_source(sources, path) for path in portal.specifications()}
    return validate_committed_sources(sources, graph, inputs)


def validate_committed_sources(sources, graph, proof_inputs):
    """Validate explicit predecessor bytes against actual committed Git provenance.

    This does not assert checkout HEAD. The current-source entry point above
    checks it separately; reviewed successors must prove their current HEAD,
    exact deltas and byte inverse before calling this historical validator.
    """
    sources = Path(sources).resolve(strict=True)
    require(set(proof_inputs) == set(portal.specifications())
            and all(type(value) is bytes for value in proof_inputs.values()),
            'Portal committed proof input closure differs')
    proofs = {owner: proof_commit(graph, owner) for owner in portal.OWNER_COMMITS}
    for owner in proofs:
        root = source_root(sources, owner)
        tree, _ = commit_info(root, graph['owners'][owner][1])
        require(tree == graph['reviewed_deltas'][owner]['tree'], 'Portal owner graph tree differs: ' + owner)

    spec = specification()
    successor = graph['owners']['SideStore'][1] == spec['commit']
    if successor:
        store = source_root(sources, 'SideStore')
        tree, parents = commit_info(store, spec['commit'])
        require(tree == spec['tree'] and parents == [spec['parent']], 'Portal owner exact parent/tree differs')
        delta = git(store, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames',
                    '-r', spec['parent'], spec['commit']).decode().splitlines()
        expected = ':160000 160000 ' + spec['sidesign_parent'] + ' ' + spec['sidesign_commit'] + ' M\t' + GITLINK
        require(delta == [expected], 'Portal owner successor is not the exact sole gitlink delta')
        side = source_root(sources, SIDESIGN)
        require(git(side, 'rev-parse', 'HEAD').decode().strip() == spec['sidesign_commit'], 'Portal owner SideSign checkout differs')
        tree, parents = commit_info(side, spec['sidesign_commit'])
        require(tree == spec['sidesign_tree'] and parents == [spec['sidesign_parent']], 'Portal owner SideSign parent/tree differs')
        changes = git(side, 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '--no-renames',
                      '-r', spec['sidesign_parent'], spec['sidesign_commit']).decode().splitlines()
        paths = []
        for row in changes:
            metadata, path = row.split('\t')
            old_mode, new_mode, old_blob, new_blob, status = metadata.split()
            require((old_mode, new_mode, status) in ((':100644', '100644', 'M'), (':000000', '100644', 'A')),
                    'Portal owner SideSign test delta mode/status differs')
            require(path in TEST_PATHS and path not in paths, 'Portal owner SideSign changed an unreviewed path')
            paths.append(path)
            data = portal.read_source(sources, SIDESIGN + '/' + path)
            require(portal.blob(data) == new_blob and data == git(side, 'cat-file', 'blob', spec['sidesign_commit'] + ':' + path),
                    'Portal owner SideSign test working bytes differ: ' + path)
            require(graph['reviewed_deltas'][SIDESIGN]['files'].get(path) == new_blob,
                    'Portal owner SideSign test graph blob differs: ' + path)
        require(sorted(paths) == list(TEST_PATHS), 'Portal owner SideSign test path closure differs')

    inputs = {}
    for path, file_spec in portal.specifications().items():
        owner, relative = path.split('/', 1)
        proof = proofs[owner]
        era = 'after' if proof == file_spec['after_commit'] else 'before'
        require(proof == file_spec[era + '_commit'], 'Portal owner source provenance differs: ' + path)
        data = proof_inputs[path]
        require(portal.sha(data) == file_spec[era + '_sha256'] and portal.blob(data) == file_spec[era + '_blob'],
                'Portal owner proof working bytes differ: ' + path)
        root = source_root(sources, owner)
        current = graph['owners'][owner][1]
        require(data == git(root, 'cat-file', 'blob', current + ':' + relative)
                == git(root, 'cat-file', 'blob', proof + ':' + relative), 'Portal owner proof Git bytes differ: ' + path)
        require(graph['reviewed_deltas'][owner]['files'].get(relative) == file_spec[era + '_blob'],
                'Portal owner proof graph blob differs: ' + path)
        inputs[path] = {'sha256': portal.sha(data), 'git_blob': portal.blob(data), 'proof_commit': proof}
    return {'schema': 'reviewed-portal-owner-binding-v1', 'current_owners': {owner: graph['owners'][owner][1] for owner in proofs},
            'proof_owners': proofs, 'gitlink_successor': spec if successor else None,
            'inputs': inputs, 'native_execution': False}
