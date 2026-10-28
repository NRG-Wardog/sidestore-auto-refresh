#!/usr/bin/env python3
"""Independently replay focused evidence; observations never imply a working guest."""
import argparse
from pathlib import Path
from contract import identity,inventory,manifest,read,require,sha
import run_attachment,run_completion
from boot_evidence import verify_selection
from run_language_switch_tests import verify_boot_readiness

def verify(source,evidence,expected_identity=None):
    source=source.resolve(strict=True);evidence=evidence.resolve(strict=True)
    expected=identity() if expected_identity is None else expected_identity
    tracked=inventory(source);owner=manifest()
    acquired=read(evidence/'source.json')
    require(acquired['schema']=='multitask-source-acquisition-v1' and acquired['source']==owner and acquired['identity']==expected and acquired['inventory']==tracked,'Source acquisition evidence differs')
    completion=read(evidence/'guest-launch-completion.json');attachment=read(evidence/'multitask-attachment.json')
    for report in (completion,attachment):
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
    require(attachment['schema']=='multitask-attachment-ci-v1' and attachment['status']=='OBSERVED_NOT_PRODUCT_QUALIFICATION' and attachment['simulator_execution'] is True,'Attachment observations incomplete')
    work=evidence/'multitask-attachment'
    require(sha(work/'simulator-result.json')==attachment['result_sha256'],'Attachment result changed')
    run_attachment.validate(read(work/'simulator-result.json'))
    require(sha(work/'generated/source-receipt.json')==attachment['generated_source_receipt_sha256'],'Attachment extraction receipt changed')
    prepared=read(work/'generated/source-receipt.json');require(prepared['source_commit']==owner['commit'],'Prepared source identity differs')
    fixture_names={'GuestBoundaryStubs.h','GuestBoundaryStubs.m','AttachmentFixture.swift','prepare.py'}
    require(set(prepared['source_files'])==set(run_attachment.BASELINE_HASHES)|fixture_names,'Attachment source extraction closure differs')
    for name in fixture_names:require(sha(run_attachment.HERE/'attachment'/name)==prepared['source_files'][name],'Attachment fixture/extractor source differs')
    require(attachment['baseline_source_sha256']==run_attachment.BASELINE_HASHES,'Attachment baseline differs')
    for name,digest in run_attachment.BASELINE_HASHES.items():
        require(sha(source/name)==digest and prepared['source_files'][name]==digest,'Unchanged attachment baseline bytes differ')
    verify_selection(attachment)
    require((work/'simulator-inventory.json').read_text()==attachment['simulator_selection']['inventory_json'],'Simulator inventory changed')
    verify_boot_readiness(work/'boot-readiness.json',attachment['simulator']['udid'])
    return {'status':'PASS_BOUNDARY_AND_OBSERVATION_EVIDENCE_NOT_DEVICE','source':owner,'identity':expected}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();print(verify(a.source,a.evidence))
