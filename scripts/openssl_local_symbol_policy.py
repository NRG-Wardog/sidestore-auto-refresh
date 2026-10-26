"""Unsigned iOS OpenSSL local-symbol qualification, never runtime approval.

No mutation/tool execution here. Both owner copies and their complete matching
native dSYM inventories are required. The unchanged local-symbol gate preserves
all code, metadata, exports, undefined/indirect symbols and loader payloads.
A separate actual transformed host crypto qualification is required for adoption.
"""
from pathlib import Path
import re
from macho_stab_guard import require, sha, image_uuid
from macho_local_symbol_guard import classify, symbol_rows, verify_local_removal
from optimize_native_candidate import retained_inventory

OWNERS = frozenset(('LiveContainer','SideStore'))


def _digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'explicit SHA256 required')
    return value


def verify_openssl_local_removal(before, after, *, expected_before_sha256,
                                 dwarf, expected_dwarf_sha256):
    require(sha(before) == _digest(expected_before_sha256), 'OpenSSL source bytes differ')
    require(sha(dwarf) == _digest(expected_dwarf_sha256), 'OpenSSL DWARF bytes differ')
    old, _, _ = classify(before)
    require(old['signature_sha256'] is None, 'OpenSSL source must remain unsigned')
    rows, _ = symbol_rows(before)
    require(not any(row[1] & 0x10 for row in rows), 'unexpected OpenSSL private-extern policy')
    require(image_uuid(dwarf) == old['uuid'], 'OpenSSL full DWARF UUID differs')
    result = verify_local_removal(before, after)
    require(result['after']['signature_sha256'] is None, 'OpenSSL stripped output must remain unsigned')
    return {'schema':'unsigned-openssl-local-symbol-policy-v1',
            'before_sha256':sha(before),'after_sha256':sha(after),'dwarf_sha256':sha(dwarf),
            'uuid':old['uuid'],'structural':result,'raw_bytes_saved':len(before)-len(after),
            'signature_added':False,'providers_or_architectures_removed':False,
            'transformed_host_crypto_qualified':False,'ios_runtime_qualified':False,
            'packaging_approved':False}


def verify_openssl_owner_pair(inputs):
    """Each entry supplies bytes plus a complete sealed native dSYM inventory.

Expected digests/inventories must come from the caller's already authenticated
native retention seal, never be calculated from the candidate at admission.
"""
    require(type(inputs) is dict and set(inputs) == OWNERS, 'both exact OpenSSL owners required')
    reports = {}
    for owner in sorted(OWNERS):
        item = inputs[owner]
        require(type(item) is dict and set(item) == {'before','after','before_sha256','dsym','dsym_inventory'},
                'OpenSSL owner input closure differs')
        bundle = Path(item['dsym'])
        require(bundle.is_dir() and not bundle.is_symlink(), 'real full OpenSSL dSYM required')
        inventory = retained_inventory(bundle)
        require(inventory == item['dsym_inventory'] and 'Contents/Info.plist' in inventory,
                'sealed full OpenSSL dSYM inventory differs')
        relative = 'Contents/Resources/DWARF/OpenSSL'
        row = inventory.get(relative)
        require(type(row) is dict and row.get('kind') == 'file', 'regular OpenSSL DWARF required')
        dwarf = bundle / relative
        require(not any((bundle/parent).is_symlink() for parent in Path(relative).parents) and not dwarf.is_symlink(),
                'linked OpenSSL DWARF path')
        reports[owner] = verify_openssl_local_removal(item['before'], item['after'],
            expected_before_sha256=item['before_sha256'],dwarf=dwarf.read_bytes(),expected_dwarf_sha256=row['sha256'])
        require(retained_inventory(bundle) == inventory, 'OpenSSL dSYM changed during validation')
    require(inputs['LiveContainer']['before'] == inputs['SideStore']['before'], 'original OpenSSL owner copies differ')
    require(inputs['LiveContainer']['after'] == inputs['SideStore']['after'], 'stripped OpenSSL owner copies differ')
    return {'schema':'paired-unsigned-openssl-local-symbol-policy-v1','owners':reports,
            'both_owner_copies_preserved':True,'paired_binary_bytes_equal':True,
            'raw_bytes_saved_total':sum(report['raw_bytes_saved'] for report in reports.values()),
            'compressed_savings_attributed':False,'transformed_host_crypto_qualified':False,
            'ios_runtime_qualified':False,'packaging_approved':False}
