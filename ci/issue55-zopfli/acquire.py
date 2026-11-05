"""Fixed approved compressor source and green input artifact, no alternate URLs/pins."""
import hashlib,json,os,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ci/issues-native'))
from recover_native_artifacts import API
REPO='NRG-Wardog/sidestore-auto-refresh'
RUN=40260607921
HEAD='e71e384f57deebb0fa5430c30269ea5acca72abe'
ARTIFACT=12624493282
OUTER_SHA='daacc4c0402cc34f662f940de7f99db8a3788fb5b890f242257db85a425a1eff'
IPA_SHA='e16afa534eb33e94c126b2b899240cb9894fd932e4cc36627efda033b08bb237'
SOURCE_COMMIT='df1517dd8518113a245e4d203a04b8a0e02ab493'
SOURCE_SHA='ae5c3afb20156a52f2086277fd102a08d902c36d84db7eff74cc7e5e19b020c6'
MEMBERS={'SHA256SUMS','native-artifact-acquisition.json','LiveContainer-SideStore-issues-experimental.ipa','native-receipt.json','provenance.json','openssl-local-stage.json','russian-localization.json','size-optimization.json','unsigned-widget-receipt.json'}
def require(ok,message):
 if not ok:raise ValueError(message)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def metadata(run,artifact):
 require(run.get('id')==RUN and run.get('run_attempt')==1 and run.get('head_sha')==HEAD and run.get('status')=='completed' and run.get('conclusion')=='success','original run not exact green')
 require(run.get('path')=='.github/workflows/recover-native-package.yml' and run.get('repository',{}).get('full_name')==REPO and run.get('head_repository',{}).get('full_name')==REPO,'original run owner/workflow differs')
 require(artifact.get('name')=='recovered-experimental-issues-'+HEAD+'-'+str(RUN)+'-1' and artifact.get('id')==ARTIFACT and artifact.get('size_in_bytes')==25471679 and artifact.get('expired') is False and artifact.get('workflow_run',{}).get('id')==RUN and artifact.get('workflow_run',{}).get('head_sha')==HEAD,'original artifact identity differs')
 if artifact.get('digest') is not None:require(artifact['digest']=='sha256:'+OUTER_SHA,'artifact digest differs')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def acquire(output):
 require(output.parent.resolve(strict=True)==output.parent and not output.is_symlink() and (not output.exists() or output.is_dir()),'canonical acquisition root required')
 require(all(not (output/name).exists() and not (output/name).is_symlink() for name in ('candidate-artifact.zip','source.tar.gz','acquisition.json')),'refusing existing acquisition outputs')
 api=API(os.environ['GH_TOKEN']);run=api.json(f'/repos/{REPO}/actions/runs/{RUN}');artifact=api.json(f'/repos/{REPO}/actions/artifacts/{ARTIFACT}');metadata(run,artifact)
 output.mkdir(exist_ok=True);archive=output/'candidate-artifact.zip';api.download(ARTIFACT,archive)
 require(archive.stat().st_size==25471679 and sha(archive)==OUTER_SHA,'original artifact bytes differ')
 # Official source endpoint only; no GitHub token is sent, redirects/retries disabled.
 url='https://codeload.github.com/google/zopfli/tar.gz/'+SOURCE_COMMIT
 request=urllib.request.Request(url,headers={'User-Agent':'approved-zopfli-packaging-validation'})
 try:
  with urllib.request.build_opener(NoRedirect).open(request,timeout=60) as response:
   require(response.status==200 and response.geturl()==url,'source download redirect/status differs')
   data=response.read(1000001)
 except Exception as exc:
  raise ValueError('Official source acquisition failed; no retry or alternate route') from None
 require(len(data)==249064 and hashlib.sha256(data).hexdigest()==SOURCE_SHA,'approved source archive differs')
 (output/'source.tar.gz').write_bytes(data)
 report={'schema':'fixed-zopfli-input-acquisition-v1','original_run':RUN,'original_commit':HEAD,'artifact_id':ARTIFACT,'artifact_sha256':OUTER_SHA,'original_ipa_sha256':IPA_SHA,'source_commit':SOURCE_COMMIT,'source_archive_sha256':SOURCE_SHA,'current_identity':{k:os.environ[k] for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')},'native_app_execution':False}
 (output/'acquisition.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 return report
if __name__=='__main__':
 require(len(sys.argv)==1,'arbitrary inputs not accepted')
 report=acquire(Path.cwd().resolve(strict=True)/'workspace')
 Path('zopfli-evidence').mkdir(exist_ok=True)
 Path('zopfli-evidence/acquisition.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
