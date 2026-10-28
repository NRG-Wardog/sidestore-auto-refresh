#!/usr/bin/env python3
"""Execute the actual-source Objective-C launch boundary fixture. No app launch."""
import argparse,json,os,platform,re,subprocess,sys
from pathlib import Path
from contract import command,identity,inventory,manifest,read,require,sha
HERE=Path(__file__).resolve().parent
EXPECTED_CASES=('nil-extension-no-error', 'nil-extension-with-error', 'nonnull-extension-with-error', 'successful-lookup', 'wiring-before-failure', 'synchronous-interruption-before-wiring', 'duplicate-interruption', 'late-completion-after-cleanup', 'lookup-error-preserved-after-cleanup', 'cancellation-before-callback-wiring', 'late-begin-after-cleanup-no-registration', 'duplicate-terminal-signals')
EXPECTED_SOURCES=('MultitaskSupport/AppSceneViewController.m', 'MultitaskSupport/DecoratedAppSceneViewController.m', 'tests/guest_launch_completion/fixture.m', 'tests/run_guest_launch_completion.py', 'LiveContainerSwiftUI/Models/LCAppModel.swift', 'LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift')
EXPECTED_COUNTERFACTUALS={'nil-guard':{'rejected':True,'expected_failure':'FAIL: lookup result'},'synchronous-interruption':{'rejected':True,'expected_failure':'FAIL: synchronous interruption settled before constructor return'}}

def validate(data,root,*,validated_native_inventory=None):
    require(data['schema']=='guest-launch-completion-v1' and data['status']=='PASS','Completion fixture did not pass')
    require(data['native_compiled'] is True and data['native_executed'] is True,'Actual Objective-C execution required')
    require(EXPECTED_CASES and data['cases']==list(EXPECTED_CASES),'Completion runtime case closure differs')
    require(data['counterfactuals']==EXPECTED_COUNTERFACTUALS,'Counterfactual rejection evidence differs')
    require(all(row['rejected'] is True for row in data['counterfactuals'].values()),'Counterfactual rejection must be a boolean')
    hashes=data['source_sha256'];require(type(hashes) is dict and set(hashes)==set(EXPECTED_SOURCES),'Production source hash closure differs')
    tracked=inventory(root) if validated_native_inventory is None else validated_native_inventory
    for name,digest in hashes.items():
        require(type(name) is str and name in tracked,'Unknown source input')
        path=root/name;require(path.is_file() and not path.is_symlink() and sha(path)==digest,'Production fixture source bytes differ')
    return data

def run(root,output):
    root=root.resolve(strict=True);require(output.is_dir() and not output.is_symlink(),'Owned evidence directory required');output=output.resolve(strict=True)
    work=output/'guest-launch-completion';require(not work.exists(),'Fresh completion evidence required');work.mkdir()
    before=inventory(root)
    report={'schema':'multitask-completion-ci-v1','status':'FAILED','identity':identity(),'source':manifest(),'source_inventory':before,'physical_device_execution':False,'real_extension_launch':False}
    try:
        require(platform.system()=='Darwin','Native Objective-C fixture requires macOS')
        log=work/'driver.log'
        with log.open('x') as stream:
            result=subprocess.run([sys.executable,'-B',root/'tests/run_guest_launch_completion.py','--require-native','--output',work/'fixture'],cwd=work,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=stream,stderr=subprocess.STDOUT,timeout=180)
        if result.returncode:
            print(log.read_text()[-16000:],file=sys.stderr,flush=True)
        require(result.returncode==0,'Actual-source launch completion fixture failed')
        fixture=work/'fixture/report.json';require(fixture.is_file() and not fixture.is_symlink(),'Missing completion receipt')
        validate(read(fixture),root)
        require(inventory(root)==before,'Source inventory changed during execution')
        native_logs={name:sha(fixture.parent/name) for name in ('compile.log','runtime.log','nil-guard-compile.log','nil-guard-runtime.log','synchronous-interruption-compile.log','synchronous-interruption-runtime.log')}
        report.update(status='PASS_ACTUAL_OBJC_BOUNDARY_NOT_DEVICE',fixture_sha256=sha(fixture),driver_log_sha256=sha(log),native_log_sha256=native_logs)
    finally:(output/'guest-launch-completion.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();run(args.source,args.output)
