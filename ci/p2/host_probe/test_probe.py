"""Portable adversarial checks. Passing these never implies a native run."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('unchanged_host_probe', HERE / 'run.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ParseTests(unittest.TestCase):
    def test_json_duplicates_and_nonfinite_fail(self):
        for raw in ['{"x":1,"x":2}', 'NaN', 'Infinity', '-Infinity', '1e400', '-1e400', '{"x":[1e400]}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                probe.strict_json(raw)
        self.assertEqual(probe.strict_json('{"x":1.5,"y":2}'), {'x': 1.5, 'y': 2})

    def test_relative_paths_reject_escape_and_ambiguity(self):
        for value in ['', '../x', '/x', 'x/../y', 'x//y', './x', 'a\\b']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.safe_relative(value)
        self.assertEqual(str(probe.safe_relative('one/two.txt')), 'one/two.txt')

    def test_tree_accepts_link_mode_and_rejects_invalid_entries(self):
        row = '100644 blob ' + 'a' * 40 + '\tx\0'
        self.assertEqual(probe.tree_entries(row)['x']['type'], 'blob')
        self.assertEqual(probe.tree_entries(row.replace('100644', '120000'))['x']['mode'], '120000')
        for bad in [row + row, row.replace('100644', '100600'), row.replace('a' * 40, 'bad'), row.replace('\tx', '\t../x')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe.tree_entries(bad)

    def test_native_counts_reject_bools_floats_skips_duplicates(self):
        summary = {'result': 'Passed', 'totalTestCount': 1, 'passedTests': 1, 'failedTests': 0, 'skippedTests': 0}
        node = {'nodeType': 'Test Case', 'nodeIdentifier': probe.TEST_ID, 'name': probe.METHOD + '()', 'result': 'Passed'}
        probe.verify_summary(summary, {'children': [node]})
        for key, value in [('passedTests', True), ('totalTestCount', 1.0), ('skippedTests', 1), ('failedTests', 1)]:
            bad = dict(summary, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe.verify_summary(bad, {'children': [node]})
        for nodes in [[], [node, node], [dict(node, nodeIdentifier='Unrelated/test()')], [dict(node, result='Skipped')], [dict(node, name='testOther()')]]:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                probe.verify_summary(summary, {'children': nodes})

    def test_simulator_platform_rejects_device_and_missing(self):
        self.assertEqual(probe.simulator_platform('  platform IOSSIMULATOR\n'), ['IOSSIMULATOR'])
        for raw in ['platform IOS\n', 'platform IOSSIMULATOR\nplatform IOS\n', '']:
            with self.assertRaises(ValueError):
                probe.simulator_platform(raw)

    def test_device_selection_is_available_and_bounded(self):
        runtimes = {'runtimes': [{'isAvailable': True, 'identifier': 'com.apple.CoreSimulator.SimRuntime.iOS-26-4', 'version': '26.4'}]}
        types = {'devicetypes': [{'identifier': 'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'}]}
        self.assertEqual(probe.choose_simulator(runtimes, types)[0]['version'], '26.4')
        for bad_runtimes, bad_types in [({'runtimes': []}, types), (runtimes, {'devicetypes': []}),
                                        ({'runtimes': [dict(runtimes['runtimes'][0], version='16.0')]}, types)]:
            with self.assertRaises(ValueError):
                probe.choose_simulator(bad_runtimes, bad_types)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Portable Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('remote', 'add', 'origin', 'https://example.invalid/source.git')
        (self.root / 'source.swift').write_text('unchanged\n')
        self.git('add', 'source.swift')
        self.git('commit', '-qm', 'portable test source')
        self.commit = self.git('rev-parse', 'HEAD')
        self.tree = self.git('rev-parse', 'HEAD^{tree}')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def proof(self, **kwargs):
        return probe.checkout_proof(self.root, kwargs.get('commit', self.commit), kwargs.get('tree', self.tree), kwargs.get('origin', 'https://example.invalid/source.git'))

    def test_clean_checkout_hashes_actual_source(self):
        result = self.proof()
        self.assertEqual(result['files']['source.swift']['sha256'], probe.sha(self.root / 'source.swift'))

    def test_only_optional_dot_git_origin_spelling_is_accepted(self):
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/source')
        self.assertEqual(self.proof()['origin'], 'https://example.invalid/source')
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/source.git/redirect')
        with self.assertRaises(ValueError): self.proof()

    def test_changed_tracked_bytes_fail(self):
        (self.root / 'source.swift').write_text('changed\n')
        with self.assertRaises(ValueError): self.proof()

    def test_untracked_source_fails(self):
        (self.root / 'injected.swift').write_text('extra')
        with self.assertRaises(ValueError): self.proof()

    def test_wrong_commit_tree_origin_fail(self):
        for item in [{'commit': '0' * 40}, {'tree': '0' * 40}, {'origin': 'https://example.invalid/substitute.git'}]:
            with self.subTest(item=item), self.assertRaises(ValueError): self.proof(**item)

    def test_source_symlink_fails(self):
        (self.root / 'source.swift').unlink()
        (self.root / 'source.swift').symlink_to('/etc/hosts')
        with self.assertRaises(ValueError): self.proof()

    def commit_links(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'portable tracked links')
        self.commit = self.git('rev-parse', 'HEAD')
        self.tree = self.git('rev-parse', 'HEAD^{tree}')

    def framework_links(self):
        framework = self.root / 'Framework.framework'
        for name in ['Headers', 'Modules', 'Resources']:
            directory = framework / 'Versions/A' / name
            directory.mkdir(parents=True)
            (directory / 'content').write_text('authentic fixture bytes')
        (framework / 'Versions/A/OpenSSL').write_text('synthetic binary')
        (framework / 'Versions/Current').symlink_to('A')
        for name in ['Headers', 'Modules', 'Resources', 'OpenSSL']:
            (framework / name).symlink_to('Versions/Current/' + name)
        self.commit_links()
        return framework

    def test_exact_framework_links_are_hashed_without_dereference(self):
        self.framework_links()
        rows = self.proof()['files']
        links = {name: row for name, row in rows.items() if row['mode'] == '120000'}
        self.assertEqual(len(links), 5)
        for name, row in links.items():
            raw = os.readlink(os.fsencode(self.root / name))
            self.assertEqual(row['sha256'], probe.hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['gitObject'], self.git('rev-parse', 'HEAD:' + name))
            self.assertTrue(row['resolvedTarget'].startswith('Framework.framework/Versions/A'))

    def test_committed_absolute_escape_missing_cycle_and_untracked_targets_fail(self):
        for target in ['/etc/hosts', '../outside', 'missing', 'link', '.git/HEAD']:
            with self.subTest(target=target):
                path = self.root / 'link'
                if path.is_symlink(): path.unlink()
                path.symlink_to(target)
                self.commit_links()
                with self.assertRaises(ValueError): self.proof()

    def test_contained_parent_relative_target_is_supported(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested/link').symlink_to('../source.swift')
        self.commit_links()
        self.assertEqual(self.proof()['files']['nested/link']['resolvedTarget'], 'source.swift')

    def test_mutual_link_cycle_fails(self):
        (self.root / 'one').symlink_to('two')
        (self.root / 'two').symlink_to('one')
        self.commit_links()
        with self.assertRaises(ValueError): self.proof()

    def test_modified_link_bytes_and_regular_file_substitution_fail(self):
        framework = self.framework_links()
        path = framework / 'Headers'
        rows = probe.tree_entries(probe.git(self.root, 'ls-tree', '-rz', '--full-tree', 'HEAD'))
        for target in ['Versions/A/Headers', '/etc/hosts']:
            path.unlink(); path.symlink_to(target)
            with self.subTest(target=target), self.assertRaises(ValueError):
                probe.source_link_target(self.root, 'Framework.framework/Headers', rows['Framework.framework/Headers'])
            with self.assertRaises(ValueError): self.proof()
        path.unlink(); path.write_text('Versions/Current/Headers')
        with self.assertRaises(ValueError): self.proof()
        with self.assertRaises(ValueError):
            probe.source_link_target(self.root, 'Framework.framework/Headers', rows['Framework.framework/Headers'])

    def test_changed_target_file_and_parent_directory_substitution_fail(self):
        framework = self.framework_links()
        (framework / 'Versions/A/Headers/content').write_text('changed target')
        with self.assertRaises(ValueError): self.proof()
        self.git('checkout', '--', 'Framework.framework/Versions/A/Headers/content')
        path = framework / 'Versions/A/Headers'
        (path / 'content').unlink(); path.rmdir(); path.symlink_to('/tmp')
        with self.assertRaises(ValueError): self.proof()

    def test_actual_pinned_source_when_available(self):
        source = os.environ.get('P2_HOST_PROBE_SOURCE')
        if not source:
            self.skipTest('Optional local exact-source check; portable suite does not claim native execution')
        source = Path(source)
        result = probe.checkout_proof(source, probe.SOURCE, probe.TREE, probe.SOURCE_URL, dependencies=True)
        self.assertEqual({p: r['gitObject'] for p, r in result['files'].items() if r['type'] == 'commit'},
                         {p: r['commit'] for p, r in probe.DEPENDENCIES.items()})
        probe.verify_declared_dependencies(source)
        shell = (source / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
        self.assertIn('Button("Later", role: .cancel) {}', shell)
        self.assertIn('WindowGroup(id: "Main")', (source / 'LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift').read_text())
        self.assertIn('LCSettingsView().tabItem', shell)
        self.assertIn('V3LanguageSettingsSection()', (source / 'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift').read_text())


class ProductAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.host = self.root / 'source'
        self.derived = self.root / 'derived'
        self.app = self.derived / 'Build/Products/Release-iphonesimulator/LiveContainer.app'
        self.app.mkdir(parents=True)
        self.info = {'CFBundleIdentifier': probe.BUNDLE_ID, 'CFBundleExecutable': 'LiveContainer',
                     'CFBundlePackageType': 'APPL', 'CFBundleSupportedPlatforms': ['iPhoneSimulator']}
        (self.app / 'Info.plist').write_bytes(plistlib.dumps(self.info))
        (self.app / 'LiveContainer').write_bytes(b'portable-test-binary-not-native')
        self.settings = {'TARGET_NAME': 'LiveContainer', 'PRODUCT_NAME': 'LiveContainer', 'CONFIGURATION': 'Release',
                         'PRODUCT_BUNDLE_IDENTIFIER': probe.BUNDLE_ID, 'WRAPPER_EXTENSION': 'app', 'MACH_O_TYPE': 'mh_execute',
                         'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator', 'FULL_PRODUCT_NAME': 'LiveContainer.app',
                         'SDK_NAME': 'iphonesimulator26.4', 'PROJECT_FILE_PATH': str(self.host / probe.PROJECT),
                         'SRCROOT': str(self.host), 'TARGET_BUILD_DIR': str(self.app.parent), 'EXECUTABLE_NAME': 'LiveContainer'}
        self.testroot = self.root / 'runner-derived/Build/Products'
        self.runner = self.testroot / 'Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app'
        self.testbundle = self.runner / 'PlugIns/HostSettingsProbeUITests.xctest'
        self.testbundle.mkdir(parents=True)
        (self.runner / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner'}))
        self.target = {'BlueprintName': 'HostSettingsProbeUITests', 'IsUITestBundle': True,
                       'TestHostBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner',
                       'TestHostPath': '__TESTROOT__/Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app',
                       'TestBundlePath': '__TESTHOST__/PlugIns/HostSettingsProbeUITests.xctest'}

    def tearDown(self): self.tmp.cleanup()

    def document(self):
        return {'__xctestrun_metadata__': {'FormatVersion': 1}, 'HostSettingsProbeUITests': copy.deepcopy(self.target)}

    def test_product_build_has_no_setting_or_source_overrides(self):
        command = probe.product_build_command(self.host, self.derived, 'device-id')
        self.assertEqual(command[command.index('-scheme') + 1], 'LiveContainer')
        self.assertEqual(command[command.index('-configuration') + 1], 'Release')
        self.assertEqual(command[command.index('-project') + 1], str(self.host / probe.PROJECT))
        self.assertFalse(any(re.match(r'^[A-Z_]+=', value) for value in command))
        self.assertNotIn('test', command)

    def test_exact_build_settings_product_match(self):
        settings, app = probe.host_build_settings(json.dumps([{'target': 'LiveContainer', 'buildSettings': self.settings}]), self.host, self.derived)
        self.assertEqual(app, self.app)
        self.assertEqual(probe.verify_product(app, settings)['bundleIdentifier'], probe.BUNDLE_ID)

    def test_target_project_sdk_bundle_path_substitution_fail(self):
        for key, value in [('PRODUCT_BUNDLE_IDENTIFIER', 'org.fixture'), ('PROJECT_FILE_PATH', '/other/project.xcodeproj'),
                           ('SDK_NAME', 'iphoneos26.4'), ('TARGET_BUILD_DIR', '/outside'), ('CONFIGURATION', 'Debug'), ('SRCROOT', '/other')]:
            settings = dict(self.settings, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.host_build_settings(json.dumps([{'target': 'LiveContainer', 'buildSettings': settings}]), self.host, self.derived)

    def test_duplicate_product_target_fails(self):
        row = {'target': 'LiveContainer', 'buildSettings': self.settings}
        with self.assertRaises(ValueError): probe.host_build_settings(json.dumps([row, row]), self.host, self.derived)

    def test_product_info_identity_fails(self):
        for key, value in [('CFBundleIdentifier', 'org.fixture'), ('CFBundleExecutable', 'Fixture'),
                           ('CFBundleSupportedPlatforms', ['iPhoneOS']), ('CFBundlePackageType', 'FMWK')]:
            (self.app / 'Info.plist').write_bytes(plistlib.dumps(dict(self.info, **{key: value})))
            with self.subTest(key=key), self.assertRaises(ValueError): probe.verify_product(self.app, self.settings)

    def test_bundle_hash_inventory_detects_change(self):
        before = probe.bundle_inventory(self.app)
        (self.app / 'LiveContainer').write_bytes(b'changed')
        self.assertNotEqual(before, probe.bundle_inventory(self.app))

    def test_escaping_bundle_symlink_fails(self):
        (self.app / 'escape').symlink_to('/etc/hosts')
        with self.assertRaises(ValueError): probe.bundle_inventory(self.app)

    def test_external_generated_v1_and_v2_are_read_only(self):
        one = self.document()
        two = {'__xctestrun_metadata__': {'FormatVersion': 2}, 'TestConfigurations': [{'IsEnabled': True, 'TestTargets': [self.target]}]}
        for data in [one, two]:
            before = copy.deepcopy(data)
            result = probe.validate_xctestrun(data, self.testroot, self.app)
            self.assertEqual(data, before)
            self.assertEqual(result['targetApplicationMode'], 'external-XCUIApplication-bundleIdentifier')

    def test_target_application_and_launch_overrides_fail(self):
        for key, value in [('UITargetAppPath', '__TESTROOT__/Fixture.app'), ('UITargetAppBundleIdentifier', 'org.fixture'),
                           ('UITargetAppEnvironmentVariables', {'AppleLanguages': ['he']}), ('CommandLineArguments', ['--stub']),
                           ('UITargetAppCommandLineArguments', ['-AppleLanguages', '(he)']), ('TestLanguage', 'he'), ('TestRegion', 'IL'),
                           ('OnlyTestIdentifiers', ['Other/test']), ('SkipTestIdentifiers', [probe.TEST_ID])]:
            data = self.document(); data['HostSettingsProbeUITests'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): probe.validate_xctestrun(data, self.testroot, self.app)

    def test_unknown_runner_schema_target_and_path_fail(self):
        for mutation in [('TestHostPath', '__TESTROOT__/../Fixture.app'), ('TestBundlePath', '__TESTHOST__/../Elsewhere.xctest'),
                         ('TestHostBundleIdentifier', 'org.fixture'), ('BlueprintName', 'Other'), ('IsUITestBundle', 1),
                         ('DependentProductPaths', ['__TESTROOT__/Fixture.app'])]:
            data = self.document(); data['HostSettingsProbeUITests'][mutation[0]] = mutation[1]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): probe.validate_xctestrun(data, self.testroot, self.app)
        data = self.document(); data['Other'] = self.target
        with self.assertRaises(ValueError): probe.validate_xctestrun(data, self.testroot, self.app)
        data = self.document(); data['__xctestrun_metadata__']['FormatVersion'] = True
        with self.assertRaises(ValueError): probe.validate_xctestrun(data, self.testroot, self.app)

    def test_generated_runner_has_no_app_target_or_product_dependency(self):
        project = (HERE / 'project/project.pbxproj').read_text()
        self.assertEqual(project.count('isa = PBXNativeTarget;'), 1)
        self.assertIn('com.apple.product-type.bundle.ui-testing', project)
        for forbidden in ['com.apple.product-type.application', 'TEST_TARGET_NAME', 'TEST_HOST', 'TestTargetID', 'LiveContainer.xcodeproj']:
            self.assertNotIn(forbidden, project)
        declared = set(re.findall(r'^\s*([A-F0-9]{24}) = ', project, re.M))
        referenced = set(re.findall(r'\b[A-F0-9]{24}\b', project))
        self.assertEqual(declared, referenced)
        for identifier in re.findall(r'\b[A-F0-9]{25,}\b', project): self.fail('Overlong project ID: ' + identifier)
        scheme = ET.fromstring((HERE / 'project/HostSettingsProbe.xcscheme').read_text())
        self.assertEqual(len(scheme.findall('.//TestableReference')), 1)
        self.assertFalse(scheme.findall('.//MacroExpansion'))

    def test_runner_only_launches_actual_bundle_and_taps_narrow_ui(self):
        source = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
        source = re.sub(r'//[^\n]*', '', source)
        self.assertEqual(source.count('app.launch()'), 1)
        for forbidden in ['@main', 'import SwiftUI', 'UserDefaults', '.launchArguments', '.launchEnvironment', '.terminate()',
                          'accessibilityActivate', 'coordinate(withNormalizedOffset', 'allow.tap()', 'alert.staticTexts[body]']:
            self.assertNotIn(forbidden, source)
        self.assertIn('XCUIApplication(bundleIdentifier: identity["bundleIdentifier"]!)', source)
        self.assertIn('cancel.tap()', source)
        self.assertIn('Array($0.utf8) == Array(body.utf8)', source)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.identity = {'executableSHA256': '1' * 64}
        self.builder = '2' * 40
        self.report = {'identity': {'sourceCommit': probe.SOURCE, 'sourceTree': probe.TREE, 'builderCommit': self.builder,
                                    'bundleIdentifier': probe.BUNDLE_ID, 'executableSHA256': self.identity['executableSHA256']},
                       'status': 'observed', 'launchCount': 1, 'navigation': [], 'observations': []}
        self.files = {'host-probe-launch.png': b'\x89PNG\r\n\x1a\nportable', 'host-probe-launch.txt': b'actual bounded AX'}
        for stage, code, native, tab in [('en-before', 'en', 'English', 'Settings'), ('he-selected', 'he', 'עברית', 'הגדרות'), ('en-returned', 'en', 'English', 'Settings')]:
            self.report['observations'].append({'stage': stage, 'language': code, 'selectedNativeName': native, 'selectedValueVisible': True,
                'settingsTabVisible': True, 'applicationForeground': True, 'pickerLabel': 'Language', 'pickerValue': native,
                'pickerChildLabels': [], 'settingsTabLabel': tab, 'pickerFrame': {'x': 10, 'y': 20, 'width': 200, 'height': 40}})
            self.files['host-probe-' + stage + '.png'] = b'\x89PNG\r\n\x1a\nportable'
            self.files['host-probe-' + stage + '.txt'] = ('actual ' + native + ' AX').encode()
        self.save()

    def tearDown(self): self.tmp.cleanup()

    def save(self, suffix=False):
        self.files['host-probe-observations.json'] = json.dumps(self.report).encode()
        manifest = [{'testIdentifier': probe.TEST_ID, 'attachments': []}]
        for index, (name, content) in enumerate(self.files.items()):
            filename = f'{index:03d}' + Path(name).suffix
            (self.root / filename).write_bytes(content)
            display = name
            if suffix:
                display = Path(name).stem + '_0_12345678-1234-1234-1234-123456789abc' + Path(name).suffix
            manifest[0]['attachments'].append({'exportedFileName': filename, 'suggestedHumanReadableName': display})
        (self.root / 'manifest.json').write_text(json.dumps(manifest))

    def verify(self): return probe.verify_observations(self.root, self.identity, self.builder)

    def test_exact_bounded_raw_evidence(self):
        self.assertEqual(len(self.verify()['attachments']), 9)
        self.save(suffix=True)
        self.assertEqual(len(self.verify()['attachments']), 9)

    def test_missing_attachment_fails(self):
        del self.files['host-probe-he-selected.txt']; self.save()
        with self.assertRaises(ValueError): self.verify()

    def test_reordered_or_wrong_raw_selection_fails(self):
        self.report['observations'][1]['pickerValue'] = 'English'; self.save()
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_source_product_or_builder_identity_fails(self):
        self.report['identity']['bundleIdentifier'] = 'org.fixture'; self.save()
        with self.assertRaises(ValueError): self.verify()

    def test_nonfinite_frame_boolean_count_and_number_flags_fail(self):
        for field, value in [('launchCount', True), ('launchCount', 1.0)]:
            self.report[field] = value; self.save()
            with self.assertRaises(ValueError): self.verify()
        self.report['launchCount'] = 1
        self.report['observations'][0]['selectedValueVisible'] = 1; self.save()
        with self.assertRaises(ValueError): self.verify()
        self.report['observations'][0]['selectedValueVisible'] = True
        self.report['observations'][0]['pickerFrame']['width'] = float('inf'); self.save()
        with self.assertRaises(ValueError): self.verify()

    def test_attachment_traversal_duplicate_and_other_test_fail(self):
        original = json.loads((self.root / 'manifest.json').read_text())
        for kind in ['path', 'duplicate', 'test', 'alias']:
            data = copy.deepcopy(original)
            if kind == 'path': data[0]['attachments'][0]['exportedFileName'] = '../escape'
            elif kind == 'duplicate': data[0]['attachments'].append(data[0]['attachments'][0])
            elif kind == 'test': data[0]['testIdentifier'] = 'Other/test()'
            elif kind == 'alias': data[0]['attachments'][1]['exportedFileName'] = data[0]['attachments'][0]['exportedFileName']
            (self.root / 'manifest.json').write_text(json.dumps(data))
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.verify()

    def test_exact_known_cancel_requires_receipts_and_actual_labels(self):
        body = 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.'
        title = 'Stay Informed About Refreshes'
        self.report['navigation'] = [{'action': 'known-notification-later', 'title': title, 'body': body,
                'actualStaticTextLabels': [title, body], 'buttons': ['Allow Notifications', 'Later'], 'tapped': 'Later',
                'hittableBeforeTap': True, 'noAlertsAfterTap': True}]
        for stage in ['notice-before', 'notice-after']:
            self.files['host-probe-' + stage + '.png'] = b'\x89PNG\r\n\x1a\nportable'
            self.files['host-probe-' + stage + '.txt'] = b'actual bounded alert AX'
        self.save(); self.assertEqual(len(self.verify()['attachments']), 13)
        self.report['navigation'][0]['tapped'] = 'Allow Notifications'; self.save()
        with self.assertRaises(ValueError): self.verify()
        self.report['navigation'][0]['tapped'] = 'Later'
        self.report['navigation'][0]['actualStaticTextLabels'] = [title]; self.save()
        with self.assertRaises(ValueError): self.verify()


if __name__ == '__main__': unittest.main()
