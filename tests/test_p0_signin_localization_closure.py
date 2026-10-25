"""Mocked build orchestration and real source checks, never native execution evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p0_localization_renderer', ROOT / 'scripts/run_p0_signin_rendering.py')
renderer = importlib.util.module_from_spec(spec); spec.loader.exec_module(renderer)


class P0LocalizationProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'P0SignIn.xcodeproj'
        shutil.copytree(ROOT / 'tests/fixtures/p0_signin_project', self.project)
        self.live = self.root / 'live'; (self.live / 'Resources').mkdir(parents=True)
        self.catalog = self.live / 'Resources/Localizable.xcstrings'; self.catalog.write_bytes(b'{"synthetic_catalog_only":true}\n')

    def test_native_project_has_exact_source_and_catalog_resource_edges(self):
        original = (self.project / 'project.pbxproj').read_text()
        renderer.configure_localized_project(self.project, self.live)
        text = (self.project / 'project.pbxproj').read_text()
        self.assertEqual((self.project / 'Localizable.xcstrings').read_bytes(), self.catalog.read_bytes())
        for identifier in range(0x100, 0x107):
            self.assertEqual(len(re.findall(r'^' + f'{identifier:024X}' + r' =', text, re.M)), 1)
        self.assertIn('lastKnownFileType = text.json.xcstrings; path = P0SignIn.xcodeproj/Localizable.xcstrings; sourceTree = SOURCE_ROOT;', text)
        self.assertIn('buildPhases = (00000000000000000000008F,000000000000000000000106,);', text)
        self.assertIn('isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (000000000000000000000105,);', text)
        self.assertIn('files = (000000000000000000000101,000000000000000000000103,', text)
        # Existing test target, source inventory and native test scheme are retained.
        for identifier in ('000000000000000000000090', '000000000000000000000093'):
            row = next(line for line in original.splitlines() if line.startswith(identifier + ' ='))
            self.assertIn(row, text)
        self.assertEqual((self.project / 'xcshareddata/xcschemes/P0SignIn.xcscheme').read_bytes(),
                         (ROOT / 'tests/fixtures/p0_signin_project/xcshareddata/xcschemes/P0SignIn.xcscheme').read_bytes())

    def test_drift_and_repeat_configuration_rejected(self):
        path = self.project / 'project.pbxproj'; raw = path.read_bytes()
        path.write_text(path.read_text().replace('knownRegions = (en,Base);', 'knownRegions = (en);'))
        with self.assertRaisesRegex(ValueError, 'anchor drift'): renderer.configure_localized_project(self.project, self.live)
        self.assertFalse((self.project / 'Localizable.xcstrings').exists())
        path.write_bytes(raw); renderer.configure_localized_project(self.project, self.live)
        with self.assertRaisesRegex(ValueError, 'already present'): renderer.configure_localized_project(self.project, self.live)

    def test_catalog_and_compiled_resource_bytes_survive_existing_transport(self):
        spec = importlib.util.spec_from_file_location('localization_transport_fixture', ROOT / 'tests/test_p0_signin_transport_fragments.py')
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        parent = self.root / 'transport-fixture'; parent.mkdir()
        evidence, logs = fixture.EvidenceFragmentTransportTests().fixture(parent)
        files = {'Localizable.xcstrings': b'{"synthetic_catalog":true}',
                 'compiled-localization/en.lproj/Localizable.strings': b'"Password" = "Password";',
                 'compiled-localization/en.lproj/Localizable.stringsdict': b'{"synthetic_plural":true}'}
        for name, raw in files.items():
            path = evidence / 'p0-signin/project' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        output = parent / 'fragments'
        index = renderer.prepare_transport_fragments(evidence, output, fixture.COMMIT, fixture.RUN, logs)
        with zipfile.ZipFile(output / index['fragments']['metadata']['file']) as archive:
            for name, raw in files.items():
                relative = evidence.name + '/p0-signin/project/' + name
                self.assertEqual(archive.read(relative), raw)
                self.assertEqual(index['fragments']['metadata']['files'][relative]['sha256'], hashlib.sha256(raw).hexdigest())

    def test_acceptance_matrix_declares_english_process_locale_and_preference(self):
        text = (ROOT / 'tests/fixtures/p0_signin_ui_tests.swift').read_text()
        self.assertIn('["-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-V3AppLanguage", "en"]', text)
        self.assertIn('+ (largest ? ["--largest"] : [])', text)
        self.assertIn('.environment(\\.locale, Locale(identifier: "en"))', (ROOT / 'tests/fixtures/p0_signin_app.swift').read_text())
        self.assertIn('error.label == "Sign-in failed before completion. Copy Details to help identify the cause.', text)
        self.assertEqual(renderer.REQUIRED_CASES, {'credentials-default', 'credentials-largest', 'submitting-default', 'submitting-largest'})


class P0ActualLocalizationClosureTests(unittest.TestCase):
    def source(self):
        configured = os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE')
        if not configured: return None
        live = Path(configured)
        if not (live / renderer.localization.LOCALIZATION_SOURCES[0]).exists(): return None
        return live

    def test_complete_production_utilities_and_prompt_locale_retained(self):
        live = self.source()
        if live is None: return
        shell = live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
        sources, hashes = renderer.extract_sources(shell, ROOT / 'scripts/templates')
        for name in renderer.localization.LOCALIZATION_SOURCES:
            path = live / name
            self.assertEqual(sources[path.name].encode(), path.read_bytes())
            self.assertEqual(hashes['maintained-source/' + name], renderer.digest(path))
        self.assertIn('@Environment(\\.locale) private var displayLocale', sources['V3PromptSection.swift'])
        self.assertIn(renderer.declaration(shell.read_text(), 'struct V3PromptSection'), sources['V3PromptSection.swift'])
        self.assertIn('enum V3AppLanguage', sources['V3Localization.swift'])
        self.assertIn('struct V3DisplayMessage', sources['V3Localization.swift'])
        self.assertIn('struct V3LocalizedText', sources['V3LanguageSettings.swift'])
        self.assertIn('struct V3LocalizedLabel', sources['V3LanguageSettings.swift'])

    def test_every_required_real_input_is_fail_closed(self):
        live = self.source()
        if live is None: return
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)
            paths = ('LiveContainerSwiftUI/Views/V3UnifiedShell.swift', 'SideStoreSupport/SideStore.swift',
                     *renderer.localization.LOCALIZATION_SOURCES, renderer.localization.LOCALIZATION_CATALOG)
            for name in paths:
                target = copied / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(live / name, target)
            for name in (*renderer.localization.LOCALIZATION_SOURCES, renderer.localization.LOCALIZATION_CATALOG):
                target = copied / name; raw = target.read_bytes(); target.unlink()
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'dependency missing'):
                    renderer.extract_sources(copied / paths[0], ROOT / 'scripts/templates')
                target.write_bytes(raw)

    def prepare_fixture(self, root, mutation=None):
        live = self.source(); output = root / 'evidence'; output.mkdir()
        catalog = json.loads((live / renderer.localization.LOCALIZATION_CATALOG).read_bytes())['strings']
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[0] == 'git': return '0' * 40
            if args[:2] == ('xcodebuild', 'build-for-testing'):
                self.assertEqual(kwargs['timeout'], 300)
                products = output / 'p0-build/DerivedData/Build/Products'; products.mkdir(parents=True)
                (products / 'fixture.xctestrun').write_text('mock only; never native evidence')
                app = products / 'Debug-iphonesimulator/P0SignIn.app'
                for language in renderer.localization.LOCALIZATION_LANGUAGES:
                    folder = app / (language + '.lproj'); folder.mkdir(parents=True)
                    values = {key: catalog[key]['localizations'][language]['stringUnit']['value'] for key in renderer.SIGNIN_LOCALIZATION_KEYS}
                    if mutation == 'label' and language == 'he': values['Password'] = 'unverified'
                    (folder / 'Localizable.strings').write_text(json.dumps(values))
                    if mutation != 'missing' or language != 'he':
                        (folder / 'Localizable.stringsdict').write_text('{"synthetic_plural_fixture":true}')
                if mutation == 'catalog-input':
                    (output / 'p0-build/P0SignIn.xcodeproj/Localizable.xcstrings').write_text('{}')
                if mutation == 'swift-input':
                    (output / 'p0-build/Sources/V3Localization.swift').write_text('// changed')
                return ''
            self.assertEqual(args[:5], ('plutil', '-convert', 'json', '-o', '-'))
            return Path(args[5]).read_text()
        prepared = renderer.prepare(output, live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift', command)
        return output, prepared, calls

    def test_preparation_binds_actual_sources_project_catalog_and_resource_bytes(self):
        live = self.source()
        if live is None: return
        with tempfile.TemporaryDirectory() as temporary:
            output, prepared, calls = self.prepare_fixture(Path(temporary))
            evidence = output / 'p0-signin'; identity = json.loads((evidence / 'source-identity.json').read_text())
            self.assertEqual(identity['sourceSHA256'], prepared['sourceSHA256'])
            for name in renderer.localization.LOCALIZATION_SOURCES:
                self.assertEqual((evidence / 'sources' / Path(name).name).read_bytes(), (live / name).read_bytes())
            self.assertEqual((evidence / 'project/Localizable.xcstrings').read_bytes(), (live / renderer.localization.LOCALIZATION_CATALOG).read_bytes())
            project = (evidence / 'project/project.pbxproj').read_text()
            refs = dict(re.findall(r'^([0-9A-F]{24}) = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ([^;]+);', project, re.M))
            build_refs = dict(re.findall(r'^([0-9A-F]{24}) = \{isa = PBXBuildFile; fileRef = ([0-9A-F]{24});', project, re.M))
            phase = re.search(r'^00000000000000000000008F = .*?files = \(([^)]*)\)', project, re.M).group(1)
            compiled = {refs[build_refs[identifier]] for identifier in phase.split(',') if identifier}
            actual_sources = {path.name for path in (evidence / 'sources').glob('*.swift')} - {'p0_signin_ui_tests.swift'}
            self.assertEqual(compiled, actual_sources)
            for path in (evidence / 'project').rglob('*'):
                if path.is_file(): self.assertEqual(identity['sourceSHA256']['project/' + str(path.relative_to(evidence / 'project'))], renderer.digest(path))
            self.assertTrue(identity['localization']['compiledResourcesVerified'])
            self.assertFalse(identity['localization']['localeSwitchingVerified'])
            self.assertEqual(identity['localization']['requiredLabels'], list(renderer.SIGNIN_LOCALIZATION_KEYS))
            self.assertEqual(len([row for row in calls if row[0] == 'plutil']), 4)
            self.assertFalse(any('simctl' in row for row in calls))

    def test_missing_or_forged_resources_stop_preparation_before_ui(self):
        if self.source() is None: return
        for mutation in ('missing', 'label', 'catalog-input', 'swift-input'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary, self.assertRaises(RuntimeError):
                self.prepare_fixture(Path(temporary), mutation)

    def test_standalone_extraction_cannot_borrow_localization_from_other_source(self):
        live = self.source()
        if live is None: return
        # Localized declarations outside their maintained source path must reject.
        shell = (live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'shell.swift'; source.write_text(shell)
            with self.assertRaisesRegex(ValueError, 'actual SideStore.swift support source'):
                renderer.extract_sources(source, ROOT / 'scripts/templates')


if __name__ == '__main__': unittest.main()
