"""Exact additive actual-authentication proof; historical exports and markers stay intact."""
import copy
TAG = 'initial_provider_rejection_successor'
SIGN = 'SideStore/Dependencies/SideSign/'
ROOT = SIGN + '.ci/initial-provider-rejection'
GENERATOR = SIGN + '.ci/auth-request-diagnostics/generate.py'
NEW_INPUTS = (ROOT + '/RuntimeCases.swift', ROOT + '/authentication-projection.json')
PINS = {'SideStore/Dependencies/SideSign/Sources/DeveloperPortal/Authentication.swift': '2bcb4f8dd7cf82fd8e5d84af9795527755c8d56de017d4fd02c0eb0e8b4135c8', 'SideStore/Dependencies/SideSign/.ci/auth-request-diagnostics/BoundaryDoubles.swift': '05f88718a3175f0af5866e512743a47a8db3c8b1b14bf407f92f18a9e275c8de', 'SideStore/Dependencies/SideSign/.ci/auth-request-diagnostics/RuntimeTests.swift': 'f79019caad283cc1fddd1d24e339ceeeec56cd486588fe8c8b01357532e26f9f', 'SideStore/Dependencies/SideSign/.ci/auth-request-diagnostics/generate.py': '06b85c553571d094779d867c8eb3a40a8d9350aae8c576cb74afb9833936d10e', 'SideStore/Dependencies/SideSign/.ci/auth-request-diagnostics/test_source.py': 'b5369b700bf058a272135d19a6d55f984990935fee0059a3038952f347c0adc5', 'SideStore/Dependencies/SideSign/.ci/initial-provider-rejection/RuntimeCases.swift': 'b7474cf969d0bfbc206a3c35550fafcbc02db64fee069568440f6801c80fd723', 'SideStore/Dependencies/SideSign/.ci/initial-provider-rejection/authentication-projection.json': '19fd308730f3d004ce21813edf36b939acae33901a8108f02e5261b43ff444cd'}
WIRE_PINS = {'LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py': '54345ce7df4c1ce54dad26c2ae6c04c200daafc873f0884e29eb6b27f4c8ee08', 'LiveContainer/.ci/comprehensive-diagnostics/test_generator.py': 'adc7478c1419db7dc40da8e99feecdb972e3bfd4285f40ec633616dac0de2c1d'}
MARKER = 'INITIAL_PROVIDER_REJECTION_AUTH_PASS cases=27 actual_authenticate=true native_provider=false'

def require(value, message):
    if not value:
        raise ValueError(message)

def descriptor():
    return {'schema': 'reviewed-initial-provider-rejection-auth-v1', 'inputs': dict(PINS, **WIRE_PINS),
            'runtime_marker': MARKER, 'historical_exports_unchanged': True,
            'live_authentication_executed': False, 'native_provider_executed': False}

def required(contract):
    inputs = contract.get('input_sha256', {})
    return TAG in contract or any(p in inputs for p in NEW_INPUTS) or inputs.get(GENERATOR) == PINS[GENERATOR]

def validate(contract):
    if not required(contract):
        return False
    require(contract.get(TAG) == descriptor(), 'Unknown or missing initial provider rejection successor')
    inputs = contract.get('input_sha256', {})
    require(all(inputs.get(p) == sha for p, sha in dict(PINS, **WIRE_PINS).items()), 'Unbound initial provider rejection source')
    require({p for p in inputs if p.startswith(ROOT + '/')} == set(NEW_INPUTS),
            'Initial provider rejection source closure differs')
    return True

def add(contract):
    require(TAG not in contract, 'Initial provider rejection already projected')
    result = copy.deepcopy(contract)
    result[TAG] = descriptor()
    validate(result)
    return result

def verify_extraction(contract, evidence):
    if not validate(contract):
        return
    expected = {p.removeprefix(SIGN): sha for p, sha in PINS.items()}
    require(all(evidence.get('inputs', {}).get(p) == sha for p, sha in expected.items()),
            'Initial provider rejection extraction inputs differ')
    for key in ('network', 'native_provider', 'swift_executed'):
        require(evidence.get(key) is False, 'Initial provider rejection extraction exceeds source-only scope')

def verify_runtime(contract, text):
    if not validate(contract):
        return
    lines = [line for line in text.splitlines() if line.startswith('INITIAL_PROVIDER_REJECTION_AUTH_PASS')]
    require(lines == [MARKER], 'Missing, duplicate or unknown initial provider rejection runtime marker')
