"""Explicit fourth-stage OpenSSL-only packaging adapter, disabled unless called.

Caller must supply authenticated prior receipt/seal and actual transformed-host
qualification. Does not edit source/native products or claim iOS runtime proof.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import zipfile
import sys
import importlib.util
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ci/issues-native'))
from experiment_strip_stabs import digest, unique_zip, write
from experiment_sidebackup_stabs import replace_one
from macho_stab_guard import require, sha, verify
from native_transfer_seal import verify as verify_seal
from optimize_native_candidate import retained_inventory, admitted_packaging_identity, verify_persisted_json_receipt
from openssl_local_symbol_policy import verify_openssl_owner_pair
from package_livecontainer_combined import verify as verify_package

TARGETS = {
 'LiveContainer':('OpenSSLHost','Payload/LiveContainer.app/Frameworks/OpenSSL.framework/OpenSSL'),
 'SideStore':('OpenSSLEmbedded','Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Frameworks/OpenSSL.framework/OpenSSL'),
}
NATIVE = 'Frameworks/OpenSSL.framework/OpenSSL'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def verify_host_qualification(path, expected_sha, packaging_identity, native_identity):
    # Source-owned verifier is mandatory; missing module/receipt never becomes success.
    require(path.is_file() and not path.is_symlink() and digest(path)==expected_sha,'host qualification receipt SHA differs')
    schema=json.loads(path.read_text()).get('schema')
    if schema=='openssl-true-local-same-run-host-qualification-v1':
        module_path=Path(__file__).resolve().parents[1]/'ci/issue56-startup/qualify_host.py'
    else:
        require(schema=='openssl-true-local-host-qualification-v1','unknown host qualification schema')
        module_path=Path(__file__).resolve().parents[1]/'ci/issue55-openssl-local/qualify_host.py'
    spec=importlib.util.spec_from_file_location('reviewed_openssl_host_qualification',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.verify_host_local_qualification(path,expected_sha256=expected_sha,
        packaging_identity=packaging_identity,expected_ios_native_identity=native_identity)


def bind_inputs(ipa, prior, prior_path, prior_sha, transfer_roots, receipt_path, seal_path,
                expected_seal_sha, native_identity, original_sidecar):
    require(prior_path.is_file() and not prior_path.is_symlink() and digest(prior_path)==prior_sha,'authenticated prior receipt differs')
    verify_persisted_json_receipt(prior_path,prior)
    require(prior['status']=='THREE_STAGE_STRUCTURAL_AND_PACKAGE_CHECKS_PASS_DEVICE_UNVERIFIED','exact prior three-stage qualification required')
    require(prior['same_run_identity']==native_identity,'prior native identity differs')
    require(set(prior['stages'])=={'debug_stabs','local_symbols','sidebackup_debug_stabs'},'prior stage closure differs')
    previous=prior['original_ipa_sha256'];saved=0
    for key in ('debug_stabs','local_symbols','sidebackup_debug_stabs'):
        stage=prior['stages'][key]
        require(stage['input_ipa_sha256']==previous,'prior stage hash chain differs')
        require(type(stage['archive_bytes_saved']) is int and stage['archive_bytes_saved']>0,'prior measured savings invalid')
        saved+=stage['archive_bytes_saved'];previous=stage['final_ipa_sha256']
    require(previous==prior['final_ipa_sha256'] and saved==prior['archive_bytes_saved'],'prior cumulative chain differs')
    require(ipa.is_file() and not ipa.is_symlink() and digest(ipa)==previous and ipa.stat().st_size==prior['final_ipa_bytes'],'prior IPA bytes differ')
    require(original_sidecar.is_file() and not original_sidecar.is_symlink() and
            digest(original_sidecar)==prior['retained_debug_sidecar_sha256'],'original full-symbol sidecar differs')
    manifests=verify_seal(seal_path,receipt_path,transfer_roots,native_identity,expected_seal_sha=expected_seal_sha)
    require(set(transfer_roots)==set(TARGETS),'exact native owner closure required')
    receipt=json.loads(receipt_path.read_text());rh=digest(receipt_path)
    require(prior['native_receipt_sha256']==rh and receipt['status']=='COMPILE_ONLY_PASS_REQUIRES_REVIEW','native receipt differs')
    require(all(receipt[k]==native_identity[k] for k in ('run_id','run_attempt','workflow_commit')),'native identity differs')
    inputs={};seals={p:digest(p) for p in (ipa,prior_path,receipt_path,seal_path,original_sidecar)}
    with unique_zip(ipa) as archive:
        for owner,(key,member) in TARGETS.items():
            root=transfer_roots[owner];manifest_path=root/'retained-files.json'
            def checked(path):
                require(path.resolve(strict=True).is_relative_to(root.resolve(strict=True)), 'OpenSSL input escapes native transfer')
                require(not path.is_symlink() and not any((root/parent).is_symlink() for parent in path.relative_to(root).parents),
                        'linked OpenSSL input ancestor')
                return path
            manifest=json.loads(manifest_path.read_text());native=root/'Products'/(owner+'.app')/NATIVE
            require(retained_inventory(root/'Products'/(owner+'.app'))==manifest['app'],'sealed native app inventory differs')
            checked(native);checked(manifest_path)
            products=checked(root/'evidence/product-hashes.json')
            require(digest(products)==receipt['evidence_sha256']['product-hashes.json'],'sealed native product hashes differ')
            native_sha=json.loads(products.read_text())[owner][NATIVE]
            require(digest(native)==native_sha,'original native OpenSSL bytes differ')
            before=archive.read(member);transformation=prior['debug_stabs_transformations'][key]
            require(transformation['before']['sha256']==native_sha and transformation['after']['sha256']==sha(before),'post-S lineage differs')
            recomputed=verify(native.read_bytes(),before)
            require(canonical(recomputed)==canonical(transformation),'post-S structural receipt differs')
            bundle=checked(root/'Symbols/OpenSSL.framework.dSYM')
            require(retained_inventory(bundle)==manifest['symbols'][bundle.name],'sealed full OpenSSL dSYM differs')
            inputs[owner]={'before':before,'after':None,'before_sha256':transformation['after']['sha256'],
                           'dsym':bundle,'dsym_inventory':manifest['symbols'][bundle.name]}
            for p in (manifest_path,native,products):seals[p]=digest(p)
    require(inputs['LiveContainer']['before']==inputs['SideStore']['before'],'post-S owner copies differ')
    return inputs,seals,dict(manifests)


def strip_copy(data, folder):
    require(os.environ.get('DEVELOPER_DIR')=='/Applications/Xcode_26.4.1.app/Contents/Developer','pinned Xcode required')
    result=subprocess.run(['xcrun','--toolchain','default','--find','strip'],capture_output=True,text=True,check=True,timeout=30)
    tool=Path(result.stdout.strip()).resolve(strict=True)
    require(tool==(Path(os.environ['DEVELOPER_DIR'])/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip').resolve(strict=True),'unrecognized Apple strip')
    source=folder/'OpenSSL.before';target=folder/'OpenSSL.after';source.write_bytes(data)
    result=subprocess.run([str(tool),'-x','-o',str(target),str(source)],capture_output=True,timeout=120)
    (folder/'strip.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode==0 and source.read_bytes()==data,'OpenSSL strip failed or source changed')
    return target.read_bytes(),{'path':str(tool),'sha256':digest(tool),'flags':['-x']}


def optimize_openssl_local_candidate(ipa, *, prior_report, prior_receipt_path, expected_prior_receipt_sha,
        transfer_roots, receipt_path, seal_path, expected_seal_sha, native_identity, packaging_identity,
        original_sidecar, host_qualification_path, expected_host_qualification_sha, output, retained):
    packaging=admitted_packaging_identity(native_identity,packaging_identity)
    require(prior_report.get('packaging_run_identity',native_identity)==packaging,'prior packaging identity differs')
    caller_prior=prior_report;prior=copy.deepcopy(prior_report);native=copy.deepcopy(native_identity)
    admission=sha(canonical({'prior':prior,'native':native,'packaging':packaging}))
    host=verify_host_qualification(host_qualification_path,expected_host_qualification_sha,packaging,native)
    inputs,seals,manifests=bind_inputs(ipa,prior,prior_receipt_path,expected_prior_receipt_sha,transfer_roots,
        receipt_path,seal_path,expected_seal_sha,native,original_sidecar)
    seals[host_qualification_path]=expected_host_qualification_sha
    for folder in (output,retained):require(not folder.exists() and not folder.is_symlink(),'fresh fourth-stage directories required')
    output.mkdir(parents=True);retained.mkdir(parents=True)
    final=output/'OpenSSL-local-qualified.ipa';report={'schema':'paired-openssl-local-stage-v1','status':'FAILED','ipa_emitted':False}
    try:
        write(output/'original-package.json',verify_package(ipa))
        changed,tool=strip_copy(inputs['LiveContainer']['before'],output)
        for item in inputs.values():item['after']=changed
        policy=verify_openssl_owner_pair(inputs)
        sidecar=retained/'openssl-local-originals.zip'
        with zipfile.ZipFile(sidecar,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for owner,item in inputs.items():archive.writestr(owner+'/OpenSSL.post-S',item['before'])
            archive.writestr('prior-stage-receipt.json',prior_receipt_path.read_bytes())
            archive.writestr('host-qualification.json',host_qualification_path.read_bytes())
        with unique_zip(sidecar) as archive:
            for owner,item in inputs.items():require(archive.read(owner+'/OpenSSL.post-S')==item['before'],'retained original OpenSSL differs')
            require(sha(archive.read('prior-stage-receipt.json'))==expected_prior_receipt_sha and
                    sha(archive.read('host-qualification.json'))==expected_host_qualification_sha,'retained qualification receipts differ')
        sidecar_sha=digest(sidecar)
        intermediate=output/'first-owner.ipa'
        replace_one(ipa,intermediate,TARGETS['LiveContainer'][1],changed)
        replace_one(intermediate,final,TARGETS['SideStore'][1],changed)
        write(output/'final-package.json',verify_package(final))
        require(final.stat().st_size<ipa.stat().st_size,'no measured OpenSSL archive reduction')
        require(all(digest(p)==value for p,value in seals.items()),'immutable fourth-stage input changed')
        require(digest(sidecar)==sidecar_sha,'retained fourth-stage originals changed')
        require(sha(canonical({'prior':caller_prior,'native':native_identity,'packaging':packaging_identity}))==admission,
                'caller provenance mutated')
        require(dict(verify_seal(seal_path,receipt_path,transfer_roots,native,expected_seal_sha=expected_seal_sha))==manifests,'native seal changed')
        verify_openssl_owner_pair(inputs)
        require(canonical(verify_host_qualification(host_qualification_path,expected_host_qualification_sha,packaging,native))==canonical(host),'host qualification changed')
        delta=ipa.stat().st_size-final.stat().st_size
        report.update(status='PAIRED_STRUCTURAL_HOST_CRYPTO_AND_PACKAGE_PASS_IOS_RUNTIME_UNVERIFIED',ipa_emitted=True,
            native_run_identity=native,packaging_run_identity=packaging,input_ipa_sha256=digest(ipa),
            final_ipa_sha256=digest(final),final_ipa_bytes=final.stat().st_size,archive_bytes_saved=delta,
            host_qualification=host,host_qualification_receipt_sha256=expected_host_qualification_sha,
            structural_policy=policy,tool=tool,retained_originals_sha256=sidecar_sha,
            original_full_symbol_sidecar_sha256=digest(original_sidecar),prior_receipt_sha256=expected_prior_receipt_sha,
            ios_runtime_qualified=False)
        write(retained/'fourth-stage-receipt.json',report)
        cumulative=copy.deepcopy(prior)
        cumulative['stages']['openssl_local_symbols']={'input_ipa_sha256':digest(ipa),'final_ipa_sha256':digest(final),
            'archive_bytes_saved':delta,'receipt_sha256':digest(retained/'fourth-stage-receipt.json')}
        cumulative.update(schema='four-stage-symbol-optimization-v1',status='FOUR_STAGE_STRUCTURAL_HOST_CRYPTO_AND_PACKAGE_PASS_IOS_RUNTIME_UNVERIFIED',
            final_ipa_sha256=digest(final),final_ipa_bytes=final.stat().st_size,archive_bytes_saved=prior['archive_bytes_saved']+delta,
            target_24_decimal_MB_met=final.stat().st_size<=24_000_000)
        from ipa_size_report import report as size_report
        cumulative['three_stage_after_size']=cumulative['after_size'];cumulative['after_size']=size_report(final)
        cumulative['uncompressed_bytes_saved']=cumulative['before_size']['uncompressed_bundle_bytes']-cumulative['after_size']['uncompressed_bundle_bytes']
        write(retained/'cumulative-receipt.json',cumulative)
        return final,cumulative
    except Exception as error:
        final.unlink(missing_ok=True);report.update(status='FAILED',ipa_emitted=False,error=str(error));raise
    finally:write(output/'experiment.json',report)
