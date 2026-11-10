"""Dependency-free acceptance for bounded guest diagnostic runtime fixtures."""
def require(ok,why):
    if not ok:raise ValueError(why)
EXPECTED_CASES=('not-started','reset-before-signing','fixed-token-allowlist','bounded-phases','stale-generation-rejected','terminal-immutable','error-classification','provider-data-redacted','completion-forwarded','copy-without-service','reload-before-service-guard','swift-overload-typecheck')
EXPECTED_SOURCES=('LiveContainerSwiftUI/Utilities/LCUtils.m','LiveContainerSwiftUI/Utilities/LCUtils.h','LiveContainerSwiftUI/Models/LCAppModel.swift','LiveContainerSwiftUI/Views/V3UnifiedShell.swift','SideStoreSupport/SideStore.swift','tests/guest_launch_diagnostics/fixture.m','tests/run_guest_launch_diagnostics.py')
EXPECTED_LOGS=('compile.log','runtime.log','copy-objc-compile.log','copy-compile.log','copy-runtime.log','ios-bindings.log')
def validate(data):
    require(data['schema']=='guest-launch-diagnostics-v1' and data['status']=='PASS','Diagnostic fixture did not pass')
    for flag in ('native_compiled','native_executed','ios_swift_typechecked','copy_policy_executed'):require(data[flag] is True,'Diagnostic native stage missing: '+flag)
    require(data['cases']==list(EXPECTED_CASES),'Diagnostic runtime case closure differs')
    hashes=data['source_sha256'];require(EXPECTED_SOURCES and type(hashes) is dict and set(hashes)==set(EXPECTED_SOURCES),'Diagnostic source closure differs')
    return data
