"""Exact direct-host dispatch successor; historical VPN fixture cases stay intact."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy
import hashlib
PATH = 'LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py'
BEFORE = 'be7b3efc7e790764ddf8e1cd051088879847cf6086de72ba5491a954927f4433'
AFTER = '5554485c801dd03470a122ed151389690fc1e8585506b283d51764cfa5923535'
OLD = "    assert hashlib.sha1(b'blob ' + str(len(client_bytes)).encode() + b'\\0' + client_bytes).hexdigest() == 'ed031c9f09879ab81f5feeacb141c72ceb076a38'\n    native_dispatch = helper.block(client_bytes.decode(), '- (void)refreshAllAppsWithIdentifier:')\n    assert '[self performRefreshForRealWithIdentifier:identifier mangledTypeName:mangledTypeName server:handler.server];' in native_dispatch\n    assert 'if (refreshRunID.length) [defaults setObject:refreshRunID' in native_dispatch\n"
NEW = "    client_blob = hashlib.sha1(b'blob ' + str(len(client_bytes)).encode() + b'\\0' + client_bytes).hexdigest()\n    if client_blob == 'ed031c9f09879ab81f5feeacb141c72ceb076a38':\n        native_dispatch = helper.block(client_bytes.decode(), '- (void)refreshAllAppsWithIdentifier:')\n        assert '[self performRefreshForRealWithIdentifier:identifier mangledTypeName:mangledTypeName server:handler.server];' in native_dispatch\n        assert 'if (refreshRunID.length) [defaults setObject:refreshRunID' in native_dispatch\n    else:\n        # Exact reviewed direct-host successor, not an arbitrary selector bypass.\n        assert client_blob == '5bf22356b3413d4377b26ed96ae8b63522855bd9'\n        native_dispatch = helper.block(client_bytes.decode(), '- (void)refreshAllAppsWithIdentifier:(NSString*)identifier mangledTypeName:(NSString *)mangledTypeName refreshRunID:(NSString* _Nonnull)refreshRunID admissionID:')\n        assert '[service refreshForHost:runID admissionID:dispatchAdmissionID progress:' in native_dispatch\n        assert 'NSString *runID = [refreshRunID copy];' in native_dispatch\n        assert 'NSString *dispatchAdmissionID = [admissionID copy];' in native_dispatch\n        assert '[server finishHostRefresh:error runID:runID admissionID:dispatchAdmissionID verification:verification];' in native_dispatch\n        assert 'performRefreshForRealWithIdentifier:' not in native_dispatch\n        assert '[defaults setObject:' not in native_dispatch\n"

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
