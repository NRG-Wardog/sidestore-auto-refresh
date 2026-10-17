"""Causal, same-app pasteboard-generation evidence. Never setter-call counts."""
from pathlib import Path
import hashlib, math, shutil, uuid

FIELDS=('probeTypingBefore','probeTypingAfter','probeActionBefore','probeTapReturned','probeActionAfter')
STAGES=('typing-before','typing-after','action-before','tap-returned','action-after')
def require(ok,message):
 if not ok:raise ValueError(message)
def number(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def retain(container,destination):
 """Copy only count-only files owned by this synthetic test app."""
 container=Path(container).resolve(strict=True)
 source=container/'Library/Caches/P2ClipboardTrace'
 require(source.is_dir() and not source.is_symlink(),'Missing or linked fixture clipboard trace directory')
 require(source.resolve().is_relative_to(container),'Trace directory escaped fixture container')
 paths=sorted(source.iterdir());require(1<=len(paths)<=32,'Missing/oversized trace inventory')
 destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
 total=0;inventory={}
 for path in paths:
  require(path.is_file() and not path.is_symlink() and path.suffix=='.jsonl','Unexpected fixture trace entry')
  require(str(uuid.UUID(path.stem)).upper()==path.stem,'Invalid fixture trace nonce filename')
  size=path.stat().st_size;total+=size
  require(0<size<=16*1024*1024 and total<=64*1024*1024,'Oversized trace evidence')
  shutil.copyfile(path,destination/path.name)
  require(digest(path)==digest(destination/path.name),'Fixture trace changed during export')
  inventory[path.name]={'bytes':size,'sha256':digest(destination/path.name)}
 return inventory

def read(directory,decode):
 directory=Path(directory);require(directory.is_dir() and not directory.is_symlink(),'Missing app-process clipboard traces')
 traces={};paths=sorted(directory.iterdir());require(1<=len(paths)<=32,'Missing/oversized app-process trace inventory')
 for path in paths:
  require(path.is_file() and not path.is_symlink() and path.suffix=='.jsonl','Unexpected app-process trace file')
  raw=path.read_bytes();require(0<len(raw)<=16*1024*1024 and raw.endswith(b'\n'),'Truncated/oversized app-process trace')
  rows=[decode(line.decode('utf-8')) for line in raw.splitlines()]
  require(1<=len(rows)<=30000,'Invalid trace row count')
  nonce=path.stem;require(str(uuid.UUID(nonce)).upper()==nonce,'Invalid trace filename')
  previous=None;requests={};acks={}
  for index,row in enumerate(rows):
   require(isinstance(row,dict) and set(row)=={'schema','sequence','processToken','sessionToken','nonce','processIdentifier','uptime','runLoopTurn','kind','requestSequence','count','failure'},'Malformed app trace row')
   require(row['schema']=='p2-app-clipboard-trace-v1' and row['failure']=='','App trace failure or wrong schema')
   require(type(row['sequence']) is int and row['sequence']==index+1,'Missing/duplicate/out-of-order trace row')
   require(row['nonce']==row['sessionToken']==nonce,'Wrong app trace session')
   uuid.UUID(row['processToken'])
   require(type(row['processIdentifier']) is int and row['processIdentifier']>0,'Invalid app trace process')
   require(number(row['uptime']) and type(row['runLoopTurn']) is int and row['runLoopTurn']>=0,'Invalid app trace clock/turn')
   require(type(row['count']) is int and row['count']>=0,'Invalid app trace generation')
   require(type(row['requestSequence']) is int and 0<=row['requestSequence']<=64,'Invalid app trace request ID')
   require(row['kind'] in ('initial','periodic','request','acknowledged-sample','stopped'),'Unexpected app trace event')
   if previous:
    require(all(row[k]==previous[k] for k in ('processToken','processIdentifier')),'Trace replaced process identity')
    require(row['uptime']>=previous['uptime'] and row['runLoopTurn']>=previous['runLoopTurn'] and row['count']>=previous['count'],'App trace clock/turn/count regressed')
    require(previous['kind']!='stopped' and row['kind']!='initial','Trace continued after stop or restarted')
   else:require(row['kind']=='initial' and row['requestSequence']==0,'Missing initial trace row')
   if row['kind']=='request':
    request=row['requestSequence'];require(request==len(requests)+1 and len(requests)==len(acks),'Unserialized app sample request')
    requests[request]=row
   elif row['kind']=='acknowledged-sample':
    request=row['requestSequence'];require(request==len(acks)+1 and request in requests,'Stale/duplicate/unrequested app sample')
    before=requests[request]
    require(row['runLoopTurn']>before['runLoopTurn'] and 0.05-1e-6<=row['uptime']-before['uptime']<=3.1,'App sample lacks later event-loop turn or is stale')
    acks[request]=row
   elif row['kind']=='periodic':require(row['requestSequence']==(len(requests) if len(requests)>len(acks) else 0),'Periodic trace has a stale request ID')
   elif row['kind'] in ('initial','stopped'):require(row['requestSequence']==0,'Unexpected nonzero trace boundary request')
   previous=row
  require(len(requests)==len(acks),'Unacknowledged app sample request')
  traces[nonce]={'rows':rows,'requests':requests,'acks':acks,'sha256':hashlib.sha256(raw).hexdigest()}
 return traces

def verify_case(data,rows,traces):
 requests=data.get('clipboardRequests');acks=data.get('clipboardAcknowledgements')
 require(isinstance(requests,list) and isinstance(acks,list) and data.get('clipboardFailures')==[],'Missing/failed causal clipboard evidence')
 if data['evidenceCase']!='textfield-buttons':
  require(requests==acks==[],'Unexpected fixture clipboard request')
  return
 require(len(requests)==len(acks)==len(rows)*5,'Missing/extra causal sample request')
 nonce=rows[0]['before']['sessionToken'];trace=traces.get(nonce)
 require(trace is not None,'Missing modal app-process trace')
 require(len(trace['acks'])==len(requests),'App and runner sample inventories differ')
 for index,(request,ack) in enumerate(zip(requests,acks)):
  id=index+1
  require(isinstance(request,dict) and set(request)=={'nonce','requestSequence','stage','runnerProcessIdentifier','requestUptime','ackUptime','returnUptime'},'Incomplete causal request receipt')
  require(isinstance(ack,dict) and set(ack)=={'nonce','requestSequence','runnerUptime','runnerProcessIdentifier'},'Malformed acknowledgement receipt')
  require(type(request['requestSequence']) is int and type(ack['requestSequence']) is int and request['requestSequence']==ack['requestSequence']==id,'Stale/duplicate/out-of-order acknowledgement')
  require(request['nonce']==ack['nonce']==nonce,'Acknowledgement belongs to another session')
  require(type(request['runnerProcessIdentifier']) is int and type(ack['runnerProcessIdentifier']) is int and request['runnerProcessIdentifier']>0 and ack['runnerProcessIdentifier']==request['runnerProcessIdentifier'],'Wrong acknowledgement process')
  require(all(number(request[k]) for k in ('requestUptime','ackUptime','returnUptime')) and number(ack['runnerUptime']),'Nonfinite causal receipt clock')
  require(request['requestUptime']<=request['ackUptime']==ack['runnerUptime']<=request['returnUptime'] and request['returnUptime']-request['requestUptime']<=5.1,'Missing or stale causal acknowledgement')
  if index:require(request['requestUptime']>=requests[index-1]['returnUptime'] and request['runnerProcessIdentifier']==requests[index-1]['runnerProcessIdentifier'],'Concurrent or replaced runner sample request')
 for index,row in enumerate(rows):
  before=row['before'];after=row['after'];group=requests[index*5:(index+1)*5];samples=[]
  for field,stage,request in zip(FIELDS,STAGES,group):
   require(row.get(field)==request and request['stage']==row['language']+'-'+row['action']+'-'+stage,'Causal sample belongs to wrong action or phase')
   app=trace['acks'][request['requestSequence']]
   require(all(app[k]==before[k] for k in ('processToken','sessionToken','processIdentifier')),'Causal sample came from another app process')
   require(request['runnerProcessIdentifier']==row['typingBefore']['processIdentifier']!=app['processIdentifier'],'App and runner provenance differ')
   samples.append(app)
  t0,t1,t2,t3,t4=group
  tap=row.get('actionTapUptime');require(number(tap),'Missing actual tap boundary')
  require(row['typingBefore']['uptime']<=t0['requestUptime']<=t0['returnUptime']<=row['typingAfter']['uptime']<=t1['requestUptime'],'Typing boundaries do not surround causal app samples')
  require(t1['returnUptime']<=row['actionBefore']['uptime']<=t2['requestUptime']<=t2['returnUptime']<=tap<=row['tapReturned']['uptime']<=t3['requestUptime'],'Real tap was not causally after the acknowledged baseline')
  require(t3['returnUptime']<=row['actionAfter']['uptime']<=t4['requestUptime'] and row['actionAfter']['uptime']-t3['returnUptime']>=0.8,'Missing settled dismissal window')
  require(samples[4]['uptime']-samples[3]['uptime']>=0.8,'App trace lacks its own settled dismissal duration')
  require(samples[0]['count']==before['clipboardChangeCount'] and samples[1]['count']>=samples[0]['count'],'Wrong same-app typing baseline')
  require(samples[1]['count']==samples[2]['count']==samples[3]['count']==samples[4]['count']==after['clipboardChangeCount'],'App clipboard generation changed after measured typing baseline or during action')
  measured=trace['rows'][samples[1]['sequence']-1:samples[4]['sequence']]
  require(measured and all(x['count']==samples[1]['count'] for x in measured),'Unexpected generation within actual action interval')
  require(all(b['uptime']-a['uptime']<=0.5 for a,b in zip(measured,measured[1:])),'Gap in app-process action trace')
