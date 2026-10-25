"""Compare an explicitly qualified -Os generated input with pinned -S O2 bytes.

No binary execution, app packaging, dependency promotion or local-symbol removal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'issue53-lts'))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
import build
import build_ios
import install_ios
import qualify
import command_evidence
from macho_stab_guard import snapshot, commands, verify, image_uuid, require


def deflated(data):
    compressor=zlib.compressobj(9,zlib.DEFLATED,-15)
    return len(compressor.compress(data)+compressor.flush())


def external_names(data):
    snapshot(data) # production strict Mach-O bounds and category checks
    sym=next(raw for cmd,raw in commands(data)[2] if cmd==2)
    offset,count,strings,size=struct.unpack_from('<4I',sym,8)
    names=[]
    for i in range(count):
        name,kind=struct.unpack_from('<IB',data,offset+16*i)
        if kind&1 and kind&14 and not kind&0xe0:
            end=data.find(b'\0',strings+name,strings+size)
            require(end>=strings+name,'Unterminated external symbol')
            names.append(data[strings+name:end].hex())
    names.sort()
    return {'count':len(names),'sha256':hashlib.sha256(json.dumps(names,separators=(',',':')).encode()).hexdigest()}


def compare(data,baseline):
    exports=external_names(data)
    require(exports['count']==baseline['defined_external_names_count'] and
            exports['sha256']==baseline['defined_external_names_sha256'],'Defined external API closure changed')
    compressed=deflated(data)
    return {'raw_bytes_per_copy':len(data),'deflate9_bytes_per_copy':compressed,
        'raw_bytes_saved_per_copy':baseline['raw_bytes_per_copy']-len(data),
        'deflate9_bytes_saved_per_copy':baseline['deflate9_bytes_per_copy']-compressed,
        'deflate9_bytes_saved_two_separate_copies':2*(baseline['deflate9_bytes_per_copy']-compressed),
        'defined_external_names':exports,'final_app_ipa_size_measured':False}


def measure(mac,ios,evidence):
    mac=mac.resolve(strict=True);ios=ios.resolve(strict=True);evidence=evidence.resolve(strict=True)
    mac_receipt,_,_=qualify.validate_generated(mac)
    host=json.loads((evidence/'lts-qualification.json').read_text())
    require(mac_receipt.get('optimization')=='Os' and host['status']=='PASS_MACOS_SYNTHETIC_ONLY' and
            host['failures']==[] and host['library_sha256']==mac_receipt['library_sha256'],
            'Matching Os host qualification did not pass')
    require(mac_receipt['build_environment']['CFLAGS'].startswith('-Os -g -fPIC '),'Unexpected host optimization')
    mac_compiler=next(path for path in mac_receipt['tools_sha256'] if Path(path).name=='clang')
    mac_commands=command_evidence.verify(mac/'build.log',{'macOS':mac/'configdata.txt'},mac_compiler,{'macOS':None})
    require(mac_commands['slices']['macOS']['configdata_sha256']==mac_receipt['configuration_sha256'],'Host config receipt differs')
    generated,receipt=install_ios.validate(ios,allow_size_experiment=True)
    require(receipt.get('optimization')=='Os','Not an explicit Os iOS experiment')
    for row in receipt['slices'].values():
        require(row['build_environment']['CFLAGS'].startswith('-Os -g -fPIC '),'Unexpected slice optimization')
    ios_commands=command_evidence.verify(ios/'build-ios.log',
        {key:ios/key/'configdata.txt' for key in receipt['slices']},receipt['tools']['clang']['invocation'],
        {key:row['target_triple'] for key,row in receipt['slices'].items()})
    require(all(ios_commands['slices'][key]['configdata_sha256']==row['configuration_sha256'] for key,row in receipt['slices'].items()),
            'iOS config receipt differs')
    device=generated/'OpenSSL.xcframework/ios-arm64'
    binary=device/'OpenSSL.framework/OpenSSL'
    dwarf=device/'dSYMs/OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL'
    before=binary.read_bytes();before_hash=build.digest(binary)
    require(image_uuid(before)==image_uuid(dwarf.read_bytes()),'Matching generated dSYM missing')
    shape=snapshot(before);require(shape['signature_sha256'] is None,'Generated input unexpectedly signed')
    require(os.environ.get('DEVELOPER_DIR')=='/Applications/Xcode_26.4.1.app/Contents/Developer','Pinned Xcode required')
    strip=Path(subprocess.run(['xcrun','--toolchain','default','--find','strip'],check=True,capture_output=True,text=True,timeout=30).stdout.strip())
    expected=Path(os.environ['DEVELOPER_DIR'])/'Toolchains/XcodeDefault.xctoolchain/usr/bin/strip'
    require(strip.resolve(strict=True)==expected.resolve(strict=True),'Unexpected strip tool')
    output=ios/'measurement-stripped-OpenSSL'
    require(not output.exists() and not output.is_symlink(),'Fresh measured binary required')
    result=subprocess.run([str(strip),'-S','-o',str(output),str(binary)],capture_output=True,timeout=120)
    (evidence/'Os-device-strip.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode==0,'Native strip failed')
    after=output.read_bytes()
    (evidence/'Os-device-observed.json').write_text(json.dumps({
        'accepted':False,'original_sha256':before_hash,'observed_sha256':build.digest(output),
        'observed_raw_bytes':len(after),'observed_deflate9_bytes':deflated(after),
        'snapshot':snapshot(after),'defined_external_names':external_names(after)},indent=2)+'\n')
    structural=verify(before,after)
    require(image_uuid(after)==image_uuid(dwarf.read_bytes()) and snapshot(after)['signature_sha256'] is None,
            'Unsigned/dSYM association changed')
    require(build.digest(binary)==before_hash,'Generated original changed')
    install_ios.validate(ios,allow_size_experiment=True)
    baseline=json.loads((HERE/'baseline.json').read_text())
    result={'schema':'issue55-openssl-size-experiment-v1','status':'MEASURED_NOT_PROMOTED',
        'baseline':baseline,'baseline_receipt_sha256':build.digest(HERE/'baseline.json'),
        'workflow_commit':os.environ['GITHUB_SHA'],'run_id':os.environ['GITHUB_RUN_ID'],
        'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'optimization':'Os',
        'mac_receipt_sha256':build.digest(mac/'build-receipt.json'),
        'host_qualification_sha256':build.digest(evidence/'lts-qualification.json'),
        'ios_receipt_sha256':build.digest(ios/'ios-build-receipt.json'),
        'original_generated_binary_sha256':before_hash,'measured_stripped_binary_sha256':build.digest(output),
        'strip_tool_sha256':build.digest(strip),'structural':structural,
        'actual_compile_evidence':{'macOS':mac_commands,'iOS':ios_commands},
        'comparison':compare(after,baseline),'production_admission':False,'device_execution_proven':False}
    (evidence/'size-measurement.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('mac','ios','evidence'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();measure(args.mac,args.ios,args.evidence)
