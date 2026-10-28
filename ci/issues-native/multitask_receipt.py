"""Bind same-run guest boundary/UIKit-helper qualification to packaged bytes.
No physical guest launch is claimed. Portable verification executes no source code.
"""
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
SCHEMA='same-run-multitask-qualification-v1'
STATUS='PASS_GUEST_LAUNCH_BOUNDARIES_NOT_DEVICE'
FILES={'completion':'guest-launch-completion.json','completion_fixture':'guest-launch-completion/fixture/report.json','attachment':'multitask-attachment.json','attachment_fixture':'multitask-attachment/simulator-result.json','diagnostics':'guest-launch-diagnostics.json','diagnostics_fixture':'guest-launch-diagnostics/fixture/report.json'}
COMPLETION_CASES=['nil-extension-no-error','nil-extension-with-error','nonnull-extension-with-error','successful-lookup','wiring-before-failure','synchronous-interruption-before-wiring','duplicate-interruption','late-completion-after-cleanup','lookup-error-preserved-after-cleanup','cancellation-before-callback-wiring','late-begin-after-cleanup-no-registration','duplicate-terminal-signals']
COMPLETION_INPUTS={'MultitaskSupport/AppSceneViewController.m','MultitaskSupport/DecoratedAppSceneViewController.m','tests/guest_launch_completion/fixture.m','tests/run_guest_launch_completion.py','LiveContainerSwiftUI/Models/LCAppModel.swift','LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift'}
COUNTERFACTUALS={'nil-guard':{'rejected':True,'expected_failure':'FAIL: lookup result'},'synchronous-interruption':{'rejected':True,'expected_failure':'FAIL: synchronous interruption settled before constructor return'}}
def require(ok,why):
    if not ok:raise ValueError(why)
def pairs(rows):
    out={}
    for key,value in rows:require(key not in out,'Duplicate receipt key');out[key]=value
    return out
def read(path):
    require(path.is_file() and not path.is_symlink(),'Missing or linked receipt '+str(path));return json.loads(path.read_text(),object_pairs_hook=pairs)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def identity():return {k:os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]}
def verify_portable(value,graph,expected_identity):
    require(set(value)=={'schema','status','identity','source_graph','source','source_inventory_sha256','physical_device_execution','real_extension_launch','reports','artifact_sha256','embedded_sha256','verifier_sha256'},'Guest qualification fields differ')
    require(value['schema']==SCHEMA and value['status']==STATUS,'Guest qualification did not pass')
    require(value['identity']==expected_identity and value['source_graph']==graph,'Guest run/source graph differs')
    require(re.fullmatch('[0-9a-f]{40}',expected_identity['workflow_commit']) and all(re.fullmatch('[1-9][0-9]*',expected_identity[k]) for k in ('run_id','run_attempt')),'Invalid guest identity')
    require(value['physical_device_execution'] is False and value['real_extension_launch'] is False,'Invalid guest execution claim')
    source=value['source'];require(source['repository']=='NRG-Wardog/LiveContainer' and source['commit']==graph['LiveContainer'][1],'Guest source is not native LC pin')
    require(source['schema']=='multitask-launch-source-v1','Guest source schema differs')
    reports=value['reports'];require(set(reports)==set(FILES) and set(value['artifact_sha256'])==set(FILES.values()) and set(value['embedded_sha256'])==set(FILES),'Guest evidence closure differs')
    for key,report in reports.items():
        require(value['embedded_sha256'][key]==canonical(report),'Embedded guest evidence changed')
        require(re.fullmatch('[0-9a-f]{64}',value['artifact_sha256'][FILES[key]]) is not None,'Invalid guest artifact hash')
    c=reports['completion'];a=reports['attachment'];f=reports['completion_fixture']
    for report in (c,a,reports['diagnostics']):
        require(report['identity']==expected_identity and report['source']==source,'Guest phase identity differs')
        require(report['physical_device_execution'] is False and report['real_extension_launch'] is False,'Invalid phase execution claim')
        require(canonical(report['source_inventory'])==value['source_inventory_sha256'],'Guest source inventory differs')
    require(c['schema']=='multitask-completion-ci-v1' and c['status']=='PASS_ACTUAL_OBJC_BOUNDARY_NOT_DEVICE','Guest completion did not qualify')
    require(c['fixture_sha256']==value['artifact_sha256'][FILES['completion_fixture']],'Completion fixture not bound')
    require(f['schema']=='guest-launch-completion-v1' and f['status']=='PASS' and f['native_compiled'] is True and f['native_executed'] is True,'Actual guest completion execution missing')
    require(f['cases']==COMPLETION_CASES and f['counterfactuals']==COUNTERFACTUALS and all(v['rejected'] is True for v in f['counterfactuals'].values()),'Guest completion controls differ')
    require(set(f['source_sha256'])==COMPLETION_INPUTS,'Guest completion source closure differs')
    for name,digest in f['source_sha256'].items():require(c['source_inventory'].get(name,{}).get('sha256')==digest,'Guest completion input differs from tracked source')
    require(a['schema']=='multitask-attachment-ci-v2' and a['status']=='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH' and a['simulator_execution'] is True,'Actual UIKit helper qualification missing')
    require(a['result_sha256']==value['artifact_sha256'][FILES['attachment_fixture']],'UIKit helper result not bound')
    import attachment_contract,diagnostics_contract
    require(value['verifier_sha256']=={'multitask_receipt.py':sha(Path(__file__)), 'attachment_contract.py':sha(Path(attachment_contract.__file__)), 'diagnostics_contract.py':sha(Path(diagnostics_contract.__file__))},'Guest portable verifier bytes differ')
    require(set(a['production_source_sha256'])==set(attachment_contract.PRODUCTION_FILES),'UIKit helper source closure differs')
    for name,digest in a['production_source_sha256'].items():require(a['source_inventory'].get(name,{}).get('sha256')==digest,'UIKit helper differs from tracked source')
    require(a['wiring_test_sha256']==a['source_inventory'].get('tests/test_guest_host_attachment.py',{}).get('sha256'),'Guest host wiring test source differs')
    require(a['unchanged_bridge_sha256']==attachment_contract.UNCHANGED_BRIDGE_HASHES and a['historical_virtual_source_sha256']==attachment_contract.BASELINE_VIRTUAL_SHA,'Guest historical counterfactual changed')
    require(set(a['screenshot_sha256'])==set(attachment_contract.SCREENSHOTS) and all(re.fullmatch('[0-9a-f]{64}',h) is not None for h in a['screenshot_sha256'].values()),'Guest synthetic screenshot hash closure differs')
    attachment_contract.validate(reports['attachment_fixture'])
    d=reports['diagnostics'];df=reports['diagnostics_fixture']
    require(d['schema']=='multitask-diagnostics-ci-v1' and d['status']=='PASS_ACTUAL_DIAGNOSTIC_BOUNDARIES_NOT_DEVICE','Actual guest diagnostic qualification missing')
    require(d['fixture_sha256']==value['artifact_sha256'][FILES['diagnostics_fixture']],'Guest diagnostic fixture not bound')
    diagnostics_contract.validate(df)
    require(set(df['source_sha256'])==set(diagnostics_contract.EXPECTED_SOURCES),'Guest diagnostic source closure differs')
    for name,digest in df['source_sha256'].items():require(d['source_inventory'].get(name,{}).get('sha256')==digest,'Guest diagnostic input differs from tracked source')
    return value

def focused_dir():return Path(__file__).resolve().parents[1]/'multitask-launch'
def invoke(*args):subprocess.run([sys.executable,'-B',*[str(x) for x in args]],check=True)
def verify_local(path,sources,evidence,graph,expected_identity):
    focused=focused_dir();sys.path.insert(0,str(focused))
    invoke(focused/'verify.py','--source',sources/'LiveContainer','--evidence',evidence)
    value=verify_portable(read(path),graph,expected_identity)
    require(read(focused/'source.json')==value['source'],'Guest manifest changed')
    for key,name in FILES.items():
        require(sha(evidence/name)==value['artifact_sha256'][name] and read(evidence/name)==value['reports'][key],'Guest phase evidence changed')
    return value

def run(sources,evidence,graph,output):
    sources=sources.resolve(strict=True);require(not evidence.is_symlink(),'Linked guest evidence root');evidence=evidence.resolve();require(not evidence.exists(),'Fresh guest evidence required');evidence.mkdir(parents=True)
    focused=focused_dir();owner=read(focused/'source.json');require(owner['commit']==graph['LiveContainer'][1],'Focused/native LC pins differ')
    # Reuse the already acquired and source-gated native checkout, never acquire another revision.
    code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from contract import inventory,manifest,identity;root=Path(sys.argv[2]);out=Path(sys.argv[3]);out.write_text(json.dumps({'schema':'multitask-source-acquisition-v1','source':manifest(),'identity':identity(),'inventory':inventory(root),'acquisition_method':'existing_native_exact_graph'},sort_keys=True,indent=2)+'\\n')"
    subprocess.run([sys.executable,'-B','-c',code,str(focused),str(sources/'LiveContainer'),str(evidence/'source.json')],check=True)
    invoke(focused/'run_completion.py','--source',sources/'LiveContainer','--output',evidence)
    invoke(focused/'run_attachment.py','--source',sources/'LiveContainer','--output',evidence)
    invoke(focused/'run_diagnostics.py','--source',sources/'LiveContainer','--output',evidence)
    invoke(focused/'verify.py','--source',sources/'LiveContainer','--evidence',evidence)
    reports={key:read(evidence/name) for key,name in FILES.items()}
    value={'schema':SCHEMA,'status':STATUS,'identity':identity(),'source_graph':graph,'source':owner,'source_inventory_sha256':canonical(reports['completion']['source_inventory']),'physical_device_execution':False,'real_extension_launch':False,'reports':reports,'artifact_sha256':{name:sha(evidence/name) for name in FILES.values()},'embedded_sha256':{key:canonical(value) for key,value in reports.items()}}
    sys.path.insert(0,str(focused));import attachment_contract,diagnostics_contract
    value['verifier_sha256']={'multitask_receipt.py':sha(Path(__file__)), 'attachment_contract.py':sha(Path(attachment_contract.__file__)), 'diagnostics_contract.py':sha(Path(diagnostics_contract.__file__))}
    verify_portable(value,graph,identity());output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    verify_local(output,sources,evidence,graph,identity())
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','verify']);p.add_argument('--sources',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    from verify_candidate_graph import load_candidate_graph
    graph=load_candidate_graph()['owners']
    if a.action=='run':run(a.sources,a.evidence,graph,a.output)
    else:verify_local(a.output,a.sources,a.evidence,graph,identity())
