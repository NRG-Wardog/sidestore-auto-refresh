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
