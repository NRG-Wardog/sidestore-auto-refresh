"""Portable strict boot evidence verifier for native and lossless-package audits."""
import hashlib,json,math,re
from pathlib import PurePosixPath

def require(ok,why):
 if not ok:raise ValueError(why)
def pairs(rows):
 result={}
 for k,v in rows:
  require(k not in result,'Duplicate boot JSON key');result[k]=v
 return result
def number(value,low,high):return type(value) in (int,float) and math.isfinite(value) and low<=value<=high
def validate_boot(raw,logs,udid,expected_hash):
 require(type(raw) is str and len(raw.encode())<=65536 and hashlib.sha256(raw.encode()).hexdigest()==expected_hash,'Boot receipt bytes differ')
 data=json.loads(raw,object_pairs_hook=pairs)
 require(data['schema']=='owned-simulator-boot-readiness-v1' and data['status']=='READY' and data['udid']==udid,'Owned simulator readiness differs')
 require(type(data['budget_seconds']) is int and data['budget_seconds']==360 and type(data['maximum_attempts']) is int and data['maximum_attempts']==2,'Boot readiness bounds differ')
 require(number(data['elapsed_seconds'],0,360),'Boot readiness exceeded total budget')
 rows=data['attempts'];require(type(rows) is list and 1<=len(rows)<=2,'Boot readiness attempt count differs')
 require(type(logs) is dict and set(logs)=={'bootstatus-attempt-'+str(i)+'.log' for i in range(1,len(rows)+1)},'Boot output closure differs')
 total=0
 for index,row in enumerate(rows,1):
  require(type(row['attempt']) is int and row['attempt']==index,'Boot attempt order differs')
  require(number(row['timeout_seconds'],0,180) and row['timeout_seconds']>0,'Boot per-attempt timeout differs');total+=row['timeout_seconds']
  if index==len(rows):require(row['status']=='ready' and type(row['exit_code']) is int and row['exit_code']==0 and row['error'] is None,'Boot final readiness not established')
  else:require(row['status']=='timeout' and row['exit_code'] is None and type(row['error']) is dict and row['error']['type']=='TimeoutExpired','Only timeout may continue boot observation')
  text=logs['bootstatus-attempt-'+str(index)+'.log'];require(type(text) is str and len(text.encode())<=262144 and hashlib.sha256(text.encode()).hexdigest()==row['output_sha256'],'Boot attempt output differs')
 require(total<=360,'Boot command budgets exceeded total')
 return data

def verify_language_boot(report):
 verify_selection(report)
 return validate_boot(report['boot_readiness_json'],report['boot_attempt_logs'],report['simulator']['udid'],report['boot_readiness_sha256'])

# Versioned policy is shared by native validation and independent package audit.
SELECTION_POLICY='installed-runtime-selected-sdk-family-v1'
def sdk_family(version):
 require(type(version) is str and re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',version),'Invalid selected simulator SDK version')
 value=runtime_number(version)
 require(value>=runtime_number('16.0'),'Selected simulator SDK must support iOS16+')
 return value>>8

def validate_sdk_path(path,sdk_version):
 require(type(path) is str and path.startswith('/') and not any(c in path for c in ('\n','\r','\x00')),'Invalid selected simulator SDK path')
 parts=PurePosixPath(path).parts
 match=re.fullmatch(r'iPhoneSimulator([0-9]+(?:\.[0-9]+){0,2})?\.sdk',parts[-1])
 require('..' not in parts and match is not None,'Invalid selected simulator SDK bundle')
 if match.group(1) is not None:require(runtime_number(match.group(1))>>8==sdk_family(sdk_version),'Selected simulator SDK path/version family differs')

def runtime_number(version):
 require(type(version) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}',version),'Invalid simulator runtime version')
 parts=[int(x) for x in version.split('.')]+[0,0]
 require(parts[0]<=65535 and parts[1]<=255 and parts[2]<=255,'Runtime version out of bounds')
 return (parts[0]<<16)|(parts[1]<<8)|parts[2]

def available_device(catalog,sdk_version):
 family=sdk_family(sdk_version)
 require(isinstance(catalog,dict),'Invalid simulator inventory')
 runtimes=[r for r in catalog['runtimes'] if r.get('isAvailable') is True and r.get('identifier','').startswith('com.apple.CoreSimulator.SimRuntime.iOS-')]
 runtimes=[r for r in runtimes if runtime_number(r['version'])>>8==family]
 require(runtimes,'No installed available iOS Simulator runtime matching selected SDK family; do not download or fall back')
 require(len({r['identifier'] for r in runtimes})==len(runtimes),'Duplicate matching runtime identity')
 # productFamily alone is insufficient: simctl classifies iPod touch as iPhone.
 phones=[d for d in catalog['devicetypes'] if d.get('name','').startswith('iPhone ') and d.get('identifier','').startswith('com.apple.CoreSimulator.SimDeviceType.iPhone-') and d.get('productFamily')=='iPhone']
 require(phones,'No installed explicit iPhone simulator device type')
 require(len({d['identifier'] for d in phones})==len(phones),'Duplicate iPhone device type identity')
 for runtime in sorted(runtimes,key=lambda r:(runtime_number(r['version']),r['identifier']),reverse=True):
  version=runtime_number(runtime['version'])
  if version<runtime_number('16.0'):continue
  supported=None
  if 'supportedDeviceTypes' in runtime:
   rows=runtime['supportedDeviceTypes'];require(type(rows) is list,'Invalid runtime supported-device inventory')
   supported=set()
   for row in rows:
    identifier=row if type(row) is str else row.get('identifier') if type(row) is dict else None
    require(type(identifier) is str,'Invalid supported device identity');supported.add(identifier)
  devices=catalog.get('devices',{});require(type(devices) is dict,'Invalid existing simulator inventory')
  existing=devices.get(runtime['identifier'],[]);require(type(existing) is list,'Invalid runtime device inventory')
  available_types={d.get('deviceTypeIdentifier') for d in existing if type(d) is dict and d.get('isAvailable') is True and type(d.get('deviceTypeIdentifier')) is str}
  eligible=[]
  for device in phones:
   lower=device.get('minRuntimeVersion');upper=device.get('maxRuntimeVersion')
   bounds=type(lower) is int and type(upper) is int and 0<=lower<=upper<=4294967295
   if ('minRuntimeVersion' in device or 'maxRuntimeVersion' in device) and not bounds:continue
   if bounds and not lower<=version<=upper:continue
   if supported is not None:
    if device['identifier'] not in supported:continue
   else:
    # Older simctl schemas can omit explicit supported identities. Accept
    # authoritative bounds or an available existing device's exact type under
    # this runtime only. Never reuse or mutate the existing device itself.
    if not bounds and device['identifier'] not in available_types:continue
   eligible.append(device)
  if eligible:
   # Prefer the newest compatible generation by minimum runtime, not array order.
   return runtime,max(eligible,key=lambda d:(d.get('minRuntimeVersion') if type(d.get('minRuntimeVersion')) is int else 0,d['identifier']))
 raise ValueError('No installed iPhone device type has verified compatibility with an available iOS16+ runtime')


def selection_evidence(inventory_raw,sdk_version,sdk_path,runtime,device):
 validate_sdk_path(sdk_path,sdk_version);sdk_family(sdk_version)
 require(type(runtime.get('buildversion')) is str and re.fullmatch(r'[A-Za-z0-9.]+',runtime['buildversion']),'Missing/invalid simulator runtime build')
 return {'policy_version':SELECTION_POLICY,'sdk_version':sdk_version,'sdk_path':sdk_path,
         'runtime_version':runtime['version'],'runtime_build':runtime['buildversion'],'runtime_identifier':runtime['identifier'],
         'device_type_identifier':device['identifier'],'inventory_json':inventory_raw,
         'inventory_sha256':hashlib.sha256(inventory_raw.encode()).hexdigest()}

def verify_selection(report):
 evidence=report['simulator_selection']
 require(type(evidence) is dict and set(evidence)=={'policy_version','sdk_version','sdk_path','runtime_version','runtime_build','runtime_identifier','device_type_identifier','inventory_json','inventory_sha256'},'Simulator selection receipt closure differs')
 require(evidence['policy_version']==SELECTION_POLICY,'Simulator selection policy differs')
 raw=evidence['inventory_json']
 require(type(raw) is str and len(raw.encode())<=4194304 and hashlib.sha256(raw.encode()).hexdigest()==evidence['inventory_sha256'],'Simulator inventory bytes differ')
 inventory=json.loads(raw,object_pairs_hook=pairs)
 runtime,device=available_device(inventory,evidence['sdk_version'])
 expected=selection_evidence(raw,evidence['sdk_version'],evidence['sdk_path'],runtime,device)
 require(evidence==expected,'Simulator selection does not match deterministic SDK-family policy')
 simulator=report['simulator']
 require(simulator['runtime']==runtime and simulator['device_type']==device and simulator['sdk']==evidence['sdk_path'],'Executed simulator identity differs from selected SDK/runtime')
 return evidence
