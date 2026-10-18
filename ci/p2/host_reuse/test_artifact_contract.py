#!/usr/bin/env python3
"""Portable, synthetic, adversarial tests. No downloads/native tools or skips.

Synthetic trust anchors are patched only in tests so inner boundaries can be
exercised without embedding the private-size original artifact in the repository.
The production authenticate_artifact API exposes no trust-anchor override.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import artifact_contract as c


def js(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def make_zip(entries):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    return stream.getvalue()


def source_row(data, mode='100644'):
    return {'mode': mode, 'type': 'blob', 'gitObject': c.git_object('blob', data), 'sha256': c.digest(data)}


def make_tar(blobs, directories, *, mutate=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT, pax_headers={'comment': c.SOURCE}) as archive:
        for name in sorted(directories):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o775
            if mutate:
                mutate(member)
            archive.addfile(member)
        for name, data in sorted(blobs.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o664
            if mutate:
                mutate(member)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def fixture():
    files = {}
    dependencies = copy.deepcopy(c.DEPENDENCIES)
    dependency_manifests = {}
    for name, pin in dependencies.items():
        rows = {'README': source_row((name + ' historical source').encode())}
        pin['tree'] = c.reconstruct_tree(rows)
        dependency_manifests[name] = {'commit': pin['commit'], 'tree': pin['tree'], 'origin': pin['fetch_url'],
                                      'root': c.HOST_ROOT + '/' + name, 'files': rows}
    blobs = {'src/file%03d' % n: ('source %d' % n).encode() for n in range(298)}
    blobs[c.CATALOG] = js({'sourceLanguage': 'en', 'strings': {}})
    blobs['README'] = b'Authenticated synthetic host source\n'
    rows = {name: source_row(data) for name, data in blobs.items()}
    rows.update({name: {'mode': '160000', 'type': 'commit', 'gitObject': pin['commit']} for name, pin in dependencies.items()})
    tree = c.reconstruct_tree(rows)
    source = {'commit': c.SOURCE, 'tree': tree, 'origin': c.SOURCE_URL.removesuffix('.git'), 'root': c.HOST_ROOT, 'files': rows}
    files['provenance/source-initial.json'] = js(source)
    for name, manifest in dependency_manifests.items():
        files['provenance/' + name + '.json'] = js(manifest)
    for phase in ('before-build', 'after-build', 'final'):
        files['provenance/source-' + phase + '.json'] = js({'host': source, 'dependencies': dependency_manifests})
    for name, manifest, origin in [('source', source, c.SOURCE_URL), *[(name, manifest, dependencies[name]['fetch_url']) for name, manifest in dependency_manifests.items()]]:
        for suffix in ('observed-before-verification-expected', 'final-observed-expected'):
            files['provenance/' + name + '-' + suffix + '.json'] = js({'commit': manifest['commit'], 'tree': manifest['tree'], 'origin': origin})
    directories = {'Resources', 'src', *dependencies}
    tar_raw = make_tar(blobs, directories)
    files['unchanged-source.tar'] = tar_raw
    literal_values = {'SOURCE': c.SOURCE, 'TREE': tree, 'BUILDER_PARENT': c.BUILDER_PARENT, 'SOURCE_URL': c.SOURCE_URL,
                      'PROJECT': 'LiveContainer.xcodeproj', 'SCHEME': 'LiveContainer', 'CONFIGURATION': 'Release',
                      'BUNDLE_ID': c.BUNDLE_ID, 'DEPENDENCIES': dependencies}
    run = '\n'.join(k + ' = ' + repr(v) for k, v in literal_values.items()).encode()
    adapters = {name: b'# synthetic original adapter\n' for name in ['HostSettingsProbeTests.swift.in', 'README.md', 'install_contract.py',
                'project/HostSettingsProbe.xcscheme', 'project/project.pbxproj', 'test_install_contract.py', 'test_probe.py']}
    adapters['run.py'] = run
    contracts = {name: b'# synthetic original contract\n' for name in ['ci/p2/preflight.py', 'scripts/p2_localization_contract.py',
                 'scripts/p2_permission_contract.py', 'tests/fixtures/p2_localization_contracts/compiled-translations.json']}
    for name, data in adapters.items():
        files['probe-inputs/' + name] = data
        files['preflight-inputs/ci/p2/host_probe/' + name] = data
    for name, data in contracts.items():
        files['resource-contract-inputs/' + name] = data
        files['preflight-inputs/' + name] = data
    inputs = {'schemaVersion': 1, 'sourceCommit': c.SOURCE, 'sourceTree': tree, 'sourceRepository': c.SOURCE_URL,
              'builderParent': c.BUILDER_PARENT, 'project': 'LiveContainer.xcodeproj', 'scheme': 'LiveContainer',
              'configuration': 'Release', 'expectedTest': 'HostSettingsProbeTests/testUnchangedHostSettingsLanguageRoundTrip()',
              'dependencies': dependencies, 'adapterFiles': {k: c.digest(v) for k, v in adapters.items()},
              'contractFiles': {k: c.digest(v) for k, v in contracts.items()}}
    files['inputs.json'] = files['preflight-inputs/ci/p2/host_probe/inputs.json'] = js(inputs)
    files['preflight-expected.json'] = js({'approvedInputsSHA256': c.digest(files['inputs.json']), 'builderAncestor': c.BUILDER_PARENT,
                                          'sourceCommit': c.SOURCE, 'sourceOrigin': c.SOURCE_URL, 'sourceTree': tree})
    settings = {'TARGET_NAME': 'LiveContainer', 'PRODUCT_NAME': 'LiveContainer', 'CONFIGURATION': 'Release',
                'PRODUCT_BUNDLE_IDENTIFIER': c.BUNDLE_ID, 'WRAPPER_EXTENSION': 'app', 'MACH_O_TYPE': 'mh_execute',
                'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator', 'FULL_PRODUCT_NAME': 'LiveContainer.app',
                'EXECUTABLE_NAME': 'LiveContainer', 'PROJECT_FILE_PATH': c.HOST_ROOT + '/LiveContainer.xcodeproj',
                'SRCROOT': c.HOST_ROOT, 'TARGET_BUILD_DIR': c.DERIVED + '/Build/Products/Release-iphonesimulator',
                'SDK_NAME': 'iphonesimulator26.4', 'CODE_SIGNING_ALLOWED': 'YES', 'CODE_SIGNING_REQUIRED': 'NO'}
    files['product-build-settings.json'] = js([{'target': 'LiveContainer', 'buildSettings': settings}])
    udid = '12BE5DE8-FF70-4AF1-9A36-7706F4BD4062'
    files['simulator.json'] = js({'newlyCreated': True, 'udid': udid})
    base = ['xcodebuild', '-project', c.HOST_ROOT + '/LiveContainer.xcodeproj', '-scheme', 'LiveContainer', '-configuration', 'Release',
            '-sdk', 'iphonesimulator', '-destination', 'platform=iOS Simulator,id=' + udid, '-derivedDataPath', c.DERIVED,
            '-disableAutomaticPackageResolution', '-onlyUsePackageVersionsFromResolvedFile']
    for n, suffix, stage in [('038', ['-showBuildSettings', '-json'], 'genuine-build-settings'),
                             ('039', ['-resultBundlePath', c.ORIGINAL_EVIDENCE + '/product-build.xcresult', 'build'], 'genuine-simulator-build')]:
        files['commands/' + n + '.json'] = js({'argv': base + suffix, 'returncode': 0, 'ambientOverrideKeys': [], 'cwd': c.ORIGINAL_CWD,
                                               'developerDir': c.DEVELOPER_DIR, 'stage': stage})
    files['commands/038.stdout'] = files['product-build-settings.json']
    files['commands/039.stdout'] = b'** BUILD SUCCEEDED **\n'
    for n in ('038', '039', '040', '041'):
        for suffix in ('.json', '.stdout', '.stderr'):
            files.setdefault('commands/' + n + suffix, b'{}\n' if suffix == '.json' else b'')
    app = {'data/file%03d' % n: ('resource %d' % n).encode() for n in range(121)}
    app['Info.plist'] = plistlib.dumps({'CFBundleIdentifier': c.BUNDLE_ID, 'CFBundleExecutable': 'LiveContainer',
                                      'CFBundlePackageType': 'APPL', 'CFBundleSupportedPlatforms': ['iPhoneSimulator']})
    app['LiveContainer'] = b'not executable: synthetic bytes only'
    inventory = {name: {'type': 'regular', 'mode': 0o755 if name == 'LiveContainer' else 0o644,
                        'sha256': c.digest(data), 'bytes': len(data), 'executable': name == 'LiveContainer'} for name, data in app.items()}
    identity = {'bundleIdentifier': c.BUNDLE_ID, 'executable': 'LiveContainer', 'executableSHA256': c.digest(app['LiveContainer']),
                'files': inventory, 'infoPlistSHA256': c.digest(app['Info.plist']), 'machOPlatforms': ['IOSSIMULATOR']}
    files['provenance/product-identity.json'] = js(identity)
    files['provenance/product-raw-inventory.json'] = js({'path': settings['TARGET_BUILD_DIR'] + '/LiveContainer.app', 'files': inventory})
    app_entries = [('LiveContainer.app/', b'', stat.S_IFDIR | 0o755), ('LiveContainer.app/data/', b'', stat.S_IFDIR | 0o755)]
    app_entries += [('LiveContainer.app/' + name, data, stat.S_IFREG | inventory[name]['mode']) for name, data in sorted(app.items())]
    files['actual-LiveContainer.app.zip'] = make_zip(app_entries)
    compiled = {key: {'status': 'passed', 'failures': []} for key in ('localization', 'permissions', 'exactPluralValues')}
    compiled['exactPluralValues'].update(checkedLanguageRows=24, runtimePluralSelection=False)
    files['actual-host-compiled-resources.json'] = js({'bundleIdentifier': c.BUNDLE_ID, 'productInventory': inventory, **compiled})
    files['workflow.yml'] = files['preflight-inputs/workflow/p2-host-settings-probe.yml'] = b'# synthetic historical workflow\n'
    files['feasibility.json'] = js({'builderCommit': c.BUILDER, 'builderTree': c.BUILDER_TREE, 'sourceCommit': c.SOURCE,
                                    'sourceTree': tree, 'builderWorktreeStatus': '', 'retentionErrors': [], 'status': 'BLOCKED',
                                    'stage': 'external-runner-generation', 'nativeExecution': False, 'fullAppAcceptance': False,
                                    'finalCompositionAcceptance': False, 'actualHostCompiledResources': compiled,
                                    'workflowSHA256': c.digest(files['workflow.yml'])})
    for n in range(326 - len(files)):
        files['extra-authenticated-evidence/%03d.txt' % n] = b'historical raw evidence\n'
    anchors = {'TREE': tree, 'DEPENDENCIES': dependencies, 'SOURCE_TAR_SHA256': c.digest(tar_raw),
               'INPUTS_SHA256': c.digest(files['inputs.json']), 'APP_ZIP_SHA256': c.digest(files['actual-LiveContainer.app.zip']),
               'BUILD_SETTINGS_SHA256': c.digest(files['product-build-settings.json']),
               'PRODUCT_IDENTITY_SHA256': c.digest(files['provenance/product-identity.json'])}
    return files, anchors, source, blobs, directories, identity, app_entries


def artifact_bytes(files):
    index = {name: {'bytes': len(raw), 'sha256': c.digest(raw)} for name, raw in files.items()}
    return make_zip([(c.PREFIX + name, raw, stat.S_IFREG | 0o644) for name, raw in files.items()] + [
        (c.PREFIX + 'evidence-sha256.json', js(index), stat.S_IFREG | 0o644),
        ('p2-host-probe-portable.log', b'historical portable log\n', stat.S_IFREG | 0o644)])


class ArtifactContractTests(unittest.TestCase):
    def setUp(self):
        self.files, self.anchors, self.source, self.blobs, self.directories, self.identity, self.app_entries = fixture()
        self.stack = ExitStack()
        for key, value in self.anchors.items():
            self.stack.enter_context(patch.object(c, key, value))
        self.addCleanup(self.stack.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def patch_anchor(self, key, raw):
        self.stack.enter_context(patch.object(c, key, c.digest(raw)))

    def modify_json(self, name, change):
        value = c.strict_json(self.files[name])
        change(value)
        self.files[name] = js(value)

    def authenticate(self, files=None):
        raw = artifact_bytes(self.files if files is None else files)
        self.patch_anchor('ARTIFACT_SHA256', raw)
        path = self.root / 'artifact.zip'
        path.write_bytes(raw)
        return c.authenticate_artifact(path, self.root / 'work', self.root / 'evidence')

    def assert_failure_receipt(self, regex):
        with self.assertRaisesRegex((ValueError, KeyError), regex):
            self.authenticate()
        receipt = c.strict_json((self.root / 'evidence/reused-host-authentication.json').read_bytes())
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertFalse((self.root / 'work/reused-host/LiveContainer.app').exists())

    def test_complete_authentication_and_restore(self):
        result = self.authenticate()
        self.assertEqual(set(result), {'app', 'sourceRoot', 'identity', 'settings', 'provenance'})
        self.assertEqual(result['identity'], self.identity)
        self.assertEqual(result['provenance']['status'], 'AUTHENTICATED')
        self.assertEqual(result['provenance']['originalBuild']['sourceBlobCountVerified'], 300)
        self.assertEqual(result['provenance']['originalBuild']['productFileCount'], 123)
        self.assertFalse(result['provenance']['nativeExecution'])
        self.assertTrue(all(not d['sourceBytesRechecked'] for d in result['provenance']['originalBuild']['dependencies'].values()))
        self.assertEqual((result['sourceRoot'] / c.CATALOG).read_bytes(), self.blobs[c.CATALOG])
        self.assertEqual(sum(p.is_file() for p in result['sourceRoot'].rglob('*')), 1)
        self.assertFalse(list(self.root.rglob('installed-LiveContainer.app.zip')))
        self.assertEqual(c.verify_restored_app(result['app'], self.identity['files']), self.identity['files'])
        self.assertEqual(stat.S_IMODE((result['app'] / 'LiveContainer').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((result['app'] / 'Info.plist').stat().st_mode), 0o644)

    def test_outer_digest_failure_is_retained_before_extraction(self):
        path = self.root / 'artifact.zip'
        path.write_bytes(b'not the pinned artifact')
        with self.assertRaisesRegex(ValueError, 'Outer artifact SHA-256'):
            c.authenticate_artifact(path, self.root / 'work', self.root / 'evidence')
        receipt = c.strict_json((self.root / 'evidence/reused-host-authentication.json').read_bytes())
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertFalse((self.root / 'work').exists())

    def test_strict_json_rejects_duplicates_and_nonfinite(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e9999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                c.strict_json(raw)

    def test_unsafe_archive_paths(self):
        for name in ('../escape', '/absolute', 'a/../b', 'a//b', './a', 'a\\b', 'C:/x', 'a\nb', 'e\u0301'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.zip_payload(make_zip([(name, b'x', stat.S_IFREG | 0o644)]), allow_directories=True)

    def test_archive_duplicate_case_prefix_and_unicode_aliases(self):
        for names in [('a', 'a'), ('A/file', 'a/other'), ('a', 'a/b'), ('Straße/file', 'STRASSE/other')]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                c.zip_payload(make_zip([(name, b'x', stat.S_IFREG | 0o644) for name in names]), allow_directories=True)

    def test_zip_symlink_fifo_and_special_bits(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644, stat.S_IFREG | 0o4644, 0o644):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                c.zip_payload(make_zip([('file', b'../../escape', mode)]), allow_directories=True)

    def test_zip_type_mismatch_and_unexpected_directories(self):
        for name, mode, allow in [('dir/', stat.S_IFREG | 0o644, True), ('file', stat.S_IFDIR | 0o755, True), ('dir/', stat.S_IFDIR | 0o755, False)]:
            with self.subTest(name=name, mode=mode, allow=allow), self.assertRaises(ValueError):
                c.zip_payload(make_zip([(name, b'', mode)]), allow_directories=allow)

    def test_index_rejects_unindexed_missing_changed_and_wrong_size(self):
        raw = artifact_bytes(self.files)
        payload = c.zip_payload(raw, allow_directories=False)
        for mode in ('extra', 'missing', 'bytes', 'size'):
            changed = copy.deepcopy(payload)
            if mode == 'extra':
                changed[c.PREFIX + 'extra'] = {'data': b'x'}
            elif mode == 'missing':
                changed.pop(c.PREFIX + 'inputs.json')
            elif mode == 'bytes':
                changed[c.PREFIX + 'inputs.json']['data'] += b' '
            else:
                index = c.strict_json(changed[c.PREFIX + 'evidence-sha256.json']['data'])
                index['inputs.json']['bytes'] += 1
                changed[c.PREFIX + 'evidence-sha256.json']['data'] = js(index)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                c.verify_index(changed)

    def test_reconstructs_known_git_empty_blob_tree(self):
        row = source_row(b'')
        # Independently assemble Git's tree object format and known empty blob.
        self.assertEqual(row['gitObject'], 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391')
        payload = b'100644 empty\0' + bytes.fromhex(row['gitObject'])
        expected = hashlib.sha1(b'tree ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
        self.assertEqual(c.reconstruct_tree({'empty': row}), expected)

    def test_git_tree_rejects_type_mode_and_object_overrides(self):
        for key, value in [('mode', '100600'), ('type', 'tree'), ('gitObject', 'not-an-object'), ('sha256', 'bad')]:
            row = source_row(b'a')
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.reconstruct_tree({'a': row})

    def test_git_tree_rejects_file_prefix_and_case_ambiguity(self):
        for names in [('a', 'a/file'), ('dir/a', 'DIR/b')]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                c.reconstruct_tree({name: source_row(b'x') for name in names})

    def test_git_tree_orders_directories_with_trailing_slash(self):
        rows = {'a/file': source_row(b'one'), 'a.c': source_row(b'two')}
        subtree = b'100644 file\0' + bytes.fromhex(rows['a/file']['gitObject'])
        subtree_id = c.git_object('tree', subtree)
        body = b'100644 a.c\0' + bytes.fromhex(rows['a.c']['gitObject']) + b'40000 a\0' + bytes.fromhex(subtree_id)
        self.assertEqual(c.reconstruct_tree(rows), c.git_object('tree', body))

    def test_historical_symlink_bytes_targets_and_cycles(self):
        def link(target, resolved):
            return {**source_row(target.encode(), '120000'), 'linkTarget': target, 'resolvedTarget': resolved}
        rows = {'dir/file': source_row(b'a'), 'alias': link('dir/file', 'dir/file')}
        self.assertRegex(c.reconstruct_tree(rows), '^[0-9a-f]{40}$')
        for target in ('../outside', '/outside', 'missing', 'alias'):
            changed = copy.deepcopy(rows)
            changed['alias'] = link(target, 'dir/file')
            with self.subTest(target=target), self.assertRaises(ValueError):
                c.reconstruct_tree(changed)
        rows['alias']['resolvedTarget'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'resolved link'):
            c.reconstruct_tree(rows)

    def test_source_manifest_tree_mutation(self):
        self.modify_json('provenance/source-initial.json', lambda v: v['files']['README'].update(gitObject='0' * 40))
        self.assert_failure_receipt('provenance changed')

    def test_consistent_source_manifest_mutation_still_fails_tree(self):
        source = copy.deepcopy(self.source)
        source['files']['README']['gitObject'] = '0' * 40
        self.files['provenance/source-initial.json'] = js(source)
        for phase in ('before-build', 'after-build', 'final'):
            self.modify_json('provenance/source-' + phase + '.json', lambda v: v.update(host=source))
        self.assert_failure_receipt('Reconstructed Git tree')

    def test_dependency_tree_and_checkout_pin_are_checked(self):
        name = 'litehook'
        manifest = c.strict_json(self.files['provenance/litehook.json'])
        manifest['files']['README']['mode'] = '100755'
        self.files['provenance/litehook.json'] = js(manifest)
        for phase in ('before-build', 'after-build', 'final'):
            self.modify_json('provenance/source-' + phase + '.json', lambda v: v['dependencies'].update({name: manifest}))
        self.assert_failure_receipt('Reconstructed Git tree')

    def test_source_tar_digest_and_blob_bytes(self):
        with self.assertRaisesRegex(ValueError, 'Source tar digest'):
            c.verify_source_tar(self.files['unchanged-source.tar'] + b'x', self.source)
        blobs = dict(self.blobs)
        blobs['README'] = b'modified source'
        raw = make_tar(blobs, self.directories)
        self.patch_anchor('SOURCE_TAR_SHA256', raw)
        with self.assertRaisesRegex(ValueError, 'Source blob SHA-256'):
            c.verify_source_tar(raw, self.source)

    def test_source_tar_git_blob_checked_independently_of_sha256(self):
        source = copy.deepcopy(self.source)
        source['files']['README']['gitObject'] = 'f' * 40
        with self.assertRaisesRegex(ValueError, 'Git blob'):
            c.verify_source_tar(self.files['unchanged-source.tar'], source)

    def test_source_tar_mode_and_link_overrides(self):
        for override in ('mode', 'link'):
            def mutate(member):
                if member.name == 'README':
                    if override == 'mode':
                        member.mode = 0o775
                    else:
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/outside'
            raw = make_tar(self.blobs, self.directories, mutate=mutate)
            self.patch_anchor('SOURCE_TAR_SHA256', raw)
            with self.subTest(override=override), self.assertRaises(ValueError):
                c.verify_source_tar(raw, self.source)

    def test_input_identity_and_adapter_bytes_are_checked(self):
        self.files['probe-inputs/run.py'] += b'\n# changed'
        self.assert_failure_receipt('adapter bytes')

    def test_input_digest_checked(self):
        self.files['inputs.json'] += b' '
        self.assert_failure_receipt('approved inputs digest')

    def test_build_command_rejects_build_setting_override(self):
        self.modify_json('commands/039.json', lambda v: v['argv'].append('CODE_SIGNING_ALLOWED=NO'))
        self.assert_failure_receipt('build command/override')

    def test_build_command_rejects_ambient_override(self):
        self.modify_json('commands/039.json', lambda v: v.update(ambientOverrideKeys=['OTHER_LDFLAGS']))
        self.assert_failure_receipt('build command/override')

    def test_build_settings_reject_any_byte_change(self):
        self.modify_json('product-build-settings.json', lambda v: v[0]['buildSettings'].update(OTHER_LDFLAGS='-inject'))
        self.assert_failure_receipt('complete build-settings digest')

    def test_build_settings_reject_wrong_source_even_with_digest_replaced(self):
        self.modify_json('product-build-settings.json', lambda v: v[0]['buildSettings'].update(SRCROOT='/another/source'))
        self.patch_anchor('BUILD_SETTINGS_SHA256', self.files['product-build-settings.json'])
        self.files['commands/038.stdout'] = self.files['product-build-settings.json']
        self.assert_failure_receipt('product build settings')

    def test_original_builder_and_native_claims_checked(self):
        self.modify_json('feasibility.json', lambda v: v.update(nativeExecution=True))
        self.assert_failure_receipt('Historical native scope')

    def test_original_compiled_resource_results_checked(self):
        self.modify_json('actual-host-compiled-resources.json', lambda v: v['localization'].update(status='failed'))
        self.assert_failure_receipt('Prior resource contract failed')

    def test_app_zip_digest_checked(self):
        with self.assertRaisesRegex(ValueError, 'built-app ZIP digest'):
            c.verify_app_zip(self.files['actual-LiveContainer.app.zip'] + b'x', self.identity)

    def test_app_zip_rejects_file_bytes_mode_symlink_and_extra(self):
        for override in ('bytes', 'mode', 'symlink', 'extra', 'directory-mode'):
            entries = list(self.app_entries)
            if override == 'extra':
                entries.append(('LiveContainer.app/unexpected', b'x', stat.S_IFREG | 0o644))
            elif override == 'directory-mode':
                entries[0] = (entries[0][0], b'', stat.S_IFDIR | 0o700)
            else:
                index = next(i for i, row in enumerate(entries) if row[0] == 'LiveContainer.app/LiveContainer')
                name, data, mode = entries[index]
                entries[index] = (name, b'changed' if override == 'bytes' else data,
                                  stat.S_IFREG | 0o644 if override == 'mode' else stat.S_IFLNK | 0o777 if override == 'symlink' else mode)
            raw = make_zip(entries)
            self.patch_anchor('APP_ZIP_SHA256', raw)
            with self.subTest(override=override), self.assertRaises(ValueError):
                c.verify_app_zip(raw, self.identity)

    def test_app_identity_info_and_executable_hash_checked(self):
        for key in ('infoPlistSHA256', 'executableSHA256'):
            identity = copy.deepcopy(self.identity)
            identity[key] = '0' * 64
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'hashes differ'):
                c.verify_app_zip(self.files['actual-LiveContainer.app.zip'], identity)

    def test_restore_cannot_write_through_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        linked = self.root / 'linked'
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'not a real directory'):
            c.write_new(linked / 'file', b'no')
        self.assertEqual(list(outside.iterdir()), [])

    def test_read_stable_rejects_symlink_and_hardlink(self):
        real = self.root / 'real'
        real.write_bytes(b'data')
        link = self.root / 'link'
        link.symlink_to(real)
        with self.assertRaises(ValueError):
            c.read_stable(link)
        link.unlink()
        os.link(real, link)
        with self.assertRaisesRegex(ValueError, 'hard-linked'):
            c.read_stable(real)

    def test_read_stable_detects_changed_metadata(self):
        path = self.root / 'input'
        path.write_bytes(b'constant bytes')
        real_fstat = os.fstat
        calls = 0
        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                os.chmod(path, 0o600)
            return real_fstat(fd)
        with patch.object(c.os, 'fstat', changed), self.assertRaisesRegex(ValueError, 'changed during read'):
            c.read_stable(path)

    def test_repeated_output_is_not_overwritten(self):
        first = self.authenticate()
        raw = (self.root / 'evidence/reused-host-authentication.json').read_bytes()
        with self.assertRaises(FileExistsError):
            c.authenticate_artifact(self.root / 'artifact.zip', self.root / 'work', self.root / 'evidence')
        self.assertEqual((self.root / 'evidence/reused-host-authentication.json').read_bytes(), raw)
        self.assertEqual(c.verify_restored_app(first['app'], self.identity['files']), self.identity['files'])

    def test_restored_inventory_detects_full_mode_and_new_directory(self):
        result = self.authenticate()
        path = result['app'] / 'Info.plist'
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            c.verify_restored_app(result['app'], self.identity['files'])
        path.chmod(0o644)
        (result['app'] / 'extra-empty-directory').mkdir()
        with self.assertRaisesRegex(ValueError, 'directory set'):
            c.verify_restored_app(result['app'], self.identity['files'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
