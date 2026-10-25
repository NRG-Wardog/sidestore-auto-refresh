"""Real login-diagnostic source closure; mocked build assembly is not UI proof."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_issue25_rendering as legacy
import run_p0_signin_rendering as renderer


class LoginTraceClosureTests(unittest.TestCase):
    def setUp(self):
        configured = os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE')
        self.live = Path(configured) if configured else ROOT / 'work/LiveContainer'
        self.shell = self.live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
        self.support = self.live / 'SideStoreSupport/SideStore.swift'
        if not self.shell.is_file() or not self.support.is_file():
            self.fail('Required acquired maintained LiveContainer source is missing')
        self.support_text = self.support.read_text()
        self.shell_text = self.shell.read_text()
        if 'V3LoginDiagnosticSnapshot' not in self.shell_text + self.support_text:
            self.fail('Required current host login-stage diagnostics are missing')
        self.snapshot = renderer.declaration(self.support_text, 'public struct V3LoginDiagnosticSnapshot')
        self.sources, self.hashes = renderer.extract_sources(self.shell, ROOT / 'scripts/templates')

    def test_exact_snapshot_parser_wire_bounds_and_render_policy_are_compiled_together(self):
        combined = self.sources['CombinedFailure.swift']
        self.assertIn(self.snapshot, combined)
        self.assertEqual(self.hashes['V3LoginDiagnosticSnapshot'], hashlib.sha256(self.snapshot.encode()).hexdigest())
        self.assertEqual(self.hashes['maintained-source/SideStoreSupport/SideStore.swift'], renderer.digest(self.support))
        self.assertIn('V3LoginDiagnosticSnapshot.boundingWire(result)', combined)
        self.assertIn('V3LoginDiagnosticSnapshot(encoded:', combined)
        self.assertIn('maximumBytes = 2048', self.snapshot)
        self.assertIn('maximumWireBytes = 4096', self.snapshot)
        self.assertIn('maximumEvents = 128', self.snapshot)
        for name in ('V3TemporaryADIConsumption', 'V3TemporaryAnisetteTrace', 'LCAnisettePairError'):
            kind = 'enum' if name == 'LCAnisettePairError' else 'struct'
            self.assertIn(renderer.declaration(self.support_text, 'public ' + kind + ' ' + name), combined)
        policy = renderer.declaration(self.shell_text, 'enum V3AuthFailureDiagnosticsPolicy')
        self.assertIn(policy, self.sources['V3AuthFailureDiagnosticsPolicy.swift'])
        self.assertIn('V3LoginDiagnosticSnapshot.init(encoded:)', policy)
        compiled = '\n'.join(text for name, text in self.sources.items() if name.endswith('.swift'))
        self.assertEqual(compiled.count('public struct V3LoginDiagnosticSnapshot'), 1)
        self.assertFalse(any(key.startswith('production-template/') for key in self.hashes))
        fixture = (ROOT / 'tests/fixtures/p0_signin_app.swift').read_text()
        self.assertNotRegex(fixture, r'\b(?:struct|enum|class)\s+V3(?:LoginDiagnosticSnapshot|TemporaryADIConsumption|TemporaryAnisetteTrace)\b')

    def test_view_and_store_members_are_verbatim_with_complete_fixture_state(self):
        view = renderer.declaration(self.shell_text, 'struct V3SignInView')
        self.assertEqual(self.sources['V3SignInView.production.txt'], view)
        self.assertEqual(self.hashes['V3SignInView.full-production'], hashlib.sha256(view.encode()).hexdigest())
        for signature in renderer.VIEW_MEMBERS:
            actual = renderer.member(view, signature)
            self.assertIn(actual, self.sources['V3SignInView.swift'])
            self.assertEqual(self.hashes['V3SignInView.' + signature], hashlib.sha256(actual.encode()).hexdigest())
        store = renderer.declaration(self.shell_text, 'final class V3AuthStore')
        for signature in ('static func failureMessage', 'static func failureDetails'):
            self.assertIn(renderer.member(store, signature), self.sources['V3AuthStoreDiagnostics.swift'])
        # Detect state dependencies introduced into a retained production member.
        # Do not "fix" such drift by deleting the member or inventing diagnostics.
        required = set(re.findall(r'\bauth\.([A-Za-z_]\w*)', self.sources['V3SignInView.swift']))
        fixture = renderer.declaration((ROOT / 'tests/fixtures/p0_signin_app.swift').read_text(),
                                       '@MainActor final class V3AuthStore')
        supplied = set(re.findall(r'\b(?:var|let|func)\s+([A-Za-z_]\w*)', fixture))
        self.assertFalse(required - supplied, 'Unclosed production auth members: ' + str(sorted(required - supplied)))
        # The retained full-source record must include actual timeout state;
        # terminal/account layout remains outside this credentials fixture.
        self.assertIn('auth.terminalDiagnosticFailure', view)
        self.assertIn('V3AuthTimeoutDiagnosticRetentionPolicy.selecting(', store)

    def test_missing_duplicate_renamed_or_relocated_snapshot_fails_closed(self):
        without = self.support_text.replace(self.snapshot, '', 1)
        mutations = {
            'missing': without,
            'duplicate': self.support_text + '\n' + self.snapshot,
            'renamed': self.support_text.replace('public struct V3LoginDiagnosticSnapshot', 'public struct MissingSnapshot', 1),
            'outside-region': self.snapshot + '\n' + without,
        }
        for name, support_text in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                live = Path(temporary)
                shell = live / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
                support = live / 'SideStoreSupport/SideStore.swift'
                shell.parent.mkdir(parents=True); support.parent.mkdir()
                shell.write_text(self.shell_text); support.write_text(support_text)
                with self.assertRaisesRegex(ValueError, 'V3LoginDiagnosticSnapshot|login snapshot'):
                    renderer.extract_sources(shell, ROOT / 'scripts/templates')

    def test_login_dependency_alone_cannot_borrow_historical_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'shell.swift'
            source.write_text(self.shell_text.replace('V3TemporaryADIConsumption', 'RemovedADIReference'))
            with self.assertRaisesRegex(ValueError, 'actual SideStore.swift support source'):
                renderer.extract_sources(source, ROOT / 'scripts/templates')

    def test_all_five_release_build_source_sets_preserve_real_dependency_edges(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            def command(*args, **kwargs):
                calls.append(args)
                if args[0] == 'git': return '0' * 40
                if args[-1] == '--show-sdk-path': return '/mock-sdk-not-compiled'
                if args[:2] == ('xcodebuild', 'build-for-testing'):
                    products = output / 'p0-build/DerivedData/Build/Products'
                    products.mkdir(parents=True)
                    (products / 'fixture.xctestrun').write_text('assembly only')
                return ''
            catalog = self.live / renderer.localization.LOCALIZATION_CATALOG
            resources = {renderer.localization.LOCALIZATION_CATALOG: renderer.digest(catalog)}
            # Only native commands are mocked. The baseline acquisition command
            # is asserted separately; its stand-in bytes are the repository's
            # genuine grid source, never asserted to be the historical baseline.
            grid = (ROOT / legacy.GRID).read_bytes()
            with patch.object(legacy, 'command', side_effect=command), \
                    patch.object(legacy, 'capture', return_value=grid) as baseline, \
                    patch.object(legacy, 'compile_localization', return_value=resources) as compiled_resources, \
                    patch.object(renderer.localization, 'verify_localization', return_value=resources) as verified_resources:
                builds = [legacy.build_app(output, self.live, True),
                          legacy.build_app(output, self.live, False),
                          legacy.build_app(output, self.live, False, fallback=True),
                          legacy.build_v3_app(output, self.live, self.shell)]
                prepared = renderer.prepare(output, self.shell, command)
            baseline.assert_called_once_with('git', '-C', str(ROOT), 'show', legacy.BASELINE + ':' + legacy.GRID)
            self.assertEqual(compiled_resources.call_count, 4)
            verified_resources.assert_called_once()
            self.assertEqual(verified_resources.call_args.kwargs['keys'], renderer.SIGNIN_LOCALIZATION_KEYS)
            commands = [args for args in calls if 'swiftc' in args]
            self.assertEqual(len(commands), 4)
            self.assertEqual(len([args for args in calls if args[:2] == ('xcodebuild', 'build-for-testing')]), 1)
            self.assertFalse(any('simctl' in args for args in calls))
            for argv, (_, _, hashes) in zip(commands, builds):
                for relative in renderer.localization.LOCALIZATION_SOURCES:
                    path = self.live / relative
                    self.assertIn(str(path), argv)
                    self.assertEqual(hashes[path.name], renderer.digest(path))
                for path in (Path(arg) for arg in argv if arg.endswith('.swift')):
                    self.assertTrue(path.is_file())
                    # These four layouts do not consume login diagnostics; the
                    # shared localization dependency remains genuine production.
                    self.assertNotIn('V3LoginDiagnosticSnapshot', path.read_text())
            project = (output / 'p0-build/P0SignIn.xcodeproj/project.pbxproj').read_text()
            refs = dict(re.findall(r'^([0-9A-F]{24}) = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = ([^;]+);', project, re.M))
            edges = dict(re.findall(r'^([0-9A-F]{24}) = \{isa = PBXBuildFile; fileRef = ([0-9A-F]{24});', project, re.M))
            phase = re.search(r'^00000000000000000000008F = .*?files = \(([^)]*)\)', project, re.M).group(1)
            compiled = {refs[edges[identifier]] for identifier in phase.split(',') if identifier}
            sources = output / 'p0-build/Sources'
            self.assertEqual(compiled, {path.name for path in sources.glob('*.swift')} - {'p0_signin_ui_tests.swift'})
            self.assertIn('CombinedFailure.swift', compiled)
            self.assertIn(self.snapshot, (sources / 'CombinedFailure.swift').read_text())
            for name, exact in self.sources.items():
                self.assertEqual((sources / name).read_text(), exact)
                self.assertEqual(prepared['sourceSHA256'][name], renderer.digest(sources / name))
            identity = json.loads((output / 'p0-signin/source-identity.json').read_text())
            self.assertEqual(identity['sourceSHA256']['V3LoginDiagnosticSnapshot'], self.hashes['V3LoginDiagnosticSnapshot'])
            self.assertEqual(identity['localization']['languages'], ['en', 'he', 'zh-Hans', 'zh-Hant'])
            self.assertEqual((output / 'p0-build/P0SignIn.xcodeproj/Localizable.xcstrings').read_bytes(), catalog.read_bytes())

    def test_actual_host_diagnostic_closure_compiles_and_copies_timeout_trace(self):
        compiler = shutil.which('swiftc')
        if compiler is None:
            self.skipTest('Swift compiler unavailable; actual login diagnostic compilation/execution NOT RUN')
        names = ('CombinedFailure.swift', 'V3WireContract.swift', 'V3ServiceBridge.swift',
                 'V3AuthFailureDiagnosticsPolicy.swift')
        source = '\n'.join('\n'.join(line for line in self.sources[name].splitlines()
                                     if line not in ('import SwiftUI', 'import UIKit')) for name in names)
        # Exercise the genuine host's timeout retention policy alongside the
        # exact support/render closure emitted for the native rendering target.
        source += '\n' + renderer.declaration(self.shell_text, 'enum V3AuthTimeoutDiagnosticRetentionPolicy')
        source += '\n' + (self.live / 'tests/login_trace/timeout_host_harness.swift').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'main.swift'; binary = root / 'closure'
            path.write_text(source)
            built = subprocess.run([compiler, str(path), '-o', str(binary)], capture_output=True, text=True, timeout=180)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('LOGIN_TIMEOUT_HOST_CONTRACT_PASS', run.stdout)


if __name__ == '__main__': unittest.main()
