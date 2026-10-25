"""Generate and validate the actual pairing extraction, not just generator exit status.

Local source-only guard; never claims the native case export or runtime passed.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from project_candidate_contracts import contracts
from verify_comprehensive_diagnostics import load_contract, pairing_recovery_extraction

def run(sources, output):
    sources=sources.resolve();output=output.resolve()
    contracts(sources,output/'contracts')
    contract=load_contract(output/'contracts/comprehensive-diagnostics-contract.json')
    subprocess.run([sys.executable,'-B',str(sources/'LiveContainer/.ci/comprehensive-diagnostics/generate_pairing_recovery_fixture.py'),
                    '--sources',str(sources),'--output',str(output/'pairing-recovery')],check=True,timeout=60,cwd=sources)
    report={'schema':'comprehensive-pairing-source-preflight-v1','native_runtime_executed':False,
            'case_names':contract['pairing_recovery_case_names'],
            'extraction':pairing_recovery_extraction(output,contract,before_compile=True)}
    (output/'source-preflight.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sources',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=run(a.sources,a.output);print('PAIRING_SOURCE_PREFLIGHT_PASS cases='+str(len(result['case_names'])))
