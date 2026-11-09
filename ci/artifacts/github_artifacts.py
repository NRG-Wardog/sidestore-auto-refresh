"""Bounded, read-only GitHub Actions artifact transport for maintained diagnostics."""
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "NRG-Wardog/sidestore-auto-refresh"
LIMIT = 550 * 1024 * 1024

def require(ok,message):
 if not ok:raise ValueError(message)

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()

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
