import tempfile
import unittest
from pathlib import Path
import verify_source_binding as V

class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sources=self.root/'sources';self.builder=self.root/'builder'
        fixture=(V.HERE/'service_fixture.swift').read_text()
        primitives='\n'.join(V.block(fixture,a) for a in ('struct V3RefreshExecutionTicket:','struct V3RefreshExecutionGate {','struct V3RefreshAdmissionLease {','enum V3RefreshIntentStartPolicy {'))
        start=fixture.index('    private var refreshExecutionGate =')
        end=fixture.index('        completion(failure?.encodedString, payload)',start)
        end=fixture.index('\n    }',end)+len('\n    }')
        methods=fixture[start:end]
        service=methods+'\n\n    private var pendingRefreshAdmissionRequests: Set<String> = []\n'
        bridge=(V.HERE/'bridge.m').read_text()
        protocol=V.span(bridge,'@protocol V3CommandService','void InvokeDirectHost')
        host_fixture=(V.HERE/'host_callback_fixture.swift').read_text()
        release_fixture=(V.HERE/'release_fixture.swift').read_text()
        wire='\n'.join(V.block(release_fixture,a) for a in ('public struct V3AuthServiceSnapshot:','enum V3WireContract {'))
        release=release_fixture[release_fixture.index('final class ReleaseService {'):]
        release=V.span(release,'        case "refreshAdmissionEnd":','default: throw ServiceError.invalidRequest')
        service+='\n'+release+'\n        case "appIcon":\n'
        self.inputs={
          'sources/LiveContainer/SideStoreSupport/SideStore.swift':'\n'.join(V.block(host_fixture,a) for a in V.HOST_ANCHORS),
          'sources/SideStore/AltStore/Intents/App Intents/RefreshAllAppsIntent.swift':V.block(fixture,'enum V3RefreshIntentBackend {'),
          'sources/SideStore/AltStore/AppDelegate.swift':wire+'\n'+primitives+'\n'+service,
          'builder/scripts/templates/v3_wire_contract.swift':wire,
          'sources/LiveContainer/SideStoreSupport/XPCClient.m':protocol+'\n@implementation SideStoreClient\n',
          'builder/scripts/templates/v3_sidestore_service.swift':service,
          'builder/scripts/templates/v3_behavioral_primitives.swift':primitives}
        for path,text in self.inputs.items():
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

    def test_exact_slices_bind_without_execution_claim(self):
        result=V.bind(self.sources,self.builder)
        self.assertEqual(len(result['fixtures']),7)
        self.assertFalse(result['real_xpc_transport_executed'])
        self.assertFalse(result['physical_device_validation'])

    def test_mutated_production_block_rejected(self):
        p=self.sources/'SideStore/AltStore/Intents/App Intents/RefreshAllAppsIntent.swift'
        p.write_text(p.read_text().replace('presentsNotifications:', 'alteredNotifications:',1))
        with self.assertRaises(ValueError):V.bind(self.sources,self.builder)

    def test_template_generated_drift_rejected(self):
        p=self.builder/'scripts/templates/v3_behavioral_primitives.swift'
        p.write_text(p.read_text().replace('consumedHostAdmissionID','alteredAdmissionID'))
        with self.assertRaisesRegex(ValueError,'Generated primitive differs'):V.bind(self.sources,self.builder)

    def test_abi_signature_drift_rejected(self):
        p=self.sources/'LiveContainer/SideStoreSupport/XPCClient.m'
        p.write_text(p.read_text().replace('double','float'))
        with self.assertRaises(ValueError):V.bind(self.sources,self.builder)

    def test_release_control_drift_rejected(self):
        p=self.sources/'SideStore/AltStore/AppDelegate.swift'
        p.write_text(p.read_text().replace('case "refreshAdmissionEnd":','case "releaseChanged":'))
        with self.assertRaises(ValueError):V.bind(self.sources,self.builder)

    def test_wire_template_drift_rejected(self):
        p=self.builder/'scripts/templates/v3_wire_contract.swift'
        p.write_text(p.read_text().replace('["state", "admissionID"]','["state"]'))
        with self.assertRaises(ValueError):V.bind(self.sources,self.builder)

    def test_host_callback_drift_rejected(self):
        p=self.sources/'LiveContainer/SideStoreSupport/SideStore.swift'
        p.write_text(p.read_text().replace('refreshAdmissionID == nil','refreshAdmissionID != nil',1))
        with self.assertRaises(ValueError):V.bind(self.sources,self.builder)

    def test_missing_or_duplicate_anchor_rejected(self):
        for value in ['missing', 'enum A { } enum A { }']:
            with self.assertRaises(ValueError):V.block(value,'enum A')

if __name__=='__main__':unittest.main(verbosity=2)
