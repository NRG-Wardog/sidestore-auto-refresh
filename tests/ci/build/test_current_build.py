
import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/build/contracts'))
from pathlib import Path as _CIPath
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci/build/contracts')))
import source_selection as policy
ROOT = (_CIPath(__file__).resolve().parents[3])
WORKFLOW = ROOT / '.github/workflows/native-build.yml'

# This successor adds only these two mandatory commands at their reviewed sites.
# The inverse must restore the previous fingerprint, not merely accept a new hash.
PORTAL_ADDITIONS = (
    ('          python3 -B harness/tests/ci/native/test_owner_fixes_projection.py --sources sources\n',
     '          python3 -B harness/tests/ci/native/test_portal_identity_projection.py --sources sources\n'),
    ('          python3 -B harness/tests/ci/native/test_refresh_truth_orchestration.py\n',
     '          python3 -B harness/tests/ci/native/test_portal_native_gates.py --sources sources\n'),
)
PRE_PORTAL_STEPS_SHA256 = 'e1a25c2437f70abe138d8cfd37e3172e23c19babb9e1f47d989075c37b3f93bb'
PRE_EVIDENCE_STEPS_SHA256 = 'd4787df1038af1e7bf9dfa86b597ca1683599499a1ba18c7b01dc929d0357525'
EVIDENCE_ADDITION = (
    '          python3 -B harness/tests/ci/native/test_portal_identity_projection.py --sources sources\n',
    '          python3 -B harness/tests/ci/native/test_preservation_evidence_projection.py --sources sources\n',
)


PRE_TEST_OWNER_STEPS_SHA256 = '176a534e0d29d523300e47bfe576b586fe4ead1c8bd7f76845daa9aa6c3a65b2'
TEST_OWNER_ADDITION = (
    '          python3 -B harness/tests/ci/native/test_preservation_evidence_projection.py --sources sources\n',
    '          python3 -B harness/tests/ci/native/test_preservation_evidence_owner_successor.py --sources sources\n',
)


def reviewed_steps(text):
    steps = text[text.index('    steps:\n'):]
    for first, following in (
        ('Identify the exact maintained source graph before building', 'Check sync guards with source-owned synthetic fixtures only'),
        ('Verify PR merge tree equals the reviewed head tree', 'Record candidate harness identity'),
    ):
        start = steps.index('      - name: ' + first + '\n')
        end = steps.index('      - name: ' + following + '\n', start)
        steps = steps[:start] + steps[end:]
    return steps.replace('record.py receipt-current', 'record.py receipt')


def inverse_portal_additions(steps):
    for preceding, added in PORTAL_ADDITIONS:
        if steps.count(added) != 1 or steps.count(preceding + added) != 1:
            raise ValueError('Portal command missing, duplicated, changed or relocated')
        steps = steps.replace(preceding + added, preceding, 1)
    if hashlib.sha256(steps.encode()).hexdigest() != PRE_PORTAL_STEPS_SHA256:
        raise ValueError('Unreviewed change outside the two portal additions')
    return steps


def inverse_evidence_addition(steps):
    preceding, added = EVIDENCE_ADDITION
    if steps.count(added) != 1 or steps.count(preceding + added) != 1:
        raise ValueError('Evidence command missing, duplicated, changed or relocated')
    steps = steps.replace(preceding + added, preceding, 1)
    if hashlib.sha256(steps.encode()).hexdigest() != PRE_EVIDENCE_STEPS_SHA256:
        raise ValueError('Unreviewed change outside the evidence addition')
    return steps


def inverse_test_owner_addition(steps):
    preceding, added = TEST_OWNER_ADDITION
    if steps.count(added) != 1 or steps.count(preceding + added) != 1:
        raise ValueError('Evidence-owner command missing, duplicated, changed or relocated')
    steps = steps.replace(preceding + added, preceding, 1)
    if hashlib.sha256(steps.encode()).hexdigest() != PRE_TEST_OWNER_STEPS_SHA256:
        raise ValueError('Unreviewed change outside the evidence-owner addition')
    return steps


class CurrentBuildTests(unittest.TestCase):
    def test_current_entry_uses_explicit_committed_graph_and_receipt_gate(self):
        workflow = (ROOT / '.github/workflows/native-build.yml').read_text()
        self.assertIn('permissions:\n  contents: read', workflow)
        self.assertIn('ci/build/contracts/source_selection.py --graph harness/ci/build/candidate-graph.json', workflow)
        self.assertIn('verify_candidate_graph.py', workflow)
        self.assertIn('record.py receipt-current', workflow)
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertNotIn('contents: write', workflow)


    def setUp(self):
        self.identity = {'builder_commit': 'a'*40, 'run_id': '123', 'run_attempt': '1'}
        self.graph = ROOT/'ci/build/candidate-graph.json'

    def test_exact_graph_report_not_latest_or_release_claim(self):
        result = policy.selection(self.graph, self.identity)
        self.assertEqual(result['owners'], json.loads(self.graph.read_text())['owners'])
        self.assertEqual(result['source_graph_sha256'], hashlib.sha256(self.graph.read_bytes()).hexdigest())
        self.assertFalse(result['production_admission'])
        self.assertFalse(result['device_validation'])
        self.assertIn('not the newest remote branches', policy.summary(result))
        self.assertIn('Optional Zopfli recompression is not part', policy.summary(result))

    def test_reject_unpinned_graph_and_malformed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'graph.json'
            for row in [['NRG-Wardog/LiveContainer','main'],['NRG-Wardog/LiveContainer','a'*39],['bad|repository','a'*40]]:
                p.write_text(json.dumps({'owners':{'LiveContainer':row}}))
                with self.assertRaises(ValueError): policy.selection(p,self.identity)
            p.write_text('{"owners":{},"owners":{}}')
            with self.assertRaises(ValueError): policy.selection(p,self.identity)
        for key in self.identity:
            identity={**self.identity,key:''}
            with self.assertRaises(ValueError): policy.selection(self.graph,identity)

    def test_manual_build_has_no_branch_lock_or_automatic_publication(self):
        text=WORKFLOW.read_text()
        self.assertIn('  workflow_dispatch:\n',text)
        self.assertNotIn('  push:',text)
        self.assertIn("github.event_name == 'workflow_dispatch'",text)
        self.assertNotIn("github.ref == 'refs/heads/validation/cold-vpn'",text)
        self.assertIn('contents: read',text)
        self.assertNotIn('contents: write',text)
        self.assertIn('package_candidate.py --same-run-qualified-size --language-switch --cold-vpn --multitask',text)
        self.assertIn('record.py receipt-current',text)
        self.assertNotIn('record.py receipt\n',text)

    def test_reviewed_build_and_package_steps_preserved(self):
        current = reviewed_steps(WORKFLOW.read_text())
        self.assertEqual(hashlib.sha256(current.encode()).hexdigest(),
                         'b1ccfea335445decd62af1641efbe19a63f2ac1617625037a7a436317adee9cd')
        evidence_steps = inverse_test_owner_addition(current)
        self.assertEqual(hashlib.sha256(evidence_steps.encode()).hexdigest(), PRE_TEST_OWNER_STEPS_SHA256)
        prior = inverse_evidence_addition(evidence_steps)
        self.assertEqual(hashlib.sha256(prior.encode()).hexdigest(), PRE_EVIDENCE_STEPS_SHA256)
        steps = inverse_portal_additions(prior)
        self.assertEqual(hashlib.sha256(steps.encode()).hexdigest(),
                         'e1a25c2437f70abe138d8cfd37e3172e23c19babb9e1f47d989075c37b3f93bb')
        added = '          python3 -B harness/tests/ci/native/test_owner_fixes_projection.py --sources sources\n'
        self.assertEqual(steps.count(added), 1)
        self.assertEqual(hashlib.sha256(steps.replace(added, '', 1).encode()).hexdigest(),
                         '08ed1cd77a0420a2efd3f3de488d38ccc13168794a2ddfa0853add20a2db03a9')

    def test_test_owner_step_inverse_rejects_missing_duplicate_changed_relocated_and_unrelated_changes(self):
        current = reviewed_steps(WORKFLOW.read_text())
        preceding, added = TEST_OWNER_ADDITION
        mutations = (
            current.replace(added, '', 1),
            current.replace(added, added + added, 1),
            current.replace(added, added.replace('--sources sources', '--sources other'), 1),
            current.replace(added, '', 1) + added,
            current.replace(preceding + added, added + preceding, 1),
            current.replace('--same-run-qualified-size', '--unreviewed-size', 1),
            current.replace('record.py receipt', 'record.py receipt-current', 1),
            current.replace('          set -euo pipefail\n', '          set -eu\n', 1),
        )
        for changed in mutations:
            self.assertNotEqual(current, changed)
            with self.assertRaises(ValueError):
                inverse_test_owner_addition(changed)

    def test_evidence_step_inverse_rejects_missing_duplicate_changed_relocated_and_unrelated_changes(self):
        current = inverse_test_owner_addition(reviewed_steps(WORKFLOW.read_text()))
        preceding, added = EVIDENCE_ADDITION
        mutations = (
            current.replace(added, '', 1),
            current.replace(added, added + added, 1),
            current.replace(added, added.replace('--sources sources', '--sources other'), 1),
            current.replace(added, '', 1) + added,
            current.replace(preceding + added, added + preceding, 1),
            current.replace('--same-run-qualified-size', '--unreviewed-size', 1),
            current.replace('record.py receipt', 'record.py receipt-current', 1),
            current.replace('          set -euo pipefail\n', '          set -eu\n', 1),
        )
        for changed in mutations:
            self.assertNotEqual(current, changed)
            with self.assertRaises(ValueError):
                inverse_evidence_addition(changed)

    def test_portal_step_inverse_rejects_missing_duplicate_changed_and_relocated_commands(self):
        current = inverse_evidence_addition(inverse_test_owner_addition(reviewed_steps(WORKFLOW.read_text())))
        for preceding, added in PORTAL_ADDITIONS:
            mutations = (
                current.replace(added, '', 1),
                current.replace(added, added + added, 1),
                current.replace(added, added.replace('--sources sources', '--sources other'), 1),
                current.replace(added, '', 1) + added,
                current.replace(preceding + added, added + preceding, 1),
            )
            for changed in mutations:
                with self.subTest(added=added, mutation=changed[-80:]):
                    with self.assertRaises(ValueError):
                        inverse_portal_additions(changed)

    def test_portal_step_inverse_rejects_unrelated_build_receipt_and_packaging_changes(self):
        current = inverse_evidence_addition(inverse_test_owner_addition(reviewed_steps(WORKFLOW.read_text())))
        for before, after in (
            ('record.py receipt', 'record.py receipt-current'),
            ('--same-run-qualified-size', '--unreviewed-size'),
            ('id: livecontainer_build', 'id: bypassed_livecontainer_build'),
            ('          set -euo pipefail\n', '          set -eu\n'),
        ):
            self.assertIn(before, current)
            changed = current.replace(before, after, 1)
            with self.subTest(before=before), self.assertRaisesRegex(ValueError, 'Unreviewed change'):
                inverse_portal_additions(changed)

    def test_current_mode_requires_cms_without_historical_branch(self):
        # Execute the real receipt preamble, stopping before unrelated native
        # evidence work. Missing CMS must fail on main, not fall through.
        tree=ast.parse((ROOT/'ci/build/record.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='receipt')
        function.body=function.body[:2]
        code=compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'receipt-preamble','exec')
        calls=[]
        def verify(*args):
            calls.append(args)
            raise ValueError('missing CMS receipt')
        module=types.ModuleType('ci.qualification.native.preservation_cms_receipt');module.verify=verify
        def require(ok,message):
            if not ok: raise ValueError(message)
        namespace={'os':os,'E':Path('/no/evidence'),'S':Path('/no/sources'),
                   'require':require,'load_candidate_graph':lambda: {'owners':{}}}
        exec(code,namespace)
        env={'GITHUB_REF':'refs/heads/main','GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}
        with patch.dict(os.environ,env),patch.dict(sys.modules,{'ci.qualification.native.preservation_cms_receipt':module}):
            with self.assertRaisesRegex(ValueError,'missing CMS'): namespace['receipt'](current=True)
            self.assertEqual(len(calls),1)
            namespace['receipt'](current=False)
            with self.assertRaises(ValueError): namespace['receipt'](current='yes')

    def test_current_mode_requires_all_runtime_receipts_on_main(self):
        tree=ast.parse((ROOT/'ci/build/record.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='receipt')
        function.body=function.body[:3]
        code=compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'receipt-gates','exec')
        expected=[('ci.qualification.native.preservation_cms_receipt','verify'),('ci.qualification.native.refresh_truth_receipt','verify_local'),
                  ('ci.qualification.native.run_database_recovery','verify_local'),('ci.qualification.native.startup_upgrade_receipt','verify_local'),
                  ('ci.qualification.native.multitask_receipt','verify_local')]
        calls=[];modules={}
        for name,method in expected:
            module=types.ModuleType(name)
            setattr(module,method,lambda *args,_name=name,**kwargs:calls.append(_name))
            modules[name]=module
        def require(ok,message):
            if not ok: raise ValueError(message)
        namespace={'os':os,'E':Path('/no/evidence'),'S':Path('/no/sources'),'ROOT':ROOT,
                   'OWNERS':{},'require':require,'load_candidate_graph':lambda:{'owners':{}}}
        exec(code,namespace)
        env={'GITHUB_REF':'refs/heads/main','GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}
        with patch.dict(os.environ,env),patch.dict(sys.modules,modules):
            namespace['receipt'](current=True)
            self.assertEqual(calls,[row[0] for row in expected])
            for name,method in expected:
                with patch.object(modules[name],method,side_effect=ValueError('invalid runtime receipt')):
                    with self.assertRaisesRegex(ValueError,'invalid runtime receipt'):
                        namespace['receipt'](current=True)

    def test_all_workflow_shell_steps_parse(self):
        text=WORKFLOW.read_text()
        for block in text.split('        run: |\n')[1:]:
            lines=[]
            for line in block.splitlines():
                if line and not line.startswith('          '): break
                lines.append(line[10:] if line else '')
            script=re.sub(r'\$\{\{.*?\}\}','OWNED_CI_VALUE','\n'.join(lines))
            run=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)

if __name__=='__main__': unittest.main(verbosity=2)
