"""Portable packaging admission for the reviewed native CMS evidence.

No Git/network/native execution. The sibling source contract was independently
constructed from genuine reviewed Git blobs, not from the artifact's claims.
Call only after exact native artifact hash/green-run acquisition qualification.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
FILES = ('preservation-cms-tests.json', 'preservation-cms-native.json',
         'preservation-cms-public.log', 'openssl-host-build-receipt.json')

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def pairs(items):
    value = {}
    for key, entry in items:
        require(key not in value, 'Duplicate CMS evidence key')
        value[key] = entry
    return value

def read_bytes(path, limit=2*1024*1024):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Missing or linked CMS evidence')
    require(0 < path.stat().st_size <= limit, 'CMS evidence size bound')
    return path.read_bytes()

def decode(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

def identity(value):
    require(type(value) is dict and set(value) == {'workflow_commit','run_id','run_attempt'}, 'CMS native identity closure')
    require(type(value['workflow_commit']) is str and re.fullmatch('[0-9a-f]{40}', value['workflow_commit']), 'CMS builder identity')
    require(all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*', value[k]) for k in ('run_id','run_attempt')), 'CMS run/attempt identity')

def verify(evidence_root, native_identity):
    """native_identity comes from the independently validated input manifest."""
    identity(native_identity)
    contract = decode(read_bytes(HERE/'cms-source-contract.json'))
    require(contract['schema'] == 'reviewed-preservation-cms-package-contract-v1', 'Unsupported CMS package contract')
    require(native_identity['workflow_commit'] == contract['native_builder'], 'Unreviewed CMS native builder')
    root = Path(evidence_root).resolve(strict=True)
    raw = {name: read_bytes(root/name) for name in FILES}
    receipt, cms, host = (decode(raw[name]) for name in (FILES[0], FILES[1], FILES[3]))
    public_lines = raw[FILES[2]].decode('utf-8').splitlines()
    require(len(public_lines) == 1 and len(raw[FILES[2]]) < 100000, 'Public CMS result must be one bounded record')
    public = json.loads(public_lines[0], object_pairs_hook=pairs)
    require(type(receipt) is dict and set(receipt) == {
        'schema','status','run','source_sha256','crypto','tool_sha256','openssl_cli_sha256',
        'cms_cases','public_cases','log_sha256','cms_verified','device_executed','public_profile_bytes_retained'}, 'CMS receipt closure')
    require(receipt['schema'] == 'preservation-cms-native-v1' and
        receipt['status'] == 'PASS_EXACT_CRYPTO_AND_SWIFT_PUBLIC_BOUNDARY_NOT_DEVICE', 'CMS receipt status')
    identity(receipt['run'])
    require(receipt['run'] == native_identity, 'CMS native run differs')
    require(receipt['source_sha256'] == contract['source_sha256'] and len(receipt['source_sha256']) == 18, 'CMS reviewed source closure differs')
    require(receipt['cms_verified'] is True and receipt['device_executed'] is False and
        receipt['public_profile_bytes_retained'] is False, 'CMS execution/privacy claim differs')
    require(receipt['cms_cases'] == contract['cms_cases'] and receipt['public_cases'] == contract['public_cases'], 'CMS receipt case closure differs')
    require(type(receipt['log_sha256']) is dict and set(receipt['log_sha256']) == set(FILES[1:3]), 'CMS retained log closure')
    for name in FILES[1:3]:
        require(receipt['log_sha256'][name] == sha(raw[name]), 'CMS retained log hash differs')
    crypto = receipt['crypto']
    require(type(crypto) is dict and set(crypto) == {'version','build_receipt_sha256','source_archive_sha256','libcrypto_sha256','headers_inventory_sha256'}, 'CMS crypto closure')
    require(crypto['version'] == '3.5.9' and all(digest(v) for k,v in crypto.items() if k != 'version'), 'CMS crypto version/digest')
    require(type(host) is dict and host.get('schema') == 'issue53-lts-generated-dependency-v1' and
        host.get('status') == 'BUILT_NOT_QUALIFIED_NOT_APP_READY', 'CMS same-run host build status')
    require(host.get('source') == contract['openssl'] and host.get('recipe_sha256') == contract['host_recipe_sha256'], 'CMS host source/recipe differs')
    require(crypto['build_receipt_sha256'] == sha(raw[FILES[3]]) and
        crypto['source_archive_sha256'] == contract['openssl']['sha256'] and
        crypto['headers_inventory_sha256'] == host.get('header_inventory_sha256'), 'CMS same-run crypto lineage differs')
    archives = host.get('static_archives_sha256')
    require(type(archives) is dict and all(type(k) is str and digest(v) for k,v in archives.items()), 'CMS host archive inventory malformed')
    actual_crypto = [v for k,v in archives.items() if Path(k).name == 'libcrypto.a']
    require(actual_crypto == [crypto['libcrypto_sha256']], 'CMS crypto archive not exact same-run libcrypto')
    require(digest(receipt['openssl_cli_sha256']), 'CMS CLI identity malformed')
    tools = receipt['tool_sha256']
    require(type(tools) is dict and 1 <= len(tools) <= 2 and
        all(type(k) is str and Path(k).is_absolute() and digest(v) for k,v in tools.items()), 'CMS compiler identity malformed')
    require(type(cms) is dict and set(cms) == {'schema','openssl','qualified','time_policy','tested_at_utc','checks','count','status'}, 'CMS native result closure')
    require(cms['schema'] == 'profile-cms-native-v1' and cms['status'] == 'passed' and
        cms['openssl'] == '3.5.9' and cms['qualified'] is True, 'CMS native qualification differs')
    require(cms['time_policy'] == 'inner-current-system-time;legacy-no-check-time', 'CMS temporal policy differs')
    require(type(cms['tested_at_utc']) is str, 'CMS test timestamp type')
    try:
        timestamp = dt.datetime.fromisoformat(cms['tested_at_utc'])
    except (ValueError, TypeError):
        raise ValueError('CMS test timestamp malformed') from None
    require(timestamp.tzinfo is not None and timestamp.utcoffset() == dt.timedelta(0), 'CMS test timestamp must be UTC')
    require(type(cms['count']) is int and cms['count'] == 28 and
        type(cms['checks']) is list and cms['checks'] == contract['cms_cases'], 'CMS exact native cases differ')
    require(type(public) is dict and set(public) == {'schema','status','cases','cms_verified','device_executed'}, 'Public CMS result closure')
    require(public['schema'] == 'preservation-der-public-v1' and public['status'] == 'PASS' and
        public['cms_verified'] is True and public['device_executed'] is False and
        type(public['cases']) is list and public['cases'] == contract['public_cases'], 'Public exact CMS/Swift cases differ')
    return {'schema':'preservation-cms-portable-admission-v1','status':'PASS',
        'native_identity':dict(native_identity),'source_count':18,'cms_case_count':28,'public_case_count':16,
        'source_contract_sha256':sha(read_bytes(HERE/'cms-source-contract.json')),
        'evidence_sha256':{name:sha(data) for name,data in raw.items()},
        'device_executed':False,'public_profile_bytes_retained':False}
