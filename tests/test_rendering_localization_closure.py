"""Source/resource assembly tests; mocked tools are not native rendering evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rendering_localization', ROOT / 'scripts/run_issue25_rendering.py')
renderer = importlib.util.module_from_spec(spec); spec.loader.exec_module(renderer)


class LocalizationClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.live = self.root / 'source'; self.live.mkdir()
        self.consumer = self.live / 'Consumer.swift'; self.consumer.write_text('V3AppLanguage.selected')
        for name in renderer.LOCALIZATION_SOURCES:
            path = self.live / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('// exact test source ' + name)
        self.catalog = self.live / renderer.LOCALIZATION_CATALOG; self.catalog.parent.mkdir()
        self.values = {key: {'localizations': {language: {'stringUnit': {'value': language + ':' + key}}
                      for language in renderer.LOCALIZATION_LANGUAGES}} for key in renderer.LOCALIZATION_KEYS}
        self.catalog.write_text(json.dumps({'strings': self.values}))
        self.bundle = self.root / 'Fixture.app'; self.bundle.mkdir()
        self.calls = []

    def tools(self, *args):
        self.calls.append(args)
        if args[:3] == ('xcrun', 'xcstringstool', 'compile'):
            self.assertEqual(args[3:], ('--output-directory', str(self.bundle), str(self.catalog)))
            for language in renderer.LOCALIZATION_LANGUAGES:
                folder = self.bundle / (language + '.lproj'); folder.mkdir(exist_ok=True)
                values = {key: row['localizations'][language]['stringUnit']['value'] for key, row in self.values.items()}
                (folder / 'Localizable.strings').write_text(json.dumps(values))
                (folder / 'Localizable.stringsdict').write_text('{"plural":"compiler fixture"}')
            return ''
        self.assertEqual(args[:5], ('plutil', '-convert', 'json', '-o', '-'))
        return Path(args[5]).read_text()

    def test_exact_real_source_paths_are_selected(self):
        self.assertEqual(renderer.localization_sources(self.live, [self.consumer]),
                         [self.live / name for name in renderer.LOCALIZATION_SOURCES])

    def test_each_missing_or_linked_production_input_rejected(self):
        for name in (*renderer.LOCALIZATION_SOURCES, renderer.LOCALIZATION_CATALOG):
            path = self.live / name; raw = path.read_bytes(); path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'dependency missing'):
                renderer.localization_sources(self.live, [self.consumer])
            path.symlink_to(self.consumer)
            with self.subTest(link=name), self.assertRaisesRegex(RuntimeError, 'dependency missing'):
                renderer.localization_sources(self.live, [self.consumer])
            path.unlink(); path.write_bytes(raw)

    def test_no_localization_source_cannot_hide_current_consumers(self):
        for name in renderer.LOCALIZATION_SOURCES: (self.live / name).unlink()
        with self.assertRaisesRegex(RuntimeError, 'dependency missing'):
            renderer.localization_sources(self.live, [self.consumer])
        self.consumer.write_text('struct HistoricalRenderer {}')
        self.assertEqual(renderer.localization_sources(self.live, [self.consumer]), [])

    def test_native_catalog_compilation_and_all_resource_hashes(self):
        with patch.object(renderer, 'command', side_effect=self.tools):
            hashes = renderer.compile_localization(self.bundle, self.live)
        self.assertEqual(len(hashes), 9)
        self.assertEqual(hashes[renderer.LOCALIZATION_CATALOG], hashlib.sha256(self.catalog.read_bytes()).hexdigest())
        for path in self.bundle.rglob('Localizable.*'):
            self.assertEqual(hashes['compiled-localization/' + str(path.relative_to(self.bundle))], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(self.calls), 5)

    def test_missing_empty_and_linked_compiled_resources_rejected(self):
        for kind in ('missing', 'empty', 'linked'):
            def tools(*args):
                result = self.tools(*args)
                if args[0] == 'xcrun':
                    path = self.bundle / 'he.lproj/Localizable.stringsdict'
                    path.unlink()
                    if kind == 'empty': path.touch()
                    if kind == 'linked': path.symlink_to(self.catalog)
                return result
            with self.subTest(kind=kind), patch.object(renderer, 'command', side_effect=tools), self.assertRaisesRegex(RuntimeError, 'resource missing'):
                renderer.compile_localization(self.bundle, self.live)
            path = self.bundle / 'he.lproj/Localizable.stringsdict'
            if path.is_symlink(): path.unlink()

    def test_missing_and_changed_real_labels_rejected(self):
        for replacement in (None, 'Some details are unavailable in the selected language.'):
            def tools(*args):
                result = self.tools(*args)
                if args[0] == 'plutil':
                    data = json.loads(result)
                    if replacement is None: data.pop('Reload Status')
                    else: data['Reload Status'] = replacement
                    return json.dumps(data)
                return result
            with self.subTest(value=replacement), patch.object(renderer, 'command', side_effect=tools), self.assertRaisesRegex(RuntimeError, 'label differs'):
                renderer.compile_localization(self.bundle, self.live)

    def test_compiler_failure_propagates_without_fallback(self):
        with patch.object(renderer, 'command', side_effect=RuntimeError('compiler failed')), self.assertRaisesRegex(RuntimeError, 'compiler failed'):
            renderer.compile_localization(self.bundle, self.live)

    def test_fixture_uses_production_accessor_only_for_localized_source(self):
        text = (ROOT / 'tests/fixtures/issue25_rendering_dependencies.swift').read_text()
        self.assertIn('#if PRODUCTION_LOCALIZATION\n', text)
        active = text.split('#if PRODUCTION_LOCALIZATION\n', 1)[1].split('#else', 1)[0]
        self.assertIn('var loc: String { V3Localization.string(self) }', active)
        self.assertNotIn('var loc: String { self }', active)

    def test_all_four_build_commands_include_exact_production_sources_when_available(self):
        configured = os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE')
        if not configured: return
        live = Path(configured)
        if not (live / renderer.LOCALIZATION_SOURCES[0]).exists(): return
        output = self.root / 'builds'; calls = []
        def command(*args):
            calls.append(args)
            return '/sdk' if args[-1] == '--show-sdk-path' else ''
        resource_hash = hashlib.sha256((live / renderer.LOCALIZATION_CATALOG).read_bytes()).hexdigest()
        with patch.object(renderer, 'command', side_effect=command), patch.object(renderer, 'compile_localization', return_value={renderer.LOCALIZATION_CATALOG: resource_hash}) as resources, patch.object(renderer, 'capture', return_value=b'// synthetic historical grid for assembly only\n') as baseline:
            results = [renderer.build_app(output, live, True), renderer.build_app(output, live, False),
                       renderer.build_app(output, live, False, fallback=True),
                       renderer.build_v3_app(output, live, live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift')]
        baseline.assert_called_once_with('git', '-C', str(ROOT), 'show', renderer.BASELINE + ':' + renderer.GRID)
        compilers = [row for row in calls if 'swiftc' in row]
        self.assertEqual(len(compilers), 4); self.assertEqual(resources.call_count, 4)
        for index, (argv, (_, _, hashes)) in enumerate(zip(compilers, results)):
            for relative in renderer.LOCALIZATION_SOURCES:
                source = live / relative
                self.assertIn(str(source), argv)
                self.assertEqual(hashes[source.name], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(hashes[renderer.LOCALIZATION_CATALOG], resource_hash)
            if index < 3: self.assertIn('PRODUCTION_LOCALIZATION', argv)
        self.assertNotIn('CORRECTED_GRID', compilers[0])
        self.assertIn('CORRECTED_GRID', compilers[1]); self.assertIn('CORRECTED_GRID', compilers[2])

    def test_actual_maintained_source_closure_when_available(self):
        configured = os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE')
        if not configured: return
        live = Path(configured)
        banner = live / 'LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerView.swift'
        shell = live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
        sources = renderer.localization_sources(live, [banner, shell])
        if not sources: return  # Historical source predates the localization feature.
        self.assertEqual(sources, [live / name for name in renderer.LOCALIZATION_SOURCES])
        shared = (live / 'LiveContainerSwiftUI/Utilities/Shared.swift').read_text()
        self.assertIn('var loc: String { V3Localization.string(self) }', shared)
        catalog = json.loads((live / renderer.LOCALIZATION_CATALOG).read_text())['strings']
        for key in renderer.LOCALIZATION_KEYS:
            for language in renderer.LOCALIZATION_LANGUAGES:
                self.assertTrue(catalog[key]['localizations'][language]['stringUnit']['value'])


if __name__ == '__main__': unittest.main()
