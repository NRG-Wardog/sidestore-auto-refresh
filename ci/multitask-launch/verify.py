#!/usr/bin/env python3
"""Independently replay focused evidence; observations never imply a working guest."""
import argparse
from pathlib import Path
from contract import identity,inventory,manifest,read,require,sha
import run_attachment,run_completion,diagnostics_contract
from boot_evidence import verify_selection
from run_language_switch_tests import verify_boot_readiness

def verify(source,evidence,expected_identity=None):
    source=source.resolve(strict=True);evidence=evidence.resolve(strict=True)
    expected=identity() if expected_identity is None else expected_identity
    tracked=inventory(source);owner=manifest()
    acquired=read(evidence/'source.json')
    require(acquired['schema']=='multitask-source-acquisition-v1' and acquired['source']==owner and acquired['identity']==expected and acquired['inventory']==tracked,'Source acquisition evidence differs')
    completion=read(evidence/'guest-launch-completion.json');attachment=read(evidence/'multitask-attachment.json');diagnostics=read(evidence/'guest-launch-diagnostics.json')
    for report in (completion,attachment,diagnostics):
        require(report['identity']==expected and report['source']==owner and report['source_inventory']==tracked,'Focused source/run ownership differs')
        require(report['physical_device_execution'] is False and report['real_extension_launch'] is False,'Invalid execution claim')
    require(completion['schema']=='multitask-completion-ci-v1' and completion['status']=='PASS_ACTUAL_OBJC_BOUNDARY_NOT_DEVICE','Completion runtime did not qualify')
    fixture=evidence/'guest-launch-completion/fixture/report.json';require(sha(fixture)==completion['fixture_sha256'],'Completion fixture evidence changed')
    require(sha(evidence/'guest-launch-completion/driver.log')==completion['driver_log_sha256'],'Completion log changed')
    run_completion.validate(read(fixture),source)
    expected_logs={'compile.log','runtime.log','nil-guard-compile.log','nil-guard-runtime.log','synchronous-interruption-compile.log','synchronous-interruption-runtime.log'}
    require(set(completion['native_log_sha256'])==expected_logs,'Native log closure differs')
    for name,digest in completion['native_log_sha256'].items():require(sha(fixture.parent/name)==digest,'Native runtime/compiler log changed')
    require('GUEST_LAUNCH_COMPLETION_PASS' in (fixture.parent/'runtime.log').read_text(),'Native runtime success marker missing')
    for name,row in run_completion.EXPECTED_COUNTERFACTUALS.items():require(row['expected_failure'] in (fixture.parent/(name+'-runtime.log')).read_text(),'Counterfactual runtime rejection missing')
    require(diagnostics['schema']=='multitask-diagnostics-ci-v1' and diagnostics['status']=='PASS_ACTUAL_DIAGNOSTIC_BOUNDARIES_NOT_DEVICE','Diagnostic runtime did not qualify')
    diagnostic_fixture=evidence/'guest-launch-diagnostics/fixture/report.json'
    require(sha(diagnostic_fixture)==diagnostics['fixture_sha256'] and sha(evidence/'guest-launch-diagnostics/driver.log')==diagnostics['driver_log_sha256'],'Diagnostic report/output changed')
    diagnostic_data=diagnostics_contract.validate(read(diagnostic_fixture))
    for name in diagnostics_contract.EXPECTED_SOURCES:require(sha(source/name)==diagnostic_data['source_sha256'][name],'Diagnostic source bytes differ')
    require(set(diagnostics['native_log_sha256'])==set(diagnostics_contract.EXPECTED_LOGS),'Diagnostic native log closure differs')
    for name,digest in diagnostics['native_log_sha256'].items():require(sha(diagnostic_fixture.parent/name)==digest,'Diagnostic compiler/runtime log changed')
    require('GUEST_LAUNCH_DIAGNOSTICS_PASS' in (diagnostic_fixture.parent/'runtime.log').read_text(),'Diagnostic runtime success marker missing')
    require('GUEST_DIAGNOSTIC_COPY_PASS' in (diagnostic_fixture.parent/'copy-runtime.log').read_text(),'Diagnostic copy runtime success marker missing')
    require(attachment['schema']=='multitask-attachment-ci-v2' and attachment['status']=='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH' and attachment['simulator_execution'] is True,'Attachment observations incomplete')
    work=evidence/'multitask-attachment'
    require(sha(source/'tests/test_guest_host_attachment.py')==attachment['wiring_test_sha256'] and sha(work/'source-wiring.log')==attachment['wiring_log_sha256'],'Host wiring test source/output differs')
    require(sha(work/'simulator-result.json')==attachment['result_sha256'],'Attachment result changed')
    run_attachment.validate(read(work/'simulator-result.json'))
    require(set(attachment['screenshot_sha256'])==set(run_attachment.SCREENSHOTS),'Screenshot hash closure differs')
    for name,digest in attachment['screenshot_sha256'].items():require(sha(work/name)==digest,'Synthetic screenshot bytes differ')
    require(sha(work/'generated/source-receipt.json')==attachment['generated_source_receipt_sha256'],'Attachment extraction receipt changed')
    prepared=read(work/'generated/source-receipt.json');require(prepared['source_commit']==owner['commit'],'Prepared source identity differs')
    require(prepared['schema']=='multitask-attachment-source-v2' and prepared['counterfactual_baseline_commit']=='dcb98b6eeff937cedffd5e0c30aa286ada14a618','Historical extraction identity differs')
    fixture_names=set(run_attachment.FIXTURE_FILES)
    require(set(prepared['source_files'])==set(run_attachment.PRODUCTION_FILES)|fixture_names,'Attachment source extraction closure differs')
    for name in fixture_names:require(sha(run_attachment.HERE/'attachment'/name)==prepared['source_files'][name],'Attachment fixture/extractor source differs')
    require(attachment['unchanged_bridge_sha256']==run_attachment.UNCHANGED_BRIDGE_HASHES,'Unchanged language bridge differs')
    require(attachment['historical_virtual_source_sha256']==run_attachment.BASELINE_VIRTUAL_SHA and sha(run_attachment.HERE/'attachment/baseline/VirtualWindowsHostView.m')==run_attachment.BASELINE_VIRTUAL_SHA,'Historical virtual implementation differs')
    require(set(attachment['production_source_sha256'])==set(run_attachment.PRODUCTION_FILES),'Production helper source closure differs')
    for name in run_attachment.PRODUCTION_FILES:require(sha(source/name)==prepared['source_files'][name]==attachment['production_source_sha256'][name],'Production helper/header/bridge bytes differ')
    for name,digest in run_attachment.UNCHANGED_BRIDGE_HASHES.items():require(sha(source/name)==digest,'Unchanged language bridge bytes differ')
    verify_selection(attachment)
    require((work/'simulator-inventory.json').read_text()==attachment['simulator_selection']['inventory_json'],'Simulator inventory changed')
    verify_boot_readiness(work/'boot-readiness.json',attachment['simulator']['udid'])
    return {'status':'PASS_COMPLETION_ATTACHMENT_DIAGNOSTICS_NOT_DEVICE','source':owner,'identity':expected}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();print(verify(a.source,a.evidence))
