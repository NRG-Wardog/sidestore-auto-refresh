import importlib.util,json,os,pathlib,subprocess,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('acquisition',ROOT/'acquire.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class AcquisitionTests(unittest.TestCase):
    def make(self,directory):
        root=pathlib.Path(directory)/'owner';root.mkdir();a.git(root,'init')
        (root/'source.swift').write_text('let value = 1\n')
        a.git(root,'add','source.swift')
        a.git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
        return root,{'commit':a.git(root,'rev-parse','HEAD').decode().strip(),'tree':a.git(root,'rev-parse','HEAD^{tree}').decode().strip()}
    def test_real_git_inventory_and_changed_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,expected=self.make(d);before=a.inventory(root,expected);self.assertIn('source.swift',before)
            (root/'source.swift').write_text('changed\n')
            with self.assertRaises(ValueError):a.inventory(root,expected)
    def test_staged_untracked_and_mode_changes_rejected(self):
        for action in ('staged','untracked','mode'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as d:
                root,expected=self.make(d)
                if action=='staged':
                    (root/'source.swift').write_text('changed\n');a.git(root,'add','source.swift')
                elif action=='untracked':(root/'new.swift').write_text('new')
                else:(root/'source.swift').chmod(0o755)
                with self.assertRaises(ValueError):a.inventory(root,expected)
    def test_head_tree_or_linked_root_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,expected=self.make(d)
            for key in ('commit','tree'):
                with self.assertRaises(ValueError):a.inventory(root,dict(expected,**{key:'0'*40}))
            link=pathlib.Path(d)/'linked';link.symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):a.inventory(link,expected)
    def test_imported_and_executed_harness_scripts_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            harness=pathlib.Path(directory)/'harness';root=harness/'ci/startup-upgrade';root.mkdir(parents=True)
            graph=harness/'ci/issues-native/candidate-graph.json'
            files=['ci/startup-upgrade/acquire.py','ci/issues-native/candidate-graph.json',
                   'ci/issues-native/verify_candidate_graph.py','ci/issue56-startup/run_startup_tests.py',
                   '.github/workflows/startup-upgrade-preflight.yml','.github/workflows/cold-vpn-native.yml','ci/issues-native/startup_upgrade_receipt.py']
            for name in files:
                path=harness/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            with patch.object(a,'ROOT',root),patch.object(a,'GRAPH',graph):
                before=a.harness_hashes()
                for name in files:
                    path=harness/name;path.write_text('changed')
                    self.assertNotEqual(before,a.harness_hashes());path.write_text('fixture')
    def test_identity_requires_actual_run_fields(self):
        valid={'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'2'}
        with patch.dict(os.environ,valid,clear=True):self.assertEqual(a.identity()['run_attempt'],'2')
        for key in valid:
            with patch.dict(os.environ,{**valid,key:'bad'},clear=True),self.assertRaises(ValueError):a.identity()
    def test_exact_two_owner_scope_and_https_only(self):
        self.assertEqual(set(a.OWNERS),{'LiveContainer','SideStore'})
        graph=a.selected_graph();self.assertEqual(set(graph),set(a.OWNERS))
        text=(ROOT/'acquire.py').read_text()
        self.assertIn("os.environ.get('GIT_ALLOW_PROTOCOL')=='https'",text)
        self.assertIn("'submodules_acquired':False",text)
        self.assertNotIn("submodule','update",text)
    def test_workflow_required_independent_stages_and_outside_checkout(self):
        text=(ROOT.parents[1]/'.github/workflows/startup-upgrade-preflight.yml').read_text()
        for stage in ('controls','startup','schema','immutable'):
            self.assertIn("test '${{ steps."+stage+".outcome }}' = success",text)
        self.assertEqual(text.count("if: ${{ !cancelled() && steps.controls.outcome == 'success' }}"),2)
        self.assertIn('SIDESTORE_SOURCE="$PWD/sources/SideStore"',text)
        self.assertIn('test "$(pwd -P)" != "$(cd harness && pwd -P)"',text)
        self.assertNotIn('continue-on-error',text)
        self.assertIn('GIT_ALLOW_PROTOCOL: https',text)
    def test_schema_report_binds_actual_acquisition_before_and_after(self):
        text=(ROOT/'run.py').read_text()
        self.assertIn("verify(source.parent,args.acquisition,output/'source-before.json')",text)
        self.assertIn("verify(source.parent,args.acquisition,output/'source-after.json')",text)
        self.assertIn("'run_identity':identity()",text)
        self.assertIn("'other_graph_owners_acquired':False",text)
if __name__=='__main__':unittest.main()
