"""Acquire one immutable, all-green host qualification. No fallback/access expansion."""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from recover_native_artifacts import API, REPOSITORY, archive_plan, extract, sha, require, LIMIT

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=Path(__file__).with_name('openssl-host-qualification.json')
FIELDS={'schema','status','repository','run_id','run_attempt','head_sha','workflow','branch',
        'artifact_id','artifact_name','artifact_size','artifact_sha256','receipt_path','receipt_sha256'}
REQUIRED_STEPS={'Source-owned qualification controls','Build unchanged official Os macOS dylib and full symbols',
    'Acquire unchanged comparison and pinned actual signer source',
    'Qualify original and transformed actual dylibs and observed pair bindings',
    'Retain immutable original framework and full matching symbols','Retain bounded evidence even on owned-step failure'}


def manifest():
    value=json.loads(MANIFEST.read_text())
    validate_manifest(value)
    return value


def validate_manifest(value):
    require(type(value) is dict and set(value)==FIELDS,'host manifest field closure differs')
    require(value['schema']=='fixed-openssl-host-qualification-artifact-v1' and
            value['status']=='VERIFIED_GREEN_IMMUTABLE_ARTIFACT','host artifact not yet independently qualified')
    require(value['repository']==REPOSITORY and value['workflow']=='.github/workflows/openssl-local-host-diagnostic.yml' and
            value['branch']=='diagnostic/openssl-local-host','unreviewed host qualification owner/workflow')
    for key in ('run_id','run_attempt','artifact_id','artifact_size'):
        require(type(value[key]) is int and value[key]>0,'incomplete host artifact identity')
    require(value['artifact_size']<=LIMIT,'host artifact exceeds acquisition bound')
    require(re.fullmatch('[0-9a-f]{40}',value['head_sha'] or '') is not None,'invalid host commit')
    for key in ('artifact_sha256','receipt_sha256'):
        require(re.fullmatch('[0-9a-f]{64}',value[key] or '') is not None,'incomplete host digest')
    require(value['artifact_name']=='openssl-local-host-evidence-'+str(value['run_id']) and
            value['receipt_path']=='qualification/host-local-qualification.json','unexpected host artifact/receipt path')


def validate_remote(run,jobs,artifact,pin):
    validate_manifest(pin)
    require(run.get('id')==pin['run_id'] and run.get('run_attempt')==pin['run_attempt'] and
            run.get('head_sha')==pin['head_sha'] and run.get('head_branch')==pin['branch'] and
            run.get('path')==pin['workflow'] and run.get('status')=='completed' and run.get('conclusion')=='success' and
            run.get('repository',{}).get('full_name')==pin['repository'] and
            run.get('head_repository',{}).get('full_name')==pin['repository'],'host run is not exact green qualification')
    require(jobs.get('total_count')==1 and len(jobs.get('jobs',[]))==1,'host job closure differs')
    job=jobs['jobs'][0];steps=job.get('steps',[])
    require(job.get('run_id')==pin['run_id'] and job.get('head_sha')==pin['head_sha'] and
            job.get('run_attempt')==pin['run_attempt'] and job.get('name')=='qualify' and
            job.get('status')=='completed' and job.get('conclusion')=='success' and steps,
            'host qualification job failed or differs')
    require(all(row.get('status')=='completed' and row.get('conclusion')=='success' for row in steps),
            'host step was failed/skipped/incomplete')
    require(len({row.get('number') for row in steps})==len(steps) and
            REQUIRED_STEPS <= {row.get('name') for row in steps},'required host step closure differs')
    owner=artifact.get('workflow_run',{})
    require(artifact.get('id')==pin['artifact_id'] and artifact.get('name')==pin['artifact_name'] and
            artifact.get('expired') is False and artifact.get('size_in_bytes')==pin['artifact_size'] and
            owner.get('id')==pin['run_id'] and owner.get('head_sha')==pin['head_sha'],'host artifact provenance differs')
    if artifact.get('digest') is not None:
        require(artifact['digest']=='sha256:'+pin['artifact_sha256'],'host artifact API digest differs')


def qualification_module():
    spec=importlib.util.spec_from_file_location('acquired_openssl_host_verifier',ROOT/'ci/issue55-openssl-local/qualify_host.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def verify_local(archive,destination,pin,packaging_identity):
    validate_manifest(pin)
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size==pin['artifact_size'] and
            sha(archive)==pin['artifact_sha256'],'downloaded host artifact bytes differ')
    plan=archive_plan(archive)
    # Qualification evidence contains public synthetic key/certificate PEMs and
    # native probe outputs already covered by the pinned evidence artifact, not apps.
    require(all(path.parts[0] in {'diagnostics','qualification','lts-build'} for _,path,_,_ in plan),
            'unexpected host evidence archive root')
    extract(archive,destination)
    receipt=destination/pin['receipt_path']
    require(receipt.is_file() and not receipt.is_symlink() and sha(receipt)==pin['receipt_sha256'],
            'acquired host qualification receipt differs')
    value=json.loads(receipt.read_text())
    identity={'workflow_commit':pin['head_sha'],'run_id':str(pin['run_id']),'run_attempt':str(pin['run_attempt'])}
    require(value.get('qualification_identity')==identity,'host receipt run identity differs')
    module=qualification_module()
    require(len(module.SOURCES)==29 and len(set(module.SOURCES))==29 and 'ci/issue55-openssl-local/framework_seal.py' in module.SOURCES and
            value.get('source_sha256')==module.sources(),
            'exact 29-source host qualification closure differs')
    verified=module.verify_host_local_qualification(receipt,pin['receipt_sha256'],packaging_identity,module.NATIVE)
    require(sha(archive)==pin['artifact_sha256'] and sha(receipt)==pin['receipt_sha256'], 'host artifact/receipt changed during verification')
    return receipt,verified


def acquire(output,packaging_identity,*,api=None):
    pin=manifest() # Pending metadata fails before any network or filesystem mutation.
    require(not output.exists() and not output.is_symlink(),'fresh owned host acquisition root required')
    require(output.parent.is_dir() and not output.parent.is_symlink() and output.parent.resolve(strict=True)==output.parent,
            'canonical real host acquisition parent required')
    output.mkdir();api=api or API(os.environ['GH_TOKEN'])
    run=api.json('/repos/'+REPOSITORY+'/actions/runs/'+str(pin['run_id']))
    jobs=api.json('/repos/'+REPOSITORY+'/actions/runs/'+str(pin['run_id'])+'/attempts/'+str(pin['run_attempt'])+'/jobs?per_page=100')
    artifact=api.json('/repos/'+REPOSITORY+'/actions/artifacts/'+str(pin['artifact_id']))
    validate_remote(run,jobs,artifact,pin)
    archive=output/'host-evidence.zip';api.download(pin['artifact_id'],archive)
    receipt,verified=verify_local(archive,output/'verified',pin,packaging_identity)
    report={'schema':'acquired-openssl-host-qualification-v1','manifest':pin,'manifest_sha256':sha(MANIFEST),
            'packaging_identity':packaging_identity,'qualification':verified,'receipt_sha256':sha(receipt),
            'artifact_sha256':sha(archive),'all_host_steps_passed':True,'validated_step_names':[row['name'] for row in jobs['jobs'][0]['steps']],
            'source_closure_count':29,
            'native_apps_rebuilt':False,'device_executed':False}
    (output/'acquisition.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return receipt,sha(receipt),report


if __name__=='__main__':
    require(not sys.argv[1:],'arbitrary artifact/manifest/output arguments are not accepted')
    identity={'workflow_commit':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT']}
    receipt,digest,report=acquire(Path.cwd().resolve(strict=True)/'openssl-host-inputs',identity)
    Path('recovery-evidence/openssl-host-acquisition.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    with open(os.environ['GITHUB_ENV'],'a') as env:
        env.write('OPENSSL_LOCAL_HOST_QUALIFICATION_RECEIPT='+str(receipt)+'\n')
        env.write('OPENSSL_LOCAL_HOST_QUALIFICATION_SHA256='+digest+'\n')
