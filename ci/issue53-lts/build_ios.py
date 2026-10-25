#!/usr/bin/env python3
"""Generate separately recorded iOS OpenSSL35 inputs; never overwrite gitlinks."""
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
import build

HERE=Path(__file__).resolve().parent
SLICES={
    'ios-arm64':('iphoneos','arm64','ios64-xcrun','arm64-apple-ios15.0','IOS'),
    'sim-arm64':('iphonesimulator','arm64','iossimulator-arm64-xcrun','arm64-apple-ios15.0-simulator','IOSSIMULATOR'),
    'sim-x86_64':('iphonesimulator','x86_64','iossimulator-x86_64-xcrun','x86_64-apple-ios15.0-simulator','IOSSIMULATOR'),
}

def uuids(text):return sorted(re.findall(r'UUID: ([0-9A-Fa-f-]+) \(([^)]+)\)',text))

def validate_macho(text,expected):
    observed=re.findall(r'^\s*platform\s+(\S+)\s*$',text,re.MULTILINE)
    minimums=re.findall(r'^\s*minos\s+(\S+)\s*$',text,re.MULTILINE)
    if not observed or any(p!=expected for p in observed):raise ValueError('Actual Mach-O platform mismatch')
    if not minimums or any(v not in ('15.0','15.0.0') for v in minimums):raise ValueError('Actual Mach-O deployment target mismatch')

def merge_headers(arm,x86,out):
    a=build.inventory(arm);b=build.inventory(x86)
    if set(a)!=set(b):raise ValueError('Simulator header sets differ')
    out.mkdir(parents=True)
    differing=[]
    for name in sorted(a):
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True)
        if a[name]==b[name]:shutil.copyfile(arm/name,target)
        else:
            # Preserve both generated header bodies. Never silently choose an
            # architecture's configuration for the other simulator architecture.
            differing.append(name)
            for arch,source in [('arm64',arm),('x86_64',x86)]:
                saved=out/'arch'/arch/name;saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source/name,saved)
            prefix='../'*len(Path(name).parent.parts)
            target.write_text('#if defined(__aarch64__) || defined(__arm64__)\n#include "'+prefix+'arch/arm64/'+name+'"\n#elif defined(__x86_64__)\n#include "'+prefix+'arch/x86_64/'+name+'"\n#else\n#error Unsupported OpenSSL simulator architecture\n#endif\n')
    return differing

def decorate(framework,sdk):
    headers=framework/'Headers'
    public=sorted(p.name for p in headers.glob('*.h') if p.name!='OpenSSL.h')
    if 'ssl.h' not in public:raise ValueError('Generated public SSL header missing')
    public.remove('ssl.h');public.insert(0,'ssl.h')
    (headers/'OpenSSL.h').write_text('#pragma once\n'+''.join('#include <OpenSSL/'+name+'>\n' for name in public))
    (framework/'Modules').mkdir()
    (framework/'Modules/module.modulemap').write_text('framework module OpenSSL {\n  umbrella header "OpenSSL.h"\n  export *\n  module * { export * }\n}\n')
    (framework/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'OpenSSL','CFBundleName':'OpenSSL',
        'CFBundleIdentifier':'org.openssl.generated.lts','CFBundlePackageType':'FMWK',
        'CFBundleShortVersionString':'3.5.9','CFBundleVersion':'1','CFBundleInfoDictionaryVersion':'6.0',
        'DTPlatformName':sdk,'CFBundleSupportedPlatforms':['iPhoneOS' if sdk=='iphoneos' else 'iPhoneSimulator'],
        'MinimumOSVersion':'15.0'}))
    # Same no-collection/no-tracking declaration as the previous framework.
    (framework/'PrivacyInfo.xcprivacy').write_bytes(plistlib.dumps({'NSPrivacyCollectedDataTypes':[],
        'NSPrivacyAccessedAPITypes':[],'NSPrivacyTrackingDomains':[],'NSPrivacyTracking':False}))

def generate(output,archive=None):
    output,archive=build.normalized_paths(output,archive)
    if platform.system()!='Darwin':raise ValueError('Apple SDK build requires macOS')
    if output.exists():raise ValueError('Refusing existing generated dependency output')
    output.mkdir(parents=True);log=output/'build-ios.log';env=build.sanitized_environment()
    pins=json.loads((HERE/'source.json').read_text())
    if pins['version']!='3.5.9':raise ValueError('Unreviewed source version')
    command=lambda args,timeout=600:build.command(args,log,env,timeout)
    xcode=command(['xcodebuild','-version'])
    if 'Xcode 26.4.1' not in xcode.splitlines():raise ValueError('Expected Xcode26.4.1')
    names=['clang','ar','ranlib','dsymutil','dwarfdump','vtool','lipo','nm','otool']
    tools={name:build.tool_invocation(command(['xcrun','--find',name])) for name in names}
    tools.update(perl=build.tool_invocation('/usr/bin/perl'),make=build.tool_invocation('/usr/bin/make'))
    env['PATH']=str(tools['clang'].parent)+':/usr/bin:/bin:/usr/sbin:/sbin'
    env.update(CC=str(tools['clang']),AR=str(tools['ar']),RANLIB=str(tools['ranlib']),ZERO_AR_DATE='1',
               SOURCE_DATE_EPOCH=str(int(datetime.datetime(2026,9,29,tzinfo=datetime.timezone.utc).timestamp())))
    if archive is None:
        archive=output/'openssl-3.5.9.tar.gz'
        command(['curl','--fail','--location','--retry','0','--max-time','180',pins['source_url'],'--output',str(archive)],190)
    receipts={};frameworks={};symbols={}
    for name,(sdk,arch,target,triple,macho_platform) in SLICES.items():
        stage=output/name;stage.mkdir();source=build.unpack(archive,stage/'source',pins['sha256'])
        original=build.inventory(source);prefix=stage/'install'
        sdk_path=command(['xcrun','--sdk',sdk,'--show-sdk-path'])
        sdk_version=command(['xcrun','--sdk',sdk,'--show-sdk-version'])
        flags=['-target',triple,'-isysroot',sdk_path]
        env.update(SDKROOT=sdk_path,CFLAGS='-O2 -g -fPIC '+' '.join(flags),LDFLAGS=' '.join(flags))
        configure=[str(tools['perl']),str(source/'Configure'),target,'no-shared','no-module','enable-legacy','no-asm',
                   '--prefix='+str(prefix),'--openssldir='+str(prefix/'ssl')]
        previous=Path.cwd()
        try:
            os.chdir(source);command(configure,120)
            config=command([str(tools['perl']),'configdata.pm','--dump'],30)
            (stage/'configdata.txt').write_text(config+'\n')
            command([str(tools['make']),'-j3','build_libs'],600)
            command([str(tools['make']),'install_dev'],120)
        finally:os.chdir(previous)
        changed=[p for p,sha in original.items() if not (source/p).is_file() or build.digest(source/p)!=sha]
        if changed:raise ValueError('Archived source changed: '+','.join(changed))
        framework=stage/'OpenSSL.framework';framework.mkdir();frameworks[name]=framework
        shutil.copytree(prefix/'include/openssl',framework/'Headers');decorate(framework,sdk)
        archives=[prefix/'lib/libssl.a',prefix/'lib/libcrypto.a']
        if not all(p.is_file() for p in archives):raise ValueError('iOS static archives missing')
        binary=framework/'OpenSSL'
        command([str(tools['clang']),'-dynamiclib',*flags,'-Wl,-all_load',*map(str,archives),
                 '-Wl,-install_name,@rpath/OpenSSL.framework/OpenSSL','-Wl,-current_version,3.5.9',
                 '-Wl,-compatibility_version,3.0.0','-o',str(binary)],120)
        dsym=stage/'OpenSSL.framework.dSYM';symbols[name]=dsym
        command([str(tools['dsymutil']),str(binary),'-o',str(dsym)],120)
        identity=uuids(command([str(tools['dwarfdump']),'--uuid',str(binary)]))
        if not identity or identity!=uuids(command([str(tools['dwarfdump']),'--uuid',str(dsym)])):raise ValueError('Slice dSYM UUID mismatch')
        actual=command([str(tools['vtool']),'-show-build',str(binary)]);validate_macho(actual,macho_platform)
        if command([str(tools['lipo']),'-archs',str(binary)]).split()!=[arch]:raise ValueError('Slice architecture mismatch')
        exported=command([str(tools['nm']),'-gjU',str(binary)])
        for symbol in ['_OpenSSL_version','_PKCS12_parse','_CMS_sign','_OSSL_PROVIDER_load']:
            if symbol not in exported.splitlines():raise ValueError('Required consumer symbol absent: '+symbol)
        installname=command([str(tools['otool']),'-D',str(binary)]).splitlines()[1:]
        if installname!=['@rpath/OpenSSL.framework/OpenSSL']:raise ValueError('Install name mismatch')
        # Compile/link only, no attempted execution of an iOS Mach-O on macOS.
        probe=stage/'module-probe.m';probe.write_text('@import OpenSSL;\nint main(void) { return OpenSSL_version(0) == 0 || OSSL_PROVIDER_load(0, "legacy") == 0; }\n')
        command([str(tools['clang']),*flags,'-fmodules','-fmodules-cache-path='+str(stage/'module-cache'),
                 '-F',str(stage),str(probe),'-framework','OpenSSL','-o',str(stage/'module-probe')],120)
        receipts[name]={'sdk':sdk,'sdk_version':sdk_version,'architecture':arch,'target_triple':triple,
            'configure':configure,'configuration_sha256':build.digest(stage/'configdata.txt'),
            'source_inventory_sha256':hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),
            'archives_sha256':{p.name:build.digest(p) for p in archives},'framework_inventory':build.inventory(framework),
            'dwarf_uuid':identity,'macho_build_commands':actual,'module_probe_sha256':build.digest(stage/'module-probe'),
            'module_probe_executed':False}
    merged=output/'sim-universal';merged.mkdir();sim=merged/'OpenSSL.framework';sim.mkdir()
    differing=merge_headers(frameworks['sim-arm64']/'Headers',frameworks['sim-x86_64']/'Headers',sim/'Headers')
    decorate(sim,'iphonesimulator')
    command([str(tools['lipo']),'-create',str(frameworks['sim-arm64']/'OpenSSL'),str(frameworks['sim-x86_64']/'OpenSSL'),'-output',str(sim/'OpenSSL')])
    sim_dsym=merged/'OpenSSL.framework.dSYM';shutil.copytree(symbols['sim-arm64'],sim_dsym)
    dwarf='Contents/Resources/DWARF/OpenSSL'
    command([str(tools['lipo']),'-create',str(symbols['sim-arm64']/dwarf),str(symbols['sim-x86_64']/dwarf),'-output',str(sim_dsym/dwarf)])
    if uuids(command([str(tools['dwarfdump']),'--uuid',str(sim/'OpenSSL')]))!=uuids(command([str(tools['dwarfdump']),'--uuid',str(sim_dsym)])):raise ValueError('Universal dSYM UUID mismatch')
    merged_probe_hashes={}
    for name in ['sim-arm64','sim-x86_64']:
        sdk,arch,target,triple,macho_platform=SLICES[name]
        sdk_path=command(['xcrun','--sdk',sdk,'--show-sdk-path'])
        probe=merged/(arch+'-probe.m');probe.write_text('@import OpenSSL;\nint main(void) { return OpenSSL_version(0) == 0 || OSSL_PROVIDER_load(0, "legacy") == 0; }\n')
        executable=merged/(arch+'-probe')
        command([str(tools['clang']),'-target',triple,'-isysroot',sdk_path,'-fmodules',
                 '-fmodules-cache-path='+str(merged/(arch+'-module-cache')),'-F',str(merged),str(probe),
                 '-framework','OpenSSL','-o',str(executable)],120)
        merged_probe_hashes[arch]=build.digest(executable)
    generated=output/'GeneratedDependencies/OpenSSL35';generated.mkdir(parents=True)
    xc=generated/'OpenSSL.xcframework'
    command(['xcodebuild','-create-xcframework','-framework',str(frameworks['ios-arm64']),'-debug-symbols',str(symbols['ios-arm64']),
             '-framework',str(sim),'-debug-symbols',str(sim_dsym),'-output',str(xc)],120)
    shutil.copyfile(source/'LICENSE.txt',generated/'LICENSE.txt')
    info=plistlib.loads((xc/'Info.plist').read_bytes());libs=info.get('AvailableLibraries',[])
    closure={(p['SupportedPlatform'],p.get('SupportedPlatformVariant'),tuple(sorted(p['SupportedArchitectures']))) for p in libs}
    if len(libs)!=2 or closure!={('ios',None,('arm64',)),('ios','simulator',('arm64','x86_64'))}:raise ValueError('Unexpected XCFramework slice closure')
    receipt={'schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
        'source':pins,'recipe_sha256':build.digest(Path(__file__)),'shared_recipe_sha256':build.digest(HERE/'build.py'),
        'xcode':xcode,'tools':{name:{'invocation':str(p),'resolved':str(p.resolve(strict=True)),'sha256':build.digest(p)} for name,p in tools.items()},
        'slices':receipts,'merged_simulator_module_probe_sha256':merged_probe_hashes,'simulator_architecture_specific_headers':differing,'generated_inventory':build.inventory(generated),
        'xcframework_relative_path':'GeneratedDependencies/OpenSSL35/OpenSSL.xcframework',
        'provider_policy':'default and legacy built-in; no-module enable-legacy',
        'device_executed':False,'simulator_executed':False,'production_admission':False,'old_gitlink_overwritten':False}
    (output/'ios-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--archive',type=Path)
    args=parser.parse_args();generate(args.output,args.archive)
