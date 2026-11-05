"""One exact existing source dependency for the actual Wi-Fi selection fixture."""
import copy
PATH = 'SideStore/Dependencies/minimuxer/Sources/Services/DeviceConnectionManager.swift'
SHA = '9122990e71e35402325d2b6beda7951d1131d901d17789f257cc050588146dce'
ROLE = 'connection_manager'
TEST_PATH = 'SideStore/tests/vpn_transport_recovery/test_contract.py'
TEST_SHA = 'e2ba9100c58a6a844b8b0703195a79ffa899e728b193b1c4b8071b8cd74c380b'
KEY = 'reviewed_local_wifi_fixture_dependency'
def require(ok, why):
    if not ok: raise ValueError(why)
def binding(contract):
    value = contract.get(KEY)
    if value is None: return False
    require(value == {'path': PATH, 'sha256': SHA, 'role': ROLE}, 'Unknown local Wi-Fi fixture dependency')
    require(contract['input_sha256'].get(PATH) == SHA, 'Unbound local Wi-Fi fixture dependency')
    return True
def inputs(contract, original):
    result = copy.deepcopy(original)
    if binding(contract):
        require(PATH not in result['transport_fixture'], 'Duplicate local Wi-Fi fixture dependency')
        result['transport_fixture'].insert(4, PATH)
    return result
def add(contract):
    result = copy.deepcopy(contract)
    require(KEY not in result, 'Local Wi-Fi dependency already declared')
    result[KEY] = {'path': PATH, 'sha256': SHA, 'role': ROLE}
    binding(result)
    result['additive_suite_inputs']['vpn-refresh-recovery'] = inputs(result, result['additive_suite_inputs']['vpn-refresh-recovery'])
    return result
