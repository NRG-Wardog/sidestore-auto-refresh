#!/usr/bin/env python3
"""Observe unchanged real UIKit attachment behavior; this is not guest qualification."""
import argparse,json,os,platform,plistlib,re,subprocess,sys,time
from pathlib import Path
from contract import command,identity,inventory,manifest,read,require,sha
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'language-switch'))
from run_language_switch_tests import wait_owned_simulator_boot,verify_boot_readiness
from boot_evidence import available_device,selection_evidence,verify_selection
LABELS=['swiftui-no-bridge-en','swiftui-no-bridge-he','swiftui-bridge-en','swiftui-bridge-he','negative-opaque-sibling']
BASELINE_HASHES={'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift': 'b2c1162a1667ae277baff498148c94bcf7dfb058ae2af6f6f833d553db3e87d9', 'MultitaskSupport/VirtualWindowsHostView.m': '3b31573f5ef5cf7d8d7ceb0ae72869eca97f52609957997174a4d820863bd785', 'MultitaskSupport/MultitaskDockView.swift': '0ac3c5185a6052c88960ada87f877e426f898dc3e7cec2b022c8ae0996b4432d'}

def validate(data):
    require(data['schema']=='multitask-attachment-baseline-v1' and data['status']=='OBSERVED_NOT_PRODUCT_QUALIFICATION','Wrong observation scope')
    require(data['physical_guest_execution'] is False,'Fixture cannot claim physical guest execution')
    records=data['records'];require(type(records) is list and [r['case'] for r in records]==LABELS,'Attachment observation closure differs')
    for row in records:
        require(row['same_parent_as_production'] is True,'Fixture attachment differs from production')
        for name in ('marker_hit','overlay_attached_to_window','first_child_interactive','first_child_hidden'):require(type(row[name]) is bool,'Invalid observation type')
        require(type(row['host_child_count']) is int and row['host_child_count']>=1,'Missing host hierarchy')
    require(records[-1]['marker_hit'] is False,'Opaque-sibling negative control did not fail as expected')
    return data

def run(root,output):
    root=root.resolve(strict=True);require(output.is_dir() and not output.is_symlink(),'Owned evidence directory required');output=output.resolve(strict=True)
    work=output/'multitask-attachment';require(not work.exists(),'Fresh attachment evidence required');work.mkdir()
    before=inventory(root);udid=None
    report={'schema':'multitask-attachment-ci-v1','status':'FAILED','identity':identity(),'source':manifest(),'source_inventory':before,'physical_device_execution':False,'real_extension_launch':False}
    try:
        require(platform.system()=='Darwin','UIKit fixture requires installed Apple simulator')
        require(BASELINE_HASHES and all(sha(root/name)==digest for name,digest in BASELINE_HASHES.items()),'Baseline attachment/language source changed')
        generated=work/'generated'
        (work/'prepare.log').write_text(command([sys.executable,'-B',HERE/'attachment/prepare.py','--source',root,'--expected-commit',manifest()['commit'],'--output',generated]))
        inventory_text=command(['xcrun','simctl','list','-j']);(work/'simulator-inventory.json').write_text(inventory_text)
        catalog=read(work/'simulator-inventory.json');sdk_version=command(['xcrun','--sdk','iphonesimulator','--show-sdk-version']).strip();sdk=command(['xcrun','--sdk','iphonesimulator','--show-sdk-path']).strip()
        runtime,device=available_device(catalog,sdk_version)
        report['simulator_selection']=selection_evidence(inventory_text,sdk_version,sdk,runtime,device)
        architecture=platform.machine();require(architecture in ('arm64','x86_64'),'Unsupported simulator architecture');target=architecture+'-apple-ios16.0-simulator'
        objects=[]
        for name in ('ProductionVirtualWindowsHostView','GuestBoundaryStubs'):
            obj=generated/(name+'.o');objects.append(obj)
            (work/(name+'-compile.log')).write_text(command(['xcrun','--sdk','iphonesimulator','clang','-fobjc-arc','-fblocks','-isysroot',sdk,'-target',target,'-c',generated/(name+'.m'),'-o',obj],timeout=180))
        app=work/'MultitaskAttachment.app';app.mkdir();bundle='org.nrg.multitask-attachment'
        (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':bundle,'CFBundleName':'MultitaskAttachment','CFBundleExecutable':'MultitaskAttachment','CFBundlePackageType':'APPL','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'16.0','LSRequiresIPhoneOS':True,'UIDeviceFamily':[1],'UILaunchScreen':{},'UIApplicationSceneManifest':{'UIApplicationSupportsMultipleScenes':True}}))
        (work/'swift-compile.log').write_text(command(['xcrun','--sdk','iphonesimulator','swiftc','-parse-as-library','-sdk',sdk,'-target',target,'-import-objc-header',generated/'GuestBoundaryStubs.h',generated/'ProductionLanguageBridge.swift',generated/'AttachmentFixture.swift',*objects,'-o',app/'MultitaskAttachment'],timeout=180))
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
        require(inventory(root)==before,'Source changed during observations')
        report.update(status='OBSERVED_NOT_PRODUCT_QUALIFICATION',result_sha256=sha(retained),generated_source_receipt_sha256=sha(generated/'source-receipt.json'),baseline_source_sha256=BASELINE_HASHES,simulator_execution=True)
    finally:
        if udid:
            for action in ('shutdown','delete'):
                try:command(['xcrun','simctl',action,udid],timeout=45)
                except subprocess.SubprocessError as error:
                    output_text=getattr(error,'output','') or type(error).__name__
                    if isinstance(output_text,bytes):output_text=output_text.decode('utf-8','replace')
                    (work/(action+'-cleanup.log')).write_text(output_text);report['status']='FAILED_SIMULATOR_CLEANUP'
        (output/'multitask-attachment.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    require(report['status']=='OBSERVED_NOT_PRODUCT_QUALIFICATION','Attachment observation failed')
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();run(args.source,args.output)
