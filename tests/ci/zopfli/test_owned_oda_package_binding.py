"""Source-integration and real portable capsule controls; never compressor/native execution."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
from pathlib import Path as _CIPath
import ast
import json
from pathlib import Path
import tempfile
import unittest
import owned_oda_package_binding as binding
import owned_oda_package_admission as admission
import test_owned_oda_package_admission as fixtures

HERE = (_CIPath(__file__).resolve().parents[3]/'ci/packaging/zopfli')


def integration_contract(test, projector, final_audit, workflow):
    tree = ast.parse(projector)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    calls = [node for node in main.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Name) and node.value.func.id == 'project_owned_oda']
    test.assertEqual(len(calls), 1)
    test.assertEqual([ast.unparse(arg) for arg in calls[0].value.args], ['archive', 'out', 'm', 'Path(__file__).resolve()'])
    writes = [node for node in main.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'write_text']
    test.assertTrue(any(node.lineno < calls[0].lineno and 'native-evidence-projection.json' in ast.unparse(node) for node in writes))
    audit = ast.parse(final_audit)
    assignments = [node for node in audit.body if isinstance(node, ast.Assign)]
    call = next(node.value for node in assignments if any(isinstance(target, ast.Name) and target.id == 'owned_oda_admission' for target in node.targets))
    test.assertEqual(ast.unparse(call), "verify_owned_oda_package(R, input_manifest, R / 'owned-oda-projector.snapshot')")
    report = next(node.value for node in assignments if any(isinstance(target, ast.Name) and target.id == 'report' for target in node.targets))
    fields = {key.value: value for key, value in zip(report.keys, report.values)}
    test.assertEqual(ast.unparse(fields['owned_oda_admission']), 'owned_oda_admission')
    test.assertEqual(ast.unparse(fields['owned_oda_admission_sha256']), "sha((R / 'owned-oda-package-admission.json').read_bytes())")
    test.assertEqual(ast.unparse(fields['final_sha256']), 'sha(new)')
    test.assertEqual(ast.unparse(fields['input']), 'input_manifest')
    for existing in ('verify_cms_package(R,input_manifest)', 'verify_refresh_truth_portable(refresh_report,native_report[\'requested_graph\'],language_report[\'run_identity\'])'):
        test.assertIn(existing, final_audit)
    stage = workflow.split('      - name: Revalidate exact owned ODA capsule before compression\n', 1)[1].split('      - name:', 1)[0]
    test.assertNotIn('if:', stage)
    test.assertIn('python3 -B ci/packaging/zopfli/owned_oda_package_binding.py --root workspace --projector ci/packaging/zopfli/project_native_evidence.py', stage)
    test.assertLess(workflow.index('Revalidate exact owned ODA capsule'), workflow.index('Bounded two-worker short iteration'))
    projection = workflow.split('      - name: Retain exact native evidence projection separately\n', 1)[1].split('      - name:', 1)[0]
    for filename in ('native-evidence-projection.zip', 'native-evidence-projection.json', admission.CAPSULE, admission.REPORT, binding.ADMISSION):
        test.assertIn('workspace/' + filename, projection)
    for line in ('cp ci/packaging/zopfli/{owned_oda_package_admission.py,owned_oda_package_binding.py,owned-oda-source-contract.json} workspace/',
                 'cp -R ci/packaging/zopfli/reviewed-native workspace/reviewed-native',
                 'cp ci/packaging/zopfli/project_native_evidence.py workspace/owned-oda-projector.snapshot'):
        test.assertIn(line, workflow)
    final = workflow.split('      - name: Upload only the final qualified copy and immutable lineage\n', 1)[1].split('      - name:', 1)[0]
    test.assertIn('if: success()', final); test.assertIn('workspace/' + binding.ADMISSION, final)
    test.assertNotIn('continue-on-error', workflow)


class SourceIntegrationControls(unittest.TestCase):
    def inputs(self):
        return ((HERE/'project_native_evidence.py').read_text(), (HERE/'independent_verify.py').read_text(),
                ((HERE.parents[2]/'.github/workflows/package-optimization.yml')).read_text())

    def test_both_admission_calls_retention_and_existing_portable_gates_are_mandatory(self):
        integration_contract(self, *self.inputs())

    def test_missing_or_optional_integration_is_detected(self):
        projector, audit, workflow = self.inputs()
        changes = [
            (projector.replace('    project_owned_oda(archive, out, m, Path(__file__).resolve())\n', ''), audit, workflow),
            (projector, audit.replace('verify_owned_oda_package(R,input_manifest', 'omitted_verifier(R,input_manifest'), workflow),
            (projector, audit, workflow.replace('            workspace/owned-oda-projection.zip\n', '')),
            (projector, audit, workflow.replace('      - name: Revalidate exact owned ODA capsule before compression\n', '      - name: Revalidate exact owned ODA capsule before compression\n        if: false\n')),
            (projector, audit, workflow.replace('          cp ci/packaging/zopfli/project_native_evidence.py workspace/owned-oda-projector.snapshot\n', '')),
        ]
        for index, values in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(AssertionError): integration_contract(self, *values)

    def test_archive_identity_is_derived_from_current_projector_without_duplicate_pins(self):
        import project_native_evidence as projector
        expected, source_sha = binding.artifact_from_source(HERE/'project_native_evidence.py')
        self.assertEqual(expected, {'artifact_id': projector.EVIDENCE_ID, 'bytes': projector.EVIDENCE_BYTES, 'sha256': projector.EVIDENCE_SHA})
        self.assertEqual(source_sha, admission.sha((HERE/'project_native_evidence.py').read_bytes()))
        tree = ast.parse((HERE/'owned_oda_package_binding.py').read_text())
        literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
        self.assertNotIn(projector.EVIDENCE_SHA, literals); self.assertNotIn(projector.EVIDENCE_ID, literals)

    def test_source_pins_are_literals_unique_and_typed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.snapshot'
            good="EVIDENCE_ID = 3\nEVIDENCE_BYTES = 99\nEVIDENCE_SHA = '"+'a'*64+"'\nraise AssertionError('source must be parsed, not executed')\n"
            path.write_text(good); self.assertEqual(binding.artifact_from_source(path)[0]['artifact_id'], 3)
            for bad in (good+'EVIDENCE_ID = 4\n', good.replace('EVIDENCE_ID = 3','EVIDENCE_ID = True'),
                        good.replace('EVIDENCE_ID = 3','EVIDENCE_ID = 1 + 2'), good.replace('EVIDENCE_BYTES = 99\n','')):
                path.write_text(bad)
                with self.assertRaises(ValueError): binding.artifact_from_source(path)


class BoundPortableControls(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PackageControls('test_roundtrip_fixed_nine_files_with_real_validator_and_no_subprocess')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f=self.fixture; self.source=f.root/'trusted-projector.snapshot'
        self.source.write_text('EVIDENCE_ID = '+repr(f.expected['artifact_id'])+'\nEVIDENCE_BYTES = '+repr(f.expected['bytes'])+'\nEVIDENCE_SHA = '+repr(f.expected['sha256'])+'\n')

    def project(self):
        f=self.fixture;return binding.project(f.archive, f.workspace, f.manifest, self.source)

    def verify(self):
        f=self.fixture;return binding.verify(f.workspace, f.manifest, self.source)

    def test_same_real_verification_before_compression_and_final_audit(self):
        result=self.project();self.assertEqual(result,self.verify())
        self.assertEqual(result['trusted_projector_sha256'],admission.sha(self.source.read_bytes()))
        self.assertEqual(len(result['admission']['reference_sha256']),9)

    def test_missing_capsule_projection_or_admission_fails(self):
        self.project()
        for name in (admission.CAPSULE,admission.REPORT,binding.ADMISSION):
            path=self.fixture.workspace/name;raw=path.read_bytes();path.unlink()
            with self.subTest(name=name),self.assertRaises(ValueError):self.verify()
            path.write_bytes(raw)

    def test_source_comment_drift_and_artifact_identity_drift_fail(self):
        self.project();original=self.source.read_bytes()
        for changed in (original+b'# source changed\n',original.replace(b'EVIDENCE_ID = 3',b'EVIDENCE_ID = 4')):
            self.source.write_bytes(changed)
            with self.assertRaises(ValueError):self.verify()
        self.source.write_bytes(original)
        manifest=dict(self.fixture.manifest,run_attempt=999)
        with self.assertRaises(ValueError):binding.verify(self.fixture.workspace,manifest,self.source)

    def test_saved_binding_hash_or_result_substitution_fails(self):
        self.project();path=self.fixture.workspace/binding.ADMISSION;original=path.read_bytes()
        for field,bad in [('trusted_projector_sha256','0'*64),('admission',{}),('extra',True)]:
            value=json.loads(original);value[field]=bad;path.write_text(json.dumps(value))
            with self.subTest(field=field),self.assertRaises(ValueError):self.verify()
            path.write_bytes(original)


if __name__=='__main__':unittest.main(verbosity=2)
