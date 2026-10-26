"""Portable strict boot evidence verifier for native and lossless-package audits."""
import hashlib,json,math

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
 return validate_boot(report['boot_readiness_json'],report['boot_attempt_logs'],report['simulator']['udid'],report['boot_readiness_sha256'])
