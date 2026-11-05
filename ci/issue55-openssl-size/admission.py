"""Source-owned, non-executing same-run qualification admission for both copies."""
import hashlib
import importlib.util
import json
import re
import struct
import zlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SOURCE_PATHS=tuple(
    ['ci/issue53-lts/'+name for name in ('source.json','build.py','build_ios.py','install_ios.py','consumer-policy.json','qualify.py')]+
    ['ci/issue53-crypto/'+name for name in ('acquire.py','pinned-inputs.json','host-headers.json','embedded-headers.json',
     'consumer.py','run.py','probe.c','verify.swift','zsign-source.json','zsign_probe.cpp','verify_cms.py','verify_zsign.swift','coload.c')]+
    ['ci/issue55-openssl-size/'+name for name in ('baseline.json','measure.py','command_evidence.py','admission.py')]+
    ['scripts/macho_stab_guard.py'])
CLANG='/Applications/Xcode_26.4.1.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang'
POLICY={'schema':'issue53-generated-consumer-policy-v1','optimization':'Os',
        'historical_builder_default':'O2','qualification':'same-run-native-macos-and-three-ios-slices'}
TRIPLES={'ios-arm64':'arm64-apple-ios15.0','sim-arm64':'arm64-apple-ios15.0-simulator','sim-x86_64':'x86_64-apple-ios15.0-simulator'}

def require(ok,message):
    if not ok:raise ValueError(message)

def sha(path):
    path=Path(path)
    require(path.is_file() and not path.is_symlink(),'Missing/linked qualification file: '+path.name)
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    sha(path)
    return json.loads(Path(path).read_text())

def qualification_sources():return {name:sha(ROOT/name) for name in SOURCE_PATHS}

def inventory(root):
    require(root.is_dir() and not root.is_symlink(),'Missing/linked generated host root')
    result={}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'Linked generated host input')
        if path.is_file():result[path.relative_to(root).as_posix()]=sha(path)
    return result

def compiler_evidence(log,configs,compiler,triples):
    path=Path(__file__).with_name('command_evidence.py')
    spec=importlib.util.spec_from_file_location('_qualified_os_commands',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.verify(log,configs,compiler,triples)

def macho_guard():
    path=ROOT/'scripts/macho_stab_guard.py'
    spec=importlib.util.spec_from_file_location('_qualified_os_macho_guard',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def defined_external_names(data):
    guard=macho_guard();guard.snapshot(data)
    sym=next(raw for cmd,raw in guard.commands(data)[2] if cmd==2)
    offset,count,strings,size=struct.unpack_from('<4I',sym,8)
    names=[]
    for index in range(count):
        name,kind=struct.unpack_from('<IB',data,offset+16*index)
        if kind&1 and kind&14 and not kind&0xe0:
            end=data.find(b'\0',strings+name,strings+size)
            require(end>=strings+name,'Unterminated external symbol')
            names.append(data[strings+name:end].hex())
    names.sort()
    return {'count':len(names),'sha256':hashlib.sha256(json.dumps(names,separators=(',',':')).encode()).hexdigest()}

def binary_evidence(original,final,dwarf):
    guard=macho_guard()
    require(guard.snapshot(original)['signature_sha256'] is None and guard.snapshot(final)['signature_sha256'] is None,
            'Generated library signature state changed')
    require(guard.image_uuid(original)==guard.image_uuid(final)==guard.image_uuid(dwarf),'Generated dSYM association changed')
    structural=guard.verify(original,final)
    return {'structural':json.loads(json.dumps(structural)),'defined_external_names':defined_external_names(final)}

def verify_measurement(evidence,host,ios,identity,expected_ios_receipt=None):
    evidence=Path(evidence).resolve(strict=True);host=Path(host).resolve(strict=True);ios=Path(ios).resolve(strict=True)
    require(set(identity)=={'workflow_commit','run_id','run_attempt'},'Invalid same-run identity closure')
    policy=read(ROOT/'ci/issue53-lts/consumer-policy.json');require(policy==POLICY,'Consumer policy differs')
    source=read(ROOT/'ci/issue53-lts/source.json')
    paths={'measurement':evidence/'size-measurement.json','host_qualification':evidence/'lts-qualification.json',
           'host_receipt':evidence/'openssl-host-build-receipt.json','ios_receipt':evidence/'openssl-ios-build-receipt.json'}
    hashes={key:sha(path) for key,path in paths.items()}
    measured,qualified,mac,device=(read(paths[key]) for key in ('measurement','host_qualification','host_receipt','ios_receipt'))
    require(all(measured.get(key)==value for key,value in identity.items()),'Qualification is not from this workflow/run/attempt')
    require(measured.get('schema')=='issue55-openssl-size-experiment-v1' and measured.get('status')=='MEASURED_NOT_PROMOTED' and
            measured.get('optimization')=='Os' and measured.get('production_admission') is False and
            measured.get('device_execution_proven') is False,'Unsupported size qualification state')
    helpers=qualification_sources()
    require(measured.get('qualification_sources_sha256')==helpers,'Qualification helper/source closure changed')
    require(measured.get('consumer_policy_sha256')==sha(ROOT/'ci/issue53-lts/consumer-policy.json'),'Qualification consumer policy changed')
    require(measured.get('mac_receipt_sha256')==hashes['host_receipt'] and
            measured.get('ios_receipt_sha256')==hashes['ios_receipt'] and
            measured.get('host_qualification_sha256')==hashes['host_qualification'],'Qualification receipt chain differs')
    require(hashes['host_receipt']==sha(host/'build-receipt.json') and
            hashes['ios_receipt']==sha(ios/'ios-build-receipt.json'),'Copied native build receipt differs from original')
    if expected_ios_receipt is not None:require(device==expected_ios_receipt,'Staged/generated iOS receipt differs from qualification')
    require(mac.get('schema')=='issue53-lts-generated-dependency-v1' and mac.get('status')=='BUILT_NOT_QUALIFIED_NOT_APP_READY' and
            device.get('schema')=='issue53-ios-generated-dependency-v1' and device.get('status')=='BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
            'Native builder receipt schema/status differs')
    require(mac.get('source')==source==device.get('source') and mac.get('optimization')=='Os'==device.get('optimization'),
            'Native qualification source/optimization differs')
    require(mac.get('recipe_sha256')==helpers['ci/issue53-lts/build.py']==device.get('shared_recipe_sha256') and
            device.get('recipe_sha256')==helpers['ci/issue53-lts/build_ios.py'],'Native build recipe changed')
    require(mac.get('build_environment',{}).get('CFLAGS','').startswith('-Os -g -fPIC '),'Host CFLAGS differ')
    require(mac.get('product_pins_changed') is False and mac.get('production_admission') is False and mac.get('device_executed') is False,
            'Host qualification overclaims execution/admission')
    require(all(device.get(key) is False for key in ('device_executed','simulator_executed','production_admission','old_gitlink_overwritten')),
            'iOS qualification overclaims execution/admission')
    require(re.fullmatch('[0-9a-f]{64}',mac.get('source_inventory_sha256','')) is not None,'Missing original source inventory binding')
    require(set(device.get('slices',{}))==set(TRIPLES),'Qualified architecture closure differs')
    for name,row in device['slices'].items():
        require(row.get('target_triple')==TRIPLES[name] and row.get('source_inventory_sha256')==mac.get('source_inventory_sha256') and
                row.get('build_environment',{}).get('CFLAGS','').startswith('-Os -g -fPIC '),'Slice source/flags/architecture differs')
    variant=qualified.get('input_variant',{})
    require(qualified.get('schema')=='issue53-lts-qualification-v1' and qualified.get('status')=='PASS_MACOS_SYNTHETIC_ONLY' and
            qualified.get('failures')==[] and qualified.get('production_admission') is False and qualified.get('device_executed') is False,
            'Same-run native crypto qualification did not pass')
    require(variant.get('source')==source and variant.get('recipe_sha256')==mac['recipe_sha256'] and
            variant.get('build_receipt_sha256')==hashes['host_receipt'] and variant.get('host_binary_sha256')==mac.get('library_sha256') and
            qualified.get('library_sha256')==mac.get('library_sha256'),'Host qualification input binding differs')
    supporting=qualified.get('evidence_sha256',{})
    require({'lts-crypto.json','lts-host-zsign.json','coload.json'}<=set(supporting),'Native qualification component receipts missing')
    for name,digest in supporting.items():
        require(Path(name).name==name and name not in ('.','..') and sha(evidence/name)==digest,'Native qualification evidence changed')
    crypto=read(evidence/'lts-crypto.json');zsign=read(evidence/'lts-host-zsign.json');coload=read(evidence/'coload.json')
    require(crypto.get('status')=='PASS_SYNTHETIC_ONLY' and crypto.get('library_sha256')==mac['library_sha256'] and
            crypto.get('builtin_providers_only') is True and crypto.get('provider_dispatch_images_bound') is True,
            'Provider/PKCS12 qualification component differs')
    require(zsign.get('status')=='PASS_SYNTHETIC_UNTRUSTED_CHAIN' and zsign.get('library_sha256')==mac['library_sha256'] and
            zsign.get('trusted_Apple_signing_identity') is False,'Actual ZSign qualification component differs')
    require(coload.get('status')=='PASS_MACOS_DIAGNOSTIC_ONLY' and coload.get('errors')==[] and
            coload.get('ios_loader_qualified') is False,'Co-load qualification component differs')
    require(inventory(host/'generated')==mac.get('generated_inventory'),'Qualified host generated bytes drifted')
    require(sha(host/'generated/OpenSSL.framework/Versions/A/OpenSSL')==mac['library_sha256'],'Qualified host library changed')
    generated=ios/'GeneratedDependencies/OpenSSL35'
    require(inventory(generated)==device.get('generated_inventory'),'Qualified iOS generated bytes drifted')
    binary=generated/'OpenSSL.xcframework/ios-arm64/OpenSSL.framework/OpenSSL'
    require(sha(binary)==measured.get('original_generated_binary_sha256'),'Qualified device binary changed')
    require(sha(ios/'measurement-stripped-OpenSSL')==measured.get('measured_stripped_binary_sha256'),'Measured stripped binary changed')
    require(read(ROOT/'ci/issue55-openssl-size/baseline.json')==measured.get('baseline') and
            sha(ROOT/'ci/issue55-openssl-size/baseline.json')==measured.get('baseline_receipt_sha256'),'Measured baseline differs')
    final=(ios/'measurement-stripped-OpenSSL').read_bytes();baseline=measured['baseline']
    dwarf=generated/'OpenSSL.xcframework/ios-arm64/dSYMs/OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL'
    sha(dwarf)
    binary_check=binary_evidence(binary.read_bytes(),final,dwarf.read_bytes())
    require(binary_check['structural']==measured.get('structural'),'Current generated-to-stripped structure differs from measurement')
    require(binary_check['defined_external_names']=={'count':baseline['defined_external_names_count'],'sha256':baseline['defined_external_names_sha256']},
            'Current measured external API closure differs from baseline')
    compressor=zlib.compressobj(9,zlib.DEFLATED,-15);compressed=len(compressor.compress(final)+compressor.flush())
    compared=measured.get('comparison',{})
    expected={'raw_bytes_per_copy':len(final),'deflate9_bytes_per_copy':compressed,
        'raw_bytes_saved_per_copy':baseline['raw_bytes_per_copy']-len(final),
        'deflate9_bytes_saved_per_copy':baseline['deflate9_bytes_per_copy']-compressed,
        'deflate9_bytes_saved_two_separate_copies':2*(baseline['deflate9_bytes_per_copy']-compressed),
        'defined_external_names':binary_check['defined_external_names'],
        'final_app_ipa_size_measured':False}
    require(compared==expected and expected['deflate9_bytes_saved_two_separate_copies']>0,'No matching measured size improvement')
    require(CLANG in mac.get('tools_sha256',{}) and device.get('tools',{}).get('clang',{}).get('invocation')==CLANG,
            'Unexpected compiler identity')
    require(mac['tools_sha256'][CLANG]==device['tools']['clang']['sha256'],'Host/iOS compiler bytes differ')
    actual={'macOS':compiler_evidence(host/'build.log',{'macOS':host/'configdata.txt'},CLANG,{'macOS':None}),
            'iOS':compiler_evidence(ios/'build-ios.log',{key:ios/key/'configdata.txt' for key in TRIPLES},CLANG,TRIPLES)}
    require(actual==measured.get('actual_compile_evidence'),'Actual native compiler evidence changed')
    require(actual['macOS']['slices']['macOS']['configdata_sha256']==mac.get('configuration_sha256') and
            all(actual['iOS']['slices'][key]['configdata_sha256']==row.get('configuration_sha256') for key,row in device['slices'].items()),
            'Compiler configuration receipt mismatch')
    return {'schema':'same-run-qualified-Os-input-v1',**identity,'optimization':'Os',
            'receipt_sha256':hashes,'consumer_policy_sha256':sha(ROOT/'ci/issue53-lts/consumer-policy.json'),
            'qualification_sources_sha256':helpers,'source':source,
            'qualified_host_binary_sha256':mac['library_sha256'],'qualified_device_binary_sha256':sha(binary),
            'device_execution_proven':False}
