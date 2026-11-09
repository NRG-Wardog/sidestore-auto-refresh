"""Exercise the real native checkout topology: harness/ beside acquired sources/."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
