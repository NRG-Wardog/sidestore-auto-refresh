"""Draft nine-file ODA capsule admission; never executes artifact code or native tests.

Caller must supply reviewed exact native artifact identity after green-run
acquisition. Trusted Python/graph snapshots are packaging source, not artifacts.
This draft is not qualified against a full native archive yet.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
import types
import zipfile

HERE = Path(__file__).resolve().parent
SUITES = ('native', 'historical_engine', 'legacy', 'logging', 'swift', 'startup', 'input_boundary')
PREFIX = 'owned-oda/'
RECEIPT = PREFIX + 'receipt.json'
STARTUP = PREFIX + 'trusted-startup.json'
FILES = (RECEIPT, *(PREFIX + name + '.log' for name in SUITES), STARTUP)
TRUSTED_FILES = ('run_owned_oda.py', 'verify_candidate_graph.py', 'candidate-graph.json')
CAPSULE = 'owned-oda-projection.zip'
REPORT = 'owned-oda-projection.json'
MEMBER_LIMIT = 8 * 1024 * 1024
CAPSULE_LIMIT = 32 * 1024 * 1024


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'Duplicate ODA evidence key')
        result[key] = value
    return result


def decode(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def regular(path, limit):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Missing or linked ODA file')
    require(0 < path.stat().st_size <= limit, 'ODA file size bound')
    return path


def read(path, limit=2*1024*1024):
    return regular(path, limit).read_bytes()


def archive_identity(value):
    require(type(value) is dict and set(value) == {'artifact_id', 'bytes', 'sha256'}, 'ODA archive identity closure')
    require(type(value['artifact_id']) is int and value['artifact_id'] > 0, 'ODA artifact identity')
    require(type(value['bytes']) is int and 0 < value['bytes'] <= 2*1024**3 and digest(value['sha256']), 'ODA archive bounds/digest')


def trusted():
    raw_contract = read(HERE / 'owned-oda-source-contract.json')
    contract = decode(raw_contract)
    require(type(contract) is dict and set(contract) == {'schema', 'native_builder', 'files_sha256', 'git_blobs'}, 'ODA source contract closure')
    require(contract['schema'] == 'reviewed-owned-oda-package-contract-v1', 'ODA source contract schema')
    require(type(contract['native_builder']) is str and re.fullmatch('[0-9a-f]{40}', contract['native_builder']), 'ODA native builder')
    require(set(contract['files_sha256']) == set(TRUSTED_FILES) and set(contract['git_blobs']) == set(TRUSTED_FILES), 'ODA trusted import closure')
    folder = HERE / 'reviewed-native'
    source_bytes = {}
    for name in TRUSTED_FILES:
        data = read(folder / name)
        source_bytes[name] = data
        require(sha(data) == contract['files_sha256'][name], 'ODA reviewed source hash differs: ' + name)
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(blob == contract['git_blobs'][name], 'ODA reviewed source blob differs: ' + name)
    def load(name, filename):
        path = folder / filename
        module = types.ModuleType(name)
        module.__file__ = str(path)
        # Compile the already hash-verified bytes, never an ambient/stale .pyc.
        exec(compile(source_bytes[filename], str(path), 'exec'), module.__dict__)
        return module
    # The native helper has exactly one non-stdlib import. Bind that name to the
    # verified dependency, restore the caller's import state, and never load code
    # from the native artifact or an ambient module of the same name.
    graph_module = load('_oda_package_graph', 'verify_candidate_graph.py')
    previous = sys.modules.get('verify_candidate_graph')
    sys.modules['verify_candidate_graph'] = graph_module
    try:
        oda = load('_oda_package_native', 'run_owned_oda.py')
    finally:
        if previous is None:
            sys.modules.pop('verify_candidate_graph', None)
        else:
            sys.modules['verify_candidate_graph'] = previous
    require(oda.SUITES == SUITES, 'ODA native suite inventory differs')
    graph_path = folder / 'candidate-graph.json'
    graph = graph_module.load_candidate_graph(graph_path)
    oda.verify_input_boundary_graph(graph)
    return contract, sha(raw_contract), graph, graph_path, oda


def native_identity(manifest, contract):
    require(manifest['commit'] == contract['native_builder'], 'ODA unreviewed native builder')
    require(all(type(manifest[key]) is int and manifest[key] > 0 for key in ('run_id', 'run_attempt')), 'ODA run/attempt type')
    return {'workflow_commit': manifest['commit'], 'run_id': str(manifest['run_id']), 'run_attempt': str(manifest['run_attempt'])}


def native_receipt(data, manifest, contract, graph):
    identity = native_identity(manifest, contract)
    native = decode(data)
    require(type(native) is dict and all(type(native.get(key)) is str and native[key] == value for key, value in identity.items()), 'ODA native run differs')
    require(native.get('status') == 'COMPILE_ONLY_PASS_REQUIRES_REVIEW' and native.get('requested_graph') == graph['owners'], 'ODA native graph/status differs')
    require(native.get('source_owned_oda_qualification_executed') is True, 'ODA qualification not executed')
    for flag in ('physical_device_execution', 'authentication_executed', 'live_authentication_executed'):
        require(native.get(flag) is False, 'ODA aggregate device/authentication claim differs')
    require(digest(native.get('owned_oda_qualification_receipt_sha256')), 'ODA aggregate receipt binding missing')
    return native, identity


def references(data, native):
    require(sha(data) == native['owned_oda_qualification_receipt_sha256'], 'ODA receipt differs from aggregate hash')
    receipt = decode(data)
    require(type(receipt) is dict and type(receipt.get('suites')) is dict and set(receipt['suites']) == set(SUITES), 'ODA finite suite closure differs')
    result = {RECEIPT: sha(data), STARTUP: receipt.get('startup_receipt_sha256')}
    for suite in SUITES:
        row = receipt['suites'][suite]
        require(type(row) is dict, 'ODA suite row type')
        result[PREFIX + suite + '.log'] = row.get('log_sha256')
    require(set(result) == set(FILES) and all(digest(value) for value in result.values()), 'ODA finite reference hash closure differs')
    return result


def member(archive, name):
    require(name in archive.namelist(), 'ODA required member missing')
    info = archive.getinfo(name)
    require(not info.is_dir() and not info.flag_bits & 1 and stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG, 'ODA archive member kind differs')
    require(0 < info.file_size <= MEMBER_LIMIT, 'ODA member size bound')
    return archive.read(info)


def members(archive, native, *, exact):
    names = archive.namelist()
    require(0 < len(names) <= 20000, 'ODA archive member count bound')
    require(len(names) == len(set(name.casefold() for name in names)), 'ODA duplicate archive members')
    require(set(FILES) <= set(names) and (not exact or set(names) == set(FILES)), 'ODA capsule member closure differs')
    raw = {name: member(archive, name) for name in FILES}
    require(sum(map(len, raw.values())) <= CAPSULE_LIMIT, 'ODA capsule total size bound')
    hashes = references(raw[RECEIPT], native)
    require(all(sha(raw[name]) == value for name, value in hashes.items()), 'ODA transitive evidence hash differs')
    return raw, hashes


def semantics(raw, graph_path, oda):
    # This original reviewed function is portable: no Git, compiler, download,
    # native procedure, or account operation lies on its verification path.
    with tempfile.TemporaryDirectory(prefix='owned-oda-package-') as temp:
        root = Path(temp)
        (root / 'owned-oda').mkdir()
        for name, data in raw.items():
            require(name in FILES, 'ODA unreviewed extraction path')
            (root / name).write_bytes(data)
        oda.verify_receipt(root / RECEIPT, graph_path)


def project(archive_path, root, manifest, expected_archive):
    """Called after green-run acquisition; expected_archive is reviewed source data."""
    archive_identity(expected_archive)
    archive_path = regular(archive_path, 2*1024**3)
    h = hashlib.sha256()
    with archive_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    require(archive_path.stat().st_size == expected_archive['bytes'] and h.hexdigest() == expected_archive['sha256'], 'ODA original archive bytes differ')
    root = Path(root).resolve(strict=True)
    contract, contract_sha, graph, graph_path, oda = trusted()
    native_raw = read(root / 'native-receipt.json')
    native, identity = native_receipt(native_raw, manifest, contract, graph)
    with zipfile.ZipFile(archive_path) as archive:
        require(member(archive, 'receipt.json') == native_raw, 'ODA native receipt differs between artifacts')
        raw, hashes = members(archive, native, exact=False)
    semantics(raw, graph_path, oda)
    with zipfile.ZipFile(root / CAPSULE, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in FILES:
            info = zipfile.ZipInfo(name); info.create_system = 3; info.external_attr = 0o100444 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw[name])
    capsule = read(root / CAPSULE, CAPSULE_LIMIT)
    report = {'schema': 'owned-oda-package-projection-v1', 'native_identity': identity,
              'source_archive': dict(expected_archive), 'native_receipt_sha256': sha(native_raw),
              'owned_receipt_sha256': hashes[RECEIPT], 'reference_sha256': hashes,
              'capsule_sha256': sha(capsule), 'capsule_bytes': len(capsule),
              'reviewed_source_contract_sha256': contract_sha, 'device_or_authentication_proven': False}
    with (root / REPORT).open('x') as output:
        output.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return verify(root, manifest, expected_archive)


def verify(root, manifest, expected_archive):
    """Use unchanged before compression and in the final independent IPA audit."""
    archive_identity(expected_archive)
    root = Path(root).resolve(strict=True)
    contract, contract_sha, graph, graph_path, oda = trusted()
    native_raw = read(root / 'native-receipt.json')
    native, identity = native_receipt(native_raw, manifest, contract, graph)
    report_raw = read(root / REPORT); report = decode(report_raw)
    require(type(report) is dict and set(report) == {'schema', 'native_identity', 'source_archive', 'native_receipt_sha256',
            'owned_receipt_sha256', 'reference_sha256', 'capsule_sha256', 'capsule_bytes',
            'reviewed_source_contract_sha256', 'device_or_authentication_proven'}, 'ODA projection report closure differs')
    archive_identity(report['source_archive'])
    require(report['schema'] == 'owned-oda-package-projection-v1' and report['native_identity'] == identity and
            report['source_archive'] == expected_archive, 'ODA projection lineage differs')
    require(report['native_receipt_sha256'] == sha(native_raw) and report['reviewed_source_contract_sha256'] == contract_sha,
            'ODA projection source/native hash differs')
    require(report['device_or_authentication_proven'] is False, 'ODA projection scope differs')
    capsule = read(root / CAPSULE, CAPSULE_LIMIT)
    require(type(report['capsule_bytes']) is int and report['capsule_bytes'] == len(capsule) and report['capsule_sha256'] == sha(capsule), 'ODA capsule bytes differ')
    with zipfile.ZipFile(io.BytesIO(capsule)) as archive:
        raw, hashes = members(archive, native, exact=True)
    require(report['reference_sha256'] == hashes and report['owned_receipt_sha256'] == hashes[RECEIPT], 'ODA projection references differ')
    semantics(raw, graph_path, oda)
    return {'schema': 'owned-oda-package-admission-v1', 'status': 'PASS_EXACT_SOURCE_OWNED_ODA_NOT_DEVICE_OR_AUTHENTICATION',
            'native_identity': identity, 'source_archive': dict(expected_archive), 'native_receipt_sha256': sha(native_raw),
            'projection_report_sha256': sha(report_raw), 'capsule_sha256': sha(capsule),
            'reviewed_source_contract_sha256': contract_sha, 'reference_sha256': hashes,
            'device_or_authentication_proven': False, 'native_execution_by_adapter': False}
