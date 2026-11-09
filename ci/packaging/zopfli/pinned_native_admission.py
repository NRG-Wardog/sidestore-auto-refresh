"""Portable admission for the one historical native input, never a build runner.

Reviewed producer bytes remain unchanged so their self-hash checks stay exact.
Only trusted repository snapshots execute, never code from an artifact. Imports
are scoped to these modules rather than mutating process-global sys.modules.
"""
import builtins
import hashlib
from pathlib import Path
import types

IDENTITY = {'workflow_commit': '02eef20523795f6ef912063a8fac712a7f28b401',
            'run_id': '41950003202', 'run_attempt': '1'}
SNAPSHOT = Path(__file__).resolve().parent / 'reviewed-native/pinned-input'
HASHES = {'attachment_contract.py': '7b69b8effdfbe21e32ff347bdeeebc67c436a5319cd906b9ad58588aed308205',
 'diagnostics_contract.py': '375692a8695764c3d3275701fdd4e4600bf6cf21ec50dd5044cbf353e0201670',
 'multitask_receipt.py': 'c364ae144c1fe53172c247451192e2c8adf3c2f1af7ee800b25c09771d502c2f',
 'owned_simulator_cleanup.py': '23be72b878921539c5aba6edfdf4f1fe74c9f2252faeb03aecbf56e7a098bc66',
 'run_attachment.py': 'f0a6e0927216cf448dbebd4cdb545d6b4cbbd81f76578acf7641e7572b4c604e',
 'startup_upgrade_receipt.py': '07127b75aabda9f178ab091bd117c53d89db6389e2f5d20af4ae747a339814d9',
 'test_owned_simulator_cleanup.py': '0e5b03f70117ff31a5eeb3c8a35a6dfcbf7c13c0fa3e3cff610dece373fb8bce'}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def trusted(identity):
    require(identity == IDENTITY, 'Unsupported pinned native input identity')
    require(SNAPSHOT.is_dir() and not SNAPSHOT.is_symlink(), 'Missing or linked pinned verifier snapshot')
    require({p.name for p in SNAPSHOT.iterdir()} == set(HASHES), 'Pinned verifier snapshot closure differs')
    raw = {}
    for name, digest in HASHES.items():
        path = SNAPSHOT / name
        require(path.is_file() and not path.is_symlink(), 'Missing or linked pinned verifier: ' + name)
        raw[name] = path.read_bytes()
        require(hashlib.sha256(raw[name]).hexdigest() == digest, 'Pinned verifier bytes differ: ' + name)
    modules = {}
    def scoped_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in ('attachment_contract', 'diagnostics_contract'):
            return modules[name]
        return builtins.__import__(name, globals, locals, fromlist, level)
    for name in ('attachment_contract', 'diagnostics_contract', 'multitask_receipt', 'startup_upgrade_receipt'):
        path = SNAPSHOT / (name + '.py')
        module = types.ModuleType('_pinned_native_' + name)
        module.__file__ = str(path)
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=scoped_import)
        exec(compile(raw[name + '.py'], str(path), 'exec'), module.__dict__)
        modules[name] = module
    return modules

def verify_multitask(value, graph, identity):
    return trusted(identity)['multitask_receipt'].verify_portable(value, graph, identity)

def verify_startup_upgrade(value, graph, identity):
    return trusted(identity)['startup_upgrade_receipt'].verify_portable(value, graph, identity)
