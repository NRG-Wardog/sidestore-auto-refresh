"""The narrow PR build validates the exact reviewed tree before native work."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
WORKFLOW=ROOT/'.github/workflows/native-build.yml'

class NativePRIdentityTests(unittest.TestCase):
    def test_only_workflow_changes_trigger_same_repository_pr_builds(self):
        text=WORKFLOW.read_text()
        self.assertEqual(text.split('  pull_request:\n',1)[1].split('permissions:',1)[0],
                         '    paths:\n      - .github/workflows/native-build.yml\n')
        self.assertNotIn('  push:',text)
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository',text)
        self.assertIn('cancel-in-progress: false',text)
        self.assertLess(text.index('Verify PR merge tree'),text.index('Record candidate harness identity'))
        self.assertIn('contents: read',text)
    def test_actual_shell_gate_accepts_only_equal_trees_and_retains_both_identities(self):
        block=WORKFLOW.read_text().split('      - name: Verify PR merge tree',1)[1].split('      - name:',1)[0]
        body=block.split('        run: |\n',1)[1]
        script='\n'.join(line[10:] for line in body.splitlines())
        fake='''git() {
          case "$*" in
            *fetch*) return 0 ;;
            *"rev-parse HEAD^{tree}") printf '%s\\n' "$MERGE_TREE" ;;
            *"rev-parse $PR_HEAD_SHA^{tree}") printf '%s\\n' "$HEAD_TREE" ;;
            *) return 97 ;;
          esac
        }
'''
        for equal in (True,False):
            with self.subTest(equal=equal),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'evidence').mkdir()
                env={**os.environ,'PR_HEAD_SHA':'a'*40,'PR_BASE_SHA':'b'*40,'GITHUB_SHA':'c'*40,
                     'MERGE_TREE':'d'*40,'HEAD_TREE':('d' if equal else 'e')*40}
                result=subprocess.run(['bash','-c',fake+script],cwd=root,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode==0,equal,result.stderr)
                receipt=(root/'evidence/pr-tree-identity.txt').read_text()
                for key in ('merge_sha','base_sha','head_sha','merge_tree','head_tree'):
                    self.assertIn(key+'=',receipt)
