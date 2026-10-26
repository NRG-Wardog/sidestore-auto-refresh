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
import plistlib
import stat
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
SOURCES = ('ci/issue55-openssl-local/qualify_host.py', 'ci/issue55-openssl-local/framework_seal.py', 'ci/issue53-lts/build.py', 'ci/issue53-lts/qualify.py', 'ci/issue53-lts/source.json', 'ci/issue53-crypto/acquire.py', 'ci/issue53-crypto/consumer.py', 'ci/issue53-crypto/run.py', 'ci/issue53-crypto/probe.c', 'ci/issue53-crypto/verify.swift', 'ci/issue53-crypto/host-headers.json', 'ci/issue53-crypto/embedded-headers.json', 'ci/issue53-crypto/coload.c', 'ci/issue53-crypto/zsign_probe.cpp', 'ci/issue53-crypto/zsign-source.json', 'ci/issue53-crypto/verify_cms.py', 'ci/issue53-crypto/verify_zsign.swift', 'ci/issue53-crypto/pinned-inputs.json', 'scripts/macho_stab_guard.py', 'scripts/macho_local_symbol_guard.py', 'scripts/openssl_local_symbol_policy.py', 'scripts/optimize_native_candidate.py', 'scripts/experiment_strip_stabs.py', 'scripts/audit_ipa_signing.py', 'scripts/ipa_size_report.py', 'scripts/packaging_adapters.py', 'ci/issue55-openssl-size/command_evidence.py', 'scripts/package_livecontainer_combined.py', '.github/workflows/openssl-local-host-diagnostic.yml')

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

def seal_module():
    spec=importlib.util.spec_from_file_location('_host_framework_seal',Path(__file__).with_name('framework_seal.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

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
            path=work/variant/owner/'OpenSSL.framework/Versions/A/OpenSSL'; copy_framework(library,path); paths[owner]=path
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

def framework_inventory(root):
    require(root.is_dir() and not root.is_symlink(),'real framework root required')
    anchor=root.resolve(strict=True); result={}
    for path in [root,*sorted(root.rglob('*'))]:
        mode=path.lstat().st_mode; row={'mode':stat.S_IMODE(mode)}
        if stat.S_ISLNK(mode):
            target=os.readlink(path)
            require(not os.path.isabs(target) and path.resolve(strict=True).is_relative_to(anchor),'framework link escapes root')
            row.update(kind='symlink',target=target)
        elif stat.S_ISDIR(mode):row['kind']='directory'
        elif stat.S_ISREG(mode):row.update(kind='file',sha256=sha(path))
        else:raise ValueError('unsupported framework node')
        result[path.relative_to(root).as_posix()]=row
    return result

def validate_framework_layout(library):
    root=library.parents[2]
    require(root.name=='OpenSSL.framework' and library.relative_to(root).as_posix()=='Versions/A/OpenSSL','framework library layout differs')
    require(library.is_file() and not library.is_symlink() and (library.parent/'Headers').is_dir(),'framework binary/headers missing')
    plist=library.parent/'Resources/Info.plist'
    if not plist.is_file():plist=root/'Info.plist'
    info=plistlib.loads(plist.read_bytes())
    require(info.get('DTPlatformName')=='macosx' and info.get('CFBundleExecutable')=='OpenSSL','macOS framework metadata differs')
    return framework_inventory(root)

def copy_framework(library,destination):
    before=validate_framework_layout(library)
    root=destination.parents[2]
    require(not root.exists() and not root.is_symlink(),'refusing existing framework destination')
    root.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(library.parents[2],root,symlinks=True,copy_function=shutil.copy2)
    require(validate_framework_layout(destination)==before,'framework copy metadata/bytes differ')
    require(validate_framework_layout(library)==before,'original framework changed during copy')
    return before

def verify_framework_delta(before,after,original_sha,final_sha):
    key='Versions/A/OpenSSL'
    require(type(before) is dict and type(after) is dict and before.get(key,{}).get('sha256')==original_sha,'framework original binding differs')
    expected={name:dict(row) for name,row in before.items()}
    expected[key]['sha256']=final_sha
    require(after==expected,'framework delta exceeds binary bytes')

def observe_framework_delta(path,before,after,original_sha,final_sha):
    # Persist rejected evidence before asserting. No observed extra file or mode
    # is normalized away or treated as admitted by this diagnostic receipt.
    added={key:after[key] for key in sorted(set(after)-set(before))}
    removed={key:before[key] for key in sorted(set(before)-set(after))}
    changed={key:{'before':before[key],'after':after[key]} for key in sorted(set(before)&set(after)) if before[key]!=after[key]}
    write(path,{'schema':'host-framework-observed-delta-v1','status':'OBSERVED_NOT_ADMITTED','before':before,'after':after,'added':added,'removed':removed,'changed':changed,'expected_original_binary_sha256':original_sha,'expected_final_binary_sha256':final_sha})

def record_framework_delta(path,before,after,original_sha,final_sha):
    observe_framework_delta(path,before,after,original_sha,final_sha)
    verify_framework_delta(before,after,original_sha,final_sha)

def verify_actual_signature(path,label,evidence):
    result=subprocess.run(['/usr/bin/codesign','--verify','--strict',str(path)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
    (evidence/(label+'-codesign-verify.log')).write_text(result.stdout)
    require(result.returncode==0,'Apple strict signature verification failed: '+label)

def isolated_codesign(destination, operation, guard, evidence):
    require(operation in ('remove-signature','adhoc-sign'),'unsupported signature operation')
    original=destination.read_bytes();before_inventory=framework_inventory(destination.parents[2])
    # Same basename, but outside the framework: Apple signing cannot implicitly
    # update resources in the retained framework. Only verified bytes return.
    with tempfile.TemporaryDirectory(prefix='bare-sign-',dir=destination.parents[3]) as temporary:
        bare=Path(temporary)/destination.name;shutil.copy2(destination,bare)
        command=['/usr/bin/codesign']
        command += ['--remove-signature'] if operation=='remove-signature' else ['--force','--sign','-','--timestamp=none']
        subprocess.run([*command,str(bare)],check=True,timeout=30)
        result=bare.read_bytes()
        if operation=='adhoc-sign':
            verify_actual_signature(bare,'bare',evidence)
            proof=guard.verify(result,original,preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)
        else:proof=guard.verify(original,result,preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)
        require(destination.read_bytes()==original,'framework binary changed during isolated signing')
        destination.write_bytes(result)
        require(destination.read_bytes()==result,'verified signed bytes changed during copyback')
        write(evidence/('isolated-'+operation+'.json'),{'operation':operation,'source_sha256':hashlib.sha256(original).hexdigest(),'result_sha256':sha(destination),'signature_only':proof,'bare_same_basename':True,'framework_resource_mutation_authorized':False})
        record_framework_delta(evidence/(operation+'-copyback-delta.json'),before_inventory,framework_inventory(destination.parents[2]),hashlib.sha256(original).hexdigest(),sha(destination))
        if operation=='adhoc-sign':
            # Verify actual target after copyback too; no ignore-resources flags.
            verify_actual_signature(destination,'actual-framework-target',evidence)

def transform(library, dwarf, destination, guard, policy, evidence):
    copied_inventory=copy_framework(library,destination)
    original=destination.read_bytes(); stages={}
    write(evidence/'framework-inventory-before.json',copied_inventory)
    if guard.snapshot(original,host_legacy_dyld=True)['signature_sha256'] is not None:
        isolated_codesign(destination,'remove-signature',guard,evidence)
    write(evidence/'framework-inventory-after-remove-signature.json',framework_inventory(destination.parents[2]))
    unsigned=destination.read_bytes()
    stages['signature_removal']=guard.verify(original,unsigned,preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)
    require(guard.snapshot(unsigned,host_legacy_dyld=True)['signature_sha256'] is None,'signature remains')
    strip=subprocess.check_output(['xcrun','--toolchain','default','--find','strip'],text=True,timeout=30).strip()
    expected_strip=Path(DEVELOPER)/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip'
    require(os.environ.get('DEVELOPER_DIR')==DEVELOPER and Path(strip).resolve()==expected_strip.resolve() and expected_strip.is_file(),'Apple strip tool identity differs')
    subprocess.run([strip,'-S',str(destination)],check=True,timeout=30)
    write(evidence/'framework-inventory-after-S.json',framework_inventory(destination.parents[2]))
    post_s=destination.read_bytes(); stages['debug']=guard.verify(unsigned,post_s,require_reduction=False,host_legacy_dyld=True)
    subprocess.run([strip,'-x',str(destination)],check=True,timeout=30)
    write(evidence/'framework-inventory-after-x.json',framework_inventory(destination.parents[2]))
    locals_removed=destination.read_bytes()
    stages['locals']=policy.verify_openssl_local_removal(post_s,locals_removed,expected_before_sha256=hashlib.sha256(post_s).hexdigest(),dwarf=dwarf.read_bytes(),expected_dwarf_sha256=sha(dwarf),host_legacy_dyld=True)
    # Apple TN2206 'Signing Frameworks': sign/validate each actual Versions/A
    # context, not a root requiring aliases absent from this generated fixture.
    # https://developer.apple.com/library/archive/technotes/tn2206/_index.html
    subprocess.run(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',str(destination.parent)],check=True,timeout=30)
    final_inventory=validate_framework_layout(destination)
    observe_framework_delta(evidence/'framework-inventory-delta.json',copied_inventory,final_inventory,sha(library),sha(destination))
    verify_actual_signature(destination.parent,'actual-framework-version',evidence)
    verify_actual_signature(destination,'actual-framework-target',evidence)
    # Reverse comparison: removing signature may shrink LINKEDIT allocation.
    stages['signature_only_reseal']=guard.verify(destination.read_bytes(),locals_removed,preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)
    stages.update({'original_sha256':sha(library),'unsigned_sha256':hashlib.sha256(locals_removed).hexdigest(),'runtime_sha256':sha(destination),'dwarf_sha256':sha(dwarf),'strip_sha256':sha(strip),'codesign_sha256':sha('/usr/bin/codesign')})
    final_inventory=validate_framework_layout(destination)
    stages['framework_seal']=seal_module().verify(destination.parents[2],copied_inventory,final_inventory,guard)
    shutil.copy2(destination.parents[2]/'Versions/A/_CodeSignature/CodeResources',evidence/'framework-CodeResources.plist')
    shutil.copy2(destination.parent/'Resources/Info.plist',evidence/'framework-Info.plist')
    require(validate_framework_layout(library)==copied_inventory,'original framework changed during transform')
    stages.update(original_framework_inventory=copied_inventory,transformed_framework_inventory=final_inventory)
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
    required={'baseline/lts-qualification.json','transformed/crypto.json','transformed/local-host-zsign.json','transformed/coload.json','transformed/paired-identical.json','transform.json','build-receipt.json','build.log','configdata.txt','headers.json','framework-CodeResources.plist','framework-Info.plist'}
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
    seal=seal_module();seal.verify_inventory(stages['original_framework_inventory'],stages['transformed_framework_inventory'])
    require(stages['original_framework_inventory'][seal.BINARY]['sha256']==receipt['original_library_sha256'] and stages['transformed_framework_inventory'][seal.BINARY]['sha256']==receipt['transformed_runtime_sha256'],'framework binary inventory binding differs')
    proof=stages['framework_seal']
    require(proof['schema']=='host-framework-resource-seal-v1' and proof['ios_policy_changed'] is False and proof['only_original_binary_and_exact_seal_changed'] is True,'host-only seal admission differs')
    require(proof['signature']['binary_sha256']==receipt['transformed_runtime_sha256'] and proof['signature']['special_slots_verified']==[1,2,3] and proof['signature']['info_plist_sha256']==sha(path.parent/'framework-Info.plist')==stages['original_framework_inventory'][seal.INFO]['sha256'] and proof['signature']['code_resources_sha256']==sha(path.parent/'framework-CodeResources.plist')==stages['transformed_framework_inventory'][seal.SEAL_FILE]['sha256'],'host resource signature proof differs')
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
    require(validate_framework_layout(library)==stages['original_framework_inventory'],'original framework metadata changed during qualification')
    require(validate_framework_layout(target)==stages['transformed_framework_inventory'],'transformed framework metadata changed during qualification')
    report={'schema':'openssl-true-local-host-qualification-v1','status':'PASS_MACOS_SYNTHETIC_ONLY','qualification_identity':identity({'workflow_commit':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT']}),'native_comparison_identity':NATIVE,'source':pins,'optimization':'Os','source_sha256':sources(),'actual_compile_evidence':compile_evidence(evidence,receipt),'runtime_library_path':str(target),'statuses':STATUS,'device_executed':False,'original_library_sha256':sha(library),'transformed_unsigned_sha256':stages['unsigned_sha256'],'transformed_runtime_sha256':sha(target),'evidence_sha256':{str(p.relative_to(evidence)):sha(p) for p in sorted(evidence.rglob('*')) if p.is_file()}}
    destination=evidence/'host-local-qualification.json';write(destination,report)
    verify_host_local_qualification(destination,sha(destination),report['qualification_identity'],NATIVE)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('built','inputs','evidence','work'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(*[getattr(args,key).resolve() for key in ('built','inputs','evidence','work')])
