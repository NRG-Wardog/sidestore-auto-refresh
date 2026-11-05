#!/usr/bin/env python3
"""Actual pinned ZSign consumer and cold-process loader checks; synthetic only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import verify_cms

HERE = Path(__file__).resolve().parent

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def execute(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError(f'Consumer command exit {result.returncode}\n{result.stdout[-8000:]}\n{result.stderr[-8000:]}')
    return result.stdout

def acquire_source(output):
    spec = json.loads((HERE/'zsign-source.json').read_text())
    repo = output/'zsign-source'
    if repo.exists(): raise ValueError('Refusing existing source output')
    execute(['git','init','-q',str(repo)])
    def git(*args): return execute(['git','-C',str(repo),*args]).strip()
    git('remote','add','origin','https://github.com/'+spec['repository']+'.git')
    git('fetch','--depth=1','--filter=blob:none','origin',spec['commit'])
    if git('rev-parse','FETCH_HEAD^{tree}') != spec['tree']: raise ValueError('ZSign source tree mismatch')
    git('sparse-checkout','init','--no-cone')
    (repo/'.git/info/sparse-checkout').write_text('/ZSign/\n')
    git('checkout','--detach',spec['commit'])
    validate_source(repo)
    return repo

def validate_source(repo):
    spec=json.loads((HERE/'zsign-source.json').read_text())
    for name,digest in spec['files'].items():
        path=repo/name
        if path.is_symlink() or not path.is_file() or sha(path)!=digest: raise ValueError('Production source byte mismatch: '+name)
    return spec

def environment(work):
    env=dict(os.environ)
    for key in list(env):
        if key.startswith(('LD_','DYLD_','OPENSSL_')): env.pop(key)
    modules=work/'empty-providers';modules.mkdir();env['OPENSSL_MODULES']=str(modules)
    return env

def framework(inputs,owner):
    base=(inputs/owner/'OpenSSL.framework/Versions/A').resolve()
    pins=json.loads((HERE/'pinned-inputs.json').read_text())[owner]
    if sha(base/'OpenSSL')!=pins['binary_sha256']: raise ValueError('Framework bytes differ')
    expected=json.loads((HERE/(owner+'-headers.json')).read_text())
    header_paths=list((base/'Headers').rglob('*'))
    if any(p.is_symlink() for p in header_paths): raise ValueError('Unexpected header symlink')
    actual={str(p.relative_to(base/'Headers')):sha(p) for p in header_paths if p.is_file()}
    if actual!=expected: raise ValueError('Framework header bytes differ')
    return base,pins

def zsign(owner,inputs,source,evidence,resolution=None,variant=None):
    spec=validate_source(source);base,pins=resolution if resolution is not None else framework(inputs,owner)
    with tempfile.TemporaryDirectory() as temp:
        work=Path(temp);env=environment(work);include=work/'include';include.mkdir();(include/'openssl').symlink_to(base/'Headers',target_is_directory=True)
        binary=work/'zsign'
        production=['ZSign/openssl.cpp','ZSign/signing.cpp']+['ZSign/common/'+name+'.cpp' for name in ['json','base64','sha','util','fs','log','timer']]
        execute(['xcrun','clang++','-std=c++17','-I'+str(include),'-I'+str(source/'ZSign'),'-I'+str(source/'ZSign/common'),
                 str(HERE/'zsign_probe.cpp'),*[str(source/p) for p in production],str(base/'OpenSSL'),
                 '-Wl,-rpath,'+str(base.parents[2]),'-o',str(binary)],env=env)
        stdout=execute([str(binary)],cwd=work,env=env)
        (evidence/(owner+'-zsign-process.log')).write_text(stdout)
        image=[line[6:] for line in stdout.splitlines() if line.startswith('IMAGE ')]
        version=[line[8:] for line in stdout.splitlines() if line.startswith('VERSION ')]
        if len(image)!=1 or Path(image[0]).resolve()!=base/'OpenSSL' or version!=[pins['version']]: raise ValueError('Actual consumer linked wrong library')
        provider_rows=[line.split('\t') for line in stdout.splitlines() if line.startswith('PROVIDER\t')]
        if len(provider_rows)!=2 or {r[1] for r in provider_rows}!={'default','legacy'} or any(Path(r[2]).resolve()!=base/'OpenSSL' for r in provider_rows): raise ValueError('Actual ZSign provider dispatch image differs')
        if 'PASS actual-ZSign-code-directories-and-CMS-produced-untrusted-chain' not in stdout.splitlines(): raise ValueError('Actual signer did not complete')
        cases=verify_cms.verify(work)
        # Independent parser must reject changed pages/CD headers and truncated CMS.
        for file in ['code.bin','cd-sha1.bin','cd-sha256.bin','cms.der','cms-slot.bin']:
            path=work/file;original=path.read_bytes();path.write_bytes(original[:-1])
            try:
                verify_cms.verify(work)
            except ValueError: pass
            else: raise ValueError('Independent parser accepted truncated '+file)
            finally: path.write_bytes(original)
        verify_cms.verify(work)
        verifier=work/'verify'
        execute(['xcrun','swiftc',str(HERE/'verify_zsign.swift'),'-o',str(verifier)],env=env)
        result=execute([str(verifier)],cwd=work,env=env).strip()
        if result!='PASS actual-ZSign-CMS-signature-and-tamper-untrusted-chain': raise ValueError('Security verifier did not complete')
        receipt={'schema':'issue53-actual-zsign-v1','status':'PASS_SYNTHETIC_UNTRUSTED_CHAIN','owner':owner,
                 'source':spec,'input_variant':variant,'provider_dispatch_images':provider_rows,'library_sha256':pins['binary_sha256'],'loaded_image':image[0],'version':version[0],
                 'cases':cases+['independent-truncation-rejection','Apple-Security-CMS-signature-and-tamper','synthetic-chain-not-trusted'],
                 'probe_sha256':sha(HERE/'zsign_probe.cpp'),'parser_sha256':sha(HERE/'verify_cms.py'),'verifier_sha256':sha(HERE/'verify_zsign.swift'),
                 'trusted_Apple_signing_identity':False,'device_executed':False,'production_app_executed':False}
        (evidence/(owner+'-zsign.json')).write_text(json.dumps(receipt,indent=2)+'\n')

def coload(inputs,evidence,resolution=None,variant=None):
    owners=resolution if resolution is not None else {owner:framework(inputs,owner) for owner in ['host','embedded']}
    if set(owners)!={'host','embedded'}: raise ValueError('Co-load requires exact host and embedded roles')
    with tempfile.TemporaryDirectory() as temp:
        work=Path(temp);env=environment(work);include=work/'include';include.mkdir();(include/'openssl').symlink_to(owners['host'][0]/'Headers',target_is_directory=True)
        binary=work/'coload'
        execute(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-I'+str(include),str(HERE/'coload.c'),'-o',str(binary)],env=env)
        outcomes=[]; errors=[]
        for scope in ['local','global']:
            for order in [('host','embedded'),('embedded','host')]:
                paths=[owners[owner][0]/'OpenSSL' for owner in order]
                try:
                    stdout=execute([str(binary),scope,*map(str,paths)],cwd=work,env=env)
                    (evidence/('coload-'+scope+'-'+order[0]+'.log')).write_text(stdout)
                    rows=[line.split('\t') for line in stdout.splitlines() if '\t' in line]
                    for i,owner in enumerate(order):
                        expected=paths[i];selected=[r for r in rows if len(r)>1 and r[1]==str(i)]
                        versions=[r[2] for r in selected if r[0]=='VERSION']
                        if versions!=[owners[owner][1]['version']]: raise ValueError('Co-load aliased a requested library version')
                        labels=[(r[0],r[2] if r[0] in ['SYMBOL','PROVIDER'] else '') for r in selected]
                        if sorted(labels)!=sorted([('HANDLE',''),('VERSION',''),('SYMBOL','PKCS12_parse'),('SYMBOL','EVP_DigestSignInit'),('SYMBOL','OSSL_PROVIDER_load'),('PROVIDER','default'),('PROVIDER','legacy')]): raise ValueError('Cold process binding evidence closure differs')
                        images=[r[-1] for r in selected if r[0] in ['HANDLE','SYMBOL','PROVIDER']]
                        if len(images)!=6 or any(Path(p).resolve()!=expected for p in images): raise ValueError('Unexpected co-loaded symbol/provider binding')
                    if 'PASS cold-process-two-library-probe' not in stdout.splitlines(): raise ValueError('Incomplete cold process')
                    outcomes.append({'scope':scope,'order':order,'observed_rows':rows})
                except Exception as error:
                    errors.append({'scope':scope,'order':order,'error':str(error)})
                    (evidence/('coload-'+scope+'-'+order[0]+'-failure.log')).write_text(str(error))
        receipt={'schema':'issue53-coload-v1','status':'FAIL_QUARANTINED' if errors else 'PASS_MACOS_DIAGNOSTIC_ONLY','cold_processes':outcomes,'errors':errors,
                 'input_variant':variant,'libraries':{o:p[1]['binary_sha256'] for o,p in owners.items()},'probe_sha256':sha(HERE/'coload.c'),
                 'ios_loader_qualified':False,'production_app_executed':False}
        (evidence/'coload.json').write_text(json.dumps(receipt,indent=2)+'\n')
        if errors: raise ValueError('One or more cold-process bindings failed; see quarantined receipt')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['acquire','zsign','coload']);parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--evidence',type=Path);parser.add_argument('--owner',choices=['host','embedded'])
    args=parser.parse_args();inputs=args.inputs.resolve()
    if args.action=='acquire': acquire_source(inputs)
    elif args.action=='zsign': zsign(args.owner,inputs,inputs/'zsign-source',args.evidence.resolve())
    else: coload(inputs,args.evidence.resolve())
