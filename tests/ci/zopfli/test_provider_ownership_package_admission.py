"""Synthetic admission controls only. These do not claim native qualification."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import warnings
import zipfile

import provider_ownership_package_admission as a


class OwnershipAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # All fixture construction, trusted loading and admission must work with
        # process launch, DNS, sockets and URL access forbidden.
        for module, name in ((subprocess, 'run'), (subprocess, 'Popen'), (subprocess, 'check_call'),
                (subprocess, 'check_output'), (subprocess, 'call'), (os, 'system'), (os, 'popen'),
                (socket, 'socket'), (socket, 'create_connection'), (socket, 'getaddrinfo'),
                (urllib.request, 'urlopen')):
            guard = patch.object(module, name, side_effect=AssertionError('forbidden process/network: '+name))
            guard.start()
            self.addCleanup(guard.stop)
        self.contract, _, _, self.helper, graph = a.projection.trusted()
        original = b'synthetic input IPA bytes, not an app'
        (self.root/'original.ipa').write_bytes(original)
        self.manifest = {'commit': self.contract['native_builder'], 'run_id': 123, 'run_attempt': 1,
                         'ipa_bytes': len(original), 'ipa_sha256': a.safe.sha(original)}
        self.projector = self.root/'projector.py'
        self.projector.write_text("EVIDENCE_ID = 42\nEVIDENCE_BYTES = 100\nEVIDENCE_SHA = '"+'a'*64+"'\nraise AssertionError('projector code executed')\n")
        self.receipt = {'features': copy.deepcopy(self.helper.RUST_FEATURES),
            'required_tests': {key: list(value) for key, value in self.helper.REQUIRED_RUST_TESTS.items()},
            'rust_suites': 'PASS_NO_SKIPS', 'generated_header_c_and_cpp': 'PASS',
            'toolchain': '1.98.1', 'target_device_execution': False}
        self.logs = {package+'-tests.log': self.rust_log(tests)
                     for package, tests in self.helper.REQUIRED_RUST_TESTS.items()}
        self.logs[a.FILES[3]] = '\n'.join(self.helper.GENERATED_PROVIDER_MARKERS)+'\n'
        self.native = {'workflow_commit': self.manifest['commit'], 'run_id': '123', 'run_attempt': '1',
            'status': 'COMPILE_ONLY_PASS_REQUIRES_REVIEW', 'requested_graph': copy.deepcopy(graph['owners']),
            'physical_device_execution': False, 'authentication_executed': False, 'live_authentication_executed': False}
        self.build()

    @staticmethod
    def rust_log(tests):
        return ('running '+str(len(tests))+' tests\n'+''.join('test module::'+name+' ... ok\n' for name in tests)+
            'test result: ok. '+str(len(tests))+' passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n')

    def build(self, *, omit=None, extra=None, duplicate=None, receipt_raw=None, member_override=None):
        self.data = {name: text.encode() for name, text in self.logs.items()}
        self.data[a.FILES[0]] = receipt_raw or json.dumps(self.receipt).encode()
        if omit: self.data.pop(omit)
        if extra: self.data[extra] = b"raise AssertionError('artifact code executed')\n"
        self.native['evidence_sha256'] = {name: a.safe.sha(data) for name, data in self.data.items()}
        raw = json.dumps(self.native).encode()
        (self.root/'native-receipt.json').write_bytes(raw)
        out = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
            warnings.simplefilter('ignore', UserWarning)
            archive.writestr('native-receipt.json', raw)
            for name, data in self.data.items():
                archive.writestr(name, member_override[1] if member_override and member_override[0] == name else data)
            if duplicate: archive.writestr(duplicate, self.data[duplicate])
        archive = out.getvalue()
        (self.root/'native-evidence-projection.zip').write_bytes(archive)
        self.report = {'schema': 'exact-native-evidence-projection-v1', 'execution': False,
            'native_run': {key: self.manifest[key] for key in ('commit', 'run_id', 'run_attempt')},
            'source_artifact_id': 42, 'source_archive_bytes': 100, 'source_archive_sha256': 'a'*64,
            'native_receipt_sha256': a.safe.sha(raw), 'projection_sha256': a.safe.sha(archive),
            'projection_bytes': len(archive), 'verified_reference_sha256': self.native['evidence_sha256'].copy(),
            'verified_reference_count': len(self.data)}
        self.save_report()

    def save_report(self):
        (self.root/'native-evidence-projection.json').write_text(json.dumps(self.report))

    def verify(self):
        return a.verify(self.root, self.manifest, self.projector)

    def test_positive_no_process_network_native_or_artifact_execution(self):
        self.build(extra='never_execute.py')
        result = self.verify()
        self.assertEqual(result['test_counts'], {'idevice': {'required_named_tests': 4, 'actual_passed_tests': 4},
            'idevice-ffi': {'required_named_tests': 25, 'actual_passed_tests': 25}})
        self.assertEqual(result['input_ipa_sha256'], self.manifest['ipa_sha256'])
        self.assertEqual(set(result['evidence_sha256']), set(a.FILES))
        for flag in ('device_executed', 'live_authentication_executed', 'adapter_native_execution'):
            self.assertIs(result[flag], False)

    def test_other_actual_passing_tests_counted_without_weakening_required_inventory(self):
        for package, tests in self.helper.REQUIRED_RUST_TESTS.items():
            self.logs[package+'-tests.log'] = self.rust_log((*tests, 'another_actual_test'))
        self.build()
        result = self.verify()
        self.assertEqual(result['test_counts']['idevice']['actual_passed_tests'], 5)
        self.assertEqual(result['test_counts']['idevice-ffi']['actual_passed_tests'], 26)

    def test_every_required_real_named_test_is_mandatory_and_unique(self):
        original = self.logs.copy()
        for package, tests in self.helper.REQUIRED_RUST_TESTS.items():
            for test in tests:
                line = 'test module::'+test+' ... ok\n'
                for replacement in ('', line+line, line.replace(' ... ok', ' ... ignored'), line.replace(test, 'wrong_name')):
                    self.logs = original.copy()
                    self.logs[package+'-tests.log'] = original[package+'-tests.log'].replace(line, replacement)
                    self.build()
                    with self.subTest(package=package, test=test, replacement=replacement), self.assertRaises(ValueError):
                        self.verify()
        self.logs = original

    def test_wrong_counts_and_filtered_ignored_failed_duplicate_summaries_rejected(self):
        original = self.logs.copy()
        for package in self.helper.REQUIRED_RUST_TESTS:
            name = package+'-tests.log'; text = original[name]
            cases = [text.replace('0 ignored', '1 ignored'), text.replace('0 filtered out', '1 filtered out'),
                text.replace('0 failed', '1 failed'), text.replace('0 measured', '1 measured'),
                text.replace(' passed;', '0 passed;'), text.replace('running ', 'running 9'),
                text+text.splitlines()[-1]+'\n', text+'test rogue ... ignored\n',
                text.replace('running ', 'not running ')]
            for changed in cases:
                self.logs = original.copy(); self.logs[name] = changed; self.build()
                with self.subTest(package=package, changed=changed), self.assertRaises(ValueError): self.verify()
        self.logs = original

    def test_generated_markers_missing_duplicate_wrong_paths_and_wrappers_rejected(self):
        original = self.logs[a.FILES[3]]
        for marker in self.helper.GENERATED_PROVIDER_MARKERS:
            for changed in (original.replace(marker+'\n', ''), original+marker+'\n',
                    original.replace(marker, marker+' altered'), original.replace('paths=13', 'paths=12'),
                    original.replace('complete_wrappers=12', 'complete_wrappers=11'),
                    original+marker.split(' ', 1)[0]+' unknown\n'):
                self.logs[a.FILES[3]] = changed; self.build()
                with self.assertRaises(ValueError): self.verify()
        self.logs[a.FILES[3]] = original

    def test_receipt_inventory_scope_toolchain_and_duplicate_json_keys_rejected(self):
        original = copy.deepcopy(self.receipt)
        mutations = [lambda v: v.update(target_device_execution=True), lambda v: v.update(target_device_execution=0),
            lambda v: v.update(toolchain='1.98.0'), lambda v: v.update(rust_suites='PASS'),
            lambda v: v.update(generated_header_c_and_cpp='NOT_RUN'), lambda v: v.update(extra=True),
            lambda v: v['required_tests']['idevice-ffi'].pop(), lambda v: v['features'].update(idevice='wrong')]
        for mutate in mutations:
            self.receipt = copy.deepcopy(original); mutate(self.receipt); self.build()
            with self.assertRaises(ValueError): self.verify()
        self.receipt = original
        self.build(receipt_raw=json.dumps(original).replace('{', '{"target_device_execution":true,', 1).encode())
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.verify()

    def test_all_four_files_mandatory_hash_qualified_and_no_duplicates(self):
        for name in a.FILES:
            for options in ({'omit': name}, {'duplicate': name}, {'member_override': (name, b'changed')}):
                self.build(**options)
                with self.subTest(name=name, options=options), self.assertRaises(ValueError): self.verify()

    def test_native_run_attempt_commit_graph_and_execution_scope_rejected(self):
        original = copy.deepcopy(self.native)
        for key, value in (('run_id', '456'), ('run_attempt', '2'), ('workflow_commit', '0'*40),
                ('status', 'FAIL'), ('requested_graph', {}), ('physical_device_execution', True),
                ('authentication_executed', True), ('live_authentication_executed', True),
                ('physical_device_execution', 0)):
            self.native = copy.deepcopy(original); self.native[key] = value; self.build()
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()
        self.native = original

    def test_previous_native_builder_receipt_cannot_be_relabelled_as_current(self):
        previous = '849742a0718bdf5409926a6885e92cc013c7a6ef'
        self.manifest['commit'] = previous; self.native['workflow_commit'] = previous; self.build()
        with self.assertRaisesRegex(ValueError, 'unreviewed native builder'): self.verify()

    def test_projection_source_archive_run_and_hash_binding(self):
        for key, value in (('source_artifact_id', 43), ('source_archive_bytes', 101),
                ('source_archive_sha256', 'b'*64), ('source_archive_bytes', 100.0),
                ('verified_reference_count', 3), ('verified_reference_count', 4.0),
                ('execution', True), ('projection_sha256', 'c'*64),
                ('native_receipt_sha256', 'd'*64), ('native_run', {'run_id': 123, 'run_attempt': True, 'commit': self.manifest['commit']})):
            self.build(); self.report[key] = value; self.save_report()
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()
        self.build(); self.report['verified_reference_sha256'][a.FILES[0]] = '0'*64; self.save_report()
        with self.assertRaises(ValueError): self.verify()

    def test_input_ipa_actual_bytes_and_identity_rejected(self):
        (self.root/'original.ipa').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'input IPA bytes'): self.verify()

    def test_trusted_source_tampering_rejected_before_execution(self):
        read = a.safe.read
        for target in ('run_issue_tests.py', 'verify_candidate_graph.py', 'candidate-graph.json'):
            def corrupt(path, *args):
                data = read(path, *args)
                return data+b"\nraise AssertionError('untrusted code executed')\n" if str(path).endswith('/'+target) else data
            with patch.object(a.safe, 'read', side_effect=corrupt), self.assertRaisesRegex(ValueError, 'trusted source differs'):
                self.verify()

    def test_workflow_keeps_three_existing_gates_and_adds_precompression_final_retention(self):
        here = Path(a.__file__).resolve().parent
        workflow = (here.parents[2]/'.github/workflows/package-optimization.yml').read_text()
        for name in ('cms_package_admission.py', 'owned_oda_package_binding.py',
                     'initial_rejection_package_admission.py', 'provider_ownership_package_admission.py'):
            self.assertLess(workflow.index('python3 -B ci/packaging/zopfli/'+name), workflow.index('1550s python3 -B workspace/sweep.py'))
        self.assertEqual(workflow.count('workspace/'+a.REPORT+'\n'), 2)
        audit = (here/'independent_verify.py').read_text()
        for text in ("provider_ownership_admission=verify_provider_ownership(R,input_manifest,R/'owned-oda-projector.snapshot')",
                     "assert provider_ownership_admission==read_json(R/'provider-ownership-package-admission.json')",
                     "'input_ipa_sha256':sha(old),'final_ipa_sha256':sha(new)"):
            self.assertIn(text, audit)


if __name__ == '__main__':
    unittest.main(verbosity=2)
