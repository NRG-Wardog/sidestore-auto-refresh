"""Owned fake command tests only, no simulator/network launched locally."""
import contextlib,copy,hashlib,io,json,subprocess,tempfile,unittest
from boot_evidence import verify_language_boot
from pathlib import Path
import run_language_switch_tests as R
UUID='12345678-1234-1234-1234-123456789ABC'
class BootReadinessTests(unittest.TestCase):
 def execute(self,events):
  directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);work=Path(directory.name).resolve();elapsed=[0.0];calls=[]
  def run(args,**kwargs):
   calls.append((args,kwargs));event=events[len(calls)-1];elapsed[0]+=event[1]
   if event[0]=='timeout':raise subprocess.TimeoutExpired(args,kwargs['timeout'],output=b'boot still starting\n')
   if event[0]=='error':raise subprocess.CalledProcessError(9,args,output='device unavailable\n')
   if event[0]=='oserror':raise OSError('owned test launch error')
   return 'boot ready\n'
  return work,elapsed,calls,lambda:R.wait_owned_simulator_boot(UUID,work,clock=lambda:elapsed[0],run=run)
 def test_immediate_ready_once_and_output_retained(self):
  work,clock,calls,action=self.execute([('ready',2)])
  result=action();self.assertEqual(result['status'],'READY');self.assertEqual(len(calls),1)
  self.assertEqual((work/'bootstatus-attempt-1.log').read_text(),'boot ready\n')
  self.assertEqual(calls[0][0],['xcrun','simctl','bootstatus',UUID,'-b'])
 def test_only_timeout_continues_same_device_and_bounds(self):
  work,clock,calls,action=self.execute([('timeout',180),('ready',70)])
  with contextlib.redirect_stderr(io.StringIO()):result=action()
  self.assertEqual(result['status'],'READY');self.assertEqual(len(calls),2);self.assertEqual(calls[0][0],calls[1][0])
  self.assertLessEqual(sum(x[1]['timeout'] for x in calls),360)
  self.assertEqual(result['attempts'][0]['error']['type'],'TimeoutExpired')
  self.assertEqual((work/'bootstatus-attempt-1.log').read_text(),'boot still starting\n')
  self.assertEqual((work/'bootstatus-attempt-2.log').read_text(),'boot ready\n')
 def test_two_timeouts_fail_no_third_attempt(self):
  work,clock,calls,action=self.execute([('timeout',180),('timeout',180)])
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(subprocess.TimeoutExpired):action()
  data=json.loads((work/'boot-readiness.json').read_text());self.assertEqual(data['status'],'FAILED');self.assertEqual(len(calls),2);self.assertEqual(data['elapsed_seconds'],360)
 def test_command_errors_fail_without_retry(self):
  for kind,error in [('error',subprocess.CalledProcessError),('oserror',OSError)]:
   with self.subTest(kind=kind):
    work,clock,calls,action=self.execute([(kind,1)])
    with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(error):action()
    data=json.loads((work/'boot-readiness.json').read_text());self.assertEqual(len(calls),1);self.assertEqual(data['status'],'FAILED');self.assertIsNotNone(data['attempts'][0]['error'])
 def test_late_ready_not_admitted_and_no_launch_retries(self):
  work,clock,calls,action=self.execute([('timeout',180),('ready',181)])
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(ValueError):action()
  self.assertEqual(json.loads((work/'boot-readiness.json').read_text())['status'],'FAILED')
  source=(R.HERE/'run_language_switch_tests.py').read_text()
  self.assertLess(source.index('wait_owned_simulator_boot(udid,work)'),source.index("command(['xcrun','simctl','install'"))
  self.assertEqual(source.count("result=launch_result([],'language-switch-result.json',120)"),1)
 def test_success_receipt_hash_bounds_and_attempt_order(self):
  work,clock,calls,action=self.execute([('timeout',180),('ready',20)])
  with contextlib.redirect_stderr(io.StringIO()):data=action()
  path=work/'boot-readiness.json';R.verify_boot_readiness(path,UUID)
  for key,value in [('udid','other'),('elapsed_seconds',361),('elapsed_seconds',float('nan')),('budget_seconds',True),('attempts',[])]:
   path.write_text(json.dumps({**data,key:value}))
   with self.assertRaises(ValueError):R.verify_boot_readiness(path,UUID)
  for key,value in [('attempt','../wrong'),('status','command_error'),('timeout_seconds',181),('exit_code',0)]:
   changed=copy.deepcopy(data);changed['attempts'][0][key]=value;path.write_text(json.dumps(changed))
   with self.assertRaises(ValueError):R.verify_boot_readiness(path,UUID)
  path.write_text(json.dumps(data));(work/'bootstatus-attempt-1.log').write_text('tampered')
  with self.assertRaises(ValueError):R.verify_boot_readiness(path,UUID)
 def test_embedded_packaging_proof_retains_outputs_and_rejects_tampering(self):
  work,clock,calls,action=self.execute([('timeout',180),('ready',20)])
  with contextlib.redirect_stderr(io.StringIO()):data=action()
  raw=(work/'boot-readiness.json').read_text()
  report={'boot_readiness_json':raw,'boot_readiness_sha256':hashlib.sha256(raw.encode()).hexdigest(),'boot_attempt_logs':{p.name:p.read_text() for p in work.glob('bootstatus-attempt-*.log')},'simulator':{'udid':UUID}}
  verify_language_boot(report)
  for logs in [{},{**report['boot_attempt_logs'],'extra.log':'unexpected'},{**report['boot_attempt_logs'],'bootstatus-attempt-1.log':'tampered'}]:
   with self.assertRaises(ValueError):verify_language_boot({**report,'boot_attempt_logs':logs})
  oversized=copy.deepcopy(report);text='x'*262145;oversized['boot_attempt_logs']['bootstatus-attempt-1.log']=text
  amended=copy.deepcopy(data);amended['attempts'][0]['output_sha256']=hashlib.sha256(text.encode()).hexdigest();oversized['boot_readiness_json']=json.dumps(amended);oversized['boot_readiness_sha256']=hashlib.sha256(oversized['boot_readiness_json'].encode()).hexdigest()
  with self.assertRaises(ValueError):verify_language_boot(oversized)
  duplicate=copy.deepcopy(data);duplicate['attempts'][1]['attempt']=1;raw=json.dumps(duplicate)
  with self.assertRaises(ValueError):verify_language_boot({**report,'boot_readiness_json':raw,'boot_readiness_sha256':hashlib.sha256(raw.encode()).hexdigest()})
  for script in ['acquire.py','independent_verify.py']:
   self.assertIn('verify_language_boot(', (R.HERE.parent/'cold-vpn-zopfli'/script).read_text())
 def test_wall_budget_reduces_second_timeout(self):
  work,clock,calls,action=self.execute([('timeout',190),('ready',3)])
  with contextlib.redirect_stderr(io.StringIO()):action()
  self.assertEqual(calls[1][1]['timeout'],170)
if __name__=='__main__':unittest.main(verbosity=2)
