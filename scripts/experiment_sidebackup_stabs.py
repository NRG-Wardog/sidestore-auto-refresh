"""Copy-only unsigned nested SideBackup debug-map experiment. Never executes app code."""
import argparse
import copy
import io
import os
from pathlib import Path
import subprocess
import zipfile
from experiment_strip_stabs import unique_zip, digest, write
from ipa_size_report import inspect
from macho_stab_guard import require, sha, snapshot, verify, image_uuid
from package_livecontainer_combined import verify as verify_combined

OUTER = 'Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideBackup.ipa'
BINARY = 'Payload/SideBackup.app/SideBackup'
NATIVE_SHA = '8cbd47809a648a10679d39ae30ef8a94887144edd249a06ed8254c3bfc612663'
INNER_SHA = '2a683f01a0bfe3e5cf0734200f93afa22208dcce88bdd8e235385d4c3444acf9'
NESTED_SHA = 'c5a90a3c792fcd033c5265178a760d85868a8ba1c3776a30f92012613a065b57'
DWARF_SHA = 'd653b4591ee2454fa51202547336528d04a7d4bf15c76c6c33e559fb95c8fc1f'
UUID = '42edf28688ef3ea6b3752ad779bc012d'
PREFIX = 'native-transfer-SideStore/Symbols/SideBackup.app.dSYM/'


def replace_one(source, output, name, data):
    require(not output.exists() and not output.is_symlink(), 'fresh ZIP output required')
    before, comment = inspect(source)
    require(sum(row['path'] == name for row in before) == 1, 'replacement target closure differs')
    with unique_zip(source) as old, zipfile.ZipFile(output, 'w') as new:
        new.comment = old.comment
        for info in old.infolist():
            new.writestr(copy.copy(info), data if info.filename == name else old.read(info),
                         compress_type=info.compress_type, compresslevel=9)
    after, new_comment = inspect(output)
    require(comment == new_comment and len(before) == len(after), 'ZIP closure changed')
    for old, new in zip(before, after):
        ignored = {'compressed_bytes'} | ({'sha256', 'crc', 'bytes'} if old['path'] == name else set())
        require({k:v for k,v in old.items() if k not in ignored} ==
                {k:v for k,v in new.items() if k not in ignored}, 'unapproved ZIP member or metadata change')
        if old['path'] == name: require(new['sha256'] == sha(data), 'replacement bytes differ')
    with unique_zip(output) as archive: require(archive.testzip() is None, 'ZIP CRC failed')


def experiment(ipa, expected_sha, native, output, *, inspect_only=False):
    require(ipa.is_file() and not ipa.is_symlink() and digest(ipa) == expected_sha, 'IPA hash differs')
    require(native.is_file() and not native.is_symlink() and digest(native) == NATIVE_SHA, 'native artifact hash differs')
    require(not output.exists() and not output.is_symlink(), 'fresh output required')
    output.mkdir(parents=True)
    report = {'schema':'sidebackup-debug-stab-experiment-v1','status':'FAILED', 'input_ipa_sha256':expected_sha,
              'native_artifact_sha256':NATIVE_SHA,'runtime_proven':False,'ipa_emitted':False,
              'run_id':os.environ.get('GITHUB_RUN_ID'),'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
              'workflow_commit':os.environ.get('GITHUB_SHA')}
    derivative = output/'SideBackup-debug-qualified.ipa'
    try:
        with unique_zip(ipa) as archive: nested = archive.read(OUTER)
        require(sha(nested) == NESTED_SHA, 'nested IPA differs from reviewed source')
        with unique_zip(native) as archive: inner = archive.read('SideStore-unsigned.zip')
        require(sha(inner) == INNER_SHA, 'native inner ZIP differs')
        with unique_zip(io.BytesIO(inner)) as archive:
            require(archive.read('native-transfer-SideStore/Products/SideStore.app/SideBackup.ipa') == nested, 'native/packaged nested IPA differs')
            names = [name for name in archive.namelist() if name.startswith(PREFIX)]
            require(PREFIX+'Contents/Info.plist' in names, 'full dSYM missing')
            dwarf = archive.read(PREFIX+'Contents/Resources/DWARF/SideBackup')
            require(sha(dwarf) == DWARF_SHA and image_uuid(dwarf) == UUID, 'dSYM identity differs')
            with zipfile.ZipFile(output/'retained-original-and-symbols.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as retained:
                retained.writestr('Original/SideBackup.ipa',nested)
                for name in names: retained.writestr(copy.copy(archive.getinfo(name)),archive.read(name))
            with unique_zip(output/'retained-original-and-symbols.zip') as retained:
                require(set(retained.namelist()) == set(names) | {'Original/SideBackup.ipa'}, 'retained diagnostic closure differs')
                require(retained.read('Original/SideBackup.ipa') == nested and
                        all(retained.read(name) == archive.read(name) for name in names), 'retained diagnostic bytes differ')
        original = output/'original-nested.ipa';original.write_bytes(nested)
        with unique_zip(original) as archive: binary = archive.read(BINARY)
        require(image_uuid(binary) == UUID, 'binary/dSYM UUID differs')
        report['before'] = snapshot(binary,unsigned_executable=True)
        report['diagnostics'] = {'uuid':UUID,'dwarf_sha256':DWARF_SHA,'native_inner_sha256':INNER_SHA,
                                 'nested_ipa_sha256':NESTED_SHA,'full_bundle_entries':len(names)}
        report['signature_state'] = 'UNSIGNED_EXECUTABLE_BEFORE_AND_AFTER_INSTALLER_SIGNING_REQUIRED'
        report['retained_diagnostics_sha256'] = digest(output/'retained-original-and-symbols.zip')
        write(output/'original-package.json',verify_combined(ipa))
        if inspect_only:
            report['status']='INPUTS_VERIFIED_NO_TRANSFORMATION';return report
        require(os.environ.get('DEVELOPER_DIR') == '/Applications/Xcode_26.4.1.app/Contents/Developer', 'pinned Xcode required')
        tool=subprocess.run(['xcrun','--toolchain','default','--find','strip'],capture_output=True,text=True,check=True,timeout=30)
        strip=Path(tool.stdout.strip()).resolve(strict=True)
        require(strip == (Path(os.environ['DEVELOPER_DIR'])/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip').resolve(strict=True),'unrecognized Apple tool')
        report['strip_tool']={'path':str(strip),'sha256':digest(strip),'flags':['-S']}
        before=output/'SideBackup.original';before.write_bytes(binary)
        after=output/'SideBackup.stripped'
        result=subprocess.run([str(strip),'-S','-o',str(after),str(before)],capture_output=True,timeout=120)
        (output/'strip.log').write_bytes(result.stdout+result.stderr)
        require(result.returncode == 0,'Apple strip failed')
        changed=after.read_bytes()
        report['observed_after']=snapshot(changed,unsigned_executable=True)
        report['structural']=verify(binary,changed,unsigned_executable=True)
        require(before.read_bytes() == binary,'original binary changed')
        stripped_nested=output/'stripped-nested.ipa'
        replace_one(original,stripped_nested,BINARY,changed)
        replace_one(ipa,derivative,OUTER,stripped_nested.read_bytes())
        write(output/'combined-package.json',verify_combined(derivative))
        require(digest(ipa) == expected_sha and digest(native) == NATIVE_SHA,'immutable source changed')
        require(derivative.stat().st_size < ipa.stat().st_size,'no archive reduction')
        report.update(status='STRUCTURAL_PACKAGE_PASS_DEVICE_UNVERIFIED',ipa_emitted=True,
                      final_ipa_sha256=digest(derivative),original_ipa_bytes=ipa.stat().st_size,
                      final_ipa_bytes=derivative.stat().st_size,archive_bytes_saved=ipa.stat().st_size-derivative.stat().st_size,
                      nested_bytes_saved=len(nested)-stripped_nested.stat().st_size)
        return report
    except Exception as error:
        derivative.unlink(missing_ok=True);report.update(status='FAILED',ipa_emitted=False,error=str(error));raise
    finally: write(output/'experiment.json',report)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ipa',type=Path,required=True)
    parser.add_argument('--ipa-sha256',required=True);parser.add_argument('--native-artifact',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--inspect-only',action='store_true')
    args=parser.parse_args();experiment(args.ipa,args.ipa_sha256,args.native_artifact,args.output,inspect_only=args.inspect_only)
