import json
from pathlib import Path
import tempfile
import unittest
import verify_cms
import consumer

HERE=Path(__file__).resolve().parent
class ConsumerContract(unittest.TestCase):
    def test_der_rejects_indefinite_truncated_and_nonminimal_lengths(self):
        for value in [b'',b'\x30',b'\x30\x80\x00\x00',b'\x30\x82\x00\x80',b'\x30\x81\x01\x00',b'\x30\x03\x01']:
            with self.assertRaises(ValueError): verify_cms.one(value,0x30)
        self.assertEqual(verify_cms.one(b'\x30\x00',0x30).children(),[])

    def test_der_rejects_trailing_and_wrong_root(self):
        for value in [b'\x30\x00\x00',b'\x31\x00']:
            with self.assertRaises(ValueError): verify_cms.one(value,0x30)

    def test_source_closure_contains_real_signing_implementation(self):
        source=json.loads((HERE/'zsign-source.json').read_text())
        self.assertEqual(source['commit'],'6f11f8843ea6ae11f0d82b902e5d9170a197862f')
        for path in ['ZSign/openssl.cpp','ZSign/signing.cpp','ZSign/common/sha.cpp','ZSign/common/json.cpp']:
            self.assertIn(path,source['files'])
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'Production source byte mismatch'):
                consumer.validate_source(Path(temp))

    def test_actual_source_calls_and_no_trusted_chain_claim(self):
        probe=(HERE/'zsign_probe.cpp').read_text()
        for call in ['ZSign::SlotBuildCodeDirectory(false','ZSign::SlotBuildCodeDirectory(true','ZSign::SlotBuildCMSSignature(&asset']:
            self.assertIn(call,probe)
        self.assertNotIn('CMS_sign(',probe)
        verifier=(HERE/'verify_zsign.swift').read_text()
        self.assertIn('SecTrustSetNetworkFetchAllowed(trust, false)',verifier)
        self.assertIn('!SecTrustEvaluateWithError',verifier)
        self.assertNotIn('SecTrustSetAnchorCertificates',verifier)

if __name__=='__main__':unittest.main()
