"""Validate candidate identity/UUID evidence without requiring an Apple device."""
import importlib.util
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_evidence', ROOT / 'scripts/combined_build_evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class CandidateEvidenceTests(unittest.TestCase):
    def test_ipa_hash_uses_bounded_reads_and_preserves_raw_digest(self):
        payload = bytes(range(256)) * 9000
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'candidate.ipa'
            ipa.write_bytes(payload)
            original_open = Path.open
            requested_reads = []

            class ReadRecorder:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def read(self, size=-1):
                    requested_reads.append(size)
                    return self.stream.read(size)

            def recorded_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                if path == ipa:
                    return ReadRecorder(stream)
                return stream

            with mock.patch.object(Path, 'open', recorded_open):
                size, digest = evidence.hash_ipa_file(ipa, {'compressed_ipa_bytes': len(payload)})
            self.assertEqual(size, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertTrue(requested_reads)
            self.assertTrue(all(0 < amount <= 1024 * 1024 for amount in requested_reads))

    def test_oversized_ipa_is_rejected_before_opening_for_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'oversized.ipa'
            ipa.write_bytes(b'0123456789')
            with mock.patch.object(Path, 'open', side_effect=AssertionError('must preflight before open')):
                with self.assertRaisesRegex(ValueError, 'compressed IPA exceeds'):
                    evidence.hash_ipa_file(ipa, {'compressed_ipa_bytes': 9})

    def test_matching_uuid_and_malformed_commands(self):
        expected = uuid.UUID('07E95F24-0DF4-3F9F-B1B4-3AF4881C1CBD')
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6, 1, 24, 0, 0)
        command = struct.pack('<II', 0x1b, 24) + expected.bytes
        self.assertEqual(evidence.macho_uuid(header + command), str(expected).upper())
        with self.assertRaises(ValueError): evidence.macho_uuid(header + struct.pack('<II', 0x1b, 7))
        with self.assertRaises(ValueError): evidence.macho_uuid(header + command[:-1])
        self.assertIsNone(evidence.macho_uuid(b'not Mach-O'))

    def test_release_product_lines_are_accepted(self):
        pattern = r'v3\.\d+(\.\d+)*(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?'
        for product in ("v2", "v3", "v3.0.1", "v3.10.2", "v3.0.3-rc"):
            self.assertTrue(product in ('v2', 'v3') or
                            __import__('re').fullmatch(pattern, product) is not None)
        for product in ("v4", "v3.x", "latest", ""):
            self.assertFalse(product in ('v2', 'v3') or
                             __import__('re').fullmatch(pattern, product) is not None)
        self.assertEqual('Combined LC+SS ' + 'v3.0.1', 'Combined LC+SS v3.0.1')
