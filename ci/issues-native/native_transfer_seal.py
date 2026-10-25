"""Anchor retained manifests before packaging; not an externally signed trust root."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

OWNERS = frozenset(('LiveContainer', 'SideStore'))
IDENTITY_KEYS = frozenset(('workflow_commit', 'run_id', 'run_attempt'))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'Malformed SHA256')
    return value


def _read(path):
    path = Path(path)
    _require(not path.is_symlink() and path.is_file(), 'Expected nonlinked regular file: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        _require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'Expected regular file')
        return source.read()


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def _identity(identity):
    _require(isinstance(identity, dict) and set(identity) == IDENTITY_KEYS, 'Unexpected run identity fields')
    result = dict(identity)
    _require(isinstance(result['workflow_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', result['workflow_commit']), 'Malformed workflow commit')
    for key in ('run_id', 'run_attempt'):
        _require(isinstance(result[key], str) and re.fullmatch(r'[1-9][0-9]*', result[key]), 'Malformed run identity')
    return result


def _snapshot(receipt_path, transfer_roots, run_identity):
    identity = _identity(run_identity)
    _require(isinstance(transfer_roots, dict) and set(transfer_roots) == OWNERS, 'Unexpected transfer owners')
    receipt_bytes = _read(receipt_path)
    receipt = _json(receipt_bytes)
    _require(isinstance(receipt, dict) and type(receipt.get('schema_version')) is int and receipt['schema_version'] == 1, 'Unexpected native receipt schema')
    _require(receipt.get('status') == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'Native compile receipt is not successful')
    _require(all(receipt.get(key) == value for key, value in identity.items()), 'Native compile receipt identity differs')
    manifests = {}
    roots = []
    for owner in sorted(OWNERS):
        root = Path(transfer_roots[owner])
        _require(not root.is_symlink() and root.is_dir(), 'Expected nonlinked transfer root')
        roots.append(root.resolve(strict=True))
        manifests[owner] = hashlib.sha256(_read(root / 'retained-files.json')).hexdigest()
    _require(len(set(roots)) == 2, 'Transfer roots must be distinct')
    return {'schema_version': 1, 'native_receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
            'same_run_identity': identity, 'retained_manifest_sha256': manifests}


def create(receipt_path, transfer_roots, output, run_identity):
    """Call immediately after trusted retention, before invoking packaging tools."""
    seal = _snapshot(receipt_path, transfer_roots, run_identity)
    payload = (json.dumps(seal, sort_keys=True, indent=2) + '\n').encode()
    output = Path(output)
    _require(not output.exists() and not output.is_symlink(), 'Seal output must be fresh')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(payload)
    return hashlib.sha256(payload).hexdigest()


def verify(seal_path, receipt_path, transfer_roots, run_identity, expected_seal_sha=None):
    """Return an immutable copy of validated owner->manifest hashes.

    Supply the create() return value from trusted phase state to also detect
    rewriting the seal itself. Without it, the seal file is the trust anchor.
    """
    payload = _read(seal_path)
    if expected_seal_sha is not None:
        _require(hashlib.sha256(payload).hexdigest() == _hash(expected_seal_sha), 'Transfer seal changed')
    seal = _json(payload)
    _require(isinstance(seal, dict) and set(seal) == {'schema_version', 'native_receipt_sha256', 'same_run_identity', 'retained_manifest_sha256'}, 'Unexpected transfer seal fields')
    _require(type(seal['schema_version']) is int and seal['schema_version'] == 1, 'Unexpected seal schema')
    _hash(seal['native_receipt_sha256'])
    _identity(seal['same_run_identity'])
    hashes = seal['retained_manifest_sha256']
    _require(isinstance(hashes, dict) and set(hashes) == OWNERS, 'Unexpected sealed owners')
    for value in hashes.values():
        _hash(value)
    _require(seal == _snapshot(receipt_path, transfer_roots, run_identity), 'Native transfer retention differs from trusted seal')
    return MappingProxyType(dict(hashes))
