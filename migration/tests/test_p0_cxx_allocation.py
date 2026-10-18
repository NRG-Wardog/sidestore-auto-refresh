"""C++ source admission tests. Synthetic checks never substitute for native evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import p0_cxx_allocation as cxx
import p0_elf_metadata_bounds as old
import maintained_sources as gate
import maintained_package_evidence as package
import verify_candidate_ipa as ipa
STAGE = ROOT / cxx.DIRECTORY
PRIOR = ROOT / old.DIRECTORY

def read(path): return json.loads(path.read_bytes())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

class CxxAdmissionTests(unittest.TestCase):
    def test_pending_gate_fails_before_untrusted_metadata(self):
        with patch.object(cxx, 'REVIEW_SHA256', None), patch.object(cxx, 'read', side_effect=AssertionError('metadata accessed')):
            with self.assertRaisesRegex(ValueError, 'pending'): cxx.approval(ROOT)
    def test_all_successor_routes_fail_closed_when_pending(self):
        with patch.object(cxx, 'REVIEW_SHA256', None):
            for invoke in (lambda: gate.contract_basis({'source_basis': cxx.BASIS}),
                           lambda: gate.expected_anisette_evidence({'source_basis': cxx.BASIS}),
                           lambda: package.contract_files({'source_basis': cxx.BASIS}),
                           lambda: gate.verify_all(Path('/not-read'), {'source_basis': cxx.BASIS}),
                           lambda: gate.acquire(Path('/not-written'), {'source_basis': cxx.BASIS})):
                with self.subTest(route=invoke), self.assertRaisesRegex(ValueError, 'pending'): invoke()
    def test_old_admission_remains_valid_and_exact(self):
        _, review, _ = old.approval(ROOT)
        pins = gate.load_pins(STAGE / 'historical-bounds-pins.json')
        self.assertEqual(pins['source_basis'], old.BASIS)
        self.assertEqual(read(STAGE/'historical-bounds-approval.json'), review)
        self.assertEqual(sha(STAGE/'historical-bounds-approval.json'), old.REVIEW_SHA256)
    def test_exact_eight_file_manifest_and_production_bytes(self):
        manifest = read(STAGE/'reviewed-ak-manifest.json')
        self.assertEqual(sha(STAGE/'reviewed-ak-manifest.json'), cxx.MANIFEST_SHA256)
        self.assertEqual(set(manifest['changes']), cxx.ALLOCATION_FILES)
        self.assertEqual((manifest['base'],manifest['commit'],manifest['tree']), (old.AK_COMMIT,cxx.AK_COMMIT,cxx.AK_TREE))
        for name, expected in cxx.REVIEWED_RUNTIME.items(): self.assertEqual(manifest['changes'][name]['after']['sha256'], expected)
    def test_allocation_receipt_counts_types_and_sources(self):
        receipt = {'results': {variant: {'allocator': {'checks':32,'failures':0,'guest_execution':False},'hooks':{'checks':51,'failures':0,'guest_execution':False}} for variant in ('normal','optimized','sanitized')}, 'guest_execution':False,'leak_sanitizer_enabled':False,'source_sha256':cxx.ALLOCATION_HELPER_SOURCES}
        cxx.validate_allocation_helper(receipt,cxx.ALLOCATION_HELPER_SOURCES)
        changes=[lambda r:r['results']['normal']['allocator'].update(checks=31),lambda r:r['results']['sanitized']['hooks'].update(failures=False),lambda r:r.update(guest_execution=True),lambda r:r['source_sha256'].pop('Native/Loader/cxx_allocation_runtime.h'),lambda r:r['results'].pop('optimized')]
        for mutation in changes:
            value=copy.deepcopy(receipt);mutation(value)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):cxx.validate_allocation_helper(value,cxx.ALLOCATION_HELPER_SOURCES)
    def test_hash_selector_and_compiled_diagnostic_gate(self):
        row={'path':'source.h','sha256':'a'*64,'prepared_sha256':'b'*64}
        self.assertEqual(gate.anisette_source_hashes({'source_basis':cxx.BASIS,'files':[row]}),{'source.h':'a'*64})
        with self.assertRaises(ValueError):ipa.verify_constructor_diagnostic_binaries(b'',b'',b'',{'source_basis':cxx.BASIS})
    def test_contracts_are_additive_and_header_is_bound(self):
        oldreg=read(PRIOR/'compatibility-registry.json');newreg=read(STAGE/'compatibility-registry.json')
        self.assertEqual(newreg['contracts'],oldreg['contracts']);self.assertEqual(newreg['edges'],oldreg['edges'])
        self.assertEqual((STAGE/'validate_contracts.py').read_bytes(),(PRIOR/'validate_contracts.py').read_bytes())
        evidence=read(STAGE/newreg['evidence']['path']);self.assertEqual(sha(STAGE/newreg['evidence']['path']),newreg['evidence']['sha256'])
        total=0
        for owner,row in newreg['owners'].items():
            path=STAGE/row['manifest_path'];contract=read(path);total+=len(contract['sources'])
            self.assertEqual(sha(path),row['manifest_sha256']);self.assertEqual(evidence['owners'][owner],{k:contract[k]for k in ('sources','provenance')})
            if owner!='AnisetteKit':self.assertEqual(path.read_bytes(),(PRIOR/row['manifest_path']).read_bytes())
            else:
                helper='Native/Loader/cxx_allocation_runtime.h';sources={r['path']:r for r in contract['sources']}
                self.assertEqual(sources[helper]['sha256'],cxx.REVIEWED_RUNTIME[helper])
                self.assertIn(helper,next(p for p in contract['provides'] if p['contract']=='anisette-provider')['source_paths'])
        self.assertEqual(total,97)
    def test_packaging_manifest_covers_every_new_file_and_old_literal(self):
        before=read(PRIOR/'anisette-generated/maintained-source-manifest.json');after=read(STAGE/'anisette-generated/maintained-source-manifest.json')
        self.assertEqual(after['required_compiled_evidence'],before['required_compiled_evidence'])
        rows={r['path']:r for r in after['files']};self.assertTrue({r['path']for r in before['files']}|cxx.ALLOCATION_FILES<=set(rows))
        for name, change in read(STAGE/'reviewed-ak-manifest.json')['changes'].items():self.assertEqual(rows[name]['sha256'],change['after']['sha256'])
    def test_historical_evidence_makes_no_successor_execution_claim(self):
        self.assertEqual(read(STAGE/'historical-evidence.json'),{'role':'historical-only','review_sha256':old.REVIEW_SHA256,'native_receipt_sha256':sha(PRIOR/'native-receipt.json'),'fresh_guest_execution':False,'full_loader_changed':True})

if __name__=='__main__':unittest.main()
