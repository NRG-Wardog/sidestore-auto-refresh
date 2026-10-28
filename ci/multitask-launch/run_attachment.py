#!/usr/bin/env python3
"""Qualify actual UIKit attachment helper and retain the historical regression control."""
import argparse,json,os,platform,plistlib,re,subprocess,sys,time
from pathlib import Path
from contract import command,identity,inventory,manifest,read,require,sha
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'language-switch'))
from run_language_switch_tests import wait_owned_simulator_boot,verify_boot_readiness
from boot_evidence import available_device,selection_evidence,verify_selection
from attachment_contract import *

def run(root,output):
    root=root.resolve(strict=True);require(output.is_dir() and not output.is_symlink(),'Owned evidence directory required');output=output.resolve(strict=True)
    work=output/'multitask-attachment';require(not work.exists(),'Fresh attachment evidence required');work.mkdir()
    before=inventory(root);udid=None
    report={'schema':'multitask-attachment-ci-v2','status':'FAILED','identity':identity(),'source':manifest(),'source_inventory':before,'physical_device_execution':False,'real_extension_launch':False}
    try:
        require(platform.system()=='Darwin','UIKit fixture requires installed Apple simulator')
        require(all(sha(root/name)==digest for name,digest in UNCHANGED_BRIDGE_HASHES.items()),'Language bridge changed outside attachment scope')
        require(sha(HERE/'attachment/baseline/VirtualWindowsHostView.m')==BASELINE_VIRTUAL_SHA,'Historical baseline source changed')
        wiring_log=work/'source-wiring.log'
        with wiring_log.open('x') as stream:
            wiring=subprocess.run([sys.executable,'-B',root/'tests/test_guest_host_attachment.py'],cwd=work,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=stream,stderr=subprocess.STDOUT,timeout=60)
        if wiring.returncode:print(wiring_log.read_text()[-16000:],file=sys.stderr,flush=True)
        require(wiring.returncode==0 and re.search(r'^OK\s*$',wiring_log.read_text(),re.M) is not None and 'skipped' not in wiring_log.read_text(),'Production host wiring controls failed or skipped')
        report['wiring_test_sha256']=sha(root/'tests/test_guest_host_attachment.py');report['wiring_log_sha256']=sha(wiring_log)
        generated=work/'generated'
        (work/'prepare.log').write_text(command([sys.executable,'-B',HERE/'attachment/prepare.py','--source',root,'--expected-commit',manifest()['commit'],'--output',generated]))
        inventory_text=command(['xcrun','simctl','list','-j']);(work/'simulator-inventory.json').write_text(inventory_text)
        catalog=read(work/'simulator-inventory.json');sdk_version=command(['xcrun','--sdk','iphonesimulator','--show-sdk-version']).strip();sdk=command(['xcrun','--sdk','iphonesimulator','--show-sdk-path']).strip()
        runtime,device=available_device(catalog,sdk_version)
        report['simulator_selection']=selection_evidence(inventory_text,sdk_version,sdk,runtime,device)
        architecture=platform.machine();require(architecture in ('arm64','x86_64'),'Unsupported simulator architecture');target=architecture+'-apple-ios16.0-simulator'
        objects=[]
        for name in ('ProductionVirtualWindowsHostView','BaselineVirtualWindowsHostView','GuestBoundaryStubs'):
            obj=generated/(name+'.o');objects.append(obj)
            (work/(name+'-compile.log')).write_text(command(['xcrun','--sdk','iphonesimulator','clang','-fobjc-arc','-fblocks','-fmodules','-isysroot',sdk,'-target',target,'-c',generated/(name+'.m'),'-o',obj],timeout=180))
        app=work/'MultitaskAttachment.app';app.mkdir();bundle='org.nrg.multitask-attachment'
        (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':bundle,'CFBundleName':'MultitaskAttachment','CFBundleExecutable':'MultitaskAttachment','CFBundlePackageType':'APPL','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'16.0','LSRequiresIPhoneOS':True,'UIDeviceFamily':[1],'UILaunchScreen':{},'UIApplicationSceneManifest':{'UIApplicationSupportsMultipleScenes':True}}))
        (work/'swift-compile.log').write_text(command(['xcrun','--sdk','iphonesimulator','swiftc','-swift-version','5','-parse-as-library','-sdk',sdk,'-target',target,'-import-objc-header',generated/'GuestBoundaryStubs.h',generated/'ProductionLanguageBridge.swift',generated/'ProductionHostSelection.swift',generated/'AttachmentFixture.swift',*objects,'-o',app/'MultitaskAttachment'],timeout=180))
        (work/'codesign.log').write_text(command(['/usr/bin/codesign','--force','--sign','-',app]))
        udid=command(['xcrun','simctl','create','NRG-Multitask-'+os.environ['GITHUB_RUN_ID'],device['identifier'],runtime['identifier']]).strip();require(re.fullmatch('[A-Fa-f0-9-]{36}',udid),'Invalid owned simulator')
        command(['xcrun','simctl','boot',udid]);wait_owned_simulator_boot(udid,work);verify_boot_readiness(work/'boot-readiness.json',udid)
        report['simulator']={'runtime':runtime,'device_type':device,'udid':udid,'sdk':sdk}
        verify_selection(report)
        command(['xcrun','simctl','install',udid,app])
        container=Path(command(['xcrun','simctl','get_app_container',udid,bundle,'data']).strip()).resolve(strict=True)
        result=container/'Documents/attachment-result.json';require(not result.exists(),'Fresh fixture result required')
        (work/'launch.log').write_text(command(['xcrun','simctl','launch','--terminate-running-process',udid,bundle]))
        deadline=time.monotonic()+60
        while not result.is_file() and time.monotonic()<deadline:time.sleep(.2)
        require(result.is_file() and not result.is_symlink(),'Attachment fixture did not finish')
        retained=work/'simulator-result.json';retained.write_bytes(result.read_bytes());validate(read(retained))
        screenshots={}
        for name in SCREENSHOTS:
            image=container/'Documents'/name;require(image.is_file() and not image.is_symlink(),'Missing synthetic screenshot')
            payload=image.read_bytes();require(payload.startswith(b'\x89PNG\r\n\x1a\n') and 0<len(payload)<=20000000,'Invalid synthetic screenshot')
            (work/name).write_bytes(payload);screenshots[name]=sha(work/name)
        require(inventory(root)==before,'Source changed during observations')
        report.update(status='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH',result_sha256=sha(retained),generated_source_receipt_sha256=sha(generated/'source-receipt.json'),unchanged_bridge_sha256=UNCHANGED_BRIDGE_HASHES,historical_virtual_source_sha256=BASELINE_VIRTUAL_SHA,production_source_sha256={name:sha(root/name) for name in PRODUCTION_FILES},simulator_execution=True,screenshot_sha256=screenshots)
    finally:
        if udid:
            for action in ('shutdown','delete'):
                try:command(['xcrun','simctl',action,udid],timeout=45)
                except subprocess.SubprocessError as error:
                    output_text=getattr(error,'output','') or type(error).__name__
                    if isinstance(output_text,bytes):output_text=output_text.decode('utf-8','replace')
                    (work/(action+'-cleanup.log')).write_text(output_text);report['status']='FAILED_SIMULATOR_CLEANUP'
        (output/'multitask-attachment.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    require(report['status']=='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH','Attachment observation failed')
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();run(args.source,args.output)
