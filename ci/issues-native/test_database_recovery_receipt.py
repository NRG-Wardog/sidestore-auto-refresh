"""Portable adversarial and actual producer controls, not native qualification."""
import copy
import ast
import importlib.util
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import run_database_recovery as m

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.schema, self.native, self.evidence = [self.root / name for name in ('schema', 'native', 'evidence')]
        self.evidence.mkdir()
        self.identity = {'workflow_commit': 'a'*40, 'run_id': '123', 'run_attempt': '1'}
        self.graph = {'SideStore': ['NRG-Wardog/SideStore', 'b'*40], 'LiveContainer': ['NRG-Wardog/LiveContainer', 'c'*40]}
        for root in (self.schema, self.native):
            for name in (*m.BASE, m.MODEL+'/.xccurrentversion', m.MODEL+'/v1.xcdatamodel/contents'):
                p = root/'SideStore'/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(name)
        (self.evidence/'schema-acquisition.json').write_text('{}')
        guard = patch.object(m, 'native_settings', side_effect=lambda source: self.settings(m.sha(source/m.PROJECT)))
        guard.start(); self.addCleanup(guard.stop)
        self.calls = []
        self.output = self.evidence/'database-recovery-tests.json'

    def acquire(self, source, evidence, output):
        self.assertEqual(source, self.schema); self.assertEqual(evidence, self.evidence)
        output.write_text('{}')

    @staticmethod
    def settings(digest):
        return {'target':'SideStore','configuration':'Release','declared':'5.0','swiftc_argument':'5','project_sha256':digest}

    def reports(self):
        closure = m.sources(self.schema/'SideStore')
        engine = {'schema':'sidestore-exact-recovery-sqlite-v2','physical_device_execution':False,'limitations':['synthetic','test','only'],'swift_build_settings':self.settings(closure[m.PROJECT]),'status': 'PASS_NATIVE_EXACT_ENGINE_SYNTHETIC_SQLITE', 'cases': list(m.ENGINE_CASES),
            'production_coredata_validation': False, 'exact_production_sources_compiled_without_rewriting': True,
            'source_sha256': {k: closure[k] for k in m.ENGINE_INPUTS}, 'swift_version': 'synthetic', 'platform': 'synthetic'}
        coredata = {'schema': 'sidestore-exact-recovery-coredata-v2', 'status':'PASS_NATIVE_EXACT_ENGINE_REAL_COREDATA', 'swift_build_settings':self.settings(closure[m.PROJECT]), 'swift_version':'synthetic', 'physical_device_execution':False,'synthetic_data_only':True,'app_startup_or_installed_app_runtime_executed':False,
            'cases': [{'mode': mode, 'status': 'PASS', 'original_persistent_bytes_unchanged': True} for mode in ('delete', 'wal')],
            'inputs': {k:v for k,v in closure.items() if k in m.COREDATA_INPUTS or k.startswith(m.MODEL+'/')}}
        return engine, coredata

    def execute(self, command, log, cwd):
        self.calls.append(command)
        self.assertNotIn(cwd, (self.schema, self.native))
        self.assertTrue(command[2].startswith(str(self.schema/'SideStore')))
        self.assertEqual(command[1], '-B')
        output = Path(command[-1]); self.assertFalse(output.exists()); output.mkdir()
        kind = output.name
        for name in m.FILES:
            if name.startswith(kind+'/') and name.endswith('.log'): (output/Path(name).name).write_text('synthetic log\n')
        engine, coredata = self.reports()
        (output/('report.json' if kind == 'engine' else 'receipt.json')).write_text(json.dumps(engine if kind == 'engine' else coredata))
        log.write_text('synthetic producer command\n')

    def produce(self, execute=None):
        with patch.object(m, 'acquisition_verify', side_effect=self.acquire), patch.object(m, 'execute', side_effect=execute or self.execute):
            value = m.produce(self.output, self.native, self.schema, self.evidence, self.graph, self.identity)
        settings = self.root/'effective.json'; settings.write_text(json.dumps([{'target':'SideStore','buildSettings':{'CONFIGURATION':'Release','SWIFT_VERSION':'5.0'}}]))
        m.seal_effective(settings,self.evidence,self.graph,self.identity)
        return value

    def test_actual_producer_and_local_verifier(self):
        value = self.produce()
        self.assertEqual(len(self.calls), 2)
        with patch.object(m, 'acquisition_verify', side_effect=self.acquire):
            self.assertEqual(m.verify_local(self.output, self.native, self.schema, self.evidence, self.graph, self.identity), value)
        self.assertEqual(set(value['evidence_sha256']), set(m.FILES))
        self.assertTrue(all((self.evidence/m.flat(name)).is_file() for name in m.FILES))
        with self.assertRaises(ValueError): self.produce()

    def test_producer_really_invokes_both_source_owned_commands(self):
        # Tiny synthetic executables exercise the real subprocess path. This is
        # a producer control, not a substitute for the macOS qualification.
        engine, coredata = self.reports()
        for kind, name, report, field, result in (
            ('engine','tests/database_recovery/run.py',engine,'source_sha256','report.json'),
            ('coredata','tests/database_recovery_coredata/run.py',coredata,'inputs','receipt.json')):
            script = "import pathlib,json,hashlib,sys\n"
            script += "root=pathlib.Path(__file__).resolve().parents[2]\n"
            script += "report=" + repr(report) + "\n"
            script += "field=" + repr(field) + "\n"
            script += "report[field]={k:hashlib.sha256((root/k).read_bytes()).hexdigest() for k in report[field]}\n"
            script += "out=pathlib.Path(sys.argv[-1]);out.mkdir()\n"
            script += "(out/"+repr(result)+").write_text(json.dumps(report))\n"
            script += "logs="+repr([Path(v).name for v in m.FILES if v.startswith(kind+'/') and v.endswith('.log')])+"\n"
            script += "for name in logs:(out/name).write_text('synthetic execution log\\n')\n"
            script += "print('SYNTHETIC DRIVER EXECUTED')\n"
            for root in (self.schema,self.native): (root/'SideStore'/name).write_text(script)
        with patch.object(m,'acquisition_verify',side_effect=self.acquire):
            m.produce(self.output,self.native,self.schema,self.evidence,self.graph,self.identity)
        for kind in ('engine','coredata'):
            self.assertIn('SYNTHETIC DRIVER EXECUTED',(self.evidence/'database-recovery'/(kind+'-driver.log')).read_text())

    def test_failed_source_command_with_plausible_report_never_seals(self):
        def falsegreen(command,log,cwd):
            self.execute(command,log,cwd)
            raise subprocess.CalledProcessError(1,command)
        with self.assertRaises(subprocess.CalledProcessError): self.produce(falsegreen)
        self.assertFalse(self.output.exists())

    def test_effective_language_mode_and_same_run_are_mandatory(self):
        value=self.produce(); path=self.evidence/'database-recovery-settings.json'; proof=m.read(path)
        for edit in (lambda v:v.update(effective_swift_version='6.0'),lambda v:v['identity'].update(run_id='999'),lambda v:v.update(recovery_receipt_sha256='0'*64)):
            wrong=copy.deepcopy(proof);edit(wrong)
            with self.assertRaises(ValueError):m.verify_effective(value,m.sha(self.output),self.identity,wrong)
        path.unlink()
        with patch.object(m,'acquisition_verify',side_effect=self.acquire),self.assertRaises(ValueError):
            m.verify_local(self.output,self.native,self.schema,self.evidence,self.graph,self.identity)

    def test_stale_raw_evidence_and_source_ancestor_alias_rejected(self):
        (self.evidence/'database-recovery').mkdir()
        with self.assertRaises(ValueError):self.produce()
        self.assertFalse(self.output.exists())
        (self.evidence/'database-recovery').rmdir()
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        self.schema=alias/'schema'
        with self.assertRaises(ValueError):self.produce()
        self.assertFalse(self.output.exists())

    def test_native_failure_cannot_emit_pass(self):
        def failure(*args): raise subprocess.CalledProcessError(1, args[0])
        with self.assertRaises(subprocess.CalledProcessError): self.produce(failure)
        self.assertFalse(self.output.exists())
        self.assertEqual(m.read(self.evidence/'database-recovery/failure.json')['status'], 'FAILED_OR_NOT_RUN')

    def test_missing_or_duplicate_case_and_skip_are_failures(self):
        value = self.produce()
        for edit in (lambda v:v['engine']['cases'].pop(), lambda v:v['engine']['cases'].append(v['engine']['cases'][0]),
                     lambda v:v['coredata']['cases'][0].update(status='SKIP'), lambda v:v['coredata']['cases'].pop(),
                     lambda v:v['engine'].update(exact_production_sources_compiled_without_rewriting=False)):
            wrong = copy.deepcopy(value); edit(wrong)
            with self.assertRaises(ValueError): m.verify_portable(wrong, self.graph, self.identity)

    def test_every_root_and_security_case_required_once(self):
        value = self.produce()
        required = {'approved-root-canonicalization', 'approved-root-leaf-symlink',
                    'missing-approved-roots', 'invalid-approved-ancestor',
                    'inaccessible-approved-ancestor', 'aliased-roots-share-selection',
                    'reset-lock-alias-contention'}
        self.assertEqual(len(m.ENGINE_CASES),24)
        self.assertTrue(required <= set(m.ENGINE_CASES))
        for case in required:
            wrong=copy.deepcopy(value);wrong['engine']['cases'].remove(case)
            with self.assertRaises(ValueError):m.verify_portable(wrong,self.graph,self.identity)
            wrong=copy.deepcopy(value);wrong['engine']['cases'].append(case)
            with self.assertRaises(ValueError):m.verify_portable(wrong,self.graph,self.identity)
        old=copy.deepcopy(value);old['engine']['cases']=[c for c in old['engine']['cases'] if c not in required]
        with self.assertRaises(ValueError):m.verify_portable(old,self.graph,self.identity)

    def test_identity_source_verifier_and_evidence_tamper(self):
        value = self.produce()
        edits = (lambda v:v['identity'].update(run_attempt='2'), lambda v:v['source_graph']['SideStore'].__setitem__(1,'d'*40),
                 lambda v:v.update(verifier_sha256='0'*64), lambda v:v['evidence_sha256'].pop(m.FILES[0]),
                 lambda v:v['evidence_sha256'].update({'../escape.log':'0'*64}), lambda v:v.update(physical_device_execution=True),
                 lambda v:v['engine']['source_sha256'].update({m.BASE[0]:'0'*64}), lambda v:v['source_sha256'].pop(m.BASE[2]))
        for edit in edits:
            wrong = copy.deepcopy(value); edit(wrong)
            with self.assertRaises((ValueError, KeyError)): m.verify_portable(wrong, self.graph, self.identity)

    def test_effective_query_wrong_mode_target_and_duplicates_fail(self):
        value=self.produce(); proof=self.evidence/'database-recovery-settings.json'; proof.unlink()
        path=self.root/'settings-negative.json'
        cases=[[],[{'target':'Wrong','buildSettings':{'CONFIGURATION':'Release','SWIFT_VERSION':'5.0'}}],
               [{'target':'SideStore','buildSettings':{'CONFIGURATION':'Release','SWIFT_VERSION':'6.0'}}],
               [{'target':'SideStore','buildSettings':{'CONFIGURATION':'Debug','SWIFT_VERSION':'5.0'}}]]
        row={'target':'SideStore','buildSettings':{'CONFIGURATION':'Release','SWIFT_VERSION':'5.0'}}
        cases.append([row,row])
        for rows in cases:
            path.write_text(json.dumps(rows))
            with self.assertRaises(ValueError):m.seal_effective(path,self.evidence,self.graph,self.identity)
            self.assertFalse(proof.exists())

    def test_projected_native_mode_mismatch_blocks_producer(self):
        def wrong(source):
            value=self.settings(m.sha(source/m.PROJECT));value.update(declared='6.0',swiftc_argument='6');return value
        with patch.object(m,'native_settings',side_effect=wrong),self.assertRaises(ValueError):self.produce()
        self.assertFalse(self.output.exists())

    def test_retained_raw_log_tamper(self):
        self.produce(); (self.evidence/m.flat('engine/native.log')).write_text('changed')
        with patch.object(m, 'acquisition_verify', side_effect=self.acquire), self.assertRaises(ValueError):
            m.verify_local(self.output, self.native, self.schema, self.evidence, self.graph, self.identity)

    def test_native_source_drift(self):
        self.produce(); (self.native/'SideStore'/m.BASE[0]).write_text('changed')
        with patch.object(m, 'acquisition_verify', side_effect=self.acquire), self.assertRaises(ValueError):
            m.verify_local(self.output, self.native, self.schema, self.evidence, self.graph, self.identity)

    def test_source_mutation_during_execution_blocks_pass(self):
        def mutate(command, log, cwd):
            self.execute(command, log, cwd)
            if 'database_recovery_coredata' in command[2]: (self.schema/'SideStore'/m.BASE[0]).write_text('changed')
        with self.assertRaises(ValueError): self.produce(mutate)
        self.assertFalse(self.output.exists())

    def test_linked_source_rejected(self):
        target = self.schema/'SideStore'/m.BASE[0]; target.unlink(); target.symlink_to(self.native/'SideStore'/m.BASE[0])
        with self.assertRaises(ValueError): self.produce()
        self.assertFalse(self.output.exists())

    def test_duplicate_json_keys_rejected(self):
        path = self.root/'duplicate.json'; path.write_text('{"status":"PASS","status":"SKIP"}')
        with self.assertRaises(ValueError): m.read(path)

    def test_portable_verifier_outside_checkout(self):
        value = self.produce(); portable = self.root/'portable'; portable.mkdir()
        shutil.copyfile(m.__file__, portable/'run_database_recovery.py')
        (portable/'receipt.json').write_text(json.dumps(value))
        code = "import run_database_recovery as m; from pathlib import Path; v=m.read(Path('receipt.json')); m.verify_portable(v,v['source_graph'],v['identity'])"
        subprocess.run([sys.executable,'-B','-c',code], cwd=portable, check=True)

    def test_mandatory_workflow_and_both_package_checks(self):
        root = Path(m.__file__).resolve().parents[2]
        workflow = (root/'.github/workflows/cold-vpn-native.yml').read_text()
        step = workflow.split('id: database_recovery_tests',1)[1].split('      - name:',1)[0]
        self.assertIn('set -euo pipefail', step)
        self.assertNotIn('continue-on-error',step); self.assertNotIn('|| true',step)
        self.assertIn('--schema-sources schema-sources',step)
        self.assertLess(workflow.index('id: schema_tests'),workflow.index('id: database_recovery_tests'))
        self.assertLess(workflow.index('id: database_recovery_tests'),workflow.index('id: livecontainer_build'))
        package = (root/'ci/issues-native/package_candidate.py').read_text()
        self.assertEqual(package.count('verify_database_recovery(database_recovery_receipt,'),2)
        self.assertIn("verify_database_recovery(E / 'database-recovery-tests.json'",(root/'ci/issues-native/record.py').read_text())

def verify_real_source_inventory(source):
    """Inspect the acquired source, never a downloaded evidence artifact."""
    source=Path(source).resolve(strict=True)
    tree=ast.parse((source/'tests/database_recovery/run.py').read_text())
    cases=[n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EXPECTED_CASES' for t in n.targets)]
    if len(cases)!=1 or sorted(ast.literal_eval(cases[0]))!=m.ENGINE_CASES:
        raise ValueError('Real source-owned SQLite case inventory differs')
    closure=m.sources(source)
    old_path=sys.path.copy();previous=sys.modules.pop('target_settings',None)
    try:
        for kind,folder,expected in [('engine','database_recovery',m.ENGINE_INPUTS),
                ('coredata','database_recovery_coredata',m.COREDATA_INPUTS|{k for k in closure if k.startswith(m.MODEL+'/')})]:
            spec=importlib.util.spec_from_file_location('_inventory_'+kind,source/'tests'/folder/'run.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            actual={p.relative_to(source).as_posix() for p in module.source_paths()}
            if actual!=expected:raise ValueError('Real '+kind+' source input closure differs')
    finally:
        sys.path[:]=old_path;sys.modules.pop('target_settings',None)
        if previous is not None:sys.modules['target_settings']=previous
    print('PASS_REAL_SOURCE_INVENTORY_24_SQLITE_7_INPUTS_30_COREDATA_INPUTS_NOT_NATIVE_EXECUTION')

if __name__ == '__main__':
    if '--source' in sys.argv:
        index=sys.argv.index('--source');source=sys.argv[index+1];del sys.argv[index:index+2]
        verify_real_source_inventory(source)
    unittest.main()

