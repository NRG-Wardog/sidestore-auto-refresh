"""Finite fixture extraction updates; original case bodies/assertions remain sealed."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy
import hashlib
import json
from pathlib import Path
MANIFEST = (Path(__file__).parent / 'direct-fixture-source-delta.json')

def specifications():
    return json.loads(MANIFEST.read_text())['files']

def require(ok, why):
    if not ok: raise ValueError(why)

def validate_bytes(path, data):
    spec = specifications()[path]
    digest = hashlib.sha256(data).hexdigest()
    if digest == spec['before_sha256']: return False
    require(digest == spec['after_sha256'], 'Unreviewed direct callback fixture: '+path)
    lines = data.decode().splitlines(keepends=True)
    for edit in reversed(spec['edits']):
        start, end = edit['after_lines']
        require(''.join(lines[start:end]) == edit['after'], 'Direct fixture delta mismatch')
        lines[start:end] = edit['before'].splitlines(keepends=True)
    require(hashlib.sha256(''.join(lines).encode()).hexdigest() == spec['before_sha256'],
            'Direct fixture inverse changed historical source')
    return True

def validate_source(sources):
    return {path:validate_bytes(path,(sources/path).read_bytes()) for path in specifications()}

def normalize(contract):
    result=copy.deepcopy(contract)
    for path,spec in specifications().items():
        if result.get('input_sha256',{}).get(path)==spec['after_sha256']:
            result['input_sha256'][path]=spec['before_sha256']
    return result
