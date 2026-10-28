"""Exact additive grace-to-host activation cases; historical89 stay ordered."""
import copy, hashlib, json
from pathlib import Path
KEY = 'reviewed_grace_recovery_cases'
FILES = {'LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py': '1374cc75da1647ede33003d8e88591c4ccea853f3371f2d3bbdaf5d13cc20680', 'LiveContainer/.ci/vpn-refresh-recovery/cases.swift': 'f647f686681a884f22cce78811befe258435c948b09e63cdba92494ff09bb160'}
ADDED = ['grace_wifi_negative_never_authorizes_vpn_activation', 'grace_eleven_pending_preserves_two_target_wire', 'grace_first_tap_opens_once_then_rechecks', 'grace_no_mutation_before_ready_then_one_admission', 'grace_still_off_never_reopens_or_mutates', 'grace_mixed_manifest_does_not_activate', 'grace_partial_success_does_not_activate', 'grace_uncertain_mutation_does_not_activate']
def require(ok, why):
    if not ok: raise ValueError(why)
def historical():
    value = json.loads(Path(__file__).with_name('setup-state-recovery-contract.json').read_text())['case_names']['vpn-refresh-recovery']
    require(len(value) == 89 and hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest() == '1c7acb45302f5ce41b9d77a9e887a7e14d1a04f01b34d09c745f90ad750e83f7', 'Historical VPN case closure changed')
    return value
def expected_names(): return historical() + ADDED
def binding(contract):
    value = contract.get(KEY)
    if value is None: return False
    require(value == {'source_sha256': FILES, 'additional_cases': ADDED}, 'Unknown grace recovery extension')
    require(all(contract['input_sha256'].get(p) == h for p,h in FILES.items()), 'Unbound grace recovery fixture')
    require(contract['case_names']['vpn-refresh-recovery'] == expected_names(), 'Grace recovery exact case closure differs')
    return True
def count(contract):
    return 97 if binding(contract) else 89
def add(contract):
    result = copy.deepcopy(contract)
    require(KEY not in result and result['case_names']['vpn-refresh-recovery'] == historical(), 'Original VPN cases must be retained')
    result[KEY] = {'source_sha256': dict(FILES), 'additional_cases': list(ADDED)}
    result['case_names']['vpn-refresh-recovery'] = expected_names()
    binding(result)
    return result
