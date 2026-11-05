"""Exact fixture-only owned-progress API successor; old readiness proof stays frozen."""
import copy
import direct_fixture_projection as direct_fixture
import early_readiness_projection as prior
GENERATOR = prior.GENERATOR
GENERATOR_SHA = 'a8301da81178f28689e24348a6ef90054a51c9c12903dc82af81f4602f3c473a'
KEY = 'reviewed_early_owned_progress_fixture'
PINS = {
    GENERATOR: GENERATOR_SHA,
    prior.TEST_SOURCE: 'a0e26b10398aac3f9171b8d70cc95fd786f64c2f672993e2e735b75b19a1a3bd',
    'LiveContainer/.ci/early-refresh-boundaries/README.md': '3490ee5abbe6b9927e6a918ac33bba7412605ece28a5313a243c8e6b64a27eef',
    'LiveContainer/.ci/early-refresh-boundaries/early_cases.swift': 'b3953b8bdb52860048b1bb0db64856568cb1d2e84f7d9a53120ba1e77ac4e215',
    'LiveContainer/.ci/early-refresh-boundaries/early_doubles.swift': 'c975369dc2d3ed25734995c8a9034e95ce723bc291e7ea467e6b99003a128cda',
}
require = prior.require

def descriptor():
    return {'fixture_sha256': dict(PINS), 'prior_generator_sha256': prior.GENERATOR_SHA,
            'prior_test_sha256': prior.TEST_SOURCE_SHA, 'runtime_case_count': 24, 'static_check_count': 121}

def current_binding(contract):
    contract=direct_fixture.normalize(contract)
    hashes = contract['input_sha256']
    if KEY not in contract:
        require(not any(hashes.get(path) == digest for path, digest in PINS.items()),
                'Missing owned-progress fixture successor binding')
        return False
    require(contract[KEY] == descriptor(), 'Unknown owned-progress fixture successor')
    require(all(hashes.get(path) == digest for path, digest in PINS.items()),
            'Unbound owned-progress fixture bytes')
    require(prior.KEY in contract, 'Missing historical readiness dependency')
    return True

def normalized(contract):
    result = direct_fixture.normalize(contract)
    if current_binding(result):
        result.pop(KEY)
        result['input_sha256'][GENERATOR] = prior.GENERATOR_SHA
        result['input_sha256'][prior.TEST_SOURCE] = prior.TEST_SOURCE_SHA
        # The old descriptor remains byte-for-byte historical: only the current
        # source hash map is normalized for its exact legacy checker.
        prior.binding(result)
    return result

def add(contract):
    actual_inputs=copy.deepcopy(contract['input_sha256'])
    contract=direct_fixture.normalize(contract)
    hashes = contract['input_sha256']
    if hashes.get(GENERATOR) != GENERATOR_SHA:
        require(not any(hashes.get(path) == digest for path, digest in PINS.items()),
                'Partial owned-progress fixture successor')
        return prior.add(contract)
    require(KEY not in contract, 'Owned-progress fixture successor already projected')
    require(all(hashes.get(path) == digest for path, digest in PINS.items()),
            'Unreviewed owned-progress fixture source')
    old = copy.deepcopy(contract)
    old['input_sha256'][GENERATOR] = prior.GENERATOR_SHA
    old['input_sha256'][prior.TEST_SOURCE] = prior.TEST_SOURCE_SHA
    result = prior.add(old)
    result['input_sha256'][GENERATOR] = GENERATOR_SHA
    result['input_sha256'][prior.TEST_SOURCE] = PINS[prior.TEST_SOURCE]
    result[KEY] = descriptor()
    normalized(result)
    result['input_sha256']=actual_inputs
    return result

def inputs(contract, original, profile=False):
    return prior.inputs(normalized(contract), original, profile=profile)

def verify_static(value, contract):
    return prior.verify_static(value, normalized(contract))
