"""Finite direct-host edits; inverse must recover the exact prior generated file."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy
import hashlib
import json
from pathlib import Path
from ci.qualification.native import owner_fixes_projection as owner_fixes
from ci.qualification.native import portal_identity_projection as portal_identity
from ci.qualification.native import preservation_evidence_projection as preservation_evidence
PATH = 'LiveContainer/SideStoreSupport/SideStore.swift'
MANIFEST = (Path(__file__).parent / 'direct-host-source-delta.json')

def specification():
    return json.loads(MANIFEST.read_text())

def require(ok, why):
    if not ok: raise ValueError(why)

def validate_bytes(data):
    data = preservation_evidence.restore_if_successor(PATH, data)
    data = portal_identity.restore_if_successor(PATH, data)
    data = owner_fixes.restore_if_successor(PATH, data)
    spec = specification()
    digest = hashlib.sha256(data).hexdigest()
    if digest == spec['before_sha256']: return False
    require(digest == spec['after_sha256'], 'Unreviewed direct host source')
    lines = data.decode().splitlines(keepends=True)
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Direct host delta mismatch')
        lines[start:end] = edit['before'].splitlines(keepends=True)
    restored = ''.join(lines).encode()
    require(hashlib.sha256(restored).hexdigest() == spec['before_sha256'],
            'Direct host inverse changed historical preservation implementation')
    return True

def validate_source(sources):
    return validate_bytes((sources / PATH).read_bytes())

def normalize(contract):
    contract = preservation_evidence.normalize(contract, PATH)
    contract = portal_identity.normalize(contract, PATH)
    contract = owner_fixes.normalize(contract, PATH)
    spec = specification()
    if contract.get('input_sha256', {}).get(PATH) != spec['after_sha256']: return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][PATH] = spec['before_sha256']
    return result
