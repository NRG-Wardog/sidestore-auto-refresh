#!/usr/bin/env python3
"""Unpack exact successful generated artifact; no network or binary execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import tarfile
import zipfile
import build
import install_ios

HERE=Path(__file__).resolve().parent

def extract_tar(raw,output):
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
        members=tar.getmembers();seen=set()
        if sum(m.size for m in members)>500_000_000:raise ValueError('Generated archive expansion limit exceeded')
        for member in members:
            p=Path(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='GeneratedDependencies':raise ValueError('Unsafe generated archive path')
            normalized=p.as_posix()
            if normalized in seen:raise ValueError('Duplicate generated archive path')
            seen.add(normalized)
            if not (member.isfile() or member.isdir()) or member.mode&0o7000:raise ValueError('Generated archive special entry rejected')
        for member in members:
            target=output/member.name
            if member.isdir():target.mkdir(parents=True,exist_ok=True);target.chmod(member.mode&0o777)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
                target.chmod(member.mode&0o777)

def validate_zip_entries(infos,expected):
    if len(infos)!=len(expected) or {p.filename for p in infos}!=expected:raise ValueError('Unexpected outer artifact closure')
    if sum(p.file_size for p in infos)>300_000_000:raise ValueError('Outer artifact expansion limit exceeded')
    for item in infos:
        kind=stat.S_IFMT(item.external_attr>>16)
        if kind not in (0,stat.S_IFREG) or item.is_dir():raise ValueError('Outer artifact special entry rejected')

def acquire(archive,output):
    archive=Path(archive).resolve(strict=True);requested=Path(output).absolute()
    if requested.is_symlink():raise ValueError('Refusing symlinked artifact output')
    output=requested.resolve()
    pins=json.loads((HERE/'generated-ios-pin.json').read_text())
    if output.exists() or output.is_symlink():raise ValueError('Refusing existing artifact output')
    if build.digest(archive)!=pins['artifact_sha256']:raise ValueError('Generated artifact outer SHA mismatch')
    expected=set(pins['members'])|{'SHA256SUMS'}
    with zipfile.ZipFile(archive) as zip:
        infos=zip.infolist()
        validate_zip_entries(infos,expected)
        data={p.filename:zip.read(p) for p in infos}
    for name,sha in pins['members'].items():
        if hashlib.sha256(data[name]).hexdigest()!=sha:raise ValueError('Generated artifact member SHA mismatch')
    sums={line.split('  ',1)[1]:line.split('  ',1)[0] for line in data['SHA256SUMS'].decode().splitlines()}
    if sums!=pins['members']:raise ValueError('Artifact checksum manifest differs')
    identity=data['workflow-identity.txt'].decode()
    expected_identity=f"workflow_commit={pins['workflow_commit']}\nrun_id={pins['run_id']}\nstatus=IOS_COMPILE_LINK_ONLY_NOT_RUNTIME\n"
    if identity!=expected_identity:raise ValueError('Generated workflow identity mismatch')
    receipt=json.loads(data['ios-build-receipt.json'])
    if receipt.get('recipe_sha256')!=pins['recipe_sha256'] or receipt.get('shared_recipe_sha256')!=pins['shared_recipe_sha256'] or receipt.get('source',{}).get('sha256')!=pins['source_sha256']:
        raise ValueError('Artifact recipe/source pin mismatch')
    install_ios.validate_receipt(receipt)
    output.mkdir(parents=True)
    extract_tar(data['generated-ios-inputs.tar.gz'],output)
    (output/'ios-build-receipt.json').write_bytes(data['ios-build-receipt.json'])
    install_ios.validate(output)
    (output/'artifact-acquisition.json').write_text(json.dumps({'schema':'issue53-generated-artifact-acquisition-v1','pin':pins,
        'pin_sha256':build.digest(HERE/'generated-ios-pin.json'),'acquisition_recipe_sha256':build.digest(Path(__file__)),
        'all_generated_bytes_verified':True,'runtime_executed':False},indent=2)+'\n')
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(acquire(args.archive,args.output))
