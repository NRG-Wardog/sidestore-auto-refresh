"""Explicit unsigned-widget debug stripping on a verified packaging copy only.

Runs before the existing packaging recipe stamps metadata and applies widget
entitlements/signing. It never edits native products or signed widget contents.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile
from macho_stab_guard import require, snapshot, verify, image_uuid
from experiment_strip_stabs import digest, write, unique_zip, sha
from optimize_native_candidate import retained_inventory, admitted_packaging_identity

RELATIVE = 'PlugIns/AltWidgetExtension.appex/AltWidgetExtension'
DSYM = 'AltWidgetExtension.appex.dSYM'


def beneath(path, root):
    require(root.is_dir() and not root.is_symlink(),'linked or absent widget input root')
    require(not path.is_symlink() and path.resolve(strict=True).is_relative_to(root.resolve(strict=True)),
            'widget input escapes owned root')
    for parent in path.relative_to(root).parents:
        require(not (root/parent).is_symlink(),'linked widget ancestor')
    return path


def apple_strip(before, work, report):
    require(os.environ.get('DEVELOPER_DIR')=='/Applications/Xcode_26.4.1.app/Contents/Developer','pinned Xcode required')
    tool=subprocess.run(['xcrun','--toolchain','default','--find','strip'],capture_output=True,text=True,check=True,timeout=30)
    strip=Path(tool.stdout.strip()).resolve(strict=True)
    require(strip==(Path(os.environ['DEVELOPER_DIR'])/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip').resolve(strict=True),'unrecognized strip tool')
    report['tool']={'path':str(strip),'sha256':digest(strip),'flags':['-S']}
    original=work/'AltWidgetExtension.original';original.write_bytes(before)
    stripped=work/'AltWidgetExtension.stripped'
    result=subprocess.run([str(strip),'-S','-o',str(stripped),str(original)],capture_output=True,timeout=120)
    (work/'strip.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode==0,'Apple unsigned widget strip failed')
    changed=stripped.read_bytes()
    require(original.read_bytes()==before,'original strip input changed')
    return changed


def strip_unsigned_widget(staged_app, *, transfer, receipt_path, expected_manifest_sha,
                          native_identity, packaging_identity, work, retained):
    current_identity=admitted_packaging_identity(native_identity,packaging_identity)
    require(not receipt_path.is_symlink() and receipt_path.is_file(),'linked native receipt')
    receipt_hash=digest(receipt_path);receipt=json.loads(receipt_path.read_text())
    require(receipt['status']=='COMPILE_ONLY_PASS_REQUIRES_REVIEW' and
            all(receipt[key]==native_identity[key] for key in ('run_id','run_attempt','workflow_commit')),
            'widget native receipt identity differs')
    manifest_path=beneath(transfer/'retained-files.json',transfer)
    require(digest(manifest_path)==expected_manifest_sha,'widget trusted retention manifest differs')
    manifest=json.loads(manifest_path.read_text())
    require(digest(beneath(transfer/'evidence/receipt.json',transfer))==receipt_hash,'transferred native receipt differs')
    product_path=beneath(transfer/'evidence/product-hashes.json',transfer)
    require(digest(product_path)==receipt['evidence_sha256']['product-hashes.json'],'sealed products differ')
    expected=json.loads(product_path.read_text())['SideStore'][RELATIVE]
    native=beneath(transfer/'Products/SideStore.app'/RELATIVE,transfer)
    binary=beneath(staged_app/RELATIVE,staged_app)
    require(digest(native)==digest(binary)==expected,'native/staged widget bytes differ')
    require(manifest['app'][RELATIVE]=={'kind':'file','mode':stat.S_IMODE(native.stat().st_mode),'sha256':expected},'native widget inventory differs')
    before=binary.read_bytes();shape=snapshot(before,unsigned_executable=True)
    bundle=beneath(transfer/'Symbols'/DSYM,transfer);symbols=retained_inventory(bundle)
    require(symbols==manifest['symbols'][DSYM] and 'Contents/Info.plist' in symbols,'native full dSYM inventory differs')
    dwarf=beneath(bundle/'Contents/Resources/DWARF/AltWidgetExtension',bundle)
    require(image_uuid(dwarf.read_bytes())==shape['uuid'],'widget dSYM UUID differs')
    staged_before=retained_inventory(staged_app)
    for folder in (work,retained):require(not folder.exists() and not folder.is_symlink(),'widget output must be fresh')
    work.mkdir(parents=True);retained.mkdir(parents=True)
    report={'schema':'unsigned-widget-debug-strip-v1','status':'FAILED','native_run_identity':dict(native_identity),
            'packaging_run_identity':current_identity,'native_receipt_sha256':receipt_hash,
            'retained_manifest_sha256':expected_manifest_sha,'native_binary_sha256':expected,
            'dwarf_sha256':digest(dwarf),'uuid':shape['uuid'],'runtime_proven':False,
            'phase':'UNSIGNED_PACKAGING_COPY_BEFORE_EXISTING_ENTITLEMENT_AND_SIGNING_RECIPE'}
    seals={path:digest(path) for path in (receipt_path,manifest_path,product_path,native,transfer/'evidence/receipt.json')}
    try:
        sidecar=retained/'unsigned-widget-original-and-symbols.zip'
        with zipfile.ZipFile(sidecar,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            archive.writestr('Original/AltWidgetExtension',before)
            for relative,row in symbols.items():
                path=bundle if relative=='.' else bundle/relative
                name='Symbols/'+DSYM+('/' if relative=='.' else '/'+relative)
                if row['kind']=='symlink':
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(stat.S_IFLNK|row['mode'])<<16
                    archive.writestr(info,row['target'].encode())
                else:archive.write(path,name)
        with unique_zip(sidecar) as archive:
            require(archive.read('Original/AltWidgetExtension')==before,'original widget retention differs')
            for relative,row in symbols.items():
                if row['kind']=='file':require(sha(archive.read('Symbols/'+DSYM+'/'+relative))==row['sha256'],'retained widget dSYM differs')
        report['retained_diagnostics_sha256']=digest(sidecar)
        changed=apple_strip(before,work,report)
        report['observed_after']=snapshot(changed,unsigned_executable=True)
        report['structural']=verify(before,changed,unsigned_executable=True)
        require(binary.read_bytes()==before,'widget packaging input mutated')
        require(retained_inventory(staged_app)==staged_before,'unapproved staged app mutation before install')
        require(all(digest(path)==value for path,value in seals.items()) and retained_inventory(bundle)==symbols,'sealed native/widget symbols mutated')
        binary.write_bytes(changed)
        expected_stage={key:dict(value) for key,value in staged_before.items()};expected_stage[RELATIVE]['sha256']=sha(changed)
        require(retained_inventory(staged_app)==expected_stage,'staged app delta exceeds exact unsigned widget')
        require(digest(sidecar)==report['retained_diagnostics_sha256'],'retained widget diagnostics mutated')
        report.update(status='UNSIGNED_COPY_STRUCTURAL_PASS_AWAITS_EXISTING_SIGNING_AND_PACKAGE_GATES',
                      before_bytes=len(before),after_bytes=len(changed),raw_bytes_saved=len(before)-len(changed),
                      staged_binary_sha256=sha(changed))
        write(retained/'unsigned-widget-receipt.json',report)
        return report
    except Exception as error:
        report.update(status='FAILED',error=str(error));raise
    finally:write(work/'unsigned-widget-receipt.json',report)


def verify_final_widget(initial, final, receipt_path, returned, sidecar):
    """Later optimizers must not touch the existing recipe's signed widget."""
    from optimize_native_candidate import verify_persisted_json_receipt
    from macho_stab_guard import commands
    verify_persisted_json_receipt(receipt_path,returned)
    require(digest(sidecar)==returned['retained_diagnostics_sha256'],'retained widget originals or symbols changed')
    entry='Payload/LiveContainer.app/PlugIns/LiveWidgetExtension.appex/LiveWidgetExtension'
    with unique_zip(initial) as archive:before=archive.read(entry)
    with unique_zip(final) as archive:after=archive.read(entry)
    require(before==after,'post-packaging stages changed signed widget or original entitlement recipe')
    require(image_uuid(after)==returned['uuid'] and commands(after)[0][2]==2,'final packaged widget identity differs')
    require(any(command==0x1d for command,_ in commands(after)[2]),'existing widget signing recipe did not emit a signature')
    from widget_signature_policy import verify_widget_signature
    signing_policy=verify_widget_signature(after)
    return {'final_signed_widget_policy':signing_policy,'receipt_sha256':digest(receipt_path),'native_binary_sha256':returned['native_binary_sha256'],
            'staged_binary_sha256':returned['staged_binary_sha256'],
            'initial_and_final_signed_binary_sha256':sha(after),'postpackaging_widget_bytes_unchanged':True,
            'raw_bytes_saved':returned['raw_bytes_saved'],'compressed_savings_attributed':False,
            'initial_ipa_already_contains_widget_transform':True,'retained_diagnostics_sha256':digest(sidecar)}
