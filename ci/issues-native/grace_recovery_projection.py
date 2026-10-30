"""Exact additive grace-to-host activation cases; historical89 stay ordered."""
import copy, hashlib, json
from pathlib import Path
KEY = 'reviewed_grace_recovery_cases'
FILES = {'LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py': '16a34b89a4651f0bf43019d3792cd1c3a21d085fadabe4bc5c2d1e28f26785b2', 'LiveContainer/.ci/vpn-refresh-recovery/cases.swift': 'd587a26f5cc2e4ddcffb2f1b3e5ff0c4e0d985544c06926eb6ccce6a71c548ac', 'SideStore/tests/vpn_transport_recovery/generate_fixture.py': 'ec9949372ac357bc26a5c169e18e5025158b66c5835133799b44388563720022'}
ADDED = ['grace_wifi_negative_never_authorizes_vpn_activation', 'grace_eleven_pending_preserves_two_target_wire', 'grace_first_tap_opens_once_then_rechecks', 'grace_no_mutation_before_ready_then_one_admission', 'grace_still_off_never_reopens_or_mutates', 'grace_mixed_manifest_does_not_activate', 'grace_partial_success_does_not_activate', 'grace_uncertain_mutation_does_not_activate', 'required_wifi_nonwifi_default_reaches_typed_vpn_failure', 'required_wifi_nonwifi_default_opens_once_then_rechecks', 'required_wifi_nonwifi_default_mutation_admitted_once', 'required_wifi_unknown_never_opens_or_mutates', 'required_wifi_unsatisfied_never_opens_or_mutates', 'required_wifi_wrong_interface_never_opens_or_mutates', 'remote_preserves_default_wifi_acceptance', 'remote_preserves_default_nonwifi_rejection']
def require(ok, why):
    if not ok: raise ValueError(why)
def historical():
    value = json.loads(Path(__file__).with_name('setup-state-recovery-contract.json').read_text())['case_names']['vpn-refresh-recovery']
    require(len(value) == 89 and hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest() == '1c7acb45302f5ce41b9d77a9e887a7e14d1a04f01b34d09c745f90ad750e83f7', 'Historical VPN case closure changed')
    return value
def expected_names():
    names = historical() + ADDED
    require(hashlib.sha256(json.dumps(names[:97], separators=(',', ':')).encode()).hexdigest() == 'cd2264979fc998974133bff3730081f97a97ac2c30c1923eff1ced4252a6bee1', 'Previous 97 VPN cases changed or reordered')
    return names
def binding(contract):
    value = contract.get(KEY)
    if value is None: return False
    require(value == {'source_sha256': FILES, 'additional_cases': ADDED}, 'Unknown grace recovery extension')
    require(all(contract['input_sha256'].get(p) == h for p,h in FILES.items()), 'Unbound grace recovery fixture')
    require(contract['case_names']['vpn-refresh-recovery'] == expected_names(), 'Grace recovery exact case closure differs')
    return True
def count(contract):
    return 105 if binding(contract) else 89
def add(contract):
    result = copy.deepcopy(contract)
    require(KEY not in result and result['case_names']['vpn-refresh-recovery'] == historical(), 'Original VPN cases must be retained')
    result[KEY] = {'source_sha256': dict(FILES), 'additional_cases': list(ADDED)}
    result['case_names']['vpn-refresh-recovery'] = expected_names()
    binding(result)
    return result
