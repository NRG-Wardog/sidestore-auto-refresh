"""Validate embedded owned-simulator cleanup using reviewed source, never archive code."""
import hashlib
from pathlib import Path
import types
from contract import require
HERE = Path(__file__).resolve().parent
SNAPSHOT_SHA256 = '34c651312c0f0c5613057ec82a383af322865ebe69abb694994d746e946ee83b'


def verify_language_cleanup(receipt):
    require(type(receipt) is dict, 'Language receipt is not a dictionary')
    text = receipt.get('cleanup_evidence_json')
    require(type(text) is str and 0 < len(text.encode('utf-8')) <= 2*1024*1024, 'Language cleanup JSON missing or oversized')
    require(hashlib.sha256(text.encode('utf-8')).hexdigest() == receipt.get('cleanup_evidence_sha256'), 'Language cleanup hash differs')
    simulator = receipt.get('simulator')
    require(type(simulator) is dict and type(simulator.get('udid')) is str, 'Language cleanup simulator owner missing')
    path = HERE / 'cleanup-evidence.snapshot'
    require(path.is_file() and not path.is_symlink(), 'Reviewed cleanup source missing or linked')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, 'Reviewed cleanup source differs')
    helper = types.ModuleType('_reviewed_language_cleanup')
    helper.__file__ = str(path)
    # Compile only the hash-verified source-owned snapshot, never artifact content.
    exec(compile(raw, str(path), 'exec'), helper.__dict__)
    # verify() parses evidence only; cleanup(), subprocess and simulator calls are not invoked.
    return helper.verify(text, simulator['udid'])
