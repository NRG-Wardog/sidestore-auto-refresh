#!/usr/bin/env python3
"""Require actual diagnostic recorder, copy policy and iOS Swift bridge qualification."""
import argparse,json,os,platform,subprocess,sys
from pathlib import Path
from contract import identity,inventory,manifest,read,require,sha
from diagnostics_contract import EXPECTED_SOURCES,EXPECTED_LOGS,validate

def run(root,output):
    root=root.resolve(strict=True);require(output.is_dir() and not output.is_symlink(),'Owned evidence directory required');output=output.resolve(strict=True)
    work=output/'guest-launch-diagnostics';require(not work.exists(),'Fresh diagnostic evidence required');work.mkdir()
    before=inventory(root)
    report={'schema':'multitask-diagnostics-ci-v1','status':'FAILED','identity':identity(),'source':manifest(),'source_inventory':before,'physical_device_execution':False,'real_extension_launch':False}
    try:
        require(platform.system()=='Darwin','Actual Foundation/Swift diagnostic fixture requires macOS')
        log=work/'driver.log'
        with log.open('x') as stream:
            result=subprocess.run([sys.executable,'-B',root/'tests/run_guest_launch_diagnostics.py','--require-native','--output',work/'fixture'],cwd=work,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=stream,stderr=subprocess.STDOUT,timeout=240)
        if result.returncode:print(log.read_text()[-16000:],file=sys.stderr,flush=True)
        require(result.returncode==0,'Diagnostic native runtime/typecheck failed')
        fixture=work/'fixture/report.json';require(fixture.is_file() and not fixture.is_symlink(),'Missing diagnostic receipt')
        data=validate(read(fixture))
        for name in EXPECTED_SOURCES:
            path=root/name;require(path.is_file() and not path.is_symlink() and sha(path)==data['source_sha256'][name],'Diagnostic production source differs')
        require(inventory(root)==before,'Source changed during diagnostic execution')
        require(EXPECTED_LOGS,'Diagnostic native log contract missing')
        logs={name:sha(fixture.parent/name) for name in EXPECTED_LOGS}
        report.update(status='PASS_ACTUAL_DIAGNOSTIC_BOUNDARIES_NOT_DEVICE',fixture_sha256=sha(fixture),driver_log_sha256=sha(log),native_log_sha256=logs)
    finally:(output/'guest-launch-diagnostics.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();run(args.source,args.output)
