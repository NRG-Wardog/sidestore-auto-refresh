"""Rendering subprocess deadlines must include discovery and child processes."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rendering_bounds', ROOT / 'scripts/run_issue25_rendering.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderingCommandBoundsTests(unittest.TestCase):
    def tearDown(self):
        renderer.COMMAND_LOG = None

    def test_capture_preserves_exact_source_bytes(self):
        self.assertEqual(renderer.capture(sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'hello\\n\\n')"), b'hello\n\n')

    def test_stderr_is_visible_but_cannot_corrupt_returned_stdout(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            result = renderer.capture(sys.executable, '-c',
                "import sys;print('{\"devices\": {}}');print('nonfatal diagnostic', file=sys.stderr)")
        self.assertEqual(json.loads(result), {"devices": {}})
        self.assertIn('nonfatal diagnostic', printed.getvalue())

    def test_nonzero_exit_remains_failure(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            renderer.command(sys.executable, '-c', 'raise SystemExit(7)')
        self.assertEqual(caught.exception.returncode, 7)

    def test_deadline_kills_descendants_and_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            renderer.COMMAND_LOG = root / 'commands.jsonl'
            marker = root / 'unexpected-child-survival'
            child = f'import time,pathlib;time.sleep(0.7);pathlib.Path({str(marker)!r}).touch()'
            parent = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{child!r}]);print("started",flush=True);time.sleep(20)'
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'exceeded its 0.2-second bound'):
                renderer.command(sys.executable, '-c', parent, timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)
            time.sleep(0.8)
            self.assertFalse(marker.exists())
            events = [json.loads(line) for line in renderer.COMMAND_LOG.read_text().splitlines()]
            self.assertEqual([item['event'] for item in events], ['start', 'timeout'])
            self.assertEqual(events[0]['timeoutSeconds'], 0.2)
            self.assertLess(events[1]['returncode'], 0)

    def test_discovery_uses_bounded_command(self):
        runtime = 'com.apple.CoreSimulator.SimRuntime.iOS-26-4'
        devices = {'devices': {runtime: [
            {'name': 'iPhone 17', 'udid': 'phone', 'isAvailable': True},
            {'name': 'iPad Pro 13-inch', 'udid': 'tablet', 'isAvailable': True}]}}
        runtimes = {'runtimes': [{'identifier': runtime, 'isAvailable': True, 'version': '26.4'}]}
        with patch.object(renderer, 'command', side_effect=[json.dumps(devices), json.dumps(runtimes)]) as run:
            self.assertEqual(renderer.available_devices(), [('phone', 'phone', '26.4'), ('tablet', 'tablet', '26.4')])
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('subprocess.check_output', (ROOT / 'scripts/run_issue25_rendering.py').read_text())

    def test_only_simulator_boot_gets_the_migration_budget(self):
        with patch.object(renderer, 'command') as run:
            renderer.wait_for_simulator_boot('fixture-device')
        run.assert_called_once_with('xcrun', 'simctl', 'bootstatus', 'fixture-device', '-b', timeout=600)
        self.assertEqual(renderer.command.__kwdefaults__['timeout'], 300)
        self.assertEqual(renderer.capture.__kwdefaults__['timeout'], 300)
        source = (ROOT / 'scripts/run_issue25_rendering.py').read_text()
        self.assertEqual(source.count('wait_for_simulator_boot(device)'), 2)

    def test_workflow_keeps_acceptance_and_saves_timeout_diagnostics(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        self.assertIn('timeout-minutes: 40', workflow)
        self.assertIn('timeout-minutes: 60', workflow)
        self.assertIn('artifacts/layout-evidence/**/*.jsonl', workflow)
        self.assertIn('artifacts/logs/layout-rendering.log', workflow)
        self.assertIn('--v3-source work/LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift', workflow)
        self.assertNotIn('--skip-v3-native', workflow)
