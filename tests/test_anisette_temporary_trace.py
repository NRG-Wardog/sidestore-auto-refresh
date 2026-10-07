"""TEMPORARY DEBUG trace: execute actual wire/render/privacy/ownership contracts."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_v3_account_diagnostics import declaration, diagnostic_sources

ROOT = Path(__file__).resolve().parents[1]


class TemporaryAnisetteTraceTests(unittest.TestCase):
    def test_release_switch_and_value_owned_finite_contract(self):
        source = (ROOT / 'scripts/templates/combined_failure.swift').read_text()
        trace = declaration(source, 'public struct V3TemporaryAnisetteTrace:')
        self.assertEqual(source.count('    public static let temporaryAnisetteTraceEnabled = true'), 1)
        self.assertNotIn('#if DEBUG', trace)
        for forbidden in ('@TaskLocal', 'static var', 'UserDefaults', 'FileManager', 'print(', 'Logger', 'NSLog'):
            self.assertNotIn(forbidden, trace)
        self.assertIn('public static let maximumEvents = 64', trace)
        self.assertIn('public static let maximumBytes = 2048', trace)
        self.assertIn('tokens.count <= 32', trace)
        self.assertIn('body.utf8.count <= 1024', trace)
        self.assertIn('var trace: V3TemporaryAnisetteTrace? = nil', source)
        native = declaration(trace, '    private enum NativeEvent:')
        values = re.findall(r'case \w+ = "([^"]+)"', native)
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(len(values), 57)
        self.assertTrue(all(re.fullmatch(r'[a-z_.]+', value) for value in values))
        self.assertIn('native.output.not_checked', values)
        self.assertIn('trace.truncated', values)
        renderer = (ROOT / 'scripts/templates/v3_behavioral_primitives.swift').read_text()
        display = declaration(renderer, '    static func display(_ message:')
        self.assertIn('CombinedFailure.validatedSigningContext', display)
        self.assertIn('DEBUG TEMPORARY failed step:', display)
        self.assertIn('V3TemporaryAnisetteTrace.init(encoded:)', display)

    def execute(self, enabled):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; production trace harness runs in macOS CI')
        source = diagnostic_sources()
        if not enabled:
            source = source.replace('public static let temporaryAnisetteTraceEnabled = true',
                                    'public static let temporaryAnisetteTraceEnabled = false')
        source += (ROOT / 'tests/fixtures/v3_temporary_anisette_trace_harness.swift').read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'
            binary = Path(directory) / 'trace'
            path.write_text(source)
            result = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('TEMPORARY_ANISETTE_TRACE_' + ('PASS' if enabled else 'DISABLED_PASS'), result.stdout)

    def test_actual_enabled_capture_wire_render_and_ownership(self):
        self.execute(True)

    def test_single_disabled_flag_removes_collection_wire_and_render(self):
        self.execute(False)
