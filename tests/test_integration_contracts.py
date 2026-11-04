"""Generic maintenance inventory controls, not device/runtime qualification."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import integration_contracts as c

spec = importlib.util.spec_from_file_location('issue52_inventory', ROOT / 'ci/issue52-integration/verify_inventory.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def fixture():
    return {'schema_version': 1, 'owners': {'Example': {'repository': 'example/project', 'graph_key': 'example'}},
            'features': [{'id': 'sample-policy', 'contract': 'Preserve sample decisions.',
                          'limitations': 'No native or device claim.',
                          'modules': [{'owner': 'Example', 'path': 'policy.swift', 'interface': 'Policy', 'rationale': 'Pure policy.'}],
                          'integration_points': [{'owner': 'Example', 'path': 'caller.swift', 'interface': 'Policy.call', 'rationale': 'Only caller.'},
                                                 {'owner': 'Example', 'path': 'project.pbxproj', 'interface': 'Source membership', 'rationale': 'Compile policy.'}],
                          'regressions': [{'owner': 'Example', 'path': 'test.py', 'command': 'python3 -B test.py', 'level': 'executable-fixture'}]}]}


def change(path, status='M', old='100644', new='100644'):
    return {'path': path, 'status': status, 'old_mode': old, 'new_mode': new}


class RegistryTests(unittest.TestCase):
    def test_real_inventory_covers_all_owners_and_required_concerns(self):
        registry = c.load_registry()
        self.assertEqual(set(registry['owners']), {'sidestore-auto-refresh', 'LiveContainer', 'SideStore', 'SideSign', 'AnisetteKit', 'idevice', 'jktcp', 'minimuxer'})
        ids = {row['id'] for row in registry['features']}
        self.assertTrue({'packaging', 'authentication-provider-sequencing', 'extension-signing', 'preserved-refresh',
                         'refresh-results-and-xpc', 'diagnostics-and-recovery', 'transport-readiness',
                         'build-membership-and-dependencies', 'integration-maintenance'} <= ids)
        for owner, path in c.references(registry):
            if owner == 'sidestore-auto-refresh':
                self.assertTrue((ROOT / path).is_file(), path)

    def test_reusable_with_unrelated_owner_and_language(self):
        registry = fixture()
        self.assertEqual(c.validate_registry(registry), registry)
        self.assertEqual(c.plan_changes(registry, 'Example', [change('policy.swift')])['affected_features'], ['sample-policy'])

    def test_missing_fields_empty_sets_and_unknown_schema_reject(self):
        for key in ('owners', 'features', 'schema_version'):
            registry = fixture(); del registry[key]
            with self.assertRaises(ValueError): c.validate_registry(registry)
        for key, value in (('schema_version', True), ('schema_version', 2), ('owners', {}), ('features', [])):
            registry = fixture(); registry[key] = value
            with self.assertRaises(ValueError): c.validate_registry(registry)
        registry = fixture(); registry['command'] = 'do something'
        with self.assertRaises(ValueError): c.validate_registry(registry)

    def test_duplicate_features_references_and_unused_owners_reject(self):
        registry = fixture(); registry['features'] *= 2
        with self.assertRaises(ValueError): c.validate_registry(registry)
        registry = fixture(); registry['features'][0]['modules'] *= 2
        with self.assertRaises(ValueError): c.validate_registry(registry)
        registry = fixture(); registry['owners']['Unused'] = {'repository': 'a/b', 'graph_key': 'u'}
        with self.assertRaises(ValueError): c.validate_registry(registry)

    def test_unsafe_paths_reject(self):
        for path in ('', '.', '..', '/tmp/x', '../x', 'x/../y', 'x//y', './x', 'x/', '.git/config', 'x\\y', 'x\ny', '*.swift', 'x?'):
            with self.subTest(path=path), self.assertRaises(ValueError): c.path_name(path)

    def test_missing_interfaces_commands_or_owners_reject(self):
        for key, value in (('interface', ''), ('rationale', ''), ('owner', 'Unknown'), ('path', '../escape')):
            registry = fixture(); registry['features'][0]['modules'][0][key] = value
            with self.assertRaises(ValueError): c.validate_registry(registry)
        for key, value in (('command', ''), ('level', []), ('level', 'device-proven')):
            registry = fixture(); registry['features'][0]['regressions'][0][key] = value
            with self.assertRaises(ValueError): c.validate_registry(registry)

    def test_duplicate_json_keys_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'registry.json'
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(ValueError, 'Duplicate'): c.load_registry(path)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('policy.swift', 'caller.swift', 'project.pbxproj', 'test.py'):
            (self.root / name).write_text('source bytes')

    def test_all_exact_paths_checked_and_hashed_without_execution(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('No execution allowed')):
            result = c.validate_inventory(fixture(), {'Example': self.root})
        self.assertEqual(len(result['files']), 4)
        self.assertFalse(result['regressions_executed'])
        self.assertFalse(result['native_compilation'])
        self.assertTrue(all(len(row['sha256']) == 64 for row in result['files']))

    def test_missing_owner_module_caller_build_or_test_fails(self):
        for roots in ({}, {'Unknown': self.root}, {'Example': self.root, 'Unexpected': self.root}):
            with self.assertRaises(ValueError): c.validate_inventory(fixture(), roots)
        for name in ('policy.swift', 'caller.swift', 'project.pbxproj', 'test.py'):
            path = self.root / name; path.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing inventory'): c.validate_inventory(fixture(), {'Example': self.root})
            path.write_text('source bytes')

    def test_symlink_file_parent_and_root_reject(self):
        path = self.root / 'policy.swift'; path.unlink(); path.symlink_to('caller.swift')
        with self.assertRaisesRegex(ValueError, 'Linked'): c.validate_inventory(fixture(), {'Example': self.root})
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked owner'): c.validate_inventory(fixture(), {'Example': alias})
        with self.assertRaisesRegex(ValueError, 'Linked'): c.regular_file(self.root, 'alias/caller.swift')


class ImpactTests(unittest.TestCase):
    def test_each_module_caller_build_and_test_change_selects_regressions(self):
        for path in ('policy.swift', 'caller.swift', 'project.pbxproj', 'test.py'):
            result = c.plan_changes(fixture(), 'Example', [change(path)])
            self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
            self.assertEqual(result['required_regressions'][0]['command'], 'python3 -B test.py')
            self.assertFalse(result['regressions_executed'])

    def test_unclassified_paths_are_never_implicitly_safe(self):
        result = c.plan_changes(fixture(), 'Example', [change('new-upstream-code.swift')])
        self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(result['unclassified'], ['new-upstream-code.swift'])

    def test_removed_hooks_gitlinks_and_type_changes_need_review(self):
        for row in (change('caller.swift', 'D', new='000000'), change('policy.swift', 'M', old='160000', new='160000'),
                    change('policy.swift', 'T', new='120000')):
            result = c.plan_changes(fixture(), 'Example', [row])
            self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')

    def test_malformed_changes_and_unknown_owners_reject(self):
        for changes in ([change('../escape')], [change('policy.swift'), change('policy.swift')],
                        [change('policy.swift', status='R')], [change('policy.swift', old='invalid')], [change('policy.swift', status='A')],
                        [change('policy.swift', status='D')], [change('policy.swift', new='000000')], [{}], None):
            with self.assertRaises(ValueError): c.plan_changes(fixture(), 'Example', changes)
        with self.assertRaises(ValueError): c.plan_changes(fixture(), 'Unknown', [])

    def test_multiple_features_sharing_hook_all_select(self):
        registry = fixture(); extra = copy.deepcopy(registry['features'][0]); extra['id'] = 'second-policy'; registry['features'].append(extra)
        result = c.plan_changes(registry, 'Example', [change('caller.swift')])
        self.assertEqual(result['affected_features'], ['sample-policy', 'second-policy'])
        self.assertEqual(len(result['required_regressions']), 2)


class ReviewedOwnershipDeltaTests(unittest.TestCase):
    def setUp(self):
        self.registry = c.load_registry()
        self.fixture = json.loads((ROOT / 'tests/fixtures/issue53-ownership-delta.json').read_text(), object_pairs_hook=c.unique_pairs)
        self.changes = [{key: row[key] for key in ('path', 'status', 'old_mode', 'new_mode')}
                        for row in self.fixture['changes']]

    def test_exact_reviewed_44_file_delta_maps_to_ownership_contract(self):
        self.assertEqual(self.fixture['owner'], 'idevice')
        self.assertEqual(self.fixture['base_commit'], 'e4cf9cb38aeee1908f5f9a1c33fd08c542f8f24a')
        self.assertEqual(self.fixture['candidate_commit'], '72c0044c62b4b28e813fb6670dfd4651622bf20a')
        self.assertEqual(len(self.changes), 44)
        self.assertEqual(len({row['path'] for row in self.changes}), 44)
        for row in self.fixture['changes']:
            self.assertEqual(set(row), {'path', 'status', 'old_mode', 'new_mode', 'old_blob', 'new_blob'})
            self.assertRegex(row['old_blob'], r'^[0-9a-f]{40}$')
            self.assertRegex(row['new_blob'], r'^[0-9a-f]{40}$')
        result = c.plan_changes(self.registry, 'idevice', self.changes)
        self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
        self.assertEqual(result['unclassified'], [])
        self.assertEqual(len(result['classified']), 44)
        self.assertEqual(result['affected_features'], ['ffi-ownership'])
        self.assertFalse(result['regressions_executed'])
        self.assertFalse(result['native_compilation'])
        required = {(row['owner'], row['path']) for row in result['required_regressions']}
        self.assertTrue({('idevice', '.ci/issue53/test_ownership.py'),
                         ('idevice', '.ci/issue53/check_listener_abi.sh'),
                         ('idevice', '.ci/issue53/check_provider_ownership.py'),
                         ('sidestore-auto-refresh', 'ci/issues-native/test_issue_receipt.py')} <= required)

    def test_lazy_mobileactivation_client_requires_full_provider_lifetime(self):
        feature = next(row for row in self.registry['features'] if row['id'] == 'ffi-ownership')
        module = next(row for row in feature['modules'] if row['path'] == 'ffi/src/mobileactivationd.rs')
        self.assertIn('entire client lifetime', module['rationale'])
        self.assertIn('until that client is freed', module['rationale'])

    def test_every_reviewed_path_requires_its_explicit_mapping(self):
        for changed in self.changes:
            registry = copy.deepcopy(self.registry)
            for feature in registry['features']:
                for kind in ('modules', 'integration_points', 'regressions'):
                    feature[kind] = [row for row in feature[kind]
                                     if (row['owner'], row['path']) != ('idevice', changed['path'])]
            with self.subTest(path=changed['path']):
                result = c.plan_changes(registry, 'idevice', self.changes)
                self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
                self.assertEqual(result['unclassified'], [changed['path']])

    def test_new_neighbor_path_still_requires_manual_review(self):
        unknown = change('ffi/src/unreviewed_new_service.rs', status='A', old='000000')
        result = c.plan_changes(self.registry, 'idevice', self.changes + [unknown])
        self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(result['unclassified'], [unknown['path']])

    def test_builder_runner_and_receipt_changes_select_ownership_regressions(self):
        changes = [change('ci/issues-native/run_issue_tests.py'), change('ci/issues-native/test_issue_receipt.py')]
        result = c.plan_changes(self.registry, 'sidestore-auto-refresh', changes)
        self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
        self.assertEqual(result['affected_features'], ['ffi-ownership'])
        self.assertTrue(any(row['path'] == '.ci/issue53/check_provider_ownership.py' for row in result['required_regressions']))
        self.assertTrue(any(row['path'] == 'ci/issues-native/test_issue_receipt.py' for row in result['required_regressions']))


class GitImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        for name in ('policy.swift', 'caller.swift', 'project.pbxproj', 'test.py'):
            (self.root / name).write_text('before')
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def test_real_git_diff_classifies_both_rename_sides(self):
        (self.root / 'caller.swift').rename(self.root / 'new-caller.swift')
        head = self.commit()
        result = c.compare_commits(fixture(), 'Example', self.root, self.base, head)
        self.assertEqual(result['removed_paths'], ['caller.swift'])
        self.assertEqual(result['unclassified'], ['new-caller.swift'])
        self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(result['candidate_commit'], head)

    def test_repo_config_cannot_hide_changed_gitlinks(self):
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.base + ',dependency')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'add gitlink')
        before = self.git('rev-parse', 'HEAD')
        self.git('update-index', '--cacheinfo', '160000,' + before + ',dependency')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'update gitlink')
        after = self.git('rev-parse', 'HEAD')
        self.git('config', 'diff.ignoreSubmodules', 'all')
        result = c.compare_commits(fixture(), 'Example', self.root, before, after)
        self.assertEqual(result['gitlink_changes'], ['dependency'])
        self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')

    def test_no_change_is_classified_not_execution(self):
        result = c.compare_commits(fixture(), 'Example', self.root, self.base, self.base)
        self.assertEqual(result['classified'], [])
        self.assertFalse(result['regressions_executed'])

    def test_dirty_worktree_cannot_change_immutable_commit_report(self):
        (self.root / 'policy.swift').write_text('uncommitted drift')
        result = c.compare_commits(fixture(), 'Example', self.root, self.base, self.base)
        self.assertEqual(result['classified'], [])

    def test_floating_refs_and_noncommit_objects_reject(self):
        for ref in ('HEAD', 'main', self.base[:10], 'A'*40, '; touch /tmp/no'):
            with self.assertRaises(ValueError): c.compare_commits(fixture(), 'Example', self.root, ref, self.base)
        tree = self.git('rev-parse', 'HEAD^{tree}')
        with self.assertRaises(ValueError): c.compare_commits(fixture(), 'Example', self.root, tree, self.base)

    def test_git_environment_cannot_redirect_immutable_review(self):
        with patch.dict(os.environ, {'GIT_DIR': '/missing', 'GIT_WORK_TREE': '/missing', 'GIT_CONFIG_COUNT': '1',
                                     'GIT_CONFIG_KEY_0': 'alias.diff', 'GIT_CONFIG_VALUE_0': '!false'}):
            self.assertEqual(c.compare_commits(fixture(), 'Example', self.root, self.base, self.base)['classified'], [])

    def test_cli_manual_review_exits_nonzero_and_does_not_execute_tests(self):
        (self.root / 'unknown.txt').write_text('new')
        head = self.commit()
        registry = self.root / 'registry.json'; registry.write_text(json.dumps(fixture()))
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/integration_contracts.py'), '--registry', str(registry),
                                 'plan', '--owner', 'Example', '--repository', str(self.root), '--base', self.base, '--candidate', head],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'MANUAL_REVIEW_REQUIRED')


class PinnedInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.sources = self.root / 'sources'; self.sources.mkdir()
        self.builder = self.root / 'builder'
        self.owner = self.sources / 'Example'
        self.commits = {}
        for root in (self.owner, self.builder):
            root.mkdir(); self.git(root, 'init', '-q')
            for name in ('policy.swift', 'caller.swift', 'project.pbxproj', 'test.py'):
                (root / name).write_text('bound source bytes')
            self.git(root, 'add', '.')
            self.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'immutable sources')
            self.commits[root] = self.git(root, 'rev-parse', 'HEAD')
        self.git(self.owner, 'remote', 'add', 'origin', 'https://github.com/NRG-Wardog/Example.git')
        self.registry = fixture()
        self.registry['owners']['Example']['repository'] = 'NRG-Wardog/Example'
        self.registry['owners']['sidestore-auto-refresh'] = {'repository': 'NRG-Wardog/sidestore-auto-refresh', 'graph_key': None}
        extra = copy.deepcopy(self.registry['features'][0]); extra['id'] = 'builder-policy'
        for kind in ('modules', 'integration_points', 'regressions'):
            for row in extra[kind]: row['owner'] = 'sidestore-auto-refresh'
        self.registry['features'].append(extra)
        self.graph = {'owners': {'example': ['NRG-Wardog/Example', self.commits[self.owner]]}}

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    def run_verify(self):
        return ci.verify(self.registry, self.graph, self.sources, self.builder)

    def test_all_source_paths_bound_to_exact_git_blobs(self):
        result = self.run_verify()
        self.assertEqual(result['result'], 'PINNED_INVENTORY_PASS_NOT_BEHAVIOR_VALIDATION')
        self.assertEqual(len(result['files']), 8)
        self.assertEqual(result['identities']['Example']['commit'], self.commits[self.owner])
        self.assertFalse(result['regressions_executed'])

    def test_edited_source_and_builder_registry_inputs_reject(self):
        for root in (self.owner, self.builder):
            path = root / 'caller.swift'; path.write_text('uncommitted replacement')
            with self.assertRaisesRegex(ValueError, 'bytes differ'): self.run_verify()
            path.write_text('bound source bytes')

    def test_missing_caller_build_membership_and_tests_reject(self):
        for name in ('caller.swift', 'project.pbxproj', 'test.py'):
            path = self.owner / name; path.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing inventory'): self.run_verify()
            path.write_text('bound source bytes')

    def test_wrong_origin_rejects(self):
        self.git(self.owner, 'remote', 'set-url', 'origin', 'https://github.com/other/Example.git')
        with self.assertRaisesRegex(ValueError, 'origin differs'): self.run_verify()

    def test_wrong_commit_rejects(self):
        self.graph['owners']['example'][1] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'commit differs'): self.run_verify()

    def test_symlinked_bound_source_rejects(self):
        path = self.owner / 'policy.swift'; path.unlink(); path.symlink_to('caller.swift')
        with self.assertRaisesRegex(ValueError, 'Linked inventory'): self.run_verify()


class WorkflowTests(unittest.TestCase):
    def test_exact_candidate_graph_drives_all_seven_owner_inputs(self):
        registry = c.load_registry(); graph = json.loads((ROOT / 'ci/issues-native/candidate-graph.json').read_text())
        plan = ci.source_plan(registry, graph)
        self.assertEqual(len(plan), 7)
        self.assertTrue(all(repository == 'NRG-Wardog/' + owner and len(commit) == 40 for owner, repository, commit in plan))
        for broken in (dict(graph, owners={}), {'owners': {'LiveContainer': ['elsewhere/LiveContainer', 'a'*40]}}):
            with self.assertRaises(ValueError): ci.source_plan(registry, broken)
        broken = copy.deepcopy(graph); broken['owners']['LiveContainer'][1] = 'main'
        with self.assertRaises(ValueError): ci.source_plan(registry, broken)

    def test_ci_is_read_only_and_retains_explicit_evidence_limits(self):
        workflow = (ROOT / '.github/workflows/custom-integration-contract.yml').read_text()
        for needle in ('pull_request:', 'push:', 'workflow_dispatch:', 'contents: read', 'persist-credentials: false',
                       'test_integration_contracts.py', 'test_packaging_adapters.py', 'test_custom_integration_boundaries.py',
                       'run_required_tests.py', 'verify_inventory.py --sources sources', 'if: always()',
                       "'scripts/**'", "'ci/issues-native/candidate-graph.json'", 'cancel-in-progress: false'):
            self.assertIn(needle, workflow)
        for forbidden in ('pull_request_target', 'contents: write', 'secrets.', 'bash sources/', 'python3 sources/'):
            self.assertNotIn(forbidden, workflow)


if __name__ == '__main__':
    unittest.main(verbosity=2)
