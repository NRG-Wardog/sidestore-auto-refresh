"""Bounded cleanup of one created simulator, with observed absence proof."""
import hashlib,json,re,subprocess,time
UUID=re.compile(r'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}')
def require(ok,message):
 if not ok:raise ValueError(message)
def strict(pairs):
 value={}
 for key,item in pairs:
  require(key not in value,'Duplicate cleanup JSON key');value[key]=item
 return value
def absent(text,udid):
 data=json.loads(text,object_pairs_hook=strict)
 require(type(data) is dict and type(data.get('devices')) is dict and bool(data['devices']),'Malformed simulator inventory')
 found=[]
 for runtime,rows in data['devices'].items():
  require(type(runtime) is str and type(rows) is list,'Malformed simulator runtime inventory')
  for row in rows:
   require(type(row) is dict and type(row.get('udid')) is str and UUID.fullmatch(row['udid']),'Malformed simulator identity')
   found.append(row['udid'].lower())
 require(len(found)==len(set(found)),'Duplicate simulator identity')
 return udid.lower() not in found

def cleanup(udid,work,*,run=None,clock=None):
 require(type(udid) is str and UUID.fullmatch(udid),'Invalid owned simulator identity')
 run=subprocess.check_output if run is None else run;clock=time.monotonic if clock is None else clock
 started=clock();deadline=started+180
 report={'schema':'owned-simulator-cleanup-v1','udid':udid,'budget_seconds':180,'attempts':[],'status':'FAILED'}
 def attempt(action,limit):
  remaining=deadline-clock()
  if remaining<=0:return None
  args=['xcrun','simctl',action,udid] if action!='list' else ['xcrun','simctl','list','devices','-j']
  row={'action':action,'timeout_seconds':min(limit,remaining),'state':'failed','exit_code':None,'output':'','error':None}
  try:
   row['output']=run(args,text=True,stderr=subprocess.STDOUT,timeout=row['timeout_seconds'])
   row['exit_code']=0;row['state']='completed'
  except (subprocess.SubprocessError,OSError) as error:
   row['output']=getattr(error,'output','') or '';row['exit_code']=getattr(error,'returncode',None)
   row['error']={'type':type(error).__name__,'message':str(error)}
  if isinstance(row['output'],bytes):row['output']=row['output'].decode('utf-8','replace')
  if clock()>deadline:row['state']='budget_exhausted'
  report['attempts'].append(row)
  return row
 attempt('shutdown',45)
 for _ in range(2):
  attempt('delete',45)
  observation=attempt('list',15)
  if observation and observation['state']=='completed':
   try:
    if absent(observation['output'],udid):
     report['status']='VERIFIED_ABSENT';break
   except (ValueError,TypeError) as error:
    observation['error']={'type':type(error).__name__,'message':str(error)}
 report['elapsed_seconds']=max(0,clock()-started)
 target=work/'cleanup-evidence.json';require(not target.exists() and not target.is_symlink(),'Fresh cleanup evidence required')
 target.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 return report

def verify(text,udid):
 report=json.loads(text,object_pairs_hook=strict)
 require(set(report)=={'schema','udid','budget_seconds','attempts','status','elapsed_seconds'},'Cleanup receipt closure differs')
 require(report['schema']=='owned-simulator-cleanup-v1' and report['udid']==udid and UUID.fullmatch(udid),'Cleanup owner differs')
 require(report['status']=='VERIFIED_ABSENT' and report['budget_seconds']==180,'Cleanup absence unverified')
 require(type(report['elapsed_seconds']) in (int,float) and 0<=report['elapsed_seconds']<=180,'Cleanup budget exceeded')
 rows=report['attempts'];require(type(rows) is list and len(rows) in (3,5),'Cleanup attempts differ')
 require([r['action'] for r in rows]==['shutdown','delete','list']+(['delete','list'] if len(rows)==5 else []),'Cleanup action scope differs')
 for row in rows:
  require(set(row)=={'action','timeout_seconds','state','exit_code','output','error'},'Cleanup attempt closure differs')
  require(type(row['timeout_seconds']) in (int,float) and 0<row['timeout_seconds']<=(15 if row['action']=='list' else 45),'Cleanup attempt budget differs')
  require(row['state'] in ('failed','completed') and type(row['output']) is str,'Cleanup attempt state differs')
  require(row['state']!='completed' or row['exit_code']==0,'Cleanup command completion differs')
 last=rows[-1]
 require(last['state']=='completed' and last['error'] is None and absent(last['output'],udid),'Cleanup final absence not proven')
 return report
