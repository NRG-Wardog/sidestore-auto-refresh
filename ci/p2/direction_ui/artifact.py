"""Authenticate the exact candidate compile artifact before restoring app bytes.

No artifact code is imported or run. Metadata pins are filled only after the
candidate compile artifact has been independently authenticated.
"""
from pathlib import Path
import hashlib,json,stat
PINS=None

def require(value,message):
 if not value:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def authenticate(path,work,source,helper,host):
 require(isinstance(PINS,dict),'Candidate artifact pins are not established')
 raw=helper.read_stable(path)
 require(sha(raw)==PINS['artifactSHA256'],'Compile artifact digest differs')
 outer=helper.zip_payload(raw,allow_directories=False)
 require(set(outer)=={'evidence.zip','transport.json'},'Unexpected compile transport members')
 transport=helper.strict_json(outer['transport.json']['data'])
 require(transport.get('status')=='stable-evidence-retained' and transport.get('ownedWritersSettled')is True,'Compile writer did not settle')
 inner=outer['evidence.zip']['data']
 require(sha(inner)==PINS['evidenceSHA256']==transport['archive']['sha256'],'Compile evidence digest differs')
 files=helper.zip_payload(inner,allow_directories=True)
 index=helper.strict_json(files['evidence-index.json']['data'])
 require(set(index)=={n for n,r in files.items() if not r['directory'] and n!='evidence-index.json'},'Compile evidence index coverage differs')
 for name,digest in index.items():require(sha(files[name]['data'])==digest,'Compile evidence member changed: '+name)
 result=helper.strict_json(files['result.json']['data'])
 require(result.get('status')=='compile-passed' and result.get('sourceCommit')==PINS['sourceCommit'] and result.get('sourceTree')==PINS['sourceTree'],'Compile candidate identity/outcome differs')
 before=helper.strict_json(files['source-before.json']['data']);after=helper.strict_json(files['source-after.json']['data'])
 require(before==after and before['commit']==PINS['sourceCommit'] and before['tree']==PINS['sourceTree'],'Source changed around compilation')
 identity=helper.strict_json(files['product-identity.json']['data'])
 settings,_=host.host_build_settings(files['build-settings.json']['data'].decode(),Path(PINS['originalSource']),Path(PINS['originalDerived']))
 appraw=files['LiveContainer.app.zip']['data']
 require(sha(appraw)==PINS['appSHA256']==result['appArchive']['sha256'],'Candidate app digest differs')
 appfiles=helper.zip_payload(appraw,allow_directories=True)
 expected=identity['files'];actual={}
 for name,row in appfiles.items():
  if not row['directory']:
   mode=stat.S_IMODE(row['mode'])
   actual[name]={'type':'regular','mode':mode,'sha256':sha(row['data']),'bytes':len(row['data']),'executable':bool(mode&0o111)}
 require(actual==expected,'Candidate full app inventory differs')
 app=work/'restored/LiveContainer.app';helper.safe_directory(app);app.chmod(0o755)
 for name,row in sorted(appfiles.items(),key=lambda x:(len(Path(x[0]).parts),x[0])):
  if row['directory']:
   helper.safe_directory(app/name);(app/name).chmod(stat.S_IMODE(row['mode']))
  else:helper.write_new(app/name,row['data'],stat.S_IMODE(row['mode']))
 helper.verify_restored_app(app,expected)
 require(host.verify_product(app,settings)==identity,'Restored candidate product identity differs')
 # The caller authenticates this fresh checkout before invoking us.
 require(host.sha(source/'Resources/Localizable.xcstrings')==PINS['catalogSHA256'],'Fresh catalog differs')
 return {'app':app,'identity':identity,'settings':settings,'sourceRoot':source,'provenance':{'run':PINS['run'],'artifactSHA256':PINS['artifactSHA256'],'compilePassed':True,'runtimeAcceptance':False}}
