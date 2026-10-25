#!/usr/bin/env python3
"""Validate and materialize explicit generated inputs into a reviewed consumer."""
import argparse
import json
from pathlib import Path
import shutil
import build

HERE=Path(__file__).resolve().parent

def validate(built):
    built=Path(built).resolve(strict=True)
    receipt=json.loads((built/'ios-build-receipt.json').read_text())
    if receipt.get('schema')!='issue53-ios-generated-dependency-v1' or receipt.get('status')!='BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED':
        raise ValueError('Unexpected generated iOS receipt')
    if receipt.get('source')!=json.loads((HERE/'source.json').read_text()) or receipt.get('recipe_sha256')!=build.digest(HERE/'build_ios.py') or receipt.get('shared_recipe_sha256')!=build.digest(HERE/'build.py'):
        raise ValueError('Generated dependency source/recipe differs')
    if any(receipt.get(k) is not False for k in ['device_executed','simulator_executed','production_admission','old_gitlink_overwritten']):
        raise ValueError('Unsupported qualification/admission claim')
    if set(receipt.get('slices',{}))!={'ios-arm64','sim-arm64','sim-x86_64'} or set(receipt.get('merged_simulator_module_probe_sha256',{}))!={'arm64','x86_64'} or receipt.get('xcframework_relative_path')!='GeneratedDependencies/OpenSSL35/OpenSSL.xcframework':
        raise ValueError('Incomplete generated slice/probe/path closure')
    generated=built/'GeneratedDependencies/OpenSSL35'
    if build.inventory(generated)!=receipt.get('generated_inventory'):raise ValueError('Generated framework/dSYM bytes differ')
    return generated,receipt

def install(built,consumer):
    generated,receipt=validate(built);consumer=Path(consumer).resolve(strict=True)
    project=consumer/'LiveContainer.xcodeproj/project.pbxproj'
    if 'path = GeneratedDependencies/OpenSSL35/OpenSSL.xcframework;' not in project.read_text():
        raise ValueError('Consumer has not explicitly selected generated dependency')
    target=consumer/'GeneratedDependencies/OpenSSL35'
    if target.exists() or target.is_symlink():raise ValueError('Refusing existing consumer dependency')
    parent=target.parent
    if parent.is_symlink():raise ValueError('Refusing symlinked generated dependency parent')
    parent.mkdir(exist_ok=True)
    shutil.copytree(generated,target)
    if build.inventory(target)!=receipt['generated_inventory']:raise ValueError('Copied dependency bytes differ')
    (target/'provenance.json').write_text(json.dumps({'schema':'issue53-consumer-generated-input-v1',
        'build_receipt_sha256':build.digest(Path(built).resolve()/'ios-build-receipt.json'),
        'consumer_project_sha256':build.digest(project),'build_receipt':receipt,
        'old_gitlink_retained_for_history_not_linked':True},indent=2)+'\n')
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--built',type=Path,required=True);parser.add_argument('--consumer',type=Path,required=True)
    args=parser.parse_args();print(install(args.built,args.consumer))
