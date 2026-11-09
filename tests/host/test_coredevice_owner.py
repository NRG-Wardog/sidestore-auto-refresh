"""Guard current CoreDevice source, not a retired generator or device outcome.

The maintained native owner test lane remains responsible for Rust execution and
ABI qualification. These guards preserve the builder's transport privacy checks
and inspect selected source invariants against the exact production owner pin.
"""
import re
import unittest
from tests.owner_source import owner_source


def transport_log_literals(source):
    # Every call is either a plain literal or the one reviewed MTU format.
    calls = re.findall(r'\btransport_log\(\s*(.*?);', source, re.S)
    calls = [call for call in calls if not call.startswith('message: &str')]
    values = []
    for call in calls:
        literal = re.fullmatch(r'"([^"\n]*)",?\s*\)', call)
        formatted = re.fullmatch(r'&format!\(\s*"([^"\n]*)"\s*\)\)', call)
        if not literal and not formatted:
            raise ValueError('Transport log has an unreviewed dynamic argument')
        value = (literal or formatted).group(1)
        if formatted and value != ('[SIDESTORE_COREDEVICE] JKTCP_MSS_CONFIG '
                                   'negotiated_mtu={negotiated_mtu} effective_mss={effective_mss}'):
            raise ValueError('Transport log contains unreviewed dynamic data')
        if literal and ('{' in value or '}' in value):
            raise ValueError('Transport log contains interpolation markers')
        values.append(value)
    if not values:
        raise ValueError('No transport logs found')
    return values


class CoreDeviceOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {p: owner_source(p, owner='idevice') for p in (
            'ffi/src/tunnel_provider.rs', 'idevice/src/tunnel.rs',
            'ffi/src/core_device_proxy.rs', 'ffi/src/afc.rs', 'ffi/src/lib.rs')}

    def test_heartbeat_logs_exclude_private_dynamic_error_values(self):
        source = self.sources['ffi/src/tunnel_provider.rs']
        logs = transport_log_literals(source)
        for phase in ('CONNECT', 'POLO', 'MARCO'):
            message = f'[SIDESTORE_COREDEVICE] HEARTBEAT_{phase}_FAIL stage=heartbeat result=failed'
            self.assertIn(message, logs)
            for replacement in ('HEARTBEAT_CONNECT_FAIL error={error}', '{path}', '{endpoint}'):
                mutated = source.replace(message, replacement)
                with self.assertRaises(ValueError):
                    transport_log_literals(mutated)
        with self.assertRaises(ValueError):
            transport_log_literals(source + '\ntransport_log(&error.to_string());')
        # Preserve rich internal errors for classification, not user-copyable logs.
        self.assertIn('CoreDevice heartbeat connection failed: {error}', source)

    def test_contiguous_tunnel_packet_and_local_afc_runtime_remain(self):
        tunnel = self.sources['idevice/src/tunnel.rs']
        ordered = ('packet.extend_from_slice(CDTUNNEL_MAGIC);',
                   'packet.extend_from_slice(&(body.len() as u16).to_be_bytes());',
                   'packet.extend_from_slice(&body);', 'stream.write_all(&packet).await?;')
        positions = [tunnel.index(text) for text in ordered]
        self.assertEqual(positions, sorted(positions))
        afc = re.sub(r'\s+', ' ', self.sources['ffi/src/afc.rs'])
        for call in ('fd.close()', 'fd.write_entire(data_slice)'):
            self.assertIn(f'run_sync_local(async move {{ {call}.await }})', afc)
        self.assertIn('let data = unsafe { Box::from_raw(slice) };', self.sources['ffi/src/lib.rs'])

    def test_heartbeat_teardown_and_mss_are_owned_by_current_transport(self):
        source = self.sources['ffi/src/tunnel_provider.rs']
        for guard in ('handle.abort();', 'HEARTBEAT_IS_ACTIVE.store(false, Ordering::SeqCst);',
                      'HeartbeatClient::connect', 'negotiated_mtu.saturating_sub(60).min(1340)',
                      'TUNNEL_RSD_HANDSHAKE_PASS', 'idevice_set_transport_log_callback'):
            self.assertIn(guard, source)
        self.assertIn('crate::tunnel_provider::tunnel_heartbeat_stop();',
                      self.sources['ffi/src/core_device_proxy.rs'])


if __name__ == '__main__':
    unittest.main()
