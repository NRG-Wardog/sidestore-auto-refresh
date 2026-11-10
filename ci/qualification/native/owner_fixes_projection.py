"""Exact scheduler, Health and diagnostic successors of reviewed owner sources.

Only the finite edits in the manifest may be undone. This proves compatibility
with historical projections, not execution of the successor's regression tests.
Actual source hashes remain in contracts and receipts.
"""
import copy
import hashlib
import json
from pathlib import Path

MANIFEST = Path(__file__).with_name('owner-fixes-source-delta.json')


def specifications():
    return json.loads(MANIFEST.read_text())['files']


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def restore_bytes(path, data):
    spec = specifications()[path]
    if sha(data) == spec['before_sha256']:
        require(blob(data) == spec['before_blob'], 'Owner predecessor blob differs: ' + path)
        return data
    require(sha(data) == spec['after_sha256'] and blob(data) == spec['after_blob'],
            'Unreviewed owner-fix source: ' + path)
    lines = data.decode().splitlines(keepends=True)
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Owner-fix delta mismatch: ' + path)
        lines[start:end] = edit['before'].splitlines(keepends=True)
    prior = ''.join(lines).encode()
    require(sha(prior) == spec['before_sha256'] and blob(prior) == spec['before_blob'],
            'Owner-fix inverse changed historical source: ' + path)
    return prior


def restore_if_successor(path, data):
    """Leave other bytes for the caller's existing strict historical validator."""
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


def validate_source(sources):
    return {path: sha(restore_bytes(path, (Path(sources) / path).read_bytes()))
            for path in specifications()}
