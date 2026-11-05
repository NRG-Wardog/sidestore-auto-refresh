#!/usr/bin/env python3
"""Qualify generated LTS as an explicit experimental host input, not an old pin."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'issue53-crypto'))
import consumer
import build

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_generated(output):
    receipt=json.loads((output/'build-receipt.json').read_text())
    pins=json.loads((HERE/'source.json').read_text())
    if receipt['schema']!='issue53-lts-generated-dependency-v1' or receipt['status']!='BUILT_NOT_QUALIFIED_NOT_APP_READY':
        raise ValueError('Unexpected generated-dependency receipt')
    if receipt['source']!=pins or receipt['recipe_sha256']!=sha(HERE/'build.py'):
        raise ValueError('Generated input does not match reviewed recipe/source')
    if receipt['product_pins_changed'] or receipt['production_admission']:
        raise ValueError('Experimental dependency crossed production boundary')
    if build.inventory(output/'generated')!=receipt['generated_inventory']:
        raise ValueError('Generated framework/dSYM bytes changed')
    if sha(output/'headers.json')!=receipt['header_inventory_sha256']:
        raise ValueError('Generated header receipt changed')
    library=output/'generated/OpenSSL.framework/Versions/A/OpenSSL'
    if library.resolve()!=Path(receipt['library']).resolve() or sha(library)!=receipt['library_sha256']:
        raise ValueError('Generated library binding changed')
    return receipt,pins,library

def run(output,inputs,evidence):
    receipt,pins,library=validate_generated(output)
    variant={'kind':'generated-LTS-host-only-experiment','source':pins,'recipe_sha256':receipt['recipe_sha256'],
             'build_receipt_sha256':sha(output/'build-receipt.json'),'host_binary_sha256':receipt['library_sha256'],
             'embedded':'unchanged pinned3.6.2 from existing release archive','product_pins_changed':False}
    (evidence/'lts-input-variant.json').write_text(json.dumps(variant,indent=2)+'\n')
    command=[sys.executable,'-B',str(HERE.parent/'issue53-crypto/run.py'),'--library',str(library),
             '--headers',str(library.parent/'Headers'),'--header-inventory',str(output/'headers.json'),
             '--sha256',receipt['library_sha256'],'--expected-version',pins['version_text'],
             '--builtin-providers','--output',str(evidence/'lts-crypto.json')]
    failures=[]
    with (evidence/'lts-crypto.log').open('w') as log:
        try:code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
        except subprocess.TimeoutExpired:code=124;log.write('LTS probe timeout\n')
    if code:failures.append('generic-crypto')
    resolution=(library.parent,{'binary_sha256':receipt['library_sha256'],'version':pins['version_text']})
    for label,action in [
        ('actual-ZSign',lambda:consumer.zsign('lts-host',inputs,inputs/'zsign-source',evidence,resolution=resolution,variant=variant)),
        ('co-load',lambda:consumer.coload(inputs,evidence,resolution={'host':resolution,'embedded':consumer.framework(inputs,'embedded')},variant=variant))]:
        try:action()
        except Exception as error:
            failures.append(label);(evidence/(label+'-failure.log')).write_text(str(error))
    validate_generated(output)
    summary={'schema':'issue53-lts-qualification-v1','status':'FAIL_QUARANTINED' if failures else 'PASS_MACOS_SYNTHETIC_ONLY',
             'input_variant':variant,'failures':failures,'ios_artifact_built':False,'production_admission':False,
             'device_executed':False,'library_sha256':receipt['library_sha256'],
             'evidence_sha256':{p.name:sha(p) for p in sorted(evidence.iterdir()) if p.is_file() and p.name!='lts-qualification-driver.log'}}
    (evidence/'lts-qualification.json').write_text(json.dumps(summary,indent=2)+'\n')
    if failures:raise ValueError('LTS qualification failed: '+','.join(failures))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built',type=Path,required=True);parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();run(args.built.resolve(),args.inputs.resolve(),args.evidence.resolve())
