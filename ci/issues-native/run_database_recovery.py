#!/usr/bin/env python3
"""Mandatory same-run exact recovery qualification; never substitutes a skipped host.

Portable verification needs only this file. Local production additionally verifies
pristine acquisition and the exact source closure in the native compiler checkout.
Synthetic tests of this producer are not native qualification.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'native-database-recovery-binding-v1'
STATUS = 'PASS_SAME_RUN_EXACT_RECOVERY_SQLITE_AND_COREDATA'
MODEL = 'AltStore/Core/Model/AltStore.xcdatamodeld'
PROJECT = 'AltStore.xcodeproj/project.pbxproj'
PRODUCT = ('SideStore/DatabaseRecovery.swift', 'SideStore/DatabaseRecoverySQLite.c',
           'AltStore/Core/Model/DatabaseManager/PersistentContainer.swift',
           'AltStore/Core/Model/DatabaseManager/DatabaseManager.swift',
           'SideStore/MaintenanceManager.swift', 'AltStore/Core/Model/InstalledApp.swift')
BASE = (*PRODUCT, 'tests/database_recovery/run.py', 'tests/database_recovery/main.swift',
        'tests/database_recovery/test_runner.py', 'tests/database_recovery/target_settings.py',
        'tests/database_recovery_coredata/run.py', 'tests/database_recovery_coredata/Cases.swift',
        'tests/database_recovery_coredata/test_contract.py', PROJECT)
ENGINE_CASES = sorted(('wal-and-process-lease', 'wal-relocation-and-resume',
    'identical-databases', 'different-databases', 'identical-apps', 'different-apps',
    'apps-changed-before-publication', 'malformed-receipt', 'source-symlink',
    'apps-symlink', 'orphan-sidecar', 'real-rollback-journal',
    'validation-failure-before-promotion', 'racer-created-generation',
    'source-ancestor-symlink', 'approved-root-canonicalization',
    'approved-root-leaf-symlink', 'missing-approved-roots', 'invalid-approved-ancestor',
    'inaccessible-approved-ancestor', 'aliased-roots-share-selection',
    'reset-lock-alias-contention', 'process-exit-before-publication',
    'process-exit-after-publication-and-divergence'))
ENGINE_INPUTS = set(PRODUCT[:2]) | {PROJECT, 'tests/database_recovery/main.swift', 'tests/database_recovery/run.py', 'tests/database_recovery/test_runner.py', 'tests/database_recovery/target_settings.py'}
COREDATA_INPUTS = set(PRODUCT) | {PROJECT, 'tests/database_recovery_coredata/Cases.swift', 'tests/database_recovery_coredata/run.py', 'tests/database_recovery_coredata/test_contract.py', 'tests/database_recovery/target_settings.py'}
FILES = ('source-before.json', 'source-after.json', 'source-immutability.json',
         'engine-driver.log', 'coredata-driver.log',
         'engine/report.json', 'engine/compile-c.log', 'engine/compile-swift.log', 'engine/native.log',
         'coredata/receipt.json', 'coredata/momc.log', 'coredata/clang.log', 'coredata/swiftc.log',
         'coredata/delete-seed.log', 'coredata/delete-recover.log',
         'coredata/wal-seed.log', 'coredata/wal-recover.log')

def require(ok, why):
    if not ok:
        raise ValueError(why)

def pairs(rows):
    value = {}
    for key, item in rows:
        require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value

def regular(path, root=None):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'missing or linked file: ' + str(path))
    require(not any(p.is_symlink() for p in path.parents), 'linked ancestor')
    if root is not None:
        require(path.resolve(strict=True).is_relative_to(root.resolve(strict=True)), 'file outside root')
    return path

def read(path):
    return json.loads(regular(path).read_text(), object_pairs_hook=pairs)

def sha(path):
    return hashlib.sha256(regular(path).read_bytes()).hexdigest()

def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def write(path, value):
    require(not path.exists() and not path.is_symlink(), 'fresh output required')
    with path.open('x') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')

def identity():
    return {k: os.environ[v] for k, v in [('workflow_commit', 'GITHUB_SHA'), ('run_id', 'GITHUB_RUN_ID'), ('run_attempt', 'GITHUB_RUN_ATTEMPT')]}

def valid_identity(value):
    require(type(value) is dict and set(value) == {'workflow_commit', 'run_id', 'run_attempt'}, 'identity closure')
    require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}', value['workflow_commit']), 'workflow commit')
    require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*', value[k]) for k in ('run_id', 'run_attempt')), 'run identity')

def hashes(value):
    require(type(value) is dict and value, 'empty hashes')
    for name, digest in value.items():
        require(type(name) is str and not Path(name).is_absolute() and '..' not in Path(name).parts, 'unsafe path')
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'invalid digest')

def sources(source):
    require(source.is_dir() and not source.is_symlink(), 'source directory missing/linked')
    model = source / MODEL
    require(model.is_dir() and not model.is_symlink(), 'model directory missing/linked')
    paths = [source / name for name in BASE] + sorted(p for p in model.rglob('*') if p.is_file())
    value = {p.relative_to(source).as_posix(): sha(regular(p, source)) for p in paths}
    validate_sources(value)
    return value

def validate_sources(value):
    hashes(value)
    require(set(BASE) < set(value), 'source closure missing')
    require(all(name in BASE or name.startswith(MODEL + '/') for name in value), 'unexpected source closure')
    require(MODEL + '/.xccurrentversion' in value, 'model version selector missing')

def validate_reports(engine, coredata, closure):
    require(set(engine) == {'schema', 'status', 'cases', 'production_coredata_validation', 'physical_device_execution', 'limitations', 'source_sha256', 'swift_build_settings', 'swift_version', 'platform', 'exact_production_sources_compiled_without_rewriting'}, 'engine report closure')
    require(engine['schema'] == 'sidestore-exact-recovery-sqlite-v2' and engine['physical_device_execution'] is False, 'engine schema/scope')
    require(type(engine['limitations']) is list and len(engine['limitations']) >= 3 and all(type(v) is str and v for v in engine['limitations']), 'engine limitations missing')
    require(engine.get('status') == 'PASS_NATIVE_EXACT_ENGINE_SYNTHETIC_SQLITE', 'engine not passed')
    require(type(engine.get('cases')) is list and sorted(engine['cases']) == ENGINE_CASES, 'engine case closure')
    require(engine.get('production_coredata_validation') is False, 'engine scope differs')
    require(engine.get('exact_production_sources_compiled_without_rewriting') is True, 'engine exact compilation absent')
    require(all(type(engine.get(k)) is str and engine[k] for k in ('swift_version', 'platform')), 'native toolchain absent')
    require(engine.get('source_sha256') == {k: closure[k] for k in ENGINE_INPUTS}, 'engine source inputs differ')
    require(set(coredata) == {'schema', 'status', 'cases', 'inputs', 'swift_build_settings', 'swift_version', 'physical_device_execution', 'synthetic_data_only', 'app_startup_or_installed_app_runtime_executed'}, 'Core Data report closure')
    require(coredata['status'] == 'PASS_NATIVE_EXACT_ENGINE_REAL_COREDATA', 'Core Data not passed')
    require(coredata['physical_device_execution'] is False and coredata['synthetic_data_only'] is True and coredata['app_startup_or_installed_app_runtime_executed'] is False, 'Core Data scope differs')
    require(type(coredata['swift_version']) is str and coredata['swift_version'], 'Core Data toolchain missing')
    require(coredata['schema'] == 'sidestore-exact-recovery-coredata-v2', 'Core Data schema')
    require(coredata['cases'] == [{'mode': mode, 'status': 'PASS', 'original_persistent_bytes_unchanged': True} for mode in ('delete', 'wal')], 'Core Data DELETE/WAL incomplete')
    require(all(row['original_persistent_bytes_unchanged'] is True for row in coredata['cases']), 'Core Data preservation must be boolean true')
    expected = COREDATA_INPUTS | {k for k in closure if k.startswith(MODEL + '/')}
    require(coredata['inputs'] == {k: closure[k] for k in expected}, 'Core Data source inputs differ')
    validate_settings(engine['swift_build_settings'], closure[PROJECT])
    require(engine['swift_build_settings'] == coredata['swift_build_settings'], 'runner language modes differ')

def validate_settings(value, project_sha):
    require(type(value) is dict and set(value) == {'target', 'configuration', 'declared', 'swiftc_argument', 'project_sha256'}, 'Swift settings closure')
    require(value['target'] == 'SideStore' and value['configuration'] == 'Release', 'Swift target/configuration differs')
    require(value['declared'] in ('5.0', '6.0') and value['swiftc_argument'] == value['declared'].split('.')[0], 'Swift language mode differs')
    require(value['project_sha256'] == project_sha, 'Swift settings project differs')

def native_settings(source):
    path = source / 'tests/database_recovery/target_settings.py'
    spec = importlib.util.spec_from_file_location('_recovery_target_settings', regular(path, source))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.inspect_project(source)

def verify_native_sources(value, native_sources):
    source = native_sources / 'SideStore'
    # Native projection intentionally edits the project. The immutable project
    # is graph-bound; compare product bytes and parsed target mode separately.
    expected = {k:v for k,v in value['source_sha256'].items() if k in PRODUCT or k.startswith(MODEL+'/')}
    model = source / MODEL
    actual = {name:sha(regular(source/name, source)) for name in PRODUCT}
    actual.update({p.relative_to(source).as_posix():sha(regular(p,source)) for p in model.rglob('*') if p.is_file()})
    require(actual == expected, 'compiled native product source closure differs')
    helper = 'tests/database_recovery/target_settings.py'
    require(sha(regular(source/helper,source)) == value['source_sha256'][helper], 'native settings helper differs')
    settings = native_settings(source)
    validate_settings(settings, sha(source/PROJECT))
    require({k:v for k,v in settings.items() if k != 'project_sha256'} == {k:v for k,v in value['engine']['swift_build_settings'].items() if k != 'project_sha256'}, 'projected native Swift mode differs')

def verify_effective(value, recovery_sha, expected_identity, settings):
    require(type(settings) is dict and set(settings) == {'schema', 'identity', 'recovery_receipt_sha256', 'target', 'configuration', 'effective_swift_version', 'swiftc_argument'}, 'effective Swift proof closure')
    require(settings['schema'] == 'database-recovery-effective-swift-v1' and settings['identity'] == expected_identity and settings['recovery_receipt_sha256'] == recovery_sha, 'effective Swift proof binding')
    declared = value['engine']['swift_build_settings']
    require(settings['target'] == 'SideStore' and settings['configuration'] == 'Release' and settings['effective_swift_version'] == declared['declared'] and settings['swiftc_argument'] == declared['swiftc_argument'], 'effective Swift mode mismatch')

def seal_effective(input_path, evidence, graph, expected_identity):
    receipt = evidence/'database-recovery-tests.json'
    value = verify_portable(read(receipt), graph, expected_identity)
    rows = read(input_path)
    require(type(rows) is list, 'effective Xcode settings must be a list')
    targets = [r for r in rows if type(r) is dict and r.get('target') == 'SideStore']
    require(len(targets) == 1, 'exactly one effective SideStore target required')
    effective = targets[0]['buildSettings']
    require(effective.get('CONFIGURATION') == 'Release', 'effective configuration differs')
    proof = {'schema':'database-recovery-effective-swift-v1', 'identity':expected_identity,
             'recovery_receipt_sha256':sha(receipt), 'target':'SideStore', 'configuration':'Release',
             'effective_swift_version':effective.get('SWIFT_VERSION'),
             'swiftc_argument':value['engine']['swift_build_settings']['swiftc_argument']}
    verify_effective(value, sha(receipt), expected_identity, proof)
    write(evidence/'database-recovery-settings.json', proof)
    return proof

def verify_portable(value, graph, expected_identity):
    valid_identity(expected_identity)
    require(type(value) is dict and set(value) == {'schema', 'status', 'identity', 'source_graph', 'source_sha256', 'engine', 'coredata', 'evidence_sha256', 'acquisition_sha256', 'verifier_sha256', 'physical_device_execution'}, 'recovery receipt closure')
    require(value['schema'] == SCHEMA and value['status'] == STATUS, 'recovery not passed')
    require(value['identity'] == expected_identity and value['source_graph'] == graph, 'recovery identity/graph differs')
    require(value['physical_device_execution'] is False, 'device claim unsupported')
    validate_sources(value['source_sha256'])
    validate_reports(value['engine'], value['coredata'], value['source_sha256'])
    hashes(value['evidence_sha256'])
    require(set(value['evidence_sha256']) == set(FILES), 'evidence closure differs')
    hashes({'acquisition': value['acquisition_sha256']})
    require(value['verifier_sha256'] == sha(Path(__file__)), 'recovery verifier changed')
    return value

def acquisition_verify(schema_sources, evidence, output):
    path = ROOT / 'ci/startup-upgrade/acquire.py'
    spec = importlib.util.spec_from_file_location('_recovery_acquire', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify(schema_sources, evidence / 'schema-acquisition.json', output)

def flat(name):
    return 'database-recovery--' + name.replace('/', '--')

def retained(evidence):
    folder = evidence / 'database-recovery'
    value = {name: sha(regular(folder / name, folder)) for name in FILES}
    require(all(sha(regular(evidence / flat(name), evidence)) == digest for name, digest in value.items()), 'top-level recovery evidence differs')
    return value

def verify_local(path, native_sources, schema_sources, evidence, graph, expected_identity, require_effective=True):
    require(path.name == 'database-recovery-tests.json' and path.parent.resolve() == evidence.resolve(), 'receipt location differs')
    value = verify_portable(read(path), graph, expected_identity)
    folder = evidence / 'database-recovery'
    require(value['evidence_sha256'] == retained(evidence), 'retained recovery evidence differs')
    require(value['acquisition_sha256'] == sha(evidence / 'schema-acquisition.json'), 'acquisition lineage differs')
    for name in ('source-before.json', 'source-after.json'):
        require(read(folder / name) == value['source_sha256'], 'source before/after differs')
    require(read(folder / 'engine/report.json') == value['engine'] and read(folder / 'coredata/receipt.json') == value['coredata'], 'raw reports differ')
    with tempfile.TemporaryDirectory(prefix='recovery-acquisition-') as temporary:
        output = Path(temporary).resolve() / 'verified.json'
        acquisition_verify(schema_sources, evidence, output)
        require(read(output) == read(folder / 'source-immutability.json'), 'acquisition seal differs')
    require(sources(schema_sources / 'SideStore') == value['source_sha256'], 'schema source closure differs')
    verify_native_sources(value, native_sources)
    if require_effective:
        verify_effective(value, sha(path), expected_identity, read(evidence/'database-recovery-settings.json'))
    return value

def execute(command, log, cwd):
    with log.open('x') as stream:
        subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=1200, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

def produce(output, native_sources, schema_sources, evidence, graph, expected_identity):
    valid_identity(expected_identity)
    require(output.name == 'database-recovery-tests.json' and output.parent.resolve() == evidence.resolve(), 'receipt location differs')
    require(not output.exists() and not output.is_symlink(), 'fresh receipt required')
    folder = evidence / 'database-recovery'
    require(not folder.exists() and not folder.is_symlink(), 'fresh recovery evidence required')
    require(schema_sources.is_dir() and not schema_sources.is_symlink() and not any(p.is_symlink() for p in schema_sources.absolute().parents), 'linked schema source ancestor')
    require(not (schema_sources/'SideStore').is_symlink(), 'linked schema owner')
    source = (schema_sources / 'SideStore').resolve(strict=True)
    require(not evidence.resolve().is_relative_to(schema_sources.resolve()) and not evidence.resolve().is_relative_to(native_sources.resolve()), 'output inside source checkout')
    folder.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix='recovery-precheck-') as temporary:
            acquisition_verify(schema_sources, evidence, Path(temporary).resolve() / 'before.json')
        before = sources(source)
        write(folder / 'source-before.json', before)
        # Fresh working/output directories are outside both source trees. Both
        # native commands must complete successfully; unsupported hosts fail.
        with tempfile.TemporaryDirectory(prefix='recovery-driver-') as temporary:
            cwd = Path(temporary).resolve()
            for kind, runner in [('engine', 'tests/database_recovery/run.py'), ('coredata', 'tests/database_recovery_coredata/run.py')]:
                execute([sys.executable, '-B', str(source / runner), '--output', str((folder / kind).resolve())], folder / (kind + '-driver.log'), cwd)
        after = sources(source)
        write(folder / 'source-after.json', after)
        require(before == after, 'source changed during native execution')
        acquisition_verify(schema_sources, evidence, folder / 'source-immutability.json')
        engine, coredata = read(folder / 'engine/report.json'), read(folder / 'coredata/receipt.json')
        validate_reports(engine, coredata, before)
        for name in FILES:
            target = evidence / flat(name)
            require(not target.exists() and not target.is_symlink(), 'fresh flattened evidence required')
            shutil.copyfile(regular(folder / name, folder), target)
        value = {'schema': SCHEMA, 'status': STATUS, 'identity': expected_identity,
                 'source_graph': graph, 'source_sha256': before, 'engine': engine, 'coredata': coredata,
                 'evidence_sha256': retained(evidence), 'acquisition_sha256': sha(evidence / 'schema-acquisition.json'),
                 'verifier_sha256': sha(Path(__file__)), 'physical_device_execution': False}
        verify_portable(value, graph, expected_identity)
        verify_native_sources(value, native_sources)
        write(output, value)
        try:
            verify_local(output, native_sources, schema_sources, evidence, graph, expected_identity, require_effective=False)
        except Exception:
            output.unlink()
            raise
        return value
    except Exception as error:
        write(folder / 'failure.json', {'status': 'FAILED_OR_NOT_RUN', 'error': str(error)})
        raise

if __name__ == '__main__':
    from verify_candidate_graph import load_candidate_graph
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['run', 'verify', 'settings'])
    for name in ('native-sources', 'schema-sources', 'evidence', 'output', 'input'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    require(args.evidence is not None, '--evidence required')
    if args.mode == 'settings':
        require(args.input is not None, '--input required')
        seal_effective(args.input, args.evidence, load_candidate_graph()['owners'], identity())
    else:
        require(all(v is not None for v in (args.output, args.native_sources, args.schema_sources)), 'source/output arguments required')
        fn = produce if args.mode == 'run' else verify_local
        fn(args.output, args.native_sources, args.schema_sources, args.evidence, load_candidate_graph()['owners'], identity())
