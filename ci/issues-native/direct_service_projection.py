"""Finite direct-service edits; inverse must recover the exact prior generated file."""
import copy
import hashlib
import json
from pathlib import Path
PATH = 'SideStore/AltStore/AppDelegate.swift'
MANIFEST = Path(__file__).with_name('direct-service-source-delta.json')

def specification():
    return json.loads(MANIFEST.read_text())

def require(ok, why):
    if not ok: raise ValueError(why)

def validate_bytes(data):
    spec = specification()
    digest = hashlib.sha256(data).hexdigest()
    if digest == spec['before_sha256']: return False
    require(digest == spec['after_sha256'], 'Unreviewed direct service source')
    lines = data.decode().splitlines(keepends=True)
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Direct service delta mismatch')
        lines[start:end] = edit['before'].splitlines(keepends=True)
    restored = ''.join(lines).encode()
    # This is the actual declaration extracted by the frozen host bundle fixture.
    def preservation_binding(text):
        anchor = 'struct V3LegacyReplacementBinding:'
        require(text.count(anchor) == 1, 'Nonunique preservation binding')
        start = text.index(anchor)
        return text[start:text.index('\n}', start) + 2]
    require(preservation_binding(data.decode()) == preservation_binding(restored.decode()),
            'Direct service changed preservation binding declaration')
    require(hashlib.sha256(restored).hexdigest() == spec['before_sha256'],
            'Direct service inverse changed historical preservation implementation')
    return True

def validate_source(sources):
    return validate_bytes((sources / PATH).read_bytes())

def normalize(contract):
    spec = specification()
    if contract.get('input_sha256', {}).get(PATH) != spec['after_sha256']: return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][PATH] = spec['before_sha256']
    return result
