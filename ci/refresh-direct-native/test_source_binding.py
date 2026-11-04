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
        self.inputs={
          'sources/SideStore/AltStore/Intents/App Intents/RefreshAllAppsIntent.swift':V.block(fixture,'enum V3RefreshIntentBackend {'),
          'sources/SideStore/AltStore/AppDelegate.swift':primitives+'\n'+service,
          'sources/LiveContainer/SideStoreSupport/XPCClient.m':protocol+'\n@implementation SideStoreClient\n',
          'builder/scripts/templates/v3_sidestore_service.swift':service,
          'builder/scripts/templates/v3_behavioral_primitives.swift':primitives}
        for path,text in self.inputs.items():
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

    def test_exact_slices_bind_without_execution_claim(self):
        result=V.bind(self.sources,self.builder)
        self.assertEqual(len(result['fixtures']),5)
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

    def test_missing_or_duplicate_anchor_rejected(self):
        for value in ['missing', 'enum A { } enum A { }']:
            with self.assertRaises(ValueError):V.block(value,'enum A')

if __name__=='__main__':unittest.main(verbosity=2)
