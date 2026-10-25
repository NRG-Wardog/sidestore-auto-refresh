"""Copy-only, diagnostic-only Apple strip -x measurement on two reviewed images.

Default mode never emits an IPA. Optional qualification requires strict signature
and archive gates and permits only an internal experimental artifact. Exact source IPA
and retained full dSYM ZIP SHA-256 are mandatory. Both inputs stay immutable.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import zlib
from experiment_strip_stabs import TARGETS, DSYMS, digest, unique_zip, write
from macho_stab_guard import image_uuid, require, sha, snapshot
from macho_local_symbol_guard import classify, verify_local_removal, symbol_rows


def deflated_size(data):
    compressor=zlib.compressobj(9,zlib.DEFLATED,-15)
    return len(compressor.compress(data)+compressor.flush())


def bind_inputs(ipa,ipa_sha,symbols,symbols_sha):
    for path,expected in ((ipa,ipa_sha),(symbols,symbols_sha)):
        require(path.is_file() and not path.is_symlink() and digest(path)==expected,'exact original artifact hash differs')
    with unique_zip(ipa) as archive:
        targets={key:archive.read(path) for key,path in TARGETS.items()}
    diagnostics={}
    with unique_zip(symbols) as archive:
        for key,(_,bundle) in DSYMS.items():
            prefix='Symbols/'+bundle+'/'
            names=[n for n in archive.namelist() if n.startswith(prefix)]
            require(names and prefix+'Contents/Info.plist' in names,'complete matching dSYM bundle missing')
            dwarf=archive.read(prefix+'Contents/Resources/DWARF/'+key)
            require(image_uuid(dwarf)==image_uuid(targets[key]),'retained dSYM UUID differs')
            diagnostics[key]={'uuid':image_uuid(dwarf),'dwarf_sha256':sha(dwarf),'bundle_entries':len(names)}
    for data in targets.values():classify(data)
    return targets,diagnostics


def capture_output(path, data):
    shape=snapshot(data)
    records,_=symbol_rows(data)
    stabs=[{'name_hex':name.hex(),'type':kind,'section':section,'description':desc,'value':value}
           for name,kind,section,desc,value in records if kind & 0xe0]
    write(path,{'accepted':False,'snapshot':shape,'stab_records':stabs[:1024],
                'stab_records_truncated':len(stabs)>1024})


def measure_all_targets(targets,diagnostics,output,report):
    """Independent diagnostic attempts; every rejected target still fails overall."""
    errors={}
    for key,data in targets.items():
        root=output/key;root.mkdir();original=root/'original';candidate=root/'stripped'
        original.write_bytes(data)
        try:
            result=subprocess.run(['xcrun','--toolchain','default','strip','-x','-o',str(candidate),str(original)],
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False,timeout=120)
            (root/'strip.log').write_bytes(result.stdout)
            require(result.returncode==0,'Apple local-symbol strip failed: '+key)
            require(original.read_bytes()==data,'strip mutated original copy')
            intermediate=candidate.read_bytes()
            capture_output(root/'after-strip-observed.json',intermediate)
            final=intermediate
            write(root/'unapproved-compression-measurement.json', {
                'approved':False, 'deflated_before_bytes':deflated_size(data),
                'deflated_after_bytes':deflated_size(final),
                'deflated_bytes_saved':deflated_size(data)-deflated_size(final)})
            evidence=verify_local_removal(data,final)
            require(image_uuid(final)==diagnostics[key]['uuid'],'final dSYM UUID differs')
            evidence.update(deflated_before_bytes=deflated_size(data),deflated_after_bytes=deflated_size(final),
                deflated_bytes_saved=deflated_size(data)-deflated_size(final),
                code_signature_state='UNVERIFIED_MUTATED_DIAGNOSTIC_NOT_DELIVERABLE')
            report['targets'][key]['measurement']=evidence
            write(root/'structural.json',evidence)
        except Exception as error:
            errors[key]={'type':type(error).__name__,'message':str(error)}
            report['targets'][key]['failure']=errors[key]
            write(root/'failure.json',errors[key])
    report['target_errors']=errors
    require(not errors,'one or more local-symbol diagnostic targets rejected; see per-target evidence')


def experiment(ipa,ipa_sha,symbols,symbols_sha,output,*,inspect_only=False,qualify_package=False):
    require(not (inspect_only and qualify_package),'inspection and package qualification are exclusive')
    require(not output.exists() and not output.is_symlink(),'fresh diagnostic directory required')
    targets,diagnostics=bind_inputs(ipa,ipa_sha,symbols,symbols_sha)
    output.mkdir(parents=True)
    report={'schema':'local-symbol-diagnostic-v1','status':'FAILED','input_ipa_sha256':ipa_sha,
            'retained_symbols_sha256':symbols_sha,'diagnostics':diagnostics,
            'workflow_commit':os.environ.get('GITHUB_SHA'),'run_id':os.environ.get('GITHUB_RUN_ID'),
            'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'runtime_proven':False,
            'packaging_approved':False,'ipa_emitted':False,'original_ipa_retained_as_archive_only':True,'signature_verified':False,'targets':{}}
    try:
        for key,data in targets.items():
            shape,removable,protected=classify(data)
            report['targets'][key]={'before':shape,'eligible_local_records':sum(removable.values()),
                                    'protected_records':sum(protected.values())}
        if inspect_only:
            report['status']='INPUTS_VERIFIED_NO_TRANSFORMATION'
            return report
        require(sys.platform=='darwin','Apple strip execution requires macOS')
        require(os.environ.get('DEVELOPER_DIR')=='/Applications/Xcode_26.4.1.app/Contents/Developer','pinned Xcode required')
        tool=subprocess.run(['xcrun','--toolchain','default','--find','strip'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,text=True,timeout=30)
        strip_path=Path(tool.stdout.strip()).resolve(strict=True)
        expected=Path(os.environ['DEVELOPER_DIR'])/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip'
        require(strip_path==expected.resolve(strict=True),'unrecognized strip tool path')
        report['strip_tool']={'path':str(strip_path),'sha256':digest(strip_path),'flags_sequence':[['-x']]}
        # Full original IPA and complete dSYM archive remain in separate retained diagnostics.
        retained=output/'retained';retained.mkdir()
        for source,name,expected in ((ipa,'original.ipa',ipa_sha),(symbols,'matching-debug-diagnostics.zip',symbols_sha)):
            shutil.copyfile(source,retained/name)
            require(digest(retained/name)==expected,'retained original readback differs')
        if qualify_package:
            from local_symbol_packaging import native
            baseline=output/'before-transform-SideStore';baseline.write_bytes(targets['SideStore'])
            native(['/usr/bin/codesign','--verify','--strict','--verbose=4',str(baseline)],output/'before-transform-strict.log')
        measure_all_targets(targets,diagnostics,output,report)
        require(digest(ipa)==ipa_sha and digest(symbols)==symbols_sha,'immutable source artifacts changed')
        if qualify_package:
            from local_symbol_packaging import qualify_package as package
            package(ipa,targets,diagnostics,output,report)
            require(digest(ipa)==ipa_sha and digest(symbols)==symbols_sha,'immutable package source artifacts changed')
            report['status']='LOCAL_SYMBOL_PACKAGE_QUALIFIED_DEVICE_AND_RELEASE_UNQUALIFIED'
        else:
            report['status']='STRUCTURAL_DIAGNOSTIC_PASS_SIGNING_AND_RUNTIME_UNQUALIFIED'
        return report
    except Exception:
        report['status']='FAILED';report['packaging_approved']=False;report['ipa_emitted']=False
        derivative=output/'local-symbol-qualified.ipa'
        if derivative.exists():derivative.unlink()
        raise
    finally:
        write(output/'diagnostic.json',report)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('ipa','symbols','output'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('ipa-sha256','symbols-sha256'):parser.add_argument('--'+name,required=True)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--inspect-only',action='store_true')
    modes.add_argument('--qualify-package',action='store_true')
    args=parser.parse_args()
    experiment(args.ipa.absolute(),args.ipa_sha256,args.symbols.absolute(),args.symbols_sha256,
               args.output.resolve(),inspect_only=args.inspect_only,qualify_package=args.qualify_package)

if __name__=='__main__':main()
