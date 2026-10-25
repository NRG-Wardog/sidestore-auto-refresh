"""Exercise the actual acquired host's sign-in diagnostic dependency closure."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_p0_signin_rendering as renderer


class MaintainedFixtureClosureTests(unittest.TestCase):
    def setUp(self):
        configured = os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE')
        self.host = Path(configured) if configured else ROOT / 'work/LiveContainer'
        self.shell = self.host / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
        self.support = self.host / 'SideStoreSupport/SideStore.swift'
        if not self.shell.is_file() or not self.support.is_file():
            if configured:
                self.fail('Explicit maintained LiveContainer source is missing')
            self.skipTest('Acquired diagnostic host is exercised in required maintained CI')
        self.sources, self.hashes = renderer.extract_sources(self.shell, ROOT / 'scripts/templates')

    def test_required_native_gate_uses_explicit_maintained_source_before_layout(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        step = workflow.split('- name: Run repository checks and historical patch regressions', 1)[1].split('- name:', 1)[0]
        self.assertIn('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE: ${{ github.workspace }}/work/LiveContainer', step)
        self.assertIn('--start-directory builder/tests', step)
        self.assertLess(workflow.index('- name: Run repository checks and historical patch regressions'),
                        workflow.index('--v3-source work/LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift'))

    def test_actual_support_and_namespace_members_are_retained_exactly(self):
        actual = self.support.read_text()
        first = renderer.declaration(actual, 'public struct CombinedRefreshTargetPlan')
        last = renderer.declaration(actual, 'public enum V3DiagnosticPresentation')
        region = actual[actual.index(first):actual.index(last) + len(last)]
        self.assertEqual(self.sources['CombinedFailure.swift'], 'import Foundation\nimport CoreFoundation\n\n' + region)
        self.assertEqual(self.hashes['maintained-combined-failure'], hashlib.sha256(region.encode()).hexdigest())
        self.assertEqual(self.hashes['maintained-source/SideStoreSupport/SideStore.swift'], renderer.digest(self.support))
        self.assertFalse(any(key.startswith('production-template/') for key in self.hashes))
        for name, signature, prefix in [('V3WireContract', 'enum V3WireContract', ''),
                                        ('V3ServiceBridge', 'public final class V3ServiceBridge', 'public ')]:
            namespace = renderer.declaration(actual, signature)
            for method in ('strictBool', 'strictInt'):
                retained = renderer.member(namespace, prefix + 'static func ' + method)
                self.assertIn(retained, self.sources[name + '.swift'])
        self.assertIn(renderer.declaration(actual, 'public struct V3TemporaryADIConsumption'), region)

    def test_missing_ambiguous_or_drifted_support_fails_without_template_fallback(self):
        original = self.support.read_text()
        declaration = renderer.declaration(original, 'public struct V3TemporaryADIConsumption')
        mutations = [None, original.replace(declaration, ''), original + '\n' + declaration,
                     original.replace('public enum V3DiagnosticPresentation', 'public enum MissingPresentation'),
                     original.replace('enum V3WireContract', 'enum MissingWireContract'),
                     original.replace('public final class V3ServiceBridge', 'public final class MissingBridge')]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shell = root / 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
                support = root / 'SideStoreSupport/SideStore.swift'
                shell.parent.mkdir(parents=True); support.parent.mkdir()
                shell.write_bytes(self.shell.read_bytes())
                if mutation is not None:
                    support.write_text(mutation)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    renderer.extract_sources(shell, ROOT / 'scripts/templates')
        with tempfile.TemporaryDirectory() as temporary:
            shell = Path(temporary) / 'shell.swift'; shell.write_bytes(self.shell.read_bytes())
            with self.assertRaisesRegex(ValueError, 'actual SideStore.swift'):
                renderer.extract_sources(shell, ROOT / 'scripts/templates')

    def test_actual_diagnostic_closure_compiles_and_renders_consumption(self):
        compiler = shutil.which('swiftc')
        if compiler is None:
            self.skipTest('Swift compiler unavailable; required macOS maintained gate executes this closure')
        names = ('CombinedFailure.swift', 'V3WireContract.swift', 'V3ServiceBridge.swift',
                 'V3AuthFailureDiagnosticsPolicy.swift')
        text = '\n'.join('\n'.join(line for line in self.sources[name].splitlines()
                                   if line not in ('import SwiftUI', 'import UIKit')) for name in names)
        text += '''
let encoded = "v1|0|0,0,0,-1,0,0,0,-1"
precondition(V3TemporaryADIConsumption(encoded: encoded)?.encoded == encoded)
let result = V3AuthFailureDiagnosticsPolicy.render([
    "kind": "anisetteFailure", "signingContext": [V3TemporaryADIConsumption.contextKey: encoded]
], underlyingCode: nil, retryableValue: false)
precondition(result.contains("DEBUG TEMPORARY adi_consumption=" + encoded))
precondition(V3WireContract.strictBool(true) == true)
precondition(V3ServiceBridge.strictInt(17) == 17)
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'main.swift'; binary = root / 'closure'
            source.write_text(text)
            built = subprocess.run([compiler, str(source), '-o', str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
