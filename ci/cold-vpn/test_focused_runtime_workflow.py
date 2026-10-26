import re,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def steps(path):
 result=[]
 for block in path.read_text().split('      - name: ')[1:]:
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
  fs=steps(ROOT/'.github/workflows/cold-vpn-preflight.yml');ns=steps(ROOT/'.github/workflows/cold-vpn-native.yml')
  for script in ['verify_setup_state_recovery.py','run_language_switch_tests.py']:
   def actual(steps):
    run=next(x['run'] for x in steps if script in x.get('run',''))
    joined=run.replace('\\\n',' ');line=next(x.strip() for x in joined.splitlines() if 'python3' in x and script in x)
    return re.sub(r'\s+',' ',line)
   self.assertEqual(actual(fs),actual(ns))
  text=(ROOT/'.github/workflows/cold-vpn-preflight.yml').read_text()
  self.assertLess(text.index('project_candidate_contracts.py'),text.index('verify_setup_state_recovery.py run'))
  self.assertNotIn('continue-on-error',text);self.assertNotIn('package_candidate.py',text);self.assertNotIn('build_ios.py',text)
  self.assertIn("steps.setup_tests.outcome == 'failure'",text)
  for identifier in ['setup_tests','language_switch_tests']:
   step=next(x for x in fs if x.get('id')==identifier)
   self.assertEqual(step['if'],"${{ !cancelled() && steps.source_gate.outcome == 'success' }}")
 def test_all_focused_shell_blocks_parse(self):
  for step in steps(ROOT/'.github/workflows/cold-vpn-preflight.yml'):
   if 'run' in step:
    script=re.sub(r'\$\{\{.*?\}\}','OWNED_CI_VALUE',step['run'])
    result=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
    self.assertEqual(result.returncode,0,(step['name'],result.stderr))
if __name__=='__main__':unittest.main(verbosity=2)
