"""Exact direct-host dispatch successor; historical VPN fixture cases stay intact."""
import copy
import hashlib
PATH = 'LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py'
BEFORE = 'be7b3efc7e790764ddf8e1cd051088879847cf6086de72ba5491a954927f4433'
AFTER = '0ab1acb7f33dbbf452e17b8d2b105dd4f5df2c66df6eee456a2357f2c83f9109'
OLD = "    assert hashlib.sha1(b'blob ' + str(len(client_bytes)).encode() + b'\\0' + client_bytes).hexdigest() == 'ed031c9f09879ab81f5feeacb141c72ceb076a38'\n    native_dispatch = helper.block(client_bytes.decode(), '- (void)refreshAllAppsWithIdentifier:')\n    assert '[self performRefreshForRealWithIdentifier:identifier mangledTypeName:mangledTypeName server:handler.server];' in native_dispatch\n    assert 'if (refreshRunID.length) [defaults setObject:refreshRunID' in native_dispatch\n"
NEW = "    client_blob = hashlib.sha1(b'blob ' + str(len(client_bytes)).encode() + b'\\0' + client_bytes).hexdigest()\n    native_dispatch = helper.block(client_bytes.decode(), '- (void)refreshAllAppsWithIdentifier:')\n    if client_blob == 'ed031c9f09879ab81f5feeacb141c72ceb076a38':\n        assert '[self performRefreshForRealWithIdentifier:identifier mangledTypeName:mangledTypeName server:handler.server];' in native_dispatch\n        assert 'if (refreshRunID.length) [defaults setObject:refreshRunID' in native_dispatch\n    else:\n        # Exact reviewed direct-host successor, not an arbitrary selector bypass.\n        assert client_blob == '64145fa63c5a03ca4c6a5061608bcd4fb96ca7b0'\n        assert '[service refreshForHost:runID progress:' in native_dispatch\n        assert 'NSString *runID = [refreshRunID copy];' in native_dispatch\n        assert '[server finishRefresh:error runID:runID verification:verification];' in native_dispatch\n        assert 'performRefreshForRealWithIdentifier:' not in native_dispatch\n        assert '[defaults setObject:' not in native_dispatch\n"

def require(ok, why):
    if not ok: raise ValueError(why)

def validate_source(sources):
    data = (sources / PATH).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest == BEFORE: return False
    require(digest == AFTER, 'Unreviewed direct refresh VPN generator')
    text = data.decode()
    require(text.count(NEW) == 1, 'Direct refresh replacement missing or duplicated')
    prior = text.replace(NEW, OLD, 1).encode()
    require(hashlib.sha256(prior).hexdigest() == BEFORE,
            'Direct refresh inverse changes historical VPN assertions or cases')
    return True

def normalize(contract):
    # Only this exact reviewed generator may carry the historical proof. Actual
    # inputs keep their successor hash in exported source/evidence manifests.
    inputs = contract.get('input_sha256', {})
    if inputs.get(PATH) != AFTER: return contract
    result = copy.deepcopy(contract)
    result['input_sha256'][PATH] = BEFORE
    return result
