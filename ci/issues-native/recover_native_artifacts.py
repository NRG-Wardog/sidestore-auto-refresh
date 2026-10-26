"""Acquire only fixed original native artifacts; no native execution or identity spoofing."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY='NRG-Wardog/sidestore-auto-refresh'
RUN=40245615437
HEAD='0205f33ff50a024d4e125b6b404e989911ac5eb2'
ARTIFACTS={
 'LiveContainer':{'id':12619830088,'sha256':'d63a9ec7d497299e2c9db38e21cbac290f8eb99e24fd9623a3ec9bb2fe91e25c','inner_sha256':'29d1d61ac7bcf7103b8c161321b15e06028c39587345175a34064a3f2349789c'},
 'SideStore':{'id':12619805132,'sha256':'2cc0b48014af9b3f77ca084048a9bd8e08db4716169cb6adf57fb36719d536ea','inner_sha256':'c5e7d6c0935b9e3b691989c999b496a24af5d3e4bdcc8f17f3b38c20544a24db'},
 'evidence':{'id':12619770522,'sha256':'f404aba22c7cb68d7b39f0c948092c944cd9de33f48e20e34eebb41a30441c09'},
}
LIMIT=550*1024*1024

def require(ok,message):
 if not ok:raise ValueError(message)

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def archive_plan(archive, *, prefix=None):
 with zipfile.ZipFile(archive) as z:
  entries=z.infolist();require(0<len(entries)<=20000,'Archive entry count exceeds bound')
  require(sum(x.file_size for x in entries)<=2*1024**3,'Archive expansion exceeds bound')
  seen={};plan=[]
  for x in entries:
   name=x.filename;path=PurePosixPath(name.rstrip('/'));mode=x.external_attr>>16;kind=stat.S_IFMT(mode);permissions=stat.S_IMODE(mode)
   require(name and '\\' not in name and '\x00' not in name and ':' not in name and not path.is_absolute() and
           all(p not in ('','.', '..') for p in path.parts) and path.as_posix()+('/' if x.is_dir() else '')==name,'Unsafe archive path')
   require(not x.flag_bits&1 and x.file_size<=LIMIT,'Encrypted or oversized archive member')
   require(kind in (stat.S_IFREG,stat.S_IFDIR),'Archive symlink/special/unspecified entry rejected')
   require((kind==stat.S_IFDIR)==x.is_dir(),'Archive directory type mismatch')
   require(permissions in ((0o755,) if x.is_dir() else (0o600,0o644,0o755)),'Unexpected archive mode')
   require(not x.is_dir() or x.file_size==0,'Nonempty directory member')
   key=path.as_posix().casefold();require(key not in seen,'Duplicate/case-colliding archive path');seen[key]=kind
   require(prefix is None or path.parts[0]==prefix,'Unexpected archive root')
   plan.append((x.filename,path,permissions,kind))
  for _,path,_,_ in plan:
   for parent in path.parents:
    if parent==PurePosixPath('.'):continue
    require(seen.get(parent.as_posix().casefold(),stat.S_IFDIR)==stat.S_IFDIR,'Archive file used as parent')
  return plan

def extract(archive,destination,*,prefix=None):
 plan=archive_plan(archive,prefix=prefix);destination=Path(destination)
 require(not destination.exists() and not destination.is_symlink(),'Fresh extraction destination required')
 require(destination.parent.is_dir() and not destination.parent.is_symlink(),'Invalid extraction parent')
 destination.mkdir(mode=0o755)
 with zipfile.ZipFile(archive) as z:
  for name,path,mode,kind in sorted(plan,key=lambda row:(len(row[1].parts),row[1].as_posix())):
   target=destination.joinpath(*path.parts)
   target.parent.mkdir(parents=True,exist_ok=True)
   if kind==stat.S_IFDIR:target.mkdir(exist_ok=True);target.chmod(mode)
   else:
    with z.open(name) as source,target.open('xb') as out:shutil.copyfileobj(source,out,1024*1024)
    require(target.stat().st_size==z.getinfo(name).file_size,'Extracted size differs');target.chmod(mode)
 return plan

def inventory(root):
 result={};root=Path(root)
 for p in [root,*sorted(root.rglob('*'))]:
  info=p.lstat();require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),'Unsupported restored entry')
  row={'mode':stat.S_IMODE(info.st_mode),'kind':'directory' if p.is_dir() else 'file'}
  if p.is_file():row['sha256']=sha(p)
  result[p.relative_to(root).as_posix()]=row
 return result

def artifact_name(owner):
 return ('native-app-compile' if owner=='evidence' else 'unsigned-'+owner)+'-'+HEAD+'-'+str(RUN)+'-1'

def validate_run(value):
 require(value.get('id')==RUN and value.get('head_sha')==HEAD and value.get('run_attempt')==1 and
         value.get('repository',{}).get('full_name')==REPOSITORY and
         value.get('head_repository',{}).get('full_name')==REPOSITORY and
         value.get('path')=='.github/workflows/issues-native.yml' and value.get('status')=='completed' and
         value.get('conclusion')=='failure','Original run provenance differs')

def validate_artifact(value,owner):
 expected=ARTIFACTS[owner];run=value.get('workflow_run',{})
 require(value.get('id')==expected['id'] and value.get('name')==artifact_name(owner) and value.get('expired') is False and
         run.get('id')==RUN and run.get('head_sha')==HEAD and type(value.get('size_in_bytes')) is int and 0<value['size_in_bytes']<=LIMIT,
         'Fixed artifact provenance differs')
 if value.get('digest') is not None:require(value['digest']=='sha256:'+expected['sha256'],'Artifact API digest differs')

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,request,fp,code,msg,headers,newurl):return None

class API:
 def __init__(self,token):self.token=token;self.last=0
 def request(self,path):
  time.sleep(max(0,3-(time.monotonic()-self.last)));self.last=time.monotonic()
  require(path.startswith('/repos/'+REPOSITORY+'/actions/'),'Unexpected API path')
  request=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
  return urllib.request.build_opener(NoRedirect).open(request,timeout=60)
 def json(self,path):
  with self.request(path) as response:
   data=response.read(2*1024*1024+1);require(len(data)<=2*1024*1024,'Oversized API metadata');return json.loads(data)
 def download(self,artifact,destination):
  try:
   response=self.request('/repos/'+REPOSITORY+'/actions/artifacts/'+str(artifact)+'/zip')
  except urllib.error.HTTPError as error:
   require(error.code in (301,302,303,307,308),'Artifact API acquisition failed')
   location=error.headers.get('Location','');u=urllib.parse.urlparse(location)
   require(u.scheme=='https' and not u.username and not u.password and u.port in (None,443) and
           any((u.hostname or '').endswith(suffix) for suffix in ('.blob.core.windows.net','.actions.githubusercontent.com','.githubusercontent.com')),
           'Unrecognized signed artifact redirect')
   # Never forward the API Authorization header to signed object storage.
   try:
    response=urllib.request.build_opener(NoRedirect).open(urllib.request.Request(location),timeout=120)
   except (urllib.error.URLError, OSError, TimeoutError):
    raise ValueError('Signed artifact storage request failed; URL withheld') from None
  total=0
  try:
   with response,Path(destination).open('xb') as output:
    for chunk in iter(lambda:response.read(1024*1024),b''):
     total+=len(chunk);require(total<=LIMIT,'Artifact download exceeds bound');output.write(chunk)
  except (urllib.error.URLError, OSError, TimeoutError):
   raise ValueError('Artifact transfer failed; URL withheld') from None

def restore(downloads,workspace,temp):
 downloads=Path(downloads).resolve(strict=True);workspace=Path(workspace).resolve(strict=True);temp=Path(temp).resolve(strict=True)
 for owner,expected in ARTIFACTS.items():
  path=downloads/(owner+'.zip');require(path.is_file() and not path.is_symlink() and sha(path)==expected['sha256'],'Fixed outer artifact bytes differ')
  archive_plan(path)
 # Quarantine and validate both nested archives before any original tree is restored.
 prepared={}
 for owner in ('LiveContainer','SideStore'):
  folder=downloads/(owner+'-outer');extract(downloads/(owner+'.zip'),folder)
  expected=ARTIFACTS[owner];name=owner+'-unsigned.zip'
  require(set(p.name for p in folder.iterdir())=={name,'SHA256SUMS'},'Unsigned artifact outer closure differs')
  require((folder/'SHA256SUMS').read_text()==expected['inner_sha256']+'  '+name+'\n' and sha(folder/name)==expected['inner_sha256'],'Fixed inner ZIP differs')
  prefix='native-transfer-'+owner;staging=downloads/(owner+'-inner');extract(folder/name,staging,prefix=prefix)
  prepared[owner]=(staging,prefix)
 extract(downloads/'evidence.zip',workspace/'evidence')
 evidence_before=inventory(workspace/'evidence')
 expected_products=json.loads((workspace/'evidence/product-hashes.json').read_text())
 for owner in ('LiveContainer','SideStore'):
  staging,prefix=prepared[owner]
  transfer=staging/prefix;manifest=json.loads((transfer/'retained-files.json').read_text())
  app=transfer/'Products'/(owner+'.app')
  require(inventory(app)==manifest['app'],'Retained app inventory differs')
  require({p.name:inventory(p) for p in (transfer/'Symbols').iterdir()}==manifest['symbols'],'Retained symbol inventory differs')
  require({p.relative_to(app).as_posix():sha(p) for p in app.rglob('*') if p.is_file()}==expected_products[owner],'Original native product hashes differ')
  destination=temp/prefix;require(not destination.exists() and not destination.is_symlink(),'Existing native transfer destination')
  shutil.copytree(transfer,destination,copy_function=shutil.copy2);require(inventory(destination)==inventory(transfer),'Restored transfer differs')
  native=temp/('native-'+owner);require(not native.exists() and not native.is_symlink(),'Existing native products root')
  products=native/'Build/Products/Release-iphoneos';products.mkdir(parents=True)
  shutil.copytree(app,products/app.name,copy_function=shutil.copy2);require(inventory(products/app.name)==manifest['app'],'Restored native app differs')
 require(inventory(workspace/'evidence')==evidence_before,'Original evidence mutated during recovery')
 return {'schema':'fixed-native-artifact-restoration-v1','original_native':{'workflow_commit':HEAD,'run_id':str(RUN),'run_attempt':'1'},
         'artifacts':ARTIFACTS,'original_evidence_inventory_sha256':hashlib.sha256(json.dumps(evidence_before,sort_keys=True).encode()).hexdigest(),
         'native_execution_performed':False,'original_evidence_modified':False}

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--downloads',type=Path,default=Path('recovery-downloads'));args=parser.parse_args()
 require(os.environ.get('GITHUB_REPOSITORY')==REPOSITORY and os.environ.get('GITHUB_REF')=='refs/heads/recovery/complete-issues-package','Wrong recovery repository/branch')
 args.downloads.mkdir(exist_ok=False)
 api=API(os.environ['GH_TOKEN']);run=api.json('/repos/'+REPOSITORY+'/actions/runs/'+str(RUN));validate_run(run)
 metadata={}
 for owner,item in ARTIFACTS.items():
  value=api.json('/repos/'+REPOSITORY+'/actions/artifacts/'+str(item['id']));validate_artifact(value,owner);metadata[owner]=value
  api.download(item['id'],args.downloads/(owner+'.zip'))
  require((args.downloads/(owner+'.zip')).stat().st_size==value['size_in_bytes'],'Downloaded artifact size differs from API metadata')
 report=restore(args.downloads,Path.cwd(),Path(os.environ['RUNNER_TEMP']))
 report['recovery_identity']={key:os.environ[key] for key in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
 report['api_artifacts']={key:{k:v[k] for k in ('id','name','size_in_bytes','expired','workflow_run')} for key,v in metadata.items()}
 Path('recovery-evidence').mkdir(exist_ok=True)
 Path('recovery-evidence/acquisition.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
