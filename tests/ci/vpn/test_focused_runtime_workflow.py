
import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/qualification/vpn'))
from pathlib import Path as _CIPath
import re,subprocess,unittest
from pathlib import Path
import sys
sys.path.insert(0,str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job

ROOT=(_CIPath(__file__).resolve().parents[3])
def steps(path,job=None):
 result=[]
 for block in workflow_job(path,job).split('      - name: ')[1:]:
  row={'name':block.splitlines()[0]}
  for key in ['id','if']:
   match=re.search(r'^        '+key+r': (.+)$',block,re.M)
   if match:row[key]=match[1]
  if '        run: |\n' in block:
   lines=[]
   for line in block.split('        run: |\n',1)[1].splitlines():
    if line and not line.startswith('          '):break
    lines.append(line[10:] if line else '')
   row['run']='\n'.join(lines)
  result.append(row)
 return result
class FocusedRuntimeTests(unittest.TestCase):
 def test_actual_native_commands_reused_and_not_model_substitute(self):
  fs=steps(ROOT/'.github/workflows/focused-diagnostics.yml','cold_vpn');ns=steps(ROOT/'.github/workflows/native-build.yml')
  for script in ['verify_setup_state_recovery.py','run_language_switch_tests.py']:
   def actual(steps):
    run=next(x['run'] for x in steps if script in x.get('run',''))
    joined=run.replace('\\\n',' ');line=next(x.strip() for x in joined.splitlines() if 'python3' in x and script in x)
    return re.sub(r'\s+',' ',line)
   self.assertEqual(actual(fs),actual(ns))
  text=workflow_job(ROOT/'.github/workflows/focused-diagnostics.yml', 'cold_vpn')
  self.assertLess(text.index('project_candidate_contracts.py'),text.index('verify_setup_state_recovery.py run'))
  self.assertNotIn('continue-on-error',text);self.assertNotIn('package_candidate.py',text);self.assertNotIn('build_ios.py',text)
  self.assertIn("steps.setup_tests.outcome == 'failure'",text)
  self.assertEqual(text.count("            evidence/setup-state-recovery/\n"),2)
  self.assertNotIn("evidence/setup-state-recovery/**/*.log",text)
  for identifier in ['setup_tests','language_switch_tests']:
   step=next(x for x in fs if x.get('id')==identifier)
   self.assertEqual(step['if'],"${{ !cancelled() && steps.source_gate.outcome == 'success' }}")
 def test_all_focused_shell_blocks_parse(self):
  for step in steps(ROOT/'.github/workflows/focused-diagnostics.yml','cold_vpn'):
   if 'run' in step:
    script=re.sub(r'\$\{\{.*?\}\}','OWNED_CI_VALUE',step['run'])
    result=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
    self.assertEqual(result.returncode,0,(step['name'],result.stderr))
if __name__=='__main__':unittest.main(verbosity=2)
