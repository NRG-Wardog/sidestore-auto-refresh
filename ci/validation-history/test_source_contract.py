import copy
import json
from pathlib import Path
import tempfile
import unittest
import source_contract as s
import relocation
from unittest.mock import patch

class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(relocation, 'BASELINE_SHA256', {}))
        self.enterContext(patch.object(s, 'verify_auxiliary'))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.history={p:('old '+p).encode() for p in s.OVERRIDES}
        self.current={p:('new '+p).encode() for p in s.OVERRIDES|s.DEPENDENCIES|s.ADDED_WORKFLOWS}
        self.history['security.py']=self.current['security.py']=b'keep strict privacy checks'
        self.current['migration/archive.yml']=b'archive frozen'
        self.current['.github/workflows/validation-history-contract.yml']=b'history workflow'
        self.m={'protected_sha256':{p:s.sha(b) for p,b in self.history.items()},'archived_workflows_sha256':{'migration/archive.yml':s.sha(b'archive frozen')}}
        self.c={'schema':'reviewed-main-source-successor-v1','historical_manifest_sha256':'','historical_commit':s.HISTORICAL,'qualified_commit':s.QUALIFIED,'qualified_tree':s.TREE,'protected_successors':{p:{'historical_sha256':s.sha(self.history[p]),'reviewed_sha256':s.sha(self.current[p]),'origin':'source_guard_repair' if p.endswith('maintained-packaging-contract.yml') else 'qualified_candidate'} for p in s.OVERRIDES},'added_workflows_sha256':{p:s.sha(self.current[p]) for p in s.ADDED_WORKFLOWS},'dependency_sha256':{p:s.sha(self.current[p]) for p in s.DEPENDENCIES}}
        for p,b in self.current.items():self.put(p,b)
        self.history['.ci/validation-history/manifest.json']=json.dumps(self.m).encode()
        self.put('ci/validation-history/manifest.json',json.dumps(self.m).encode());self.c['historical_manifest_sha256']=s.sha(json.dumps(self.m).encode());self.save()
    def put(self,p,b):
        p=self.root/p;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    def save(self):self.put('ci/validation-history/reviewed-successor.json',json.dumps(self.c).encode())
    def objects(self,root,ref,path):
        if ref == s.BASELINE and path == '.ci/validation-history/reviewed-successor.json':
            return (self.root/'ci/validation-history/reviewed-successor.json').read_bytes()
        return (self.history if ref==s.HISTORICAL else self.current)[path]
    def verify(self):return s.verify_sources(self.root,object_read=self.objects,tree_read=lambda root,ref:s.TREE)
    def test_exact_successor_and_historical_source_pass(self):self.verify()
    def test_unknown_protected_override_rejected(self):
        self.c['protected_successors']['security.py']=dict(next(iter(self.c['protected_successors'].values())));self.save()
        with self.assertRaisesRegex(ValueError,'scope'):self.verify()
    def test_changed_privacy_guard_rejected(self):
        self.put('security.py',b'weakened')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
    def test_historical_manifest_not_rewritten(self):
        self.put('ci/validation-history/manifest.json',json.dumps(dict(self.m,extra=True)).encode())
        with self.assertRaisesRegex(ValueError,'Frozen historical'):self.verify()
    def test_coordinated_manifest_and_digest_tamper_rejected(self):
        self.m['protected_sha256'].pop('security.py')
        raw=json.dumps(self.m).encode();self.put('ci/validation-history/manifest.json',raw)
        self.c['historical_manifest_sha256']=s.sha(raw);self.save()
        with self.assertRaisesRegex(ValueError,'Frozen historical manifest object'):self.verify()
    def test_historical_mapping_not_rewritten(self):
        self.c['protected_successors']['scripts/package_livecontainer_combined.py']['historical_sha256']='0'*64;self.save()
        with self.assertRaisesRegex(ValueError,'Historical mapping'):self.verify()
    def test_current_source_tamper_rejected(self):
        self.put('scripts/package_livecontainer_combined.py',b'tampered')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
    def test_source_cannot_be_rebound_to_other_commit(self):
        self.c['qualified_commit']='a'*40;self.save()
        with self.assertRaisesRegex(ValueError,'lineage'):self.verify()
    def test_qualified_object_mismatch_rejected(self):
        self.current['scripts/package_livecontainer_combined.py']=b'other'
        with self.assertRaisesRegex(ValueError,'Qualified source object'):self.verify()
    def test_original_historical_object_mismatch_rejected(self):
        self.history['scripts/package_livecontainer_combined.py']=b'other'
        with self.assertRaisesRegex(ValueError,'Historical source object'):self.verify()
    def test_dependency_omission_rejected(self):
        self.c['dependency_sha256'].pop('scripts/ipa_size_report.py');self.save()
        with self.assertRaisesRegex(ValueError,'dependency closure'):self.verify()
    def test_dependency_bytes_tamper_rejected(self):
        self.put('scripts/packaging_adapters.py',b'tampered')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()
    def test_workflow_scope_omission_rejected(self):
        self.c['added_workflows_sha256'].pop(next(iter(s.ADDED_WORKFLOWS)));self.save()
        with self.assertRaisesRegex(ValueError,'workflow scope'):self.verify()
    def test_unknown_workflow_rejected(self):
        self.put('.github/workflows/unreviewed.yml',b'write permissions')
        with self.assertRaisesRegex(ValueError,'workflow inventory'):self.verify()
    def test_workflow_bytes_tamper_rejected(self):
        self.put(next(iter(s.ADDED_WORKFLOWS)),b'tampered')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()
    def test_linked_protected_source_rejected(self):
        p=self.root/'security.py';p.unlink();p.symlink_to(self.root/'migration/archive.yml')
        with self.assertRaisesRegex(ValueError,'linked'):self.verify()
    def test_duplicate_contract_keys_rejected(self):
        raw=json.dumps(self.c);self.put('ci/validation-history/reviewed-successor.json',(raw[:-1]+',"schema":"reviewed-main-source-successor-v1"}').encode())
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.verify()
    def test_qualified_tree_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'Git tree'):
            s.verify_sources(self.root,object_read=self.objects,tree_read=lambda root,ref:'0'*40)
    def test_archived_workflow_tamper_rejected(self):
        self.put('migration/archive.yml',b'new history')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
class RelocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = {name: s.git_read(s.ROOT, relocation.BASELINE, name)
                       for name in relocation.BASELINE_SHA256}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name, raw in self.objects.items():
            for prefix, replacement in relocation.PATH_REWRITES.get(name, ()):
                raw = raw.replace(prefix.encode(), replacement.encode())
            for original, replacement in relocation.WORKFLOW_LAYOUT_EDITS.get(name, ()):
                raw = raw.replace(original.encode(), replacement.encode())
            path = self.root / relocation.RELOCATED.get(name, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def object_read(self, root, ref, path):
        self.assertEqual(ref, relocation.BASELINE)
        return self.objects[path]

    def verify(self, name):
        relocation.verify_current(self.root, name, relocation.BASELINE_SHA256[name], self.object_read)

    def test_all_enumerated_relocations_and_path_edits_pass(self):
        for name in relocation.BASELINE_SHA256:
            with self.subTest(name=name): self.verify(name)
        relocation.verify_auxiliary(self.root, self.object_read)

    def test_every_relocated_or_rewritten_file_rejects_tampering(self):
        for name in relocation.BASELINE_SHA256:
            with self.subTest(name=name):
                path = self.root / relocation.RELOCATED.get(name, name)
                original = path.read_bytes()
                path.write_bytes(original + b'\n# unauthorized change\n')
                with self.assertRaisesRegex(ValueError, 'Relocated source bytes'): self.verify(name)
                path.write_bytes(original)

    def test_unlisted_source_receives_no_path_rewrite(self):
        raw = b'load .ci/unreviewed/file'
        (self.root/'unreviewed.py').write_bytes(raw.replace(b'.ci/', b'ci/'))
        with self.assertRaisesRegex(ValueError, 'Reviewed source bytes'):
            relocation.verify_current(self.root, 'unreviewed.py', s.sha(raw), self.object_read)

    def test_unlisted_destination_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        path = self.root/relocation.RELOCATED[name]
        path.rename(path.with_name('renamed.py'))
        with self.assertRaisesRegex(ValueError, 'Missing or linked'): self.verify(name)

    def test_wrong_baseline_object_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        with self.assertRaisesRegex(ValueError, 'baseline'):
            relocation.verify_current(self.root, name, relocation.BASELINE_SHA256[name],
                                      lambda *args: b'changed baseline')

    def test_changed_expected_hash_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        with self.assertRaisesRegex(ValueError, 'baseline'):
            relocation.verify_current(self.root, name, '0'*64, self.object_read)

    def test_old_source_copy_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        path = self.root/name
        path.parent.mkdir(parents=True)
        path.write_bytes(self.objects[name])
        with self.assertRaisesRegex(ValueError, 'Obsolete relocation'): self.verify(name)

    def test_symlinked_destination_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        path = self.root/relocation.RELOCATED[name]
        target = self.root/'target.py'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'linked'): self.verify(name)

    def test_symlinked_directory_rejected(self):
        name = '.ci/branch-forwardports/run.py'
        path = (self.root/relocation.RELOCATED[name]).parent
        target = self.root/'target'
        path.rename(target)
        path.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked relocation'): self.verify(name)

    def test_old_directory_rejected_even_if_empty(self):
        (self.root/'.ci').mkdir()
        with self.assertRaisesRegex(ValueError, 'Obsolete .ci'):
            relocation.verify_auxiliary(self.root, self.object_read)

    def test_other_repositories_and_historical_api_paths_unchanged(self):
        name = '.ci/branch-forwardports/run.py'
        raw = (self.root/relocation.RELOCATED[name]).read_bytes()
        self.assertIn(b'.ci/iloader-auth-parity/test_source.py', raw)
        self.assertIn(b'.ci/iloader-auth-parity/generate.py', raw)
        name = '.ci/release-v6.0.0/promote.py'
        self.assertEqual((self.root/relocation.RELOCATED[name]).read_bytes(), self.objects[name])
        self.assertIn(b'.ci/release-v6.0.0/reviewed-inputs.json', self.objects[name])

    def test_completed_mutation_workflows_are_archived_exactly(self):
        self.assertEqual(s.ARCHIVED_WORKFLOWS,
                         {'amend-v6.0.0-notes.yml', 'publish-v6.0.0.yml', 'reviewed-branch-cleanup.yml', 'reviewed-branch-semantic-inventory.yml', 'reviewed-branch-forwardports.yml', 'reviewed-branch-classify.yml'})
        for basename in s.ARCHIVED_WORKFLOWS:
            name = '.github/workflows/' + basename
            self.assertNotIn(name, relocation.PATH_REWRITES)
            self.assertFalse((s.ROOT/name).exists())
            self.assertEqual((s.ROOT/relocation.RELOCATED[name]).read_bytes(), self.objects[name])

    def test_archived_mutation_workflow_reactivation_rejected(self):
        name = '.github/workflows/amend-v6.0.0-notes.yml'
        (self.root/name).write_bytes(self.objects[name])
        with self.assertRaisesRegex(ValueError, 'Obsolete relocation'): self.verify(name)

    def test_real_checkout_and_frozen_manifests_pass(self):
        s.verify_sources()
        for name in ('manifest.json', 'reviewed-successor.json'):
            self.assertEqual((s.ROOT/'ci/validation-history'/name).read_bytes(),
                             s.git_read(s.ROOT, relocation.BASELINE, '.ci/validation-history/'+name))

    def test_linux_semantic_suite_is_required_before_native_jobs(self):
        for basename, native_job in (
                ('validation-history-contract.yml', 'bounded-contracts'),
                ('maintained-packaging-contract.yml', 'packaging-contract')):
            text = (s.ROOT/'.github/workflows'/basename).read_text()
            linux, native = text.split('  '+native_job+':\n', 1)
            self.assertIn('  historical-semantic:\n', linux)
            self.assertIn('    runs-on: ubuntu-24.04\n', linux)
            self.assertIn("-p 'test_*.py' -v", linux)
            self.assertTrue(native.startswith('    needs: historical-semantic\n'))
            self.assertIn('    runs-on: macos-26\n', native)
            self.assertNotIn('continue-on-error', text)
            self.assertNotIn('if:', linux)

    def test_required_linux_job_cannot_be_disabled_or_narrowed(self):
        for name in relocation.WORKFLOW_LAYOUT_EDITS:
            path = self.root/name
            original = path.read_bytes()
            mutations = (
                (b'    needs: historical-semantic\n', b''),
                (b"-p 'test_*.py'", b"-p 'test_source_contract.py'"),
                (b'    runs-on: ubuntu-24.04', b'    runs-on: macos-26'),
                (b'  historical-semantic:\n', b'  historical-semantic:\n    continue-on-error: true\n'),
                (relocation.LINUX_SEMANTIC_JOB.encode(), b''),
            )
            for before, after in mutations:
                with self.subTest(workflow=name, mutation=before):
                    self.assertIn(before, original)
                    path.write_bytes(original.replace(before, after))
                    with self.assertRaisesRegex(ValueError, 'Relocated source bytes'):
                        self.verify(name)
                    path.write_bytes(original)

    def test_frozen_successor_cannot_be_coordinately_rebound(self):
        def objects(root, ref, name):
            raw = s.git_read(root, ref, name)
            return raw + b' ' if name.endswith('reviewed-successor.json') else raw
        with self.assertRaisesRegex(ValueError, 'Frozen reviewed successor object'):
            s.verify_sources(object_read=objects)

class HistoricalSemanticFixtureTests(unittest.TestCase):
    def test_missing_or_tampered_inputs_rejected_before_copy(self):
        import historical_semantic as h
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()/'source'
            for original in h.INPUTS:
                path = root/relocation.RELOCATED[original]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((s.ROOT/relocation.RELOCATED[original]).read_bytes())
            def objects(root, ref, name): return s.git_read(s.ROOT, ref, name)
            for original in h.INPUTS:
                with self.subTest(original=original):
                    path = root/relocation.RELOCATED[original]
                    raw = path.read_bytes()
                    path.write_bytes(raw + b'tampered')
                    destination = Path(folder).resolve()/'fixture'
                    with self.assertRaisesRegex(ValueError, 'fixture input differs'):
                        h.prepare_fixture(root, destination, objects)
                    self.assertFalse(destination.exists())
                    path.unlink()
                    with self.assertRaisesRegex(ValueError, 'Missing or linked'):
                        h.prepare_fixture(root, destination, objects)
                    self.assertFalse(destination.exists())
                    path.write_bytes(raw)

    def test_baseline_mismatch_rejected_before_copy(self):
        import historical_semantic as h
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder).resolve()/'fixture'
            with self.assertRaisesRegex(ValueError, 'fixture baseline differs'):
                h.prepare_fixture(s.ROOT, destination, lambda *args: b'unreviewed')
            self.assertFalse(destination.exists())

    def test_suite_failure_propagates(self):
        import historical_semantic as h
        import subprocess
        with patch.object(h, 'prepare_fixture'), patch.object(
                h.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'test')) as run:
            with self.assertRaises(subprocess.CalledProcessError): h.run_tests()
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.kwargs['check'])
            self.assertEqual(run.call_args.kwargs['timeout'], 120)

class TemporaryRootAliasTests(unittest.TestCase):
    def test_platform_temp_alias_is_resolved_without_accepting_in_tree_links(self):
        # macOS commonly exposes temporary storage through /var -> /private/var.
        # Resolve the newly allocated fixture root, never its untrusted children.
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            physical = parent/'physical'
            physical.mkdir()
            alias = parent/'alias'
            alias.symlink_to(physical, target_is_directory=True)
            suite = unittest.TestSuite([
                SourceContractTests('test_exact_successor_and_historical_source_pass'),
                RelocationTests('test_all_enumerated_relocations_and_path_edits_pass'),
                RelocationTests('test_symlinked_destination_rejected'),
                RelocationTests('test_symlinked_directory_rejected'),
                HistoricalSemanticFixtureTests('test_missing_or_tampered_inputs_rejected_before_copy'),
            ])
            result = unittest.TestResult()
            with patch.object(tempfile, 'tempdir', str(alias)):
                suite.run(result)
                with tempfile.TemporaryDirectory() as allocated:
                    root = Path(allocated).resolve()
                    payload = root/'payload'
                    payload.write_bytes(b'exact fixture')
                    self.assertEqual(relocation.read(payload), b'exact fixture')
                    # The validator still rejects an unresolved parent alias.
                    with self.assertRaisesRegex(ValueError, 'Linked relocation'):
                        relocation.read(Path(allocated)/'payload')
                    (root/'linked').symlink_to(payload)
                    with self.assertRaisesRegex(ValueError, 'Missing or linked'):
                        relocation.read(root/'linked')
            self.assertEqual(result.testsRun, 5)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.failures, [])

if __name__=='__main__':unittest.main()
