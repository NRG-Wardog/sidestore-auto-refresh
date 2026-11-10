"""Inert process/state controls; never invoke simctl or touch a simulator."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import attachment_contract as C
import owned_simulator_cleanup as O
OWNER='11111111-1111-4111-8111-111111111111';OTHER='22222222-2222-4222-8222-222222222222'
RUNTIME='com.apple.CoreSimulator.SimRuntime.iOS-26-4';TYPE='com.apple.CoreSimulator.SimDeviceType.iPhone-17e'
def inventory(devices=()):
 return {'devices':{RUNTIME:list(devices)}}
def device(udid,state='Shutdown'):
 return {'udid':udid,'name':'synthetic','state':state,'isAvailable':True,'deviceTypeIdentifier':TYPE}
def ownership():return O.creation_ownership(OWNER+'\n',['xcrun','simctl','create','NRG-Multitask-10',TYPE,RUNTIME])
def report(cleanup,observation='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH'):
 return {'identity':{'run_id':'10'},'simulator':{'udid':OWNER,'device_type':{'identifier':TYPE},'runtime':{'identifier':RUNTIME}},'observation_status':observation,'cleanup':cleanup,'cleanup_source_sha256':{n:'a'*64 for n in C.CLEANUP_SOURCE_FILES}}
class CleanupTests(unittest.TestCase):
 def run_cleanup(self,body=None,kinds=('timeout','success','success')):
  calls=[]
  with tempfile.TemporaryDirectory() as directory:
   def runner(args,timeout,path):
    calls.append((args,timeout));kind=kinds[len(calls)-1]
    content=(json.dumps(inventory()) if body is None else body) if args[2]=='list' else ''
    path.write_text(content)
    return {'argv':args,'timeout_seconds':timeout,'elapsed_ms':1,'outcome':kind,'returncode':0 if kind=='success' else -9,'reaped':True,'log':path.name,'log_sha256':hashlib.sha256(content.encode()).hexdigest()}
   result=O.cleanup_owned(ownership(),Path(directory),runner)
  return report(result),calls
 def test_timeout_then_exact_absence_qualifies(self):
  r,calls=self.run_cleanup();C.validate_cleanup_receipt(r)
  self.assertEqual(r['cleanup']['status'],'completeByAbsence');self.assertEqual(len(calls),3)
  self.assertEqual(calls, [(['xcrun','simctl','shutdown',OWNER],45),(['xcrun','simctl','delete',OWNER],45),(['xcrun','simctl','list','devices','--json'],10)])
 def test_success_commands_do_not_query(self):
  r,calls=self.run_cleanup(kinds=('success','success'));C.validate_cleanup_receipt(r);self.assertEqual(len(calls),2)
 def test_delete_timeout_with_absence_qualifies(self):
  r,_=self.run_cleanup(kinds=('success','timeout','success'));C.validate_cleanup_receipt(r)
 def test_unrelated_valid_devices_do_not_require_empty_machine(self):
  r,_=self.run_cleanup(json.dumps(inventory([device(OTHER,'Booted')])));C.validate_cleanup_receipt(r)
 def test_present_shutdown_or_booted_never_qualifies(self):
  for state in ['Shutdown','Booted','Shutting Down']:
   r,_=self.run_cleanup(json.dumps(inventory([device(OWNER,state)])))
   self.assertEqual(r['cleanup']['status'],'failed')
   with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_invalid_missing_truncated_or_unknown_inventory_fails(self):
  cases=['','{}','{"devices":','{"devices":{}}']
  for mutate in [lambda d:d.update(hidden=[device(OWNER)]),lambda d:d['devices'].update(hidden={'udid':OWNER}),lambda d:d['devices'].update({RUNTIME:[{'udid':OWNER}]}),lambda d:d['devices'].update({RUNTIME:[dict(device(OTHER),hidden=device(OWNER))]}),lambda d:d['devices'].update({RUNTIME:[dict(device(OTHER),dataPath={'udid':OWNER})]}),lambda d:d['devices'].update({'com.apple.CoreSimulator.SimRuntime.unknown':[{'udid':OWNER}]})]:
   d=inventory();mutate(d);cases.append(json.dumps(d))
  for body in cases:
   with self.subTest(body=body):
    r,_=self.run_cleanup(body);self.assertEqual(r['cleanup']['status'],'failed')
    with self.assertRaises((ValueError,TypeError,KeyError)):C.validate_cleanup_receipt(r)
 def test_duplicate_uuid_or_json_key_fails(self):
  for body in [json.dumps(inventory([device(OTHER),device(OTHER.lower())])), '{"devices":{},"devices":{},"runtimes":[],"devicetypes":[],"pairs":{}}']:
   r,_=self.run_cleanup(body)
   with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_wrong_owner_or_query_binding_fails(self):
  for field in ['udid','create_stdout']:
   o=ownership();o[field]=OTHER
   with tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):O.cleanup_owned(o,Path(d),Mock())
  r,_=self.run_cleanup();r['cleanup']['readback']['owned_uuid']=OTHER
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
  r,_=self.run_cleanup();r['simulator']['udid']=OTHER
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
  r,_=self.run_cleanup();o=r['cleanup']['ownership'];o['create_stdout']=OWNER+' '*129;o['create_stdout_sha256']=hashlib.sha256(o['create_stdout'].encode()).hexdigest()
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_readback_failure_or_hash_tampering_fails(self):
  for kinds in [('timeout','success','timeout'),('timeout','success','nonzero')]:
   r,_=self.run_cleanup(kinds=kinds)
   with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
  for invalid in [45.0,True]:
   r,_=self.run_cleanup();r['cleanup']['actions'][0]['timeout_seconds']=invalid
   with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
  r,_=self.run_cleanup();r['cleanup']['readback']['inventory_sha256']='f'*64
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_failed_observation_cannot_be_rescued(self):
  r,_=self.run_cleanup();r['observation_status']='FAILED'
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_unreaped_process_fails_even_if_absent(self):
  r,_=self.run_cleanup();r['cleanup']['actions'][0].update(outcome='unreaped',reaped=False,returncode=None)
  with self.assertRaises(ValueError):C.validate_cleanup_receipt(r)
 def test_process_timeout_kills_only_private_group_and_reaps_bounded(self):
  import subprocess
  child=Mock(pid=12345);child.wait.side_effect=[subprocess.TimeoutExpired(['synthetic'],45),-9]
  with tempfile.TemporaryDirectory() as d,patch.object(O.subprocess,'Popen',return_value=child) as spawn,patch.object(O.os,'killpg') as kill:
   r=O.bounded_command(['xcrun','simctl','shutdown',OWNER],45,Path(d)/'shutdown-cleanup.log')
  self.assertTrue(spawn.call_args.kwargs['start_new_session']);self.assertNotEqual(spawn.call_args.kwargs['stdout'],subprocess.PIPE)
  kill.assert_called_once_with(12345,O.signal.SIGKILL);self.assertEqual(child.wait.call_args_list,[unittest.mock.call(timeout=45),unittest.mock.call(timeout=5)])
  self.assertEqual(r['outcome'],'timeout');self.assertTrue(r['reaped'])
 def test_actual_runner_and_portable_validator_bind_cleanup(self):
  root=Path(__file__).resolve().parent;s=(root/'run_attachment.py').read_text()
  self.assertIn('created_stdout=command(create_argv)',s);self.assertIn('creation_ownership(created_stdout,create_argv)',s)
  self.assertIn("report['observation_status']=report['status']",s);self.assertIn('cleanup_owned(ownership,work)',s)
  self.assertIn('validate_cleanup_receipt(attachment)',(root/'verify.py').read_text())
  self.assertIn('validate_cleanup_receipt(a)',(root.parent/'issues-native/multitask_receipt.py').read_text())
  for forbidden in ['shutdown all','delete all','pkill','killall','Task.detached']:
   self.assertNotIn(forbidden,(root/'owned_simulator_cleanup.py').read_text())
if __name__=='__main__':unittest.main(verbosity=2)
