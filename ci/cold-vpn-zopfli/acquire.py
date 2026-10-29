"""Acquire only the manifest-bound successful issue56 native artifact and approved source."""
import json,os,sys,urllib.request,zipfile
from pathlib import Path
from contract import manifest,require,SHA,PIN,SOURCE_SHA,REPO,WORKFLOW,IPA
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ci/issues-native'))
from recover_native_artifacts import API
sys.path.insert(0,str(ROOT/'ci/language-switch'))
from boot_evidence import verify_language_boot
RECEIPTS={'provenance.json','native-receipt.json','size-optimization.json','russian-localization.json','unsigned-widget-receipt.json','openssl-local-stage.json','issue56-startup-tests.json','language-switch-tests.json','cold-vpn-tests.json','multitask-launch-tests.json','startup-upgrade-tests.json','refresh-truth-tests.json'}
def validate_metadata(m,run,artifact):
    require(run.get('id')==m['run_id'] and run.get('run_attempt')==m['run_attempt'] and run.get('head_sha')==m['commit'] and run.get('status')=='completed' and run.get('conclusion')=='success','native run must be exact completed green')
    require(run.get('path')==WORKFLOW and run.get('repository',{}).get('full_name')==REPO and run.get('head_repository',{}).get('full_name')==REPO,'native workflow or ownership mismatch')
    expected=f"experimental-issues-{m['commit']}-{m['run_id']}-{m['run_attempt']}"
    require(artifact.get('id')==m['artifact_id'] and artifact.get('name')==expected and artifact.get('size_in_bytes')==m['artifact_bytes'] and artifact.get('expired') is False,'artifact identity mismatch')
    require(artifact.get('workflow_run',{}).get('id')==m['run_id'] and artifact['workflow_run'].get('head_sha')==m['commit'],'artifact source run mismatch')
    if artifact.get('digest') is not None: require(artifact['digest']=='sha256:'+m['artifact_sha256'],'artifact digest mismatch')
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def unpack(m,archive,out):
    require(archive.stat().st_size==m['artifact_bytes'] and SHA(archive.read_bytes())==m['artifact_sha256'],'artifact bytes mismatch')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        require(len(names)==len(set(names)) and set(names)==RECEIPTS|{IPA,'SHA256SUMS'},'unexpected native artifact closure')
        require(all(x.file_size<=64*1024**2 for x in z.infolist()) and sum(x.file_size for x in z.infolist())<=128*1024**2,'artifact extraction size bounds')
        require(z.testzip() is None,'artifact CRC')
        data=z.read(IPA)
        require(len(data)==m['ipa_bytes'] and SHA(data)==m['ipa_sha256'],'native IPA bytes mismatch')
        with (out/'original.ipa').open('xb') as handle: handle.write(data)
        for name in sorted(RECEIPTS):
            with (out/name).open('xb') as handle: handle.write(z.read(name))
    from contract import read_json
    p=read_json(out/'provenance.json')
    require(p.get('workflow_commit')==m['commit'] and str(p.get('native_run_id'))==str(m['run_id']) and str(p.get('native_run_attempt'))==str(m['run_attempt']),'package provenance lineage mismatch')
    require(p.get('ipa_bytes')==m['ipa_bytes'] and p.get('ipa_sha256')==m['ipa_sha256'],'package provenance bytes mismatch')
    require(p.get('issue56_startup_regression_sha256')==SHA((out/'issue56-startup-tests.json').read_bytes()),'startup regression receipt mismatch')
    require(p.get('language_switch_regression_sha256')==SHA((out/'language-switch-tests.json').read_bytes()),'language regression receipt mismatch')

    verify_language_boot(read_json(out/'language-switch-tests.json'))
    require(p.get('cold_vpn_regression_sha256')==SHA((out/'cold-vpn-tests.json').read_bytes()),'cold VPN regression receipt mismatch')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'issues-native'))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'multitask-launch'))
    from multitask_receipt import verify_portable
    guest=read_json(out/'multitask-launch-tests.json');native=read_json(out/'native-receipt.json')
    guest_sha=SHA((out/'multitask-launch-tests.json').read_bytes())
    require(p.get('multitask_launch_regression_sha256')==guest_sha and native['evidence_sha256'].get('multitask-launch-tests.json')==guest_sha,'guest qualification receipt mismatch')
    verify_portable(guest,native['requested_graph'],{'workflow_commit':m['commit'],'run_id':str(m['run_id']),'run_attempt':str(m['run_attempt'])})
    from startup_upgrade_receipt import verify_portable as verify_schema
    schema_sha=SHA((out/'startup-upgrade-tests.json').read_bytes())
    require(p.get('startup_upgrade_regression_sha256')==schema_sha and native['evidence_sha256'].get('startup-upgrade-tests.json')==schema_sha,'Core Data qualification receipt mismatch')
    verify_schema(read_json(out/'startup-upgrade-tests.json'),native['requested_graph'],{'workflow_commit':m['commit'],'run_id':str(m['run_id']),'run_attempt':str(m['run_attempt'])})
    from refresh_truth_receipt import verify_portable as verify_refresh_truth
    refresh_sha=SHA((out/'refresh-truth-tests.json').read_bytes())
    require(p.get('refresh_truth_regression_sha256')==refresh_sha and native['evidence_sha256'].get('refresh-truth-tests.json')==refresh_sha,'refresh truth qualification receipt mismatch')
    verify_refresh_truth(read_json(out/'refresh-truth-tests.json'),native['requested_graph'],{'workflow_commit':m['commit'],'run_id':str(m['run_id']),'run_attempt':str(m['run_attempt'])})

def main():
    require(len(sys.argv)==1,'arbitrary acquisition inputs forbidden')
    source=Path(__file__).resolve().parent
    m=manifest(source/'input-manifest.json')
    out=Path.cwd().resolve(strict=True)/'workspace'
    require(out.is_dir() and not out.is_symlink() and not any(out.iterdir()),'fresh canonical workspace required')
    api=API(os.environ['GH_TOKEN'])
    run=api.json(f"/repos/{REPO}/actions/runs/{m['run_id']}")
    artifact=api.json(f"/repos/{REPO}/actions/artifacts/{m['artifact_id']}")
    validate_metadata(m,run,artifact)
    archive=out/'candidate-artifact.zip';api.download(m['artifact_id'],archive);unpack(m,archive,out)
    url='https://codeload.github.com/google/zopfli/tar.gz/'+PIN
    request=urllib.request.Request(url,headers={'User-Agent':'approved-issue56-zopfli-qualification'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=60) as response:
            require(response.status==200 and response.geturl()==url,'source response mismatch')
            data=response.read(1000001)
    except Exception: raise ValueError('Approved source acquisition failed; no retries or alternate route') from None
    require(len(data)==249064 and SHA(data)==SOURCE_SHA,'approved source archive mismatch')
    with (out/'source.tar.gz').open('xb') as handle: handle.write(data)
    (out/'input-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
    report={'schema':'cold-vpn-zopfli-acquisition-v1','input':m,'source_commit':PIN,'source_archive_sha256':SOURCE_SHA,'packaging_identity':{key:os.environ[key] for key in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')},'native_execution':False}
    (out/'acquisition.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__': main()
