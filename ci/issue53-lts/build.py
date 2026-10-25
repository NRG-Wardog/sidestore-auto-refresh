#!/usr/bin/env python3
"""Build an isolated macOS LTS test dependency. Never changes product inputs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tarfile

HERE=Path(__file__).resolve().parent

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def command(args, log, env=None, timeout=600):
    result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=timeout)
    with log.open('a') as out:
        out.write('$ '+json.dumps(list(map(str,args)))+'\n'+result.stdout+result.stderr+'\n')
    if result.returncode: raise RuntimeError('Build command failed; see '+str(log))
    return result.stdout.strip()

def unpack(archive,output,expected):
    if digest(archive)!=expected: raise ValueError('Official source archive checksum differs')
    if output.exists(): raise ValueError('Refusing pre-existing source directory')
    output.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if sum(m.size for m in members)>500_000_000: raise ValueError('Source extraction limit exceeded')
        names=set()
        for member in members:
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='openssl-3.5.9':
                raise ValueError('Unexpected source archive path')
            if member.name in names: raise ValueError('Duplicate source archive path')
            names.add(member.name)
            if not (member.isfile() or member.isdir()): raise ValueError('Nonregular official source member requires explicit review')
        tar.extractall(output,members=members,filter='data')
    return output/'openssl-3.5.9'

def inventory(root):
    files={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError('Unexpected generated output symlink')
        if path.is_file(): files[str(path.relative_to(root))]=digest(path)
    return files

def sanitized_environment():
    env=dict(os.environ)
    for key in list(env):
        if key.startswith(('LD_','DYLD_','OPENSSL_')) or key in ('CC','CXX','CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS','SDKROOT','ARCHFLAGS','CROSS_COMPILE','CONFIGURE_ARGS','PERL5OPT','PERL5LIB','PERLLIB','MAKEFLAGS','MFLAGS','AR','ARFLAGS','RANLIB','LD','NM','STRIP','CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','CONFIG_SITE'):
            env.pop(key)
    return env

def normalized_paths(output,archive=None):
    return Path(output).resolve(), Path(archive).resolve() if archive is not None else None

def build(output,archive=None):
    output,archive=normalized_paths(output,archive)
    if platform.system()!='Darwin': raise ValueError('Only native macOS qualification is enabled')
    arch=platform.machine()
    if arch not in ('arm64','x86_64'): raise ValueError('Unsupported native macOS architecture')
    if output.exists(): raise ValueError('Output directory must not exist')
    pins=json.loads((HERE/'source.json').read_text())
    if pins['version']!='3.5.9': raise ValueError('Unreviewed LTS source version')
    output.mkdir(parents=True);log=output/'build.log'
    env=sanitized_environment()
    xcode=command(['xcodebuild','-version'],log,env)
    if 'Xcode 26.4.1' not in xcode.splitlines(): raise ValueError('Expected Xcode26.4.1')
    sdk=command(['xcrun','--sdk','macosx','--show-sdk-path'],log,env)
    sdk_version=command(['xcrun','--sdk','macosx','--show-sdk-version'],log,env)
    clang=Path(command(['xcrun','--find','clang'],log,env)).resolve(strict=True)
    perl=Path('/usr/bin/perl').resolve(strict=True);make=Path('/usr/bin/make').resolve(strict=True)
    selected_tools={name:Path(command(['xcrun','--find',name],log,env)).resolve(strict=True)
                    for name in ['ar','ranlib','ld','dsymutil','dwarfdump','vtool']}
    tools={str(p):digest(p) for p in [clang,perl,make,*selected_tools.values()]}
    env['PATH']=str(clang.parent)+':/usr/bin:/bin:/usr/sbin:/sbin'
    compiler=command([str(clang),'--version'],log,env)
    if archive is None:
        archive=output/'openssl-3.5.9.tar.gz'
        command(['curl','--fail','--location','--retry','0','--max-time','180',pins['source_url'],'--output',str(archive)],log,env,190)
    source=unpack(archive,output/'source',pins['sha256'])
    before=inventory(source)
    minimum='11.0' if arch=='arm64' else '10.15'
    env.update({'CC':str(clang),'AR':str(selected_tools['ar']),'RANLIB':str(selected_tools['ranlib']),'SDKROOT':sdk,'CFLAGS':'-O2 -g -fPIC -mmacosx-version-min='+minimum,
                'LDFLAGS':'-mmacosx-version-min='+minimum,'ZERO_AR_DATE':'1',
                'SOURCE_DATE_EPOCH':str(int(datetime.datetime(2026,9,29,tzinfo=datetime.timezone.utc).timestamp()))})
    prefix=output/'install'
    # Explicit provider configuration: static archives with built-in default and
    # legacy, no dynamically loaded module dependency and no copied wrapper Makefile.
    configure=[str(perl),str(source/'Configure'),'darwin64-'+arch+'-cc','no-shared','no-module',
               'enable-legacy','no-asm','--prefix='+str(prefix),'--openssldir='+str(prefix/'ssl')]
    previous=Path.cwd()
    try:
        os.chdir(source)
        command(configure,log,env,120)
        command([str(make),'-j3','build_libs'],log,env,600)
        command([str(make),'install_dev'],log,env,120)
        config=command([str(perl),'configdata.pm','--dump'],log,env,30)
    finally: os.chdir(previous)
    (output/'configdata.txt').write_text(config+'\n')
    # Source files may generate additional outputs, but no archived input bytes
    # may silently change during the recipe. Report exceptions rather than hide them.
    changed={name: {'before':sha,'after':digest(source/name) if (source/name).is_file() else None}
             for name,sha in before.items() if not (source/name).is_file() or digest(source/name)!=sha}
    if changed: raise ValueError('Configure/build modified archived source bytes: '+','.join(changed))
    framework=output/'generated/OpenSSL.framework/Versions/A';framework.mkdir(parents=True)
    shutil.copytree(prefix/'include/openssl',framework/'Headers')
    (framework/'Resources').mkdir()
    (framework/'Resources/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'OpenSSL','CFBundleName':'OpenSSL',
        'CFBundleIdentifier':'org.openssl.qualification.lts.synthetic','CFBundlePackageType':'FMWK',
        'CFBundleShortVersionString':'3.5.9','CFBundleVersion':'1','DTPlatformName':'macosx',
        'CFBundleSupportedPlatforms':['MacOSX'],'LSMinimumSystemVersion':minimum}))
    library=framework/'OpenSSL'
    archives=[prefix/'lib/libssl.a',prefix/'lib/libcrypto.a']
    if not all(p.is_file() for p in archives):
        archives=[prefix/'lib64/libssl.a',prefix/'lib64/libcrypto.a']
    if not all(p.is_file() for p in archives): raise ValueError('Built static libraries missing')
    command([str(clang),'-dynamiclib','-arch',arch,'-isysroot',sdk,'-mmacosx-version-min='+minimum,
             '-Wl,-all_load',*map(str,archives),'-Wl,-install_name,@rpath/OpenSSL.framework/Versions/A/OpenSSL',
             '-Wl,-current_version,3.5.9','-Wl,-compatibility_version,3.0.0','-o',str(library)],log,env,120)
    dsym=output/'generated/OpenSSL.framework.dSYM'
    command(['xcrun','dsymutil',str(library),'-o',str(dsym)],log,env,120)
    uuid=command(['xcrun','dwarfdump','--uuid',str(library)],log,env)
    dsym_uuid=command(['xcrun','dwarfdump','--uuid',str(dsym)],log,env)
    identities=lambda text:re.findall(r'UUID: ([0-9A-Fa-f-]+) \(([^)]+)\)',text)
    if not identities(uuid) or identities(uuid)!=identities(dsym_uuid): raise ValueError('Generated dSYM identity mismatch')
    macho=command(['xcrun','vtool','-show-build',str(library)],log,env)
    platforms=re.findall(r'^\s*platform\s+(\S+)\s*$',macho,re.MULTILINE)
    if not platforms or any(p!='MACOS' for p in platforms): raise ValueError('Generated non-macOS binary')
    header_inventory=inventory(framework/'Headers')
    (output/'headers.json').write_text(json.dumps(header_inventory,indent=2)+'\n')
    shutil.copyfile(source/'LICENSE.txt',output/'generated/LICENSE.txt')
    receipt={'schema':'issue53-lts-generated-dependency-v1','status':'BUILT_NOT_QUALIFIED_NOT_APP_READY',
             'source':pins,'source_inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
             'recipe_sha256':digest(Path(__file__)),'tools_sha256':tools,'xcode':xcode,'compiler':compiler,
             'architecture':arch,'minimum_os':minimum,'sdk_version':sdk_version,'sdk_path':sdk,
             'static_archives_sha256':{str(p):digest(p) for p in archives},
             'configure_command':configure,'configuration_sha256':digest(output/'configdata.txt'),
             'provider_policy':'no-module enable-legacy; both must be runtime-qualified as built-in',
             'build_environment':{k:env[k] for k in ['CFLAGS','LDFLAGS','ZERO_AR_DATE','SOURCE_DATE_EPOCH']},
             'library':str(library),'library_sha256':digest(library),'header_inventory_sha256':digest(output/'headers.json'),
             'generated_inventory':inventory(output/'generated'),'dwarf_uuid':identities(uuid),'macho_build_commands':macho,
             'product_pins_changed':False,'signing_identity_discovery':False,'device_executed':False,
             'production_admission':False,'upstream_tests_executed':False,'limitations':['Native macOS experiment only; no iOS artifact or consumer integration.',
                 'Known inputs and generated outputs recorded; bit-for-bit repeat-build equivalence not yet established.']}
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--archive',type=Path)
    args=parser.parse_args();build(args.output.resolve(),args.archive.resolve() if args.archive else None)
