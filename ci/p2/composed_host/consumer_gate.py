"""Run the five exact maintained initializer consumer tests, rejecting skips."""
import argparse,json,os,sys,unittest
from pathlib import Path

def result_contract(result):
 return {'testsRun':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'successful':result.wasSuccessful() and result.testsRun==5 and not result.skipped}

def main():
 p=argparse.ArgumentParser()
 for n in ['host','peer','producer','output']:p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args()
 # These are source roots only. No account/authentication/service execution.
 os.environ['INIT_DIAGNOSTICS_PEER_ROOT']=str(a.peer.resolve())
 os.environ['INIT_DIAGNOSTICS_PRODUCER_ROOT']=str(a.producer.resolve())
 for key in ['SIDE_STORE_TEST_SOURCE','MAINTAINED_LIVE_CONTAINER_TEST_SOURCE','LIVE_CONTAINER_TEST_SOURCE']:
  os.environ.pop(key,None)
 suite=unittest.defaultTestLoader.discover(str(a.host/'tests/init_diagnostics'),pattern='test_contract.py')
 result=unittest.TextTestRunner(verbosity=2).run(suite);receipt=result_contract(result)
 a.output.write_text(json.dumps(receipt,sort_keys=True)+'\n')
 if not receipt['successful']:raise SystemExit('Composed native consumer tests did not all execute successfully')
if __name__=='__main__':main()
