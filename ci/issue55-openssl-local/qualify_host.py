#!/usr/bin/env python3
"""Qualify true-local removal on a copy of the actual Os macOS dylib.

Original build/qualification stay immutable. Paired identical-image aliases are
observed, never manufactured by changing install names or UUIDs. No iOS proof.
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
NATIVE = {'workflow_commit':'0205f33ff50a024d4e125b6b404e989911ac5eb2', 'run_id':'40245615437', 'run_attempt':'1'}
DEVELOPER = '/Applications/Xcode_26.4.1.app/Contents/Developer'
STATUS = {'original_qualification':'PASS', 'transformed_crypto':'PASS', 'actual_ZSign_Apple_Security':'PASS', 'mixed_version_cold_orders':'PASS', 'identical_pair_binding_equivalence':'PASS', 'structural_transform':'PASS'}
SOURCES = ('ci/issue55-openssl-local/qualify_host.py', 'ci/issue53-lts/build.py', 'ci/issue53-lts/qualify.py', 'ci/issue53-lts/source.json', 'ci/issue53-crypto/acquire.py', 'ci/issue53-crypto/consumer.py', 'ci/issue53-crypto/run.py', 'ci/issue53-crypto/probe.c', 'ci/issue53-crypto/verify.swift', 'ci/issue53-crypto/host-headers.json', 'ci/issue53-crypto/embedded-headers.json', 'ci/issue53-crypto/coload.c', 'ci/issue53-crypto/zsign_probe.cpp', 'ci/issue53-crypto/zsign-source.json', 'ci/issue53-crypto/verify_cms.py', 'ci/issue53-crypto/verify_zsign.swift', 'ci/issue53-crypto/pinned-inputs.json', 'scripts/macho_stab_guard.py', 'scripts/macho_local_symbol_guard.py', 'scripts/openssl_local_symbol_policy.py', 'scripts/optimize_native_candidate.py', 'scripts/experiment_strip_stabs.py', 'scripts/audit_ipa_signing.py', 'scripts/ipa_size_report.py', 'scripts/packaging_adapters.py', 'ci/issue55-openssl-size/command_evidence.py', 'scripts/package_livecontainer_combined.py', '.github/workflows/openssl-local-host-diagnostic.yml')

def require(ok, message):
    if not ok: raise ValueError(message)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value): Path(path).write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')
def read(path): return json.loads(Path(path).read_text())
def sources(): return {p:sha(ROOT/p) for p in SOURCES}
def identity(value):
    require(type(value) is dict and set(value)=={'workflow_commit','run_id','run_attempt'}, 'identity closure differs')
    require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}', value['workflow_commit']), 'invalid source identity')
    for key in ('run_id','run_attempt'):
        require(type(value[key]) is str and re.fullmatch('[1-9][0-9]*',value[key]), 'invalid run identity')
    return value

def compile_evidence(evidence, built):
    helper=ROOT/'ci/issue55-openssl-size/command_evidence.py'
    spec=importlib.util.spec_from_file_location('_local_host_compile_evidence',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    compilers=[p for p in built['tools_sha256'] if Path(p).name=='clang']
    require(len(compilers)==1,'compiler closure differs')
    return module.verify(evidence/'build.log',{'macOS':evidence/'configdata.txt'},compilers[0],{'macOS':None})

def load_dependencies():
    # All imports are rooted in this checkout, never cwd or an external plugin.
    for directory in ('scripts','ci/issue53-crypto','ci/issue53-lts'):
        sys.path.insert(0,str(ROOT/directory))
    import qualify, consumer, macho_stab_guard, openssl_local_symbol_policy
    return qualify, consumer, macho_stab_guard, openssl_local_symbol_policy

def parse_pair(stdout, paths, expected_sha, version):
    """Exact unchanged coload.c row closure, allowing only observed known aliases."""
    paths={name:Path(path).resolve() for name,path in paths.items()}
    require(set(paths)=={'A','B'} and len(set(paths.values()))==2, 'pair path closure differs')
    def selected(raw):
        path=Path(raw).resolve()
        matches=[name for name,p in paths.items() if p==path]
        require(len(matches)==1 and sha(path)==expected_sha, 'unexpected selected image bytes/path')
        return matches[0]
    lines=stdout.splitlines()
    require(lines.count('PASS cold-process-two-library-probe')==1, 'cold process incomplete')
    result={}
    for line in lines:
        if line=='PASS cold-process-two-library-probe': continue
        row=line.split('\t'); kind=row[0]
        if kind=='GLOBAL':
            require('GLOBAL' not in result, 'duplicate global row')
            if row==['GLOBAL','NONE']: result['GLOBAL']=None
            else:
                require(len(row)==3 and row[2]==version, 'global version differs')
                result['GLOBAL']=selected(row[1])
            continue
        require(len(row)>=3 and row[1] in ('0','1'), 'unexpected pair row')
        if kind in ('HANDLE','VERSION'):
            require(len(row)==3, 'bad pair scalar row'); key=kind+':'+row[1]
            if kind=='VERSION': require(row[2]==version, 'pair version differs'); value=version
            else: value=selected(row[2])
        else:
            require(kind in ('SYMBOL','PROVIDER') and len(row)==4, 'bad pair binding row')
            allowed=('PKCS12_parse','EVP_DigestSignInit','OSSL_PROVIDER_load') if kind=='SYMBOL' else ('default','legacy')
            require(row[2] in allowed, 'unknown pair binding'); key=kind+':'+row[1]+':'+row[2]; value=selected(row[3])
        require(key not in result, 'duplicate pair binding'); result[key]=value
    expected={'GLOBAL'}
    for i in ('0','1'):
        expected.update(('HANDLE:'+i,'VERSION:'+i))
        expected.update('SYMBOL:'+i+':'+s for s in ('PKCS12_parse','EVP_DigestSignInit','OSSL_PROVIDER_load'))
        expected.update('PROVIDER:'+i+':'+s for s in ('default','legacy'))
    require(set(result)==expected, 'pair row closure differs')
    return result

def validate_matrices(matrices, version):
    require(type(matrices) is dict and set(matrices)=={'original','transformed'},'pair variant closure differs')
    for variant, cases in matrices.items():
        require(set(cases)=={'local-AB','local-BA','global-AB','global-BA'},'pair case closure differs')
        for case, rows in cases.items():
            expected={'GLOBAL'}
            for i in ('0','1'):
                expected.update(('HANDLE:'+i,'VERSION:'+i))
                expected.update('SYMBOL:'+i+':'+s for s in ('PKCS12_parse','EVP_DigestSignInit','OSSL_PROVIDER_load'))
                expected.update('PROVIDER:'+i+':'+s for s in ('default','legacy'))
            require(set(rows)==expected,'normalized pair row closure differs')
            for key,value in rows.items():
                require(value==version if key.startswith('VERSION:') else value in ('A','B',None) if key=='GLOBAL' else value in ('A','B'),'invalid normalized binding')
            require(rows['GLOBAL'] is None if case.startswith('local-') else rows['GLOBAL'] in ('A','B'),'global visibility differs')
    require(matrices['original']==matrices['transformed'],'identical-image binding behavior changed')

def paired(consumer, original, transformed, version, work, evidence):
    env=consumer.environment(work)
    include=work/'include'; include.mkdir(); (include/'openssl').symlink_to(original.parent/'Headers', target_is_directory=True)
    probe=work/'coload'
    consumer.execute(['xcrun','clang','-std=c11','-Wall','-Wextra','-Werror','-I'+str(include),str(ROOT/'ci/issue53-crypto/coload.c'),'-o',str(probe)],env=env)
    matrices={}; observations={}
    for variant, library in [('original',original),('transformed',transformed)]:
        paths={}
        for owner in ('A','B'):
            path=work/variant/owner/'OpenSSL.framework/Versions/A/OpenSSL'; path.parent.mkdir(parents=True)
            shutil.copy2(library,path); paths[owner]=path
        matrices[variant]={}; observations[variant]=[]
        for scope in ('local','global'):
            for order in (('A','B'),('B','A')):
                output=consumer.execute([str(probe),scope,*[str(paths[o]) for o in order]],cwd=work,env=env)
                label=scope+'-'+''.join(order)
                (evidence/(variant+'-pair-'+label+'.log')).write_text(output)
                matrices[variant][label]=parse_pair(output,paths,sha(library),version)
                observations[variant].append({'scope':scope,'requested_order':list(order),'requested_paths':{k:str(v) for k,v in paths.items()},'selected':matrices[variant][label]})
    validate_matrices(matrices,version)
    result={'status':'PASS_BASELINE_EQUIVALENT_BINDINGS','matrices':matrices,'observations':observations,'distinct_images_required':False,'uuid_or_install_name_rewritten':False}
    write(evidence/'paired-identical.json',result)
    return result

def transform(library, dwarf, destination, guard, policy, evidence):
    destination.parent.mkdir(parents=True)
    shutil.copy2(library,destination)
    shutil.copytree(library.parent/'Headers',destination.parent/'Headers')
    original=destination.read_bytes(); stages={}
    if guard.snapshot(original)['signature_sha256'] is not None:
        subprocess.run(['/usr/bin/codesign','--remove-signature',str(destination)],check=True,timeout=30)
    unsigned=destination.read_bytes()
    stages['signature_removal']=guard.verify(original,unsigned,preserve_stabs=True,require_reduction=False)
    require(guard.snapshot(unsigned)['signature_sha256'] is None,'signature remains')
    strip=subprocess.check_output(['xcrun','--toolchain','default','--find','strip'],text=True,timeout=30).strip()
    expected_strip=Path(DEVELOPER)/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip'
    require(os.environ.get('DEVELOPER_DIR')==DEVELOPER and Path(strip).resolve()==expected_strip.resolve() and expected_strip.is_file(),'Apple strip tool identity differs')
    subprocess.run([strip,'-S',str(destination)],check=True,timeout=30)
    post_s=destination.read_bytes(); stages['debug']=guard.verify(unsigned,post_s,require_reduction=False)
    subprocess.run([strip,'-x',str(destination)],check=True,timeout=30)
    locals_removed=destination.read_bytes()
    stages['locals']=policy.verify_openssl_local_removal(post_s,locals_removed,expected_before_sha256=hashlib.sha256(post_s).hexdigest(),dwarf=dwarf.read_bytes(),expected_dwarf_sha256=sha(dwarf))
    subprocess.run(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',str(destination)],check=True,timeout=30)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(destination)],check=True,timeout=30)
    # Reverse comparison: removing signature may shrink LINKEDIT allocation.
    stages['signature_only_reseal']=guard.verify(destination.read_bytes(),locals_removed,preserve_stabs=True,require_reduction=False)
    stages.update({'original_sha256':sha(library),'unsigned_sha256':hashlib.sha256(locals_removed).hexdigest(),'runtime_sha256':sha(destination),'dwarf_sha256':sha(dwarf),'strip_sha256':sha(strip),'codesign_sha256':sha('/usr/bin/codesign')})
    write(evidence/'transform.json',stages)
    return stages

def verify_host_local_qualification(receipt_path, expected_sha256, packaging_identity, expected_ios_native_identity):
    path=Path(receipt_path).resolve(); require(sha(path)==expected_sha256,'host qualification receipt SHA differs')
    receipt=read(path); identity(packaging_identity)
    require(identity(expected_ios_native_identity)==NATIVE,'native comparison identity differs')
    require(receipt['schema']=='openssl-true-local-host-qualification-v1' and receipt['status']=='PASS_MACOS_SYNTHETIC_ONLY','host qualification incomplete')
    identity(receipt['qualification_identity'])
    require(receipt['native_comparison_identity']==NATIVE and receipt['source']==read(ROOT/'ci/issue53-lts/source.json') and receipt['optimization']=='Os','source/optimization binding differs')
    require(receipt['source_sha256']==sources() and receipt['statuses']==STATUS and receipt['device_executed'] is False,'qualification contract differs')
    inventory=receipt['evidence_sha256']; require(type(inventory) is dict and inventory,'missing evidence closure')
    for name,digest in inventory.items():
        relative=Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and relative.as_posix()==name,'unsafe evidence path')
        file=path.parent/relative
        require(file.is_file() and not file.is_symlink() and all(not p.is_symlink() for p in file.parents if p!=path.parent.parent),'linked evidence')
        require(sha(file)==digest,'supporting evidence changed')
    baseline=read(path.parent/'baseline/lts-qualification.json'); crypto=read(path.parent/'transformed/crypto.json'); signer=read(path.parent/'transformed/local-host-zsign.json'); coload=read(path.parent/'transformed/coload.json'); pair=read(path.parent/'transformed/paired-identical.json'); stages=read(path.parent/'transform.json')
    required={'baseline/lts-qualification.json','transformed/crypto.json','transformed/local-host-zsign.json','transformed/coload.json','transformed/paired-identical.json','transform.json','build-receipt.json','build.log','configdata.txt','headers.json'}
    require(required<=set(inventory),'required supporting receipt missing')
    require(baseline['status']=='PASS_MACOS_SYNTHETIC_ONLY' and baseline['failures']==[] and baseline['library_sha256']==receipt['original_library_sha256'],'original qualification differs')
    require(crypto['status']=='PASS_SYNTHETIC_ONLY' and signer['status']=='PASS_SYNTHETIC_UNTRUSTED_CHAIN' and coload['status']=='PASS_MACOS_DIAGNOSTIC_ONLY' and coload['errors']==[],'runtime component failed')
    require(crypto['library_sha256']==receipt['transformed_runtime_sha256'] and crypto['builtin_providers_only'] is True and crypto['provider_dispatch_images_bound'] is True,'crypto library/provider binding differs')
    require('Apple-Security-CMS-signature-and-tamper' in signer['cases'],'Apple Security verification missing')
    built=read(path.parent/'build-receipt.json')
    require(built['source']==receipt['source'] and built['optimization']=='Os' and built['recipe_sha256']==sources()['ci/issue53-lts/build.py'] and built['library_sha256']==receipt['original_library_sha256'],'original recipe binding differs')
    for name,digest in baseline['evidence_sha256'].items():
        require(inventory.get('baseline/'+name)==digest,'original qualification evidence not retained')
    require(crypto['version']==signer['version']==receipt['source']['version_text'] and crypto['loaded_image']==signer['loaded_image']==receipt['runtime_library_path'],'loaded image/version differs')
    require(built['configuration_sha256']==sha(path.parent/'configdata.txt') and built['header_inventory_sha256']==sha(path.parent/'headers.json'),'config/header receipt differs')
    require(receipt['actual_compile_evidence']==compile_evidence(path.parent,built),'actual compile evidence differs')
    require(signer['library_sha256']==receipt['transformed_runtime_sha256'] and coload['libraries']['host']==receipt['transformed_runtime_sha256'],'runtime library differs')
    require(pair['status']=='PASS_BASELINE_EQUIVALENT_BINDINGS' and pair['matrices']['original']==pair['matrices']['transformed'] and set(pair['matrices']['original'])=={'local-AB','local-BA','global-AB','global-BA'},'paired topology differs')
    validate_matrices(pair['matrices'],receipt['source']['version_text'])
    require(stages['dwarf_sha256']==built['generated_inventory'].get('OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL') and 'OpenSSL.framework.dSYM/Contents/Info.plist' in built['generated_inventory'],'full original DWARF binding differs')
    require(built['generated_inventory'].get('OpenSSL.framework/Versions/A/OpenSSL')==receipt['original_library_sha256'],'original generated binary binding differs')
    require(stages['original_sha256']==receipt['original_library_sha256'] and stages['unsigned_sha256']==receipt['transformed_unsigned_sha256'] and stages['runtime_sha256']==receipt['transformed_runtime_sha256'],'transform chain differs')
    return {'schema':'qualified-openssl-local-host-binding-v1','receipt_sha256':expected_sha256,'qualification_identity':receipt['qualification_identity'],'packaging_identity':packaging_identity,'native_comparison_identity':NATIVE,**{key:receipt[key] for key in ('source','optimization','source_sha256','original_library_sha256','transformed_unsigned_sha256','transformed_runtime_sha256','statuses','device_executed')}}

def run(built, inputs, evidence, work):
    require(sys.platform=='darwin','native macOS required')
    initial_sources=sources()
    qualify,consumer,guard,policy=load_dependencies()
    receipt,pins,library=qualify.validate_generated(built)
    require(receipt.get('optimization')=='Os','explicit Os host recipe required')
    evidence.mkdir(); work.mkdir()
    for name in ('baseline','transformed'): (evidence/name).mkdir()
    shutil.copy2(built/'build-receipt.json',evidence/'build-receipt.json')
    shutil.copy2(built/'configdata.txt',evidence/'configdata.txt')
    shutil.copy2(built/'build.log',evidence/'build.log')
    shutil.copy2(built/'headers.json',evidence/'headers.json')
    qualify.run(built,inputs,evidence/'baseline')
    target=work/'runtime/OpenSSL.framework/Versions/A/OpenSSL'
    stages=transform(library,built/'generated/OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL',target,guard,policy,evidence)
    output=evidence/'transformed'
    command=[sys.executable,'-B',str(ROOT/'ci/issue53-crypto/run.py'),'--library',str(target),'--headers',str(target.parent/'Headers'),'--header-inventory',str(built/'headers.json'),'--sha256',sha(target),'--expected-version',pins['version_text'],'--builtin-providers','--output',str(output/'crypto.json')]
    with (output/'crypto.log').open('w') as log: subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    resolution=(target.parent,{'binary_sha256':sha(target),'version':pins['version_text']})
    variant={'kind':'true-local-host-copy','original_sha256':sha(library),'transformed_sha256':sha(target)}
    consumer.zsign('local-host',inputs,inputs/'zsign-source',output,resolution=resolution,variant=variant)
    consumer.coload(inputs,output,resolution={'host':resolution,'embedded':consumer.framework(inputs,'embedded')},variant=variant)
    pairwork=work/'pair';pairwork.mkdir();paired(consumer,library,target,pins['version_text'],pairwork,output)
    qualify.validate_generated(built)
    require(sources()==initial_sources,'qualification sources changed during execution')
    require(sha(target)==stages['runtime_sha256'],'transformed runtime changed during qualification')
    report={'schema':'openssl-true-local-host-qualification-v1','status':'PASS_MACOS_SYNTHETIC_ONLY','qualification_identity':identity({'workflow_commit':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT']}),'native_comparison_identity':NATIVE,'source':pins,'optimization':'Os','source_sha256':sources(),'actual_compile_evidence':compile_evidence(evidence,receipt),'runtime_library_path':str(target),'statuses':STATUS,'device_executed':False,'original_library_sha256':sha(library),'transformed_unsigned_sha256':stages['unsigned_sha256'],'transformed_runtime_sha256':sha(target),'evidence_sha256':{str(p.relative_to(evidence)):sha(p) for p in sorted(evidence.rglob('*')) if p.is_file()}}
    destination=evidence/'host-local-qualification.json';write(destination,report)
    verify_host_local_qualification(destination,sha(destination),report['qualification_identity'],NATIVE)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('built','inputs','evidence','work'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(*[getattr(args,key).resolve() for key in ('built','inputs','evidence','work')])
