"""Merge-specific orchestration contracts; synthetic inputs are not native UI proof."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_p0_signin_rendering as renderer


class MergedRenderingContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / 'host'
        self.source = self.live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
        self.source.parent.mkdir(parents=True)
        declarations = [signature + ' {\n}\n' for signature in renderer.DECLARATIONS]
        declarations.append('struct V3SignInView {\n' + ''.join(
            '    ' + signature + ' { return 0 }\n' for signature in renderer.VIEW_MEMBERS) + '}\n')
        declarations.append('final class V3AuthStore {\n    static func failureMessage() {}\n    static func failureDetails() {}\n}\n')
        self.source.write_text('\n'.join(declarations))
        support = self.live / 'SideStoreSupport/SideStore.swift'
        support.parent.mkdir()
        support.write_text('''enum V3WireContract {
    static func strictBool() {}
    static func strictInt() {}
}
public final class V3ServiceBridge {
    public static func strictBool() {}
    public static func strictInt() {}
}
public struct CombinedRefreshTargetPlan {
}
public struct V3TemporaryAnisetteTrace {
}
public enum V3DiagnosticPresentation {
}
''')
        for relative in renderer.localization.LOCALIZATION_SOURCES:
            path = self.live / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// synthetic ' + path.name + '\n')
        self.catalog = self.live / renderer.localization.LOCALIZATION_CATALOG
        self.catalog.parent.mkdir()
        self.catalog.write_text('{"strings": {}}\n')
        self.shared = self.live / 'LiveContainerSwiftUI/Utilities/Shared.swift'
        self.shared.write_text('''extension String {
    var loc: String { return self }
    func localizeWithFormat(_ arguments: CVarArg...) -> String {
        return String(format: self, arguments: arguments)
    }
}
''')
        self.project = self.root / 'P0SignIn.xcodeproj'
        shutil.copytree(ROOT / 'tests/fixtures/p0_signin_project', self.project)

    def test_extraction_keeps_both_source_identities_and_exact_formatting(self):
        extracted, hashes = renderer.extract_sources(self.source, self.root / 'unused')
        for relative in (*renderer.localization.LOCALIZATION_SOURCES,
                         renderer.localization.LOCALIZATION_CATALOG,
                         'LiveContainerSwiftUI/Utilities/Shared.swift'):
            path = self.live / relative
            self.assertEqual(hashes['maintained-source/' + relative], renderer.digest(path))
            self.assertEqual(hashes['maintained-localization/' + path.name], renderer.digest(path))
        for signature in ('var loc: String', 'func localizeWithFormat'):
            self.assertIn(renderer.member(self.shared.read_text(), signature), extracted['LocalizedStringFormatting.swift'])
        self.assertEqual(extracted['Localizable.xcstrings'], self.catalog.read_text())

    def test_missing_and_linked_inputs_are_fail_closed(self):
        for relative in (*renderer.localization.LOCALIZATION_SOURCES,
                         renderer.localization.LOCALIZATION_CATALOG,
                         'LiveContainerSwiftUI/Utilities/Shared.swift'):
            path = self.live / relative
            saved = path.read_bytes()
            for linked in (False, True):
                with self.subTest(relative=relative, linked=linked):
                    path.unlink()
                    if linked:
                        target = self.root / 'linked-input'
                        target.write_bytes(saved)
                        path.symlink_to(target)
                    with self.assertRaisesRegex(renderer.LocalizationDependencyError, 'dependency missing or linked'):
                        renderer.extract_sources(self.source, self.root / 'unused')
                    if linked:
                        path.unlink()
                    path.write_bytes(saved)

    def test_combined_project_has_one_resource_edge_and_all_sources(self):
        renderer.configure_localized_project(self.project, self.live, p2=True)
        text = (self.project / 'project.pbxproj').read_text()
        ids = re.findall(r'^([0-9A-F]{24}) =', text, re.M)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(text.count('isa = PBXResourcesBuildPhase;'), 1)
        self.assertEqual(text.count('P2_LOCALIZATION'), 6)
        for filename in ('V3Localization.swift', 'V3LanguageSettings.swift', 'LocalizedStringFormatting.swift'):
            self.assertEqual(text.count('path = ' + filename + ';'), 1)
        self.assertIn('path = P0SignIn.xcodeproj/Localizable.xcstrings; sourceTree = SOURCE_ROOT;', text)
        self.assertEqual((self.project / 'Localizable.xcstrings').read_bytes(), self.catalog.read_bytes())
        with self.assertRaisesRegex(ValueError, 'already present'):
            renderer.enable_localization_project(self.project / 'project.pbxproj')

    def test_p2_project_rejects_drift_without_partial_write(self):
        path = self.project / 'project.pbxproj'
        original = path.read_text()
        for anchor in ('objectVersion = 56; objects = {', 'knownRegions = (en,Base);',
                       'buildPhases = (00000000000000000000008F,);', 'SWIFT_VERSION = 5.0;'):
            with self.subTest(anchor=anchor):
                changed = original.replace(anchor, 'DRIFT', 1)
                path.write_text(changed)
                with self.assertRaisesRegex(ValueError, 'anchor drift'):
                    renderer.enable_localization_project(path)
                self.assertEqual(path.read_text(), changed)

    def prepare(self, mutation=None):
        output = self.root / 'output'
        output.mkdir()
        resource = b'compiled mock resource'
        resource_hash = hashlib.sha256(resource).hexdigest()
        def command(*args, **kwargs):
            if args[0] == 'git':
                return 'a' * 40
            self.assertEqual(args[:2], ('xcodebuild', 'build-for-testing'))
            products = output / 'p0-build/DerivedData/Build/Products'
            products.mkdir(parents=True)
            (products / 'fixture.xctestrun').write_text('mock only')
            app = products / 'Debug-iphonesimulator/P0SignIn.app'
            (app / 'en.lproj').mkdir(parents=True)
            (app / 'en.lproj/Localizable.strings').write_bytes(resource)
            if mutation:
                (output / 'p0-build' / mutation).write_text('changed')
            return ''
        with (patch('p2_localization_contract.verify_app', return_value={'status': 'passed'}) as p2,
             patch.object(renderer.localization, 'verify_localization', return_value={
                 'compiled-localization/en.lproj/Localizable.strings': resource_hash}) as p0):
            prepared = renderer.prepare(output, self.source, command)
        self.assertEqual(p2.call_count, 1)
        self.assertEqual(p0.call_count, 1)
        return output, prepared

    def test_preparation_preserves_combined_evidence_and_copy_feedback(self):
        self.source.write_text(self.source.read_text() + '// copiedDiagnostic\n')
        output, prepared = self.prepare()
        evidence = output / 'p0-signin'
        identity = json.loads((evidence / 'source-identity.json').read_text())
        self.assertEqual(identity['sourceSHA256'], prepared['sourceSHA256'])
        self.assertTrue(identity['localization']['compiledResourcesVerified'])
        self.assertFalse(identity['localization']['localeSwitchingVerified'])
        self.assertEqual((evidence / 'project/Localizable.xcstrings').read_bytes(), self.catalog.read_bytes())
        self.assertEqual((evidence / 'project/project.pbxproj').read_text().count('P2_LOCALIZATION P1_COPY_FEEDBACK'), 6)
        for path in (evidence / 'project').rglob('*'):
            if path.is_file():
                self.assertEqual(prepared['sourceSHA256']['project/' + str(path.relative_to(evidence / 'project'))], renderer.digest(path))

    def test_formatting_input_mutation_stops_preparation(self):
        with self.assertRaisesRegex(RuntimeError, 'source changed during build'):
            self.prepare('Sources/LocalizedStringFormatting.swift')

    def test_ui_keeps_conditional_locale_and_persistence_assertions(self):
        text = (ROOT / 'tests/fixtures/p0_signin_ui_tests.swift').read_text()
        start = text.index('        language = "en"', text.index('private func credentials('))
        block = text[start:text.index('// XCTest invokes teardown', start)]
        p2, p0 = block.split('#else')
        self.assertIn('app.launchArguments += ["--language", language]', p2)
        self.assertNotIn('AppleLanguages', p2)
        self.assertIn('"-AppleLanguages", "(en)"', p0)
        self.assertIn('P0FixtureLanguageExpectation.error(language: language)', text)
        self.assertIn('terminatedAndRelaunched', text)
        self.assertIn('copy.label == self.localized("Copied")', text)
        app = (ROOT / 'tests/fixtures/p0_signin_app.swift').read_text()
        modifiers = app.split('.navigationViewStyle(.stack)', 1)[1].split('.environment(\\.sizeCategory', 1)[0]
        p2_modifiers, p0_modifiers = modifiers.split('#else')
        self.assertIn('Locale(identifier: language)', p2_modifiers)
        self.assertNotIn('Locale(identifier: "en")', p2_modifiers)
        self.assertIn('Locale(identifier: "en")', p0_modifiers)
        self.assertEqual(app.count('Locale(identifier: "en")'), 1)


if __name__ == '__main__':
    unittest.main()
