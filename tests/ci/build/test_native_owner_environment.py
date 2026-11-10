"""Exercise the real native checkout topology: harness/ beside acquired sources/."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT=Path(__file__).resolve().parents[3]
WORKFLOW=ROOT/'.github/workflows/native-build.yml'

class NativeOwnerEnvironmentTests(unittest.TestCase):
    def test_all_owner_roots_and_package_imports_are_explicit(self):
        text=WORKFLOW.read_text()
        owners={'LIVE_CONTAINER':'LiveContainer','SIDESTORE':'SideStore','SIDESIGN':'SideStore/Dependencies/SideSign',
                'MINIMUXER':'SideStore/Dependencies/minimuxer','IDEVICE':'idevice','ANISETTEKIT':'AnisetteKit'}
        for variable,path in owners.items():
            self.assertIn('MAINTAINED_'+variable+'_TEST_SOURCE: ${{ github.workspace }}/sources/'+path,text)
        self.assertIn('PYTHONPATH: ${{ github.workspace }}/harness:${{ github.workspace }}/harness/scripts',text)
        self.assertEqual(text.count("top_level_dir='harness'"),2)
        self.assertLess(text.index('record.py acquire'),text.index("python3 - <<'PYTEST'"))
        self.assertNotIn('generation-inputs/',text)
        self.assertNotIn('EMBEDDED_SIDESTORE_TEST_SOURCE',text)

    def test_actual_discovery_uses_sibling_owner_and_rejects_missing_or_wrong_roots(self):
        text=WORKFLOW.read_text()
        body=text.split("          python3 - <<'PYTEST'\n",1)[1].split('          PYTEST',1)[0]
        script='\n'.join(line[10:] for line in body.splitlines())
        script=script.replace("('test_v3_service.py', 'test_v3_auth_recovery_audit.py')", "('test_owner_boundary.py',)")
        with tempfile.TemporaryDirectory() as name:
            workspace=Path(name).resolve();harness=workspace/'harness'
            (harness/'tests').mkdir(parents=True);(harness/'tests/__init__.py').touch()
            shutil.copy2(ROOT/'tests/owner_source.py',harness/'tests/owner_source.py')
            (harness/'ci/build').mkdir(parents=True)
            source=workspace/'sources/LiveContainer';source.mkdir(parents=True)
            (source/'Owner.swift').write_text('current owner\n')
            subprocess.run(['git','init','-q',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'add','Owner.swift'],check=True)
            subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                            'commit','-qm','synthetic current owner'],check=True)
            commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
            (harness/'ci/build/candidate-graph.json').write_text(json.dumps({'owners':{'LiveContainer':['fixture',commit]}}))
            (harness/'tests/test_owner_boundary.py').write_text(
                "import unittest\nfrom tests.owner_source import owner_source\n"
                "class Boundary(unittest.TestCase):\n"
                " def test_current(self):self.assertEqual(owner_source('Owner.swift'), 'current owner\\n')\n")
            base={k:v for k,v in os.environ.items() if not k.startswith('MAINTAINED_')}
            base['PYTHONPATH']=str(harness)+':'+str(harness/'scripts')
            for target in (str(source),None,str(workspace/'generation-inputs/LiveContainer')):
                env=dict(base)
                if target is not None:env['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']=target
                result=subprocess.run(['python3','-B','-c',script],cwd=workspace,env=env,capture_output=True,text=True)
                with self.subTest(target=target):
                    self.assertEqual(result.returncode==0,target==str(source),result.stdout+result.stderr)
                    self.assertNotIn('ModuleNotFoundError',result.stderr)

    def test_lts_inline_verifier_import_resolves_current_package(self):
        text = WORKFLOW.read_text()
        block = text.split("          python3 -B - <<'PY_VERIFY_CMS'\n", 1)[1].split('          PY_VERIFY_CMS', 1)[0]
        imports = textwrap.dedent(block).split('cms.host_build(', 1)[0]
        current = 'from ci.qualification.native import preservation_cms_receipt as cms'
        self.assertIn(current, imports)
        # Run the actual workflow import preamble, not the host build or verifier.
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            (workspace/'harness').symlink_to(ROOT, target_is_directory=True)
            env = dict(os.environ, PYTHONPATH=str(workspace/'harness')+':'+str(workspace/'harness/scripts'))
            check = "\nassert pathlib.Path(cms.__file__).resolve() == pathlib.Path('harness/ci/qualification/native/preservation_cms_receipt.py').resolve()\n"
            for script, valid in ((imports, True), (imports.replace(current, 'import preservation_cms_receipt as cms'), False)):
                result = subprocess.run(['python3', '-B', '-c', script+check], cwd=workspace,
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, valid, result.stdout+result.stderr)
                if not valid:
                    self.assertIn("No module named 'preservation_cms_receipt'", result.stderr)

    def test_packaging_deferred_imports_resolve_in_native_topology(self):
        import ast
        path = ROOT/'ci/packaging/native/package_candidate.py'
        tree = ast.parse(path.read_text())
        statements = []
        for function in tree.body:
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        statements.append(node)
                    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                        if ast.unparse(node.value.func) == 'sys.path.insert':
                            statements.append(node)
        statements.sort(key=lambda node: node.lineno)
        imports = '\n'.join(ast.unparse(node) for node in statements)
        current = 'from ci.qualification.startup.run import verify_receipt as verify_startup_receipt'
        self.assertIn(current, imports)
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            (workspace/'harness').symlink_to(ROOT, target_is_directory=True)
            env = dict(os.environ, PYTHONPATH=str(workspace/'harness')+':'+str(workspace/'harness/scripts'))
            preamble = "import runpy\nns=runpy.run_path('harness/ci/packaging/native/package_candidate.py')\n"
            check = "\nassert verify_startup_receipt.__module__ == 'ci.qualification.startup.run'\n"
            for body, valid in ((imports, True), (imports.replace(current, 'from run_startup_tests import verify_receipt as verify_startup_receipt'), False)):
                script = preamble + 'exec('+repr(body+check)+', ns)\n'
                result = subprocess.run(['python3','-B','-c',script],cwd=workspace,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode == 0, valid, result.stdout+result.stderr)
                if not valid:
                    self.assertIn("No module named 'run_startup_tests'",result.stderr)
