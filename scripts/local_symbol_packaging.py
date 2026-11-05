"""Finite Apple-ad-hoc to Apple-ad-hoc qualification after local-symbol removal.

Does not change the existing production ldid-to-Apple or debug-only validators.
No credentials, entitlement changes, feature removal, or installer-signing claim.
"""
import subprocess
from pathlib import Path
import zipfile
from macho_stab_guard import (_checked_signature_entries, _verify_code_directory_hashes,
                              require, sha, image_uuid, snapshot)
from macho_local_symbol_guard import verify_local_removal
from experiment_strip_stabs import TARGETS, digest, write, repackage


def apple_policy(original, final, *, zero_padding=True):
    policies=[];padding=0
    for index,data in enumerate((original,final)):
        offset,pad,entries=_checked_signature_entries(data,require_zero_padding=(True if index==0 else zero_padding))
        require(set(entries)=={0,2,0x1000,0x10000},'Apple-to-Apple signature slot closure differs')
        require(entries[2]==bytes.fromhex('fade0c010000000c00000000'),'Apple-to-Apple requirements changed')
        require(entries[0x10000]==bytes.fromhex('fade0b0100000008'),'Apple-to-Apple CMS changed')
        policies.append(_verify_code_directory_hashes(data,offset,entries,2))
        if index:padding=pad
    require(policies[0]==policies[1],'Apple-to-Apple executable geometry or hash algorithms changed')
    return padding


def verify_local_reseal(original,final):
    structural=verify_local_removal(original,final)
    padding=apple_policy(original,final)
    return {'schema':'local-symbol-apple-to-apple-reseal-v1','structural':structural,
            'all_code_pages_and_special_slots_verified':True,'signature_zero_padding_bytes':padding,
            'signature_policy':'unchanged_flags2_dual_hash_empty_requirements_and_CMS',
            'device_execution_verified':False,'installer_resigning_required':True}


def canonicalize_local_signature_padding(original,signed):
    verify_local_removal(original,signed)
    padding=apple_policy(original,signed,zero_padding=False)
    end=len(signed)-padding;tail=signed[end:]
    result=signed[:end]+bytes(padding)
    require(len(result)==len(signed) and result[:end]==signed[:end],'occupied signature bytes changed')
    verify_local_reseal(original,result)
    return result,{'operation':'zero_only_validated_signature_allocation_padding','padding_bytes':padding,
        'before_nonzero_padding_bytes':sum(byte!=0 for byte in tail),'declared_signature_end':end,
        'before_padding_sha256':sha(tail),'after_padding_sha256':sha(bytes(padding)),
        'occupied_bytes_unchanged':True,'file_size_unchanged':True}


def native(command,log):
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False,timeout=120)
    log.write_bytes(result.stdout)
    require(result.returncode==0,'Apple signing/verification failed; see '+str(log))


def qualify_package(original,targets,diagnostics,output,report):
    """Only called after every local-strip guard succeeds. No IPA on gate failure."""
    require(set(targets)==set(TARGETS),'local package target closure differs')
    baseline_sha=digest(original)
    replacements={}
    for key,data in targets.items():
        path=output/key/'stripped'
        candidate=path.read_bytes()
        verify_local_removal(data,candidate)
        require(image_uuid(candidate)==diagnostics[key]['uuid'],'package dSYM UUID differs')
        if key=='SideStore':
            native(['/usr/bin/codesign','--verify','--strict','--verbose=4',str(output/key/'original')],output/key/'original-strict.log')
            signed=output/key/'SideStore';signed.write_bytes(candidate)
            native(['/usr/bin/codesign','--force','--sign','-','--identifier','SideStore',
                    '--digest-algorithm=sha1,sha256','--timestamp=none',str(signed)],output/key/'reseal.log')
            write(output/key/'signed-observed.json',{'accepted':False,'snapshot':snapshot(signed.read_bytes())})
            canonical,padding=canonicalize_local_signature_padding(data,signed.read_bytes())
            signed.write_bytes(canonical)
            write(output/key/'padding-canonicalization.json',padding)
            policy=verify_local_reseal(data,canonical)
            native(['/usr/bin/codesign','--verify','--strict','--verbose=4',str(signed)],output/key/'final-strict.log')
            report['apple_to_apple_signature_qualification']=policy
            candidate=canonical
        else:
            require(snapshot(data)['signature_sha256'] is None and snapshot(candidate)['signature_sha256'] is None,
                    'unsigned SwiftUI signature state changed')
        verify_local_removal(data,candidate)
        replacements[TARGETS[key]]=candidate
    require(digest(original)==baseline_sha,'original IPA mutated before packaging')
    staging=output/'package-staging';staging.mkdir()
    staged=staging/'local-symbol-qualified.ipa'
    try:
        verification=repackage(original,staged,replacements)
        with zipfile.ZipFile(staged) as archive:require(archive.testzip() is None,'final IPA CRC failure')
        require(staged.stat().st_size<original.stat().st_size,'qualified package is not smaller')
        require(digest(original)==baseline_sha,'original IPA mutated during packaging')
        final=output/'local-symbol-qualified.ipa'
        require(not final.exists(),'fresh qualified package required')
        staged.rename(final)
        report.update(ipa_emitted=True,signature_verified=True,packaging_approved=True,
            signature_verification_scope='SideStore executable only; SwiftUI remains unsigned',
            qualification_scope='structural_and_Apple_ad_hoc_only_not_device_or_release',
            original_ipa_bytes=original.stat().st_size,final_ipa_bytes=final.stat().st_size,
            final_ipa_sha256=digest(final),archive_bytes_saved=original.stat().st_size-final.stat().st_size,
            target_24_decimal_MB_met=final.stat().st_size<=24_000_000)
        write(output/'package-verification.json',verification)
    finally:
        if staged.exists():staged.unlink() # Never leave a rejected derivative IPA as a product.
