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


class TestSuiteLayoutTests(unittest.TestCase):
    """Check import/discovery coverage without executing the discovered tests."""

    def discover(self, start="tests", pattern="test_*.py", package_root=False):
        probe = r'''
import json
from pathlib import Path
import sys
import unittest
root = Path(sys.argv[1])
loader = unittest.TestLoader()
suite = loader.discover(str(root / sys.argv[2]), pattern=sys.argv[3],
    top_level_dir=str(root) if sys.argv[4] == "package" else None)
def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item
found = list(cases(suite))
paths = set()
for case in found:
    module = sys.modules[type(case).__module__]
    path = Path(module.__file__).resolve()
    if path.is_relative_to(root):
        paths.add(path.relative_to(root).as_posix())
print(json.dumps({"errors": loader.errors, "ids": [case.id() for case in found],
    "paths": sorted(paths)}))
'''
        result = subprocess.run([
            sys.executable, "-B", "-c", probe, str(ROOT), start, pattern,
            "package" if package_root else "default",
        ], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["ids"], "discovery must not silently select zero tests")
        self.assertFalse(any("_FailedTest" in name for name in report["ids"]))
        self.assertEqual(len(report["ids"]), len(set(report["ids"])))
        return set(report["paths"])

    def test_recursive_discovery_includes_every_root_and_domain_module(self):
        expected = {path.relative_to(ROOT).as_posix()
                    for path in (ROOT / "tests").rglob("test_*.py")}
        self.assertEqual(self.discover(), expected)

    def test_focused_root_and_domain_discovery_preserve_selection(self):
        self.assertEqual(self.discover(pattern="test_combined_packaging.py"),
                         {"tests/test_combined_packaging.py"})
        self.assertEqual(self.discover(pattern="test_settings.py"),
                         {"tests/settings/test_settings.py"})
        expected = {path.relative_to(ROOT).as_posix()
                    for path in (ROOT / "tests/settings").glob("test_*.py")}
        self.assertEqual(self.discover(start="tests/settings", package_root=True), expected)


class RegistryTests(unittest.TestCase):
    def test_real_inventory_covers_all_owners_and_required_concerns(self):
        registry = c.load_registry()
        self.assertEqual(set(registry['owners']), {'sidestore-auto-refresh', 'LiveContainer', 'SideStore', 'SideSign', 'AnisetteKit', 'idevice', 'jktcp', 'minimuxer'})
        ids = {row['id'] for row in registry['features']}
        self.assertTrue({'packaging', 'authentication-provider-sequencing', 'extension-signing', 'preserved-refresh',
                         'refresh-results-and-xpc', 'diagnostics-and-recovery', 'transport-readiness',
                         'build-membership-and-dependencies', 'integration-maintenance', 'database-preserving-recovery'} <= ids)
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


class ReviewedRecoveryBoundaryTests(unittest.TestCase):
    # Exact expected path/status pairs keep the historical fixture finite and explicit.
    SOURCE_PATHS = (
        ('.github/workflows/database-recovery.yml', 'A'),
        ('AltStore/Core/Model/DatabaseManager/DatabaseManager.swift', 'M'),
        ('AltStore/Core/Model/DatabaseManager/PersistentContainer.swift', 'M'),
        ('AltStore/Core/Model/InstalledApp.swift', 'M'),
        ('SideStore/DatabaseRecovery.swift', 'A'),
        ('SideStore/DatabaseRecoverySQLite.c', 'A'),
        ('SideStore/MaintenanceManager.swift', 'M'),
        ('docs/database-recovery/README.md', 'A'),
        ('tests/cache_compatibility/Cases.swift', 'M'),
        ('tests/cache_compatibility/generate_fixture.py', 'M'),
        ('tests/cache_compatibility/test_host_preservation.py', 'M'),
        ('tests/database_recovery/README.md', 'A'),
        ('tests/database_recovery/main.swift', 'A'),
        ('tests/database_recovery/run.py', 'A'),
        ('tests/database_recovery/test_runner.py', 'A'),
        ('tests/database_recovery/target_settings.py', 'A'),
        ('tests/database_recovery_coredata/Cases.swift', 'A'),
        ('tests/database_recovery_coredata/run.py', 'A'),
        ('tests/database_recovery_coredata/test_contract.py', 'A'),
        ('tests/startup_outcome/test_contract.py', 'M'),
        ('tests/startup_outcome/test_model_resource_owner.py', 'M'),
        ('tests/upstream_sync/test_maintenance_preservation.py', 'M'),
    )

    def setUp(self):
        self.registry = c.load_registry()
        self.feature = next(row for row in self.registry['features']
                            if row['id'] == 'database-preserving-recovery')
        self.fixture = json.loads((ROOT / 'tests/fixtures/issue56-recovery-delta.json').read_text(),
                                  object_pairs_hook=c.unique_pairs)
        self.changes = [{key: row[key] for key in ('path', 'status', 'old_mode', 'new_mode')}
                        for row in self.fixture['changes']]

    def test_frozen_delta_retains_exact_published_identity_modes_and_blob_ids(self):
        self.assertEqual(self.fixture['owner'], 'SideStore')
        self.assertEqual(self.fixture['base_commit'], '4f1021277ccc49572a17649bf062cd02c583071f')
        self.assertEqual(self.fixture['candidate_commit'], 'e1d2f47405f8becc102608ab066513a8754ce8da')
        self.assertEqual(self.fixture['base_tree'], '030ca9722b1f46ad660fb414c85b90d5ae0d72d7')
        self.assertEqual(self.fixture['candidate_tree'], '8e1e1b1f1115a191633fa8cbfb3e52be9df28129')
        self.assertEqual(len(self.changes), 22)
        self.assertEqual(len({row['path'] for row in self.changes}), 22)
        self.assertEqual({(row['path'], row['status']) for row in self.changes}, set(self.SOURCE_PATHS))
        for row in self.fixture['changes']:
            self.assertEqual(set(row), {'path', 'status', 'old_mode', 'new_mode', 'old_blob', 'new_blob'})
            self.assertEqual(row['old_mode'], '000000' if row['status'] == 'A' else '100644')
            self.assertEqual(row['new_mode'], '100644')
            self.assertRegex(row['old_blob'], r'^[0-9a-f]{40}$')
            self.assertRegex(row['new_blob'], r'^[0-9a-f]{40}$')
            self.assertNotEqual(row['new_blob'], '0' * 40)
            self.assertEqual(row['old_blob'] == '0' * 40, row['status'] == 'A')

    def test_exact_recovery_paths_select_both_native_suites_and_portable_controls(self):
        result = c.plan_changes(self.registry, 'SideStore', self.changes)
        self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
        self.assertEqual(result['unclassified'], [])
        self.assertEqual(len(result['classified']), 22)
        self.assertEqual(result['affected_features'], ['database-preserving-recovery', 'preserved-refresh',
                                                       'shared-storage-and-startup'])
        self.assertFalse(result['regressions_executed'])
        self.assertFalse(result['native_compilation'])
        self.assertFalse(result['physical_device_validation'])
        required = {(row['owner'], row['path']) for row in result['required_regressions']}
        self.assertTrue({('SideStore', 'tests/database_recovery/run.py'),
                         ('SideStore', 'tests/database_recovery_coredata/run.py'),
                         ('SideStore', 'tests/database_recovery/test_runner.py'),
                         ('SideStore', 'tests/database_recovery_coredata/test_contract.py'),
                         ('SideStore', 'tests/upstream_sync/test_maintenance_preservation.py')} <= required)

    def test_each_recovery_mapping_is_required_individually(self):
        for changed in self.changes:
            registry = copy.deepcopy(self.registry)
            for feature in registry['features']:
                for kind in ('modules', 'integration_points', 'regressions'):
                    feature[kind] = [row for row in feature[kind]
                                     if (row['owner'], row['path']) != ('SideStore', changed['path'])]
            with self.subTest(path=changed['path']):
                result = c.plan_changes(registry, 'SideStore', self.changes)
                self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
                self.assertEqual(result['unclassified'], [changed['path']])

    def test_unknown_engine_fixture_and_schema_neighbors_still_require_review(self):
        for path in ('SideStore/DatabaseRecoveryUnreviewed.swift',
                     'tests/database_recovery/unreviewed_case.swift',
                     'AltStore/Core/Model/AltStore.xcdatamodeld/AltStore 18.xcdatamodel/contents'):
            result = c.plan_changes(self.registry, 'SideStore',
                                    self.changes + [change(path, 'A', old='000000')])
            with self.subTest(path=path):
                self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
                self.assertEqual(result['unclassified'], [path])

    def test_removed_recovery_module_and_symlink_fixture_are_not_safe(self):
        for row in (change('SideStore/DatabaseRecovery.swift', 'D', new='000000'),
                    change('tests/database_recovery/main.swift', 'T', new='120000')):
            result = c.plan_changes(self.registry, 'SideStore', [row])
            self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')

    def test_original_model_resources_have_exact_reviewed_paths(self):
        prefix = 'AltStore/Core/Model/AltStore.xcdatamodeld/'
        paths = {row['path'] for row in self.feature['integration_points']
                 if row['path'].startswith(prefix)}
        versions = ('', ' 2', ' 3', ' 4', ' 5', ' 6', ' 7', ' 8', ' 9', ' 10', ' 11',
                    ' 17_1', ' 17_2', ' 17_3', ' 17_4', ' 17_5', ' 17_6', ' 17_7')
        expected = {prefix + 'AltStore' + version + '.xcdatamodel/contents' for version in versions}
        expected.add(prefix + '.xccurrentversion')
        self.assertEqual(paths, expected)
        for path in sorted(paths):
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'SideStore', [change(path)])
                self.assertEqual(result['affected_features'], ['database-preserving-recovery'])
                self.assertTrue(any(row['path'] == 'tests/database_recovery_coredata/run.py'
                                    for row in result['required_regressions']))

    def test_shared_xcode_membership_selects_recovery_and_build_checks(self):
        result = c.plan_changes(self.registry, 'SideStore', [change('AltStore.xcodeproj/project.pbxproj')])
        self.assertEqual(result['affected_features'],
                         ['build-membership-and-dependencies', 'database-preserving-recovery'])
        self.assertTrue(any(row['path'] == 'tests/database_recovery/run.py' for row in result['required_regressions']))
        self.assertTrue(any(row['path'] == 'ci/issues-native/test_candidate_graph.py' for row in result['required_regressions']))

    def test_cache_getter_and_fixture_seams_select_preservation_and_recovery(self):
        for path in ('AltStore/Core/Model/InstalledApp.swift',
                     'tests/cache_compatibility/generate_fixture.py',
                     'tests/cache_compatibility/Cases.swift',
                     'tests/cache_compatibility/test_host_preservation.py'):
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'SideStore', [change(path)])
                self.assertEqual(result['affected_features'],
                                 ['database-preserving-recovery', 'preserved-refresh'])
                required = {row['path'] for row in result['required_regressions']}
                self.assertTrue({'tests/preservation_refresh/test_contract.py',
                                 'tests/cache_compatibility/test_host_preservation.py',
                                 'ci/issues-native/verify_setup_state_recovery.py',
                                 'tests/database_recovery_coredata/run.py'} <= required)
        command = next(row['command'] for row in self.feature['regressions']
                       if row['path'] == 'tests/cache_compatibility/Cases.swift')
        self.assertIn('verify_setup_state_recovery.py', command)
        self.assertIn('--contract "$SETUP_RECOVERY_CONTRACT"', command)
        self.assertIn('inert optional-URL MaintenanceManager boundary', self.feature['limitations'])

    def test_native_builder_entrypoints_select_recovery_receipt_and_native_suites(self):
        paths = ('ci/issues-native/run_database_recovery.py',
                 'ci/issues-native/test_database_recovery_receipt.py',
                 'ci/issues-native/record.py', 'ci/issues-native/package_candidate.py',
                 'ci/startup-upgrade/acquire.py', 'ci/startup-upgrade/test_acquire.py',
                 '.github/workflows/cold-vpn-native.yml')
        for path in paths:
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'sidestore-auto-refresh', [change(path)])
                self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
                self.assertIn('database-preserving-recovery', result['affected_features'])
                required = {row['path'] for row in result['required_regressions']}
                self.assertTrue({'ci/issues-native/test_database_recovery_receipt.py',
                                 'tests/database_recovery/run.py',
                                 'tests/database_recovery_coredata/run.py',
                                 'ci/startup-upgrade/test_acquire.py'} <= required)
        unknown = change('ci/issues-native/unreviewed_recovery_verifier.py', 'A', old='000000')
        self.assertEqual(c.plan_changes(self.registry, 'sidestore-auto-refresh', [unknown])['result'],
                         'MANUAL_REVIEW_REQUIRED')

    def test_upstream_successor_paths_select_explicit_case_map_controls(self):
        paths = ('ci/issues-native/database_recovery_upstream_projection.py',
                 'ci/issues-native/test_database_recovery_upstream_projection.py',
                 'ci/issues-native/database-recovery-upstream-tests-extension.json',
                 'ci/issues-native/upstream_candidate_projection.py',
                 'ci/issues-native/verify_upstream_sync.py',
                 'ci/issues-native/test_preservation_upstream_tests_projection.py',
                 '.github/workflows/refresh-truth-preflight.yml',
                 '.github/workflows/cold-vpn-native.yml',
                 '.github/workflows/issues-native.yml',
                 '.github/workflows/language-switch-native.yml',
                 '.github/workflows/issue56-native.yml')
        for path in paths:
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'sidestore-auto-refresh', [change(path)])
                self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
                self.assertIn('database-preserving-recovery', result['affected_features'])
                required = {row['path'] for row in result['required_regressions']}
                self.assertTrue({'ci/issues-native/test_database_recovery_upstream_projection.py',
                                 'ci/issues-native/test_preservation_upstream_tests_projection.py'} <= required)

    def test_every_recovery_builder_path_triggers_the_inventory_workflow(self):
        workflow = (ROOT / '.github/workflows/custom-integration-contract.yml').read_text()
        paths = {row['path'] for kind in ('modules', 'integration_points', 'regressions')
                 for row in self.feature[kind] if row['owner'] == 'sidestore-auto-refresh'}
        paths.update(('ci/issues-native/run_issue_tests.py', 'ci/issues-native/test_issue_receipt.py'))
        for path in sorted(paths):
            with self.subTest(path=path):
                self.assertEqual(workflow.count("      - '" + path + "'"), 2,
                                 'Exact push and pull-request trigger required')

    def test_evidence_levels_do_not_promote_inventory_or_synthetic_coredata(self):
        regressions = {row['path']: row for row in self.feature['regressions']}
        for path in ('tests/database_recovery/main.swift', 'tests/database_recovery/run.py',
                     'tests/database_recovery_coredata/Cases.swift', 'tests/database_recovery_coredata/run.py'):
            self.assertEqual(regressions[path]['level'], 'native')
        for path in ('tests/database_recovery/test_runner.py', 'tests/database_recovery_coredata/test_contract.py',
                     'tests/upstream_sync/test_maintenance_preservation.py'):
            self.assertIn('run_required_tests.py', regressions[path]['command'])
        builder_command = regressions['ci/issues-native/test_database_recovery_receipt.py']['command']
        self.assertIn('--source "$SIDESTORE_ROOT"', builder_command)
        self.assertIn('run_required_tests.py', builder_command)
        self.assertIn(' && ', builder_command)
        self.assertIn('synthetic validation callback, not Core Data', self.feature['limitations'])
        self.assertIn('v17.6-to-v17.7 DELETE and committed-WAL', self.feature['limitations'])
        self.assertIn('physical-device upgrade', self.feature['limitations'])
        self.assertIn('full recovered-store lifetime', next(row for row in self.feature['integration_points']
                          if row['path'] == 'SideStore/MaintenanceManager.swift')['rationale'])


    def test_startup_source_boundaries_select_native_and_recovery_controls(self):
        for path in ('tests/startup_outcome/test_contract.py',
                     'tests/startup_outcome/test_model_resource_owner.py'):
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'SideStore', [change(path)])
                self.assertEqual(result['affected_features'],
                                 ['database-preserving-recovery', 'shared-storage-and-startup'])
                required = {row['path'] for row in result['required_regressions']}
                self.assertTrue({'tests/startup_outcome/test_contract.py',
                                 'tests/startup_outcome/test_model_resource_owner.py',
                                 'ci/issue56-startup/test_startup_runner.py',
                                 'tests/database_recovery_coredata/run.py'} <= required)

    def test_portable_startup_command_never_replaces_mandatory_native_coverage(self):
        for name in ('database-preserving-recovery', 'shared-storage-and-startup'):
            feature = next(row for row in self.registry['features'] if row['id'] == name)
            rows = {row['path']: row for row in feature['regressions']}
            full = rows['tests/startup_outcome/test_contract.py']
            portable = rows['tests/startup_outcome/test_model_resource_owner.py']
            self.assertEqual(full['command'], 'REQUIRE_SWIFT_STARTUP_TESTS=1 python3 -B tests/startup_outcome/test_contract.py')
            self.assertEqual(full['level'], 'native')
            self.assertEqual(portable['command'], 'python3 -B tests/startup_outcome/test_contract.py --source-only')
            self.assertEqual(portable['level'], 'source-contract')
            self.assertIn('14 portable cases', feature['limitations'])
            self.assertIn('16 cases including two extracted-production Swift fixtures', feature['limitations'])
            self.assertIn('cannot replace the mandatory same-run native startup receipt', feature['limitations'])

    def test_builder_startup_preflight_selects_both_boundaries(self):
        for path in ('ci/issue56-startup/test_startup_runner.py',
                     '.github/workflows/cold-vpn-native.yml',
                     '.github/workflows/refresh-truth-preflight.yml'):
            with self.subTest(path=path):
                result = c.plan_changes(self.registry, 'sidestore-auto-refresh', [change(path)])
                self.assertTrue({'database-preserving-recovery', 'shared-storage-and-startup'} <= set(result['affected_features']))
                rows = [row for row in result['required_regressions']
                        if row['path'] == 'ci/issue56-startup/test_startup_runner.py']
                self.assertTrue(rows)
                for row in rows:
                    self.assertIn('run_required_tests.py', row['command'])
                    self.assertIn('--pattern test_startup_runner.py', row['command'])
                    self.assertEqual(row['level'], 'executable-fixture')

    def test_effective_settings_diagnostics_do_not_admit_native_builds(self):
        paths = ('ci/issues-native/capture_release_settings.py',
                 'ci/issues-native/test_release_settings_cache.py')
        for path in paths:
            result = c.plan_changes(self.registry, 'sidestore-auto-refresh', [change(path)])
            self.assertEqual(result['affected_features'], ['database-preserving-recovery'])
            rows = {row['path']: row for row in result['required_regressions']}
            self.assertIn('ci/issues-native/test_database_recovery_receipt.py', rows)
            command = rows['ci/issues-native/test_release_settings_cache.py']['command']
            self.assertIn('run_required_tests.py', command)
            self.assertIn('--pattern test_release_settings_cache.py', command)
        diagnostic = next(row for row in self.feature['modules']
                          if row['path'] == paths[0])
        self.assertIn('policy_admission remains false', diagnostic['rationale'])
        self.assertIn('diagnostic output never admits a build', diagnostic['rationale'])
        seal = next(row for row in self.feature['modules']
                    if row['path'] == 'ci/issues-native/run_database_recovery.py')
        self.assertIn('entire canonical target rows are identical', seal['rationale'])
        self.assertIn('divergent fields or types', seal['rationale'])

    def test_new_builder_mappings_are_individually_required(self):
        for path in ('ci/issue56-startup/test_startup_runner.py',
                     'ci/issues-native/capture_release_settings.py',
                     'ci/issues-native/test_release_settings_cache.py'):
            registry = copy.deepcopy(self.registry)
            for feature in registry['features']:
                for kind in ('modules', 'integration_points', 'regressions'):
                    feature[kind] = [row for row in feature[kind]
                                     if (row['owner'], row['path']) != ('sidestore-auto-refresh', path)]
            with self.subTest(path=path):
                result = c.plan_changes(registry, 'sidestore-auto-refresh', [change(path)])
                self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
                self.assertEqual(result['unclassified'], [path])

    def test_startup_and_settings_unknown_removed_and_linked_paths_require_review(self):
        for owner, path in (('SideStore', 'tests/startup_outcome/unreviewed.py'),
                            ('sidestore-auto-refresh', 'ci/issue56-startup/unreviewed.py'),
                            ('sidestore-auto-refresh', 'ci/issues-native/unreviewed_settings.py')):
            result = c.plan_changes(self.registry, owner, [change(path, 'A', old='000000')])
            self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
            self.assertEqual(result['unclassified'], [path])
        for owner, path in (('SideStore', 'tests/startup_outcome/test_model_resource_owner.py'),
                            ('sidestore-auto-refresh', 'ci/issues-native/capture_release_settings.py')):
            for row in (change(path, 'D', new='000000'), change(path, 'T', new='120000')):
                self.assertEqual(c.plan_changes(self.registry, owner, [row])['result'], 'MANUAL_REVIEW_REQUIRED')


class ReviewedPackageAdmissionTests(unittest.TestCase):
    # Finite reviewed composition paths, not a wildcard allowance or artifact pin.
    PATHS = (
        '.github/workflows/cold-vpn-zopfli.yml',
        'ci/cold-vpn-zopfli/README.md',
        'ci/cold-vpn-zopfli/acquire.py',
        'ci/cold-vpn-zopfli/cleanup-evidence.snapshot',
        'ci/cold-vpn-zopfli/language_cleanup_package_admission.py',
        'ci/cold-vpn-zopfli/test_language_cleanup_package_admission.py',
        'ci/cold-vpn-zopfli/cms-native-graph.json',
        'ci/cold-vpn-zopfli/cms-native-helper.snapshot',
        'ci/cold-vpn-zopfli/cms-source-contract.json',
        'ci/cold-vpn-zopfli/cms_package_admission.py',
        'ci/cold-vpn-zopfli/contract.py',
        'ci/cold-vpn-zopfli/database-recovery-source-contract.json',
        'ci/cold-vpn-zopfli/database_recovery_package_admission.py',
        'ci/cold-vpn-zopfli/fallback-source-contract.json',
        'ci/cold-vpn-zopfli/fixtures/actual-macos-boundary-25fc5b5d.json',
        'ci/cold-vpn-zopfli/independent_verify.py',
        'ci/cold-vpn-zopfli/initial_rejection_package_admission.py',
        'ci/cold-vpn-zopfli/input-manifest.json',
        'ci/cold-vpn-zopfli/owned-oda-source-contract.json',
        'ci/cold-vpn-zopfli/owned_oda_package_admission.py',
        'ci/cold-vpn-zopfli/owned_oda_package_binding.py',
        'ci/cold-vpn-zopfli/preservation_cms_portable.py',
        'ci/cold-vpn-zopfli/project_native_evidence.py',
        'ci/cold-vpn-zopfli/provider_ownership_package_admission.py',
        'ci/cold-vpn-zopfli/reviewed-native/candidate-graph.json',
        'ci/cold-vpn-zopfli/reviewed-native/refresh_truth_receipt.py',
        'ci/cold-vpn-zopfli/reviewed-native/run_database_recovery.py',
        'ci/cold-vpn-zopfli/reviewed-native/run_issue_tests.py',
        'ci/cold-vpn-zopfli/reviewed-native/run_owned_oda.py',
        'ci/cold-vpn-zopfli/reviewed-native/verify_candidate_graph.py',
        'ci/cold-vpn-zopfli/test_acquire.py',
        'ci/cold-vpn-zopfli/test_cms_package_admission.py',
        'ci/cold-vpn-zopfli/test_database_recovery_package_admission.py',
        'ci/cold-vpn-zopfli/test_independent_verify.py',
        'ci/cold-vpn-zopfli/test_initial_rejection_package_admission.py',
        'ci/cold-vpn-zopfli/test_owned_oda_package_admission.py',
        'ci/cold-vpn-zopfli/test_owned_oda_package_binding.py',
        'ci/cold-vpn-zopfli/test_preservation_cms_portable.py',
        'ci/cold-vpn-zopfli/test_project_native_evidence.py',
        'ci/cold-vpn-zopfli/test_provider_ownership_package_admission.py',
    )

    def setUp(self):
        self.registry = c.load_registry()
        self.changes = [change(path) for path in self.PATHS]

    def test_all_40_composed_paths_select_complete_package_admission_controls(self):
        self.assertEqual(len(self.PATHS), 40)
        self.assertEqual(len(set(self.PATHS)), 40)
        result = c.plan_changes(self.registry, 'sidestore-auto-refresh', self.changes)
        self.assertEqual(result['result'], 'IMPACT_CLASSIFIED_NOT_TESTED')
        self.assertEqual(result['unclassified'], [])
        self.assertEqual(len(result['classified']), 40)
        self.assertEqual(result['affected_features'],
                         ['database-preserving-recovery', 'ffi-ownership', 'package-native-evidence'])
        required = {row['path'] for row in result['required_regressions']}
        self.assertTrue({'ci/cold-vpn-zopfli/test_provider_ownership_package_admission.py',
                         'ci/cold-vpn-zopfli/test_database_recovery_package_admission.py',
                         'ci/cold-vpn-zopfli/test_cms_package_admission.py',
                         'ci/cold-vpn-zopfli/test_owned_oda_package_admission.py',
                         'ci/cold-vpn-zopfli/test_initial_rejection_package_admission.py',
                         'ci/cold-vpn-zopfli/test_independent_verify.py'} <= required)
        self.assertFalse(result['regressions_executed'])
        self.assertFalse(result['native_compilation'])
        self.assertFalse(result['physical_device_validation'])

    def test_each_package_mapping_is_required_even_when_shared_with_native_boundary(self):
        for path in self.PATHS:
            registry = copy.deepcopy(self.registry)
            for feature in registry['features']:
                for kind in ('modules', 'integration_points', 'regressions'):
                    feature[kind] = [row for row in feature[kind]
                                     if (row['owner'], row['path']) != ('sidestore-auto-refresh', path)]
            with self.subTest(path=path):
                result = c.plan_changes(registry, 'sidestore-auto-refresh', self.changes)
                self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
                self.assertEqual(result['unclassified'], [path])

    def test_unknown_neighbor_removal_and_linked_helper_remain_manual_review(self):
        unknown = change('ci/cold-vpn-zopfli/unreviewed_admission.py', 'A', old='000000')
        result = c.plan_changes(self.registry, 'sidestore-auto-refresh', self.changes + [unknown])
        self.assertEqual(result['result'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(result['unclassified'], [unknown['path']])
        for row in (change('ci/cold-vpn-zopfli/provider_ownership_package_admission.py', 'D', new='000000'),
                    change('ci/cold-vpn-zopfli/reviewed-native/run_database_recovery.py', 'T', new='120000')):
            self.assertEqual(c.plan_changes(self.registry, 'sidestore-auto-refresh', [row])['result'],
                             'MANUAL_REVIEW_REQUIRED')

    def test_specialized_package_admission_keeps_its_native_boundary(self):
        for feature, paths in (
            ('ffi-ownership', ('provider_ownership_package_admission.py',
                              'reviewed-native/run_issue_tests.py',
                              'test_provider_ownership_package_admission.py')),
            ('database-preserving-recovery', ('database_recovery_package_admission.py',
                                             'database-recovery-source-contract.json',
                                             'reviewed-native/run_database_recovery.py',
                                             'test_database_recovery_package_admission.py'))):
            for path in paths:
                result = c.plan_changes(self.registry, 'sidestore-auto-refresh',
                                        [change('ci/cold-vpn-zopfli/' + path)])
                with self.subTest(path=path):
                    self.assertEqual(result['affected_features'], sorted((feature, 'package-native-evidence')))

    def test_native_verifier_changes_also_require_corresponding_package_controls(self):
        for path, test in (
            ('ci/issues-native/run_issue_tests.py', 'test_provider_ownership_package_admission.py'),
            ('ci/issues-native/run_database_recovery.py', 'test_database_recovery_package_admission.py')):
            result = c.plan_changes(self.registry, 'sidestore-auto-refresh', [change(path)])
            self.assertTrue(any(row['path'] == 'ci/cold-vpn-zopfli/' + test
                                for row in result['required_regressions']))

    def test_every_package_path_has_exact_push_and_pull_request_inventory_triggers(self):
        workflow = (ROOT / '.github/workflows/custom-integration-contract.yml').read_text()
        pull = workflow.split('  pull_request:\n', 1)[1].split('  push:\n', 1)[0]
        push = workflow.split('  push:\n', 1)[1].split('\npermissions:', 1)[0]
        permissions = workflow.split('\npermissions:', 1)[1].split('\nconcurrency:', 1)[0]
        self.assertEqual(permissions.strip(), 'contents: read')
        for path in self.PATHS:
            with self.subTest(path=path):
                marker = "      - '" + path + "'"
                self.assertEqual(workflow.count(marker), 2)
                self.assertEqual(pull.count(marker), 1)
                self.assertEqual(push.count(marker), 1)

    def test_package_scope_requires_strict_portable_controls_without_native_claim(self):
        feature = next(row for row in self.registry['features'] if row['id'] == 'package-native-evidence')
        self.assertEqual({row['path'] for kind in ('modules', 'integration_points', 'regressions')
                          for row in feature[kind]}, set(self.PATHS))
        for row in feature['regressions']:
            self.assertIn('run_required_tests.py', row['command'])
            self.assertIn("--pattern 'test_*.py'", row['command'])
            self.assertIn(row['level'], ('executable-fixture', 'source-contract'))
        self.assertIn('before compression', feature['contract'])
        self.assertIn('independent final audit', feature['contract'])
        self.assertIn('never execute artifact-supplied code', feature['limitations'])
        self.assertIn('placeholder/old artifact metadata is unqualified', feature['limitations'])


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
