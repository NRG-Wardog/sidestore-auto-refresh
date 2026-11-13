"""Finite portal-identity inverse over the unchanged owner-fixes proof chain.

The whole current and predecessor files are SHA-256 and Git-blob bound. Only
listed edits may be removed, so repinning an unrelated edit cannot admit it.
This proves source compatibility, never native or device execution.
"""
import copy
import hashlib
import json
from pathlib import Path
from ci.qualification.native import owner_fixes_projection as owner_fixes

MANIFEST = Path(__file__).with_name('portal-identity-source-delta.json')
MANIFEST_SHA256 = 'e8a48d420664880432fd04594351fd1ddaf9901606e435be67caef9d609beda4'
HOST = 'LiveContainer/SideStoreSupport/SideStore.swift'
SERVICE = 'SideStore/AltStore/AppDelegate.swift'
SELECTION = 'SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift'
SELECTION_KEY = 'reviewed_portal_identity_selection_dependency'
OWNER_COMMITS = {
    'LiveContainer': ('12e77fad6517288de544e766fb0e84594c2a1912', '511e25b647c80bd070bef31e80b1020cfd4a5b7b'),
    'SideStore': ('d0fc537310c921cd5732e16e16f2037d215ecde2', '0eba6008c724c9f86a287b696748a74aef25f289'),
}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def specifications():
    require(MANIFEST.is_file() and not MANIFEST.is_symlink(), 'Missing/linked portal-identity manifest')
    raw = MANIFEST.read_bytes()
    require(sha(raw) == MANIFEST_SHA256, 'Unreviewed portal-identity manifest')
    value = json.loads(raw)
    require(set(value) == {'schema', 'files'} and value['schema'] == 'reviewed-portal-identity-source-delta-v1',
            'Portal-identity manifest schema differs')
    require(set(value['files']) == {HOST, SERVICE, SELECTION}, 'Portal-identity input closure differs')
    for path, spec in value['files'].items():
        require((spec['before_commit'], spec['after_commit']) == OWNER_COMMITS[path.split('/', 1)[0]],
                'Portal-identity owner commits differ: ' + path)
        if path in (HOST, SERVICE):
            prior = owner_fixes.specifications()[path]
            require(all(spec['before_' + field] == prior['after_' + field]
                        for field in ('commit', 'sha256', 'blob')),
                    'Portal-identity predecessor differs from frozen owner fix: ' + path)
    return value['files']


def restore_bytes(path, data):
    spec = specifications()[path]
    if sha(data) == spec['before_sha256']:
        require(blob(data) == spec['before_blob'], 'Portal-identity predecessor blob differs: ' + path)
        return data
    require(sha(data) == spec['after_sha256'] and blob(data) == spec['after_blob'],
            'Unreviewed portal-identity source: ' + path)
    lines = data.decode().splitlines(keepends=True)
    last_end = 0
    for edit in spec['edits']:
        start, end = edit['after_lines']
        require(type(start) is int and type(end) is int and last_end <= start <= end <= len(lines),
                'Invalid portal-identity delta span: ' + path)
        last_end = end
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Portal-identity delta mismatch: ' + path)
        lines[start:end] = edit['before'].splitlines(keepends=True)
    prior = ''.join(lines).encode()
    require(sha(prior) == spec['before_sha256'] and blob(prior) == spec['before_blob'],
            'Portal-identity inverse changed historical source: ' + path)
    return prior


def restore_if_successor(path, data):
    if sha(data) == specifications()[path]['after_sha256']:
        return restore_bytes(path, data)
    return data


def normalize(contract, path):
    spec = specifications()[path]
    if contract.get('input_sha256', {}).get(path) != spec['after_sha256']:
        return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][path] = spec['before_sha256']
    return result


def read_source(sources, path):
    sources = Path(sources).resolve(strict=True)
    target = sources / path
    require(target.is_file() and not any((sources / part).is_symlink()
            for part in (Path(path), *Path(path).parents)), 'Missing/linked portal-identity source: ' + path)
    return target.read_bytes()


def validate_source(sources):
    return {path: sha(restore_bytes(path, read_source(sources, path)))
            for path in specifications()}


def validate_owner_chain(sources):
    """Run the frozen owner inverse on restored bytes, including unchanged inputs."""
    validate_source(sources)
    return {path: sha(owner_fixes.restore_bytes(path,
                restore_if_successor(path, read_source(sources, path))
                if path in (HOST, SERVICE) else read_source(sources, path)))
            for path in owner_fixes.specifications()}


def selection_descriptor():
    spec = specifications()[SELECTION]
    return {'path': SELECTION, 'prior_sha256': spec['before_sha256'],
            'sha256': spec['after_sha256'], 'git_blob': spec['after_blob'],
            'manifest_sha256': MANIFEST_SHA256}


def selection_normalized(contract):
    current = contract.get('input_sha256', {}).get(SELECTION)
    spec = specifications()[SELECTION]
    if current != spec['after_sha256'] and SELECTION_KEY not in contract:
        return contract
    require(contract.get(SELECTION_KEY) == selection_descriptor(), 'Unknown/missing portal-identity selection proof')
    require(current == spec['after_sha256'], 'Unbound portal-identity selection successor')
    result = normalize(contract, SELECTION)
    result.pop(SELECTION_KEY)
    return result
