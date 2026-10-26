#!/usr/bin/env python3
"""Run exact source-owned startup suites and bind their non-skipped evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'ci/issues-native'))
from verify_candidate_graph import load_candidate_graph

SUITES = {
    'LiveContainer': ('tests/test_startup_diagnostics.py', (
        'SideStoreSupport/SideStore.swift', 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
        'Resources/Localizable.xcstrings', 'tests/test_startup_diagnostics.py',
        'tests/startup_diagnostics/cases.swift')),
    'SideStore': ('tests/startup_outcome/test_contract.py', (
        'AltStore/AppDelegate.swift', 'AltStore/Core/Model/DatabaseManager/DatabaseManager.swift',
        'SideStore/MaintenanceManager.swift', 'tests/startup_outcome/test_contract.py')),
}

def require(value, message):
    if not value: raise ValueError(message)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result

def validate_identity(value):
    require(type(value) is dict and set(value)=={'workflow_commit','run_id','run_attempt'}, 'invalid run identity closure')
    require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}', value['workflow_commit']), 'invalid workflow identity')
    require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*', value[k]) for k in ('run_id','run_attempt')), 'invalid run identity')
    return value

def identity():
    value = {key: os.environ[env] for key, env in (
        ('workflow_commit','GITHUB_SHA'), ('run_id','GITHUB_RUN_ID'), ('run_attempt','GITHUB_RUN_ATTEMPT'))}
    return validate_identity(value)

def hashes(sources):
    result = {}
    for owner, (_, paths) in SUITES.items():
        root = sources/owner
        require(root.is_dir() and not root.is_symlink(), 'source root missing/linked')
        for name in paths:
            path = root/name
            require(path.is_file() and not path.is_symlink() and
                    path.resolve(strict=True).is_relative_to(root.resolve(strict=True)), 'source file missing/linked')
            require(not any((root/ancestor).is_symlink() for ancestor in Path(name).parents), 'linked source ancestor')
            result[owner+'/'+name] = sha(path)
    return result

def completed_tests(text):
    counts = re.findall(r'^Ran ([1-9][0-9]*) tests? in [0-9.]+s$', text, re.M)
    require(len(counts)==1, 'one nonempty unittest completion required')
    require(re.search(r'^OK\s*$', text, re.M) is not None and
            re.search(r'^(?:FAILED|OK \()', text, re.M) is None,
            'tests failed or skipped/expected-failure results present')
    return int(counts[0])

def verify_receipt(path, sources, native_identity):
    require(path.is_file() and not path.is_symlink(), 'startup receipt missing/linked')
    report = json.loads(path.read_text(), object_pairs_hook=unique_pairs)
    require(type(report) is dict and set(report)=={'schema','status','run_identity','source_graph','source_sha256',
            'swift_required','swiftc_sha256','physical_device_execution','suites'}, 'startup receipt closure differs')
    require(type(report['swiftc_sha256']) is str and re.fullmatch('[0-9a-f]{64}',report['swiftc_sha256']), 'invalid Swift compiler digest')
    require(report['schema']=='issue56-startup-source-owned-v1' and
            report['status']=='PASS_EXTRACTED_PRODUCTION_SWIFT_NOT_DEVICE', 'startup tests incomplete')
    require(validate_identity(report['run_identity'])==validate_identity(native_identity), 'startup run identity differs')
    require(report['source_graph']==load_candidate_graph()['owners'], 'startup graph differs')
    require(report['source_sha256']==hashes(sources), 'startup source hashes differ')
    require(type(report['suites']) is dict and set(report['suites'])==set(SUITES), 'startup suite closure differs')
    for owner, row in report['suites'].items():
        require(type(row) is dict and set(row)=={'script','exit_code','tests','skipped','log_sha256'}, 'startup suite row closure differs')
        require(row['script']==SUITES[owner][0] and type(row['exit_code']) is int and row['exit_code']==0 and
                type(row['skipped']) is int and row['skipped']==0,
                'startup suite not passed')
        log = path.parent/('issue56-startup-'+owner+'.log')
        require(log.is_file() and not log.is_symlink() and sha(log)==row['log_sha256'], 'startup log differs')
        require(type(row['tests']) is int and row['tests']==completed_tests(log.read_text()), 'startup test count differs')
    require(report['swift_required'] is True and report['physical_device_execution'] is False,
            'startup validation scope differs')
    return report

def run(sources, output):
    sources=sources.resolve(strict=True)
    require(output.is_dir() and not output.is_symlink(), 'owned evidence directory required')
    output=output.resolve(strict=True)
    destination=output/'issue56-startup-tests.json'
    require(not destination.exists() and not destination.is_symlink(), 'fresh startup receipt required')
    compiler=shutil.which('swiftc');require(compiler is not None, 'Swift compiler required')
    compiler=Path(compiler).resolve(strict=True)
    source_hashes=hashes(sources)
    report={'schema':'issue56-startup-source-owned-v1','status':'FAILED', 'run_identity':identity(),
            'source_graph':load_candidate_graph()['owners'], 'source_sha256':source_hashes,
            'swift_required':True,'swiftc_sha256':sha(compiler),'physical_device_execution':False,'suites':{}}
    try:
        for owner, (script, _) in SUITES.items():
            log=output/('issue56-startup-'+owner+'.log')
            require(not log.exists() and not log.is_symlink(), 'fresh startup log required')
            with log.open('x') as stream:
                result=subprocess.run([sys.executable,'-B',str(sources/owner/script)],
                    cwd=output, env={**os.environ,'REQUIRE_SWIFT_STARTUP_TESTS':'1'},
                    stdout=stream,stderr=subprocess.STDOUT,timeout=300)
            require(result.returncode==0,'startup suite failed: '+owner)
            count=completed_tests(log.read_text())
            report['suites'][owner]={'script':script,'exit_code':0,'tests':count,'skipped':0,'log_sha256':sha(log)}
        require(hashes(sources)==source_hashes,'startup sources changed during tests')
        report['status']='PASS_EXTRACTED_PRODUCTION_SWIFT_NOT_DEVICE'
    finally:
        destination.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    verify_receipt(destination,sources,report['run_identity'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.sources,args.output)
