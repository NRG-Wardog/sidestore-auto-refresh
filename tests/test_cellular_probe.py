"""Standalone diagnostic safety contracts and portable path/result policy tests."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'experiments/cellular/app'
SOURCE = (APP / 'main.m').read_text()
spec = importlib.util.spec_from_file_location('probe_build', APP.parent / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class StandaloneProbeTests(unittest.TestCase):
    def test_only_standalone_app_built(self):
        workflow = (ROOT / '.github/workflows/cellular-probe.yml').read_text()
        for forbidden in ('work/LiveContainer', 'work/EmbeddedSideStore', '-scheme SideStore', 'patch.py'):
            self.assertNotIn(forbidden, workflow)
        self.assertIn('fsyntax-only', workflow)
        self.assertLess(workflow.index('fsyntax-only'), workflow.index('cargo rustc'))
        self.assertEqual(workflow.count('python3 builder/scripts/patch_jktcp_reliability.py jktcp'), 1)
        self.assertEqual(workflow.count('python3 builder/scripts/patch_coredevice_idevice.py idevice jktcp'), 1)
        self.assertNotIn('cellular_diagnostic', (ROOT / '.github/workflows/livecontainer-build.yml').read_text())

    def test_no_signing_install_or_usb_path(self):
        for forbidden in ('installation_proxy_install(', 'afc_file_write(', 'usbmuxd_provider_new(',
                          'rppairing', 'AppManager', 'BGTask', 'NSTimer', 'openURL:', 'idevice_init_logger('):
            self.assertNotIn(forbidden, SOURCE)
        self.assertIn('idevice_tcp_provider_new(', SOURCE)
        self.assertIn('installation_proxy_get_apps(', SOURCE)

    def test_ownership_and_fresh_serial_run(self):
        self.assertIn('if (self.running ||', SOURCE)
        self.assertIn('DISPATCH_QUEUE_SERIAL', SOURCE)
        for cleanup in ('idevice_plist_array_free', 'installation_proxy_client_free', 'rsd_handshake_free',
                        'adapter_free', 'tunnel_heartbeat_stop', 'idevice_provider_free', 'idevice_pairing_file_free'):
            self.assertIn(cleanup + '(', SOURCE)
        start = SOURCE.index('idevice_tcp_provider_new(')
        self.assertLess(SOURCE.index('pf = NULL;', start), SOURCE.index('tunnel_create_usb(', start))

    def test_privacy_and_bounded_log(self):
        self.assertNotIn('error->message', SOURCE)
        self.assertNotIn('record:message', SOURCE)
        self.assertIn('self.lines.count > 200', SOURCE)
        self.assertIn('NSFileProtectionComplete', SOURCE)
        self.assertIn('NSDataWritingFileProtectionComplete', SOURCE)
        self.assertIn('NSURLIsExcludedFromBackupKey', SOURCE)
        self.assertIn('NSApplicationSupportDirectory', SOURCE)
        self.assertIn('1024 * 1024', SOURCE)

    def test_no_false_cellular_or_refresh_claim(self):
        self.assertIn('PATH_BEFORE', SOURCE)
        self.assertIn('PATH_AFTER', SOURCE)
        self.assertIn('if (timedOut) result = -1', SOURCE)
        self.assertIn('self.interrupted = YES', SOURCE)
        self.assertIn('snapshots_only_not_continuous_route_proof', SOURCE)
        self.assertIn('refresh_performed=false', SOURCE)

    def test_automatic_flow_and_resume_are_event_driven(self):
        self.assertNotIn('selectedSegmentIndex', SOURCE)
        self.assertNotIn('probe.peer', SOURCE)
        self.assertIn('probe_detect_mode(wifi, mobile)', SOURCE)
        self.assertIn('if (!pairing) { [self importPairing]; return; }', SOURCE)
        self.assertIn('completion:^{ [self run]; }', SOURCE)
        self.assertIn('UIApplicationDidBecomeActiveNotification', SOURCE)
        self.assertIn('!self.automaticRunPending || self.running', SOURCE)
        self.assertIn('self.automaticRunPending = NO;', SOURCE)
        self.assertIn('self.awaitingCellular = valid && !cellular', SOURCE)
        self.assertIn('WAITING_FOR_CELLULAR no_coredevice=true', SOURCE)
        self.assertIn('reachable.count == 1 && !self.interrupted', SOURCE)

    def test_discovery_is_bounded_and_never_guesses_a_peer(self):
        discovery = (APP / 'PeerDiscovery.m').read_text()
        self.assertIn('"utun", 4', discovery)
        self.assertIn('[locals containsObject:candidate]', discovery)
        self.assertIn('result.count > 8', discovery)
        self.assertIn('.tv_sec = 2', discovery)
        self.assertIn('SO_ERROR', discovery)
        self.assertIn('close(fd)', discovery)
        self.assertIn('probe_route_peers', discovery)
        for address in ('10.0.0.241', '192.168.50.241'):
            self.assertNotIn(address, SOURCE + discovery)

    def test_package_contract_rejects_combined_and_invalid_binary(self):
        info = plistlib.loads((APP / 'Info.plist').read_bytes())
        info['ProbeBuilderCommit'] = 'a' * 40
        code = struct.pack('<IIII', 0xfeedfacf, 0x100000c, 0, 2)
        code += b' PROBE_BEGIN COREDEVICE_RSD_BEGIN BROWSE_BEGIN RESOURCES_RELEASED PATH_BEFORE PATH_AFTER '
        code += b' PEER_DISCOVERY MODE_DETECTED WAITING_FOR_CELLULAR '
        with tempfile.TemporaryDirectory() as folder:
            ipa = Path(folder) / 'probe.ipa'
            def package(extra=False, binary=code):
                with zipfile.ZipFile(ipa, 'w') as archive:
                    archive.writestr('Payload/CellularProbe.app/Info.plist', plistlib.dumps(info))
                    archive.writestr('Payload/CellularProbe.app/CellularProbe', binary)
                    if extra:
                        archive.writestr('Payload/CellularProbe.app/PlugIns/Widget.appex/Info.plist', plistlib.dumps(info))
            package()
            self.assertEqual(build.verify(ipa)['app_bundles'], 1)
            package(extra=True)
            with self.assertRaises(AssertionError): build.verify(ipa)
            package(binary=b'not a mach-o' + code)
            with self.assertRaises(AssertionError): build.verify(ipa)

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_executable_path_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'policy'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', '-I', str(APP),
                            str(APP.parent / 'policy_test.c'), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
