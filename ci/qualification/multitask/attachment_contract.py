"""Dependency-free actual attachment fixture acceptance shared by CI and package audits."""
def require(ok,why):
    if not ok:raise ValueError(why)
LABELS=['swiftui-no-bridge-en','swiftui-no-bridge-he','swiftui-bridge-en','swiftui-bridge-he','negative-opaque-sibling']
UNCHANGED_BRIDGE_HASHES={'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift':'b2c1162a1667ae277baff498148c94bcf7dfb058ae2af6f6f833d553db3e87d9'}
BASELINE_VIRTUAL_SHA='3b31573f5ef5cf7d8d7ceb0ae72869eca97f52609957997174a4d820863bd785'
PRODUCTION_FILES=('LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift','MultitaskSupport/VirtualWindowsHostView.m','MultitaskSupport/VirtualWindowsHostView.h','MultitaskSupport/MultitaskDockView.swift')
FIXTURE_FILES=('GuestBoundaryStubs.h','GuestBoundaryStubs.m','AttachmentFixture.swift','prepare.py','baseline/VirtualWindowsHostView.m')
CORRECTED_LABELS=LABELS[:4]+['corrected-opaque-sibling','same-controller-child-replacement','host-bounds-resize','hidden-then-resume','root-replacement','invalid-hosts']
RESOLVER_EXPECTED=[('typed-host-marker','main'),('native-guest-excluded','none'),('host-with-virtual-guest','main'),('ambiguous-hosts','none'),('detached-marker','none'),('hidden-host','none'),('non-normal-window','none'),('registered-main-session','main'),('missing-registration-marker-fallback','main'),('stale-registration-no-fallback','none'),('background-main-rejected','none'),('foreground-inactive-main','main'),('ambiguous-fallback-rejected','none'),('missing-window-rejected','none')]
SCREENSHOTS=['baseline-swiftui-bridge-en.png','corrected-swiftui-bridge-en.png','baseline-swiftui-bridge-he.png','corrected-swiftui-bridge-he.png']

def validate(data):
    require(data['schema']=='multitask-attachment-qualified-v2' and data['status']=='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH','Actual attachment helper did not pass')
    require(data['physical_guest_execution'] is False,'Fixture cannot claim physical guest execution')
    records=data['records'];require(type(records) is list and [r['case'] for r in records]==LABELS,'Historical observation closure differs')
    for row in records:
        require(row['same_parent_as_production'] is True,'Historical projection differs')
        for name in ('marker_hit','overlay_attached_to_window','first_child_interactive','first_child_hidden'):require(type(row[name]) is bool,'Invalid observation type')
        require(type(row['host_child_count']) is int and row['host_child_count']>=1,'Missing host hierarchy')
    require([r['marker_hit'] for r in records]==[True,True,False,False,False],'Historical bridge regression counterfactual did not reproduce')
    corrected=data['corrected_records'];require(type(corrected) is list and [r['case'] for r in corrected]==CORRECTED_LABELS,'Corrected runtime case closure differs')
    for row in corrected[:5]:
        for name in ('marker_hit','repeated_marker_hit','direct_host_parent','same_guest_identity','single_attachment','bounds_match','guest_explicit_ltr_preserved'):require(row[name] is True,'Corrected attachment invariant failed: '+name)
        require(type(row['bridge_refresh_count']) is int and row['bridge_refresh_count']>=0,'Invalid bridge execution count')
        if row['case'].startswith('swiftui-bridge-'):require(row['bridge_refresh_count']>0,'Actual language bridge was not exercised')
    for row,names in zip(corrected[5:],[('marker_hit','same_guest_identity','direct_host_parent'),('marker_hit','same_guest_identity','bounds_match'),('marker_hit','same_guest_identity','hidden_state_preserved_by_helper','caller_unhid_marker_only'),('marker_hit','same_guest_identity','old_parent_released','guest_explicit_ltr_preserved','bounds_match'),('marker_hit','detached_rejected','empty_rejected','same_guest_identity','original_parent_preserved')]):
        for name in names:require(row[name] is True,'Lifecycle helper invariant failed: '+name)
    require(corrected[8]['scope']=='helper_marker_only_not_live_controller_migration','Root replacement scope differs')
    resolver=data['resolver_records'];require(type(resolver) is list and len(resolver)==len(RESOLVER_EXPECTED),'Resolver case count differs')
    for row,(name,expected) in zip(resolver,RESOLVER_EXPECTED):require(row=={'case':name,'selected_expected':True,'expected':expected} and row['selected_expected'] is True,'Resolver ownership case failed')
    require(data['scene_provider_scope']=='projected_inputs_real_uiwindows','Scene provider scope differs')
    require(data['screenshots']==SCREENSHOTS,'Synthetic visual evidence closure differs')
    return data

# Prospective exact-owned cleanup evidence. Portable: standard library only.
CLEANUP_SCHEMA='owned-simulator-cleanup-v1'
CLEANUP_SOURCE_FILES=('run_attachment.py','owned_simulator_cleanup.py','attachment_contract.py','test_owned_simulator_cleanup.py')

def cleanup_source_path(name):
    """Resolve the closed source/test closure without assuming one flat folder."""
    from pathlib import Path
    require(name in CLEANUP_SOURCE_FILES, 'Unknown cleanup source')
    here = Path(__file__).resolve().parent
    if name == 'test_owned_simulator_cleanup.py':
        return here.parents[2]/'tests/ci/multitask'/name
    return here/name
def _uuid(value):
    import uuid
    require(type(value) is str,'Invalid simulator UUID type')
    try:canonical=str(uuid.UUID(value)).upper()
    except (ValueError,AttributeError):raise ValueError('Invalid simulator UUID')
    require(value.upper()==canonical,'Noncanonical simulator UUID');return canonical

def _strict_pairs(rows):
    result={}
    for key,value in rows:require(key not in result,'Duplicate inventory key');result[key]=value
    return result

def owned_absent(inventory_json,owned_uuid,owned_runtime):
    import json
    owner=_uuid(owned_uuid)
    require(type(inventory_json) is str and 0<len(inventory_json.encode())<=8*1024*1024,'Invalid inventory size')
    def invalid_constant(value):raise ValueError('Non-JSON numeric constant')
    data=json.loads(inventory_json,object_pairs_hook=_strict_pairs,parse_constant=invalid_constant)
    require(type(data) is dict and set(data)=={'devices'} and type(data['devices']) is dict,'Incomplete unfiltered device inventory')
    require(owned_runtime in data['devices'],'Created runtime missing from inventory')
    seen=set()
    for runtime,rows in data['devices'].items():
        prefix='com.apple.CoreSimulator.SimRuntime.'
        require(type(runtime) is str and runtime.startswith(prefix) and len(runtime)>len(prefix) and type(rows) is list,'Unknown runtime or malformed device list')
        for row in rows:
            required={'udid','state','name','isAvailable','deviceTypeIdentifier'}
            optional={'dataPath','dataPathSize','logPath','availability','availabilityError','lastBootedAt','lastBootedTime'}
            require(type(row) is dict and required<=set(row)<=required|optional,'Unknown or malformed device record')
            udid=_uuid(row['udid']);require(udid not in seen,'Duplicate simulator UUID');seen.add(udid)
            prefix='com.apple.CoreSimulator.SimDeviceType.'
            require(type(row['deviceTypeIdentifier']) is str and row['deviceTypeIdentifier'].startswith(prefix) and len(row['deviceTypeIdentifier'])>len(prefix) and type(row['isAvailable']) is bool,'Unknown device type/availability')
            require(type(row['name']) is str and bool(row['name']),'Missing simulator name')
            require(type(row['state']) is str and row['state'] in ('Shutdown','Booted','Booting','Shutting Down','Creating','Deleting'),'Unknown simulator state')
            for key in optional & set(row):
                if key=='dataPathSize':require(type(row[key]) is int and row[key]>=0,'Invalid device size')
                else:require(type(row[key]) is str,'Invalid device metadata')
    # Parse every runtime and record; unrelated valid devices may remain.
    return owner not in seen

def validate_cleanup_receipt(report):
    import hashlib,re
    c=report['cleanup'];require(c['schema']==CLEANUP_SCHEMA,'Cleanup schema differs')
    require(set(c)=={'schema','ownership','actions','readback','status'},'Cleanup fields differ')
    o=c['ownership'];require(set(o)=={'udid','create_argv','create_exit_code','create_stdout','create_stdout_sha256'},'Ownership fields differ')
    owner=_uuid(o['udid']);require(owner==_uuid(report['simulator']['udid']),'Cleanup owner differs from created simulator')
    expected=['xcrun','simctl','create','NRG-Multitask-'+report['identity']['run_id'],report['simulator']['device_type']['identifier'],report['simulator']['runtime']['identifier']]
    require(o['create_argv']==expected and type(o['create_exit_code']) is int and o['create_exit_code']==0,'Successful creation provenance missing')
    require(type(o['create_stdout']) is str and len(o['create_stdout'])<=128 and _uuid(o['create_stdout'].strip())==owner,'Creation result/owner mismatch')
    require(hashlib.sha256(o['create_stdout'].encode()).hexdigest()==o['create_stdout_sha256'],'Creation result hash differs')
    require(report['observation_status']=='PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH','Cleanup cannot rescue failed attachment observations')
    def outcome(row,args,timeout):
        require(set(row)=={'argv','timeout_seconds','elapsed_ms','outcome','returncode','reaped','log','log_sha256'},'Command outcome fields differ')
        require(row['argv']==args and type(row['timeout_seconds']) is int and row['timeout_seconds']==timeout,'Cleanup command scope/bound differs')
        require(type(row['elapsed_ms']) is int and 0<=row['elapsed_ms']<=(timeout+6)*1000,'Invalid command duration')
        require(type(row['reaped']) is bool and re.fullmatch('[0-9a-f]{64}',row['log_sha256']) is not None,'Missing process/log outcome')
        code=row['returncode'];kind=row['outcome'];require(code is None or type(code) is int,'Invalid return code')
        require(kind in ('success','nonzero','timeout','spawn_error','unreaped'),'Unknown command outcome')
        require((kind=='success' and code==0 and row['reaped']) or (kind=='nonzero' and type(code) is int and code!=0 and row['reaped']) or (kind=='timeout' and type(code) is int and row['reaped']) or (kind=='spawn_error' and code is None and row['reaped']) or (kind=='unreaped' and code is None and not row['reaped']),'Inconsistent command outcome')
        return kind=='success'
    require(type(c['actions']) is list and len(c['actions'])==2,'Cleanup action count differs')
    successful=[]
    for row,action in zip(c['actions'],('shutdown','delete')):
        successful.append(outcome(row,['xcrun','simctl',action,owner],45))
        require(row['log']==action+'-cleanup.log','Cleanup log path differs')
    require(all(row['reaped'] for row in c['actions']),'Unreaped cleanup process')
    if all(successful):
        require(c['readback'] is None and c['status']=='completeByCommand','Unexpected cleanup readback or status')
    else:
        r=c['readback'];require(type(r) is dict and set(r)=={'command','owned_uuid','inventory_json','inventory_sha256'},'Missing bounded readback')
        require(_uuid(r['owned_uuid'])==owner,'Readback queried/claimed wrong owner')
        require(outcome(r['command'],['xcrun','simctl','list','devices','--json'],10),'Inventory command did not succeed')
        require(r['command']['log']=='cleanup-final-inventory.json','Inventory log path differs')
        digest=hashlib.sha256(r['inventory_json'].encode()).hexdigest()
        require(digest==r['inventory_sha256']==r['command']['log_sha256'],'Inventory evidence hash differs')
        require(owned_absent(r['inventory_json'],owner,o['create_argv'][5]) and c['status']=='completeByAbsence','Owned simulator not proven absent')
    require(set(report['cleanup_source_sha256'])==set(CLEANUP_SOURCE_FILES),'Cleanup source closure differs')
    require(all(re.fullmatch('[0-9a-f]{64}',value) for value in report['cleanup_source_sha256'].values()),'Invalid cleanup source digest')
    return c
