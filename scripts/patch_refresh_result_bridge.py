"""Carry refresh metadata across the existing XPC boundary, not guest preferences."""
from pathlib import Path
import re
import importlib.util

MARKER = "LC_REFRESH_RESULT_XPC_V1"
TEMPLATES = Path(__file__).parent / "templates"


def runtime_app_group_environment_key() -> str:
    """The one key the host publishes and every process reads.

    Generated Objective-C cannot see the Swift constant, so the literal is
    repeated in the emitters. Deriving it from the authoritative template means a
    rename fails here instead of silently splitting the two halves of one
    process-boundary contract.
    """
    shared = (TEMPLATES / "v3_shared_app_group.swift").read_text(encoding="utf-8")
    match = re.search(r'static let runtimeGroupEnvironmentKey = "([A-Za-z0-9_]+)"', shared)
    if not match:
        raise SystemExit("refresh result bridge: the runtime App Group environment key moved")
    return match.group(1)


RUNTIME_APP_GROUP_ENV_KEY = runtime_app_group_environment_key()


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"refresh result bridge: expected one anchor {old[:80]!r}")
    return text.replace(old, new, 1)


HOST = r'''
    // LC_REFRESH_RESULT_XPC_V1: accept only this outstanding host request.
    // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the expected run ID, the verification
    // manifest and the host-handoff record are the host/service contract, so
    // they are read from the one runtime App Group the host published. A fixed
    // suite name silently reads a store the embedded service never writes.
    func finishRefresh(_ error: String?, runID: String, verification: Data?) {
        guard let defaults = V3SharedAppGroup.sharedUserDefaults() else {
            finish("LiveContainer could not open its refresh-state store. Refresh is unconfirmed.")
            return
        }
        guard c != nil, defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") == runID else {
            NSLog("[LIVE_CONTAINER_REFRESH] RESULT_REJECTED reason=stale_run")
            return
        }
        if let verification, verification.count <= 262144 {
            do {
                let object = try PropertyListSerialization.propertyList(from: verification, options: [], format: nil)
                guard let payload = object as? [String: Any],
                      let manifest = payload["liveContainerAutoRefreshVerification"] as? [String: Any],
                      manifest["run_id"] as? String == runID else {
                    finish(error ?? "SideStore returned results for a different refresh attempt. Open SideStore and check its refresh history.")
                    return
                }
                defaults.set(manifest, forKey: "liveContainerAutoRefreshVerification")
                if payload["liveContainerAutoRefreshHostHandoffRunID"] as? String == runID {
                    for key in ["liveContainerAutoRefreshHostHandoff", "liveContainerAutoRefreshHostHandoffRunID", "liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
                        if let value = payload[key] { defaults.set(value, forKey: key) }
                    }
                }
                NSLog("[LIVE_CONTAINER_REFRESH] RESULT_RECEIVED run_id=%@", runID)
            } catch {
                finish("SideStore's installation results could not be read. Check its refresh history before retrying.")
                return
            }
        } else if error == nil {
            finish("SideStore returned no readable installation results. Refresh is unconfirmed; check embedded SideStore history.")
            return
        }
        finish(error)
    }
'''

CLIENT = r'''
    // LC_REFRESH_RESULT_XPC_V1: bounded, allowlisted non-secret metadata only.
    // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the same runtime App Group the service
    // wrote the manifest into. Without it the result is reported unconfirmed
    // rather than read from a store the service can never have written.
    func reportRefreshResult(_ error: String?, server: any RefreshServer) {
        guard let defaults = V3SharedAppGroup.sharedUserDefaults() else {
            server.finish("SideStore could not open its refresh-state store. Refresh is unconfirmed.")
            return
        }
        guard let runID = defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") else {
            server.finish(error)
            return
        }
        var payload: [String: Any] = [:]
        for key in ["liveContainerAutoRefreshVerification", "liveContainerAutoRefreshHostHandoff", "liveContainerAutoRefreshHostHandoffRunID", "liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
            if let value = defaults.object(forKey: key) { payload[key] = value }
        }
        do {
            let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .binary, options: 0)
            guard data.count <= 262144 else {
                server.finishRefresh("SideStore's verification results exceeded the allowed size.", runID: runID, verification: nil)
                return
            }
            server.finishRefresh(error, runID: runID, verification: data)
        } catch {
            server.finishRefresh("SideStore could not encode installation results: " + error.localizedDescription, runID: runID, verification: nil)
        }
    }
'''



# Exact successor of the host XPC dispatch; never silently accept partial migration.
DIRECT_HOST_METHOD = r'''- (void)refreshAllAppsWithIdentifier:(NSString*)identifier mangledTypeName:(NSString *)mangledTypeName refreshRunID:(NSString* _Nullable)refreshRunID {
    // Uncorrelated legacy dispatch is never permitted to claim a host admission.
    if (handler) [handler.server finish:@"Host refresh requires an admission identity."];
}

- (void)refreshAllAppsWithIdentifier:(NSString*)identifier mangledTypeName:(NSString *)mangledTypeName refreshRunID:(NSString* _Nonnull)refreshRunID admissionID:(NSString* _Nonnull)admissionID {
    if(!handler) {
        return;
    }
    // V3_DIRECT_HOST_REFRESH_V1: only the service with the explicit admission
    // may clear state or start the canonical backend. No private AppIntent run.
    Class<V3CommandService> service = (Class<V3CommandService>)NSClassFromString(@"V3SideStoreService");
    if (refreshRunID.length && admissionID.length && service &&
        [(id)service respondsToSelector:@selector(refreshForHost:admissionID:progress:completion:)]) {
        id<RefreshServer> server = handler.server;
        NSString *runID = [refreshRunID copy];
        NSString *dispatchAdmissionID = [admissionID copy];
        [service refreshForHost:runID admissionID:dispatchAdmissionID progress:^(double value) {
            [server updateHostProgress:value runID:runID admissionID:dispatchAdmissionID];
        } completion:^(NSString *error, NSData *verification) {
            [server finishHostRefresh:error runID:runID admissionID:dispatchAdmissionID verification:verification];
        }];
    } else {
        [self reportHostRefreshUnavailable:refreshRunID ?: @"" admissionID:admissionID ?: @"" server:handler.server];
    }
}'''
DIRECT_HOST_PROTOCOL = r'''@protocol V3CommandService
+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;
+ (void)refreshForHost:(NSString *)runID
           admissionID:(NSString *)admissionID
              progress:(void (^)(double))progress
            completion:(void (^)(NSString * _Nullable, NSData * _Nullable))completion;
@end'''
DIRECT_HOST_FAILURE = r'''    @objc(reportHostRefreshUnavailable:admissionID:server:)
    func reportHostRefreshUnavailable(_ runID: String, admissionID: String, server: any RefreshServer) {
        let failure = CombinedFailure(operation: "refresh", stage: .serviceReadiness,
            code: .unavailable, id: runID, retryable: false)
        server.finishHostRefresh(failure.encodedString, runID: runID, admissionID: admissionID, verification: nil)
    }'''

LEGACY_HOST_METHOD = r'''- (void)refreshAllAppsWithIdentifier:(NSString*)identifier mangledTypeName:(NSString *)mangledTypeName refreshRunID:(NSString* _Nullable)refreshRunID {
    if(!handler) {
        return;
    }
    // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the run ID, the previous verification
    // manifest and the host-handoff record form the host/service contract, so
    // they are cleared and stamped in the one runtime App Group the host
    // published. A fixed suite name would clear a store the embedded service
    // never writes, leaving the previous run's manifest to match a new run.
    // +[NSUserDefaults initWithSuiteName:] accepts any name and silently writes
    // the process's own domain when the suite is not a group this build is
    // entitled to, so the container is proven openable first. Otherwise the
    // stamps would land where the service can never read them.
    const char *runtimeAppGroup = getenv("__RUNTIME_APP_GROUP_ENV_KEY__");
    NSString *runtimeAppGroupID = (runtimeAppGroup != NULL && runtimeAppGroup[0] != '\0')
        ? [NSString stringWithUTF8String:runtimeAppGroup] : nil;
    BOOL runtimeAppGroupOpenable = runtimeAppGroupID.length > 0 &&
        [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:runtimeAppGroupID] != nil;
    NSUserDefaults *defaults = runtimeAppGroupOpenable
        ? [[NSUserDefaults alloc] initWithSuiteName:runtimeAppGroupID] : nil;
    if (defaults == nil) {
        NSLog(@"[LIVE_CONTAINER_REFRESH] RESULT_STORE_UNAVAILABLE reason=runtime_app_group_unresolved");
    } else {
        [defaults removeObjectForKey:@"liveContainerAutoRefreshVerification"];
        [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoff"];
        [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoffRunID"];
        if (refreshRunID.length) [defaults setObject:refreshRunID forKey:@"liveContainerAutoRefreshExpectedRunID"];
        else [defaults removeObjectForKey:@"liveContainerAutoRefreshExpectedRunID"];
    }
    [self performRefreshForRealWithIdentifier:identifier mangledTypeName:mangledTypeName server:handler.server];
}'''.replace("__RUNTIME_APP_GROUP_ENV_KEY__", RUNTIME_APP_GROUP_ENV_KEY)

def install_direct_host_objc(text: str) -> str:
    if "V3_DIRECT_HOST_REFRESH_V1" in text:
        if text.count(DIRECT_HOST_METHOD) != 1 or text.count(DIRECT_HOST_PROTOCOL) != 1:
            raise SystemExit("refresh result bridge: partial direct host dispatch")
        return text
    text = replace(text, LEGACY_HOST_METHOD, DIRECT_HOST_METHOD)
    legacy_protocol = "@protocol V3CommandService\n+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;\n@end"
    text = replace(text, legacy_protocol, DIRECT_HOST_PROTOCOL)
    category = "                                     server:(id <RefreshServer> _Nonnull)server;"
    text = replace(text, category, category + "\n- (void)reportHostRefreshUnavailable:(NSString *)runID admissionID:(NSString *)admissionID server:(id<RefreshServer>)server;")
    return text


def install_direct_host_client(text: str) -> str:
    if "@objc(reportHostRefreshUnavailable:" in text:
        if text.count(DIRECT_HOST_FAILURE) != 1:
            raise SystemExit("refresh result bridge: partial direct host failure bridge")
        return text
    anchor = "    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)"
    return replace(text, anchor, DIRECT_HOST_FAILURE + "\n\n" + anchor)


def callback_admission_module():
    path = (Path(__file__).parent / "patch_refresh_callback_admission.py")
    spec = importlib.util.spec_from_file_location("v3_refresh_callback_admission", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def install_direct_host_header(text: str) -> str:
    return callback_admission_module().install_header(text)


def validate_direct_host_bridge(objc: str, host: str, client: str) -> None:
    callback_admission_module().validate_host(host)
    for fragment, value in ((DIRECT_HOST_METHOD, objc), (DIRECT_HOST_PROTOCOL, objc),
                            (DIRECT_HOST_FAILURE, client)):
        if value.count(fragment) != 1:
            raise SystemExit("refresh result bridge: direct host dispatch differs from the exact reviewed successor")
    # The receiving host still owns stale-run, payload-size and terminal checks.
    for guard in ('fileprivate func completedRefresh(_ error: String?, runID: String, verification: Data?, id: UUID)',
                  'guard launchID == id, refreshContinuation != nil, refreshRunID == runID else { return }',
                  'verification.count <= 262144',
                  'CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID)',
                  'finishRefreshContinuation(.failure(failure))'):
        if guard not in host:
            raise SystemExit("refresh result bridge: direct host receiver boundary missing")


def patch(root: Path):
    paths = [root / "SideStoreSupport" / name for name in
             ("XPCServer.h", "XPCClient.m", "SideStore.swift", "SideStoreClient.swift")]
    header, objc, host, client = [p.read_text(encoding="utf-8") for p in paths]
    if MARKER in header:
        if "V3_DIRECT_HOST_REFRESH_V1" in objc:
            callback_admission_module().validate_header(header)
            validate_direct_host_bridge(objc, host, client)
        else:
            assert HOST in host and CLIENT in client and "refreshRunID" in objc
        return
    header = replace(header, "- (void)finish:(NSString*)error;", "- (void)finish:(NSString*)error;\n// LC_REFRESH_RESULT_XPC_V1\n- (void)finishRefresh:(NSString* _Nullable)error runID:(NSString* _Nonnull)runID verification:(NSData* _Nullable)verification NS_SWIFT_NAME(finishRefresh(_:runID:verification:));")
    signature = "mangledTypeName:(NSString *)mangledTypeName"
    header = replace(header, signature + ";", signature + " refreshRunID:(NSString* _Nullable)refreshRunID;")
    objc = replace(objc, signature + " {", signature + " refreshRunID:(NSString* _Nullable)refreshRunID {")
    objc = replace(objc, "    [self performRefreshForRealWithIdentifier:", f'''    // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the run ID, the previous verification
    // manifest and the host-handoff record form the host/service contract, so
    // they are cleared and stamped in the one runtime App Group the host
    // published. A fixed suite name would clear a store the embedded service
    // never writes, leaving the previous run's manifest to match a new run.
    // +[NSUserDefaults initWithSuiteName:] accepts any name and silently writes
    // the process's own domain when the suite is not a group this build is
    // entitled to, so the container is proven openable first. Otherwise the
    // stamps would land where the service can never read them.
    const char *runtimeAppGroup = getenv("{RUNTIME_APP_GROUP_ENV_KEY}");
    NSString *runtimeAppGroupID = (runtimeAppGroup != NULL && runtimeAppGroup[0] != '\\0')
        ? [NSString stringWithUTF8String:runtimeAppGroup] : nil;
    BOOL runtimeAppGroupOpenable = runtimeAppGroupID.length > 0 &&
        [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:runtimeAppGroupID] != nil;
    NSUserDefaults *defaults = runtimeAppGroupOpenable
        ? [[NSUserDefaults alloc] initWithSuiteName:runtimeAppGroupID] : nil;
    if (defaults == nil) {{
        NSLog(@"[LIVE_CONTAINER_REFRESH] RESULT_STORE_UNAVAILABLE reason=runtime_app_group_unresolved");
    }} else {{
        [defaults removeObjectForKey:@"liveContainerAutoRefreshVerification"];
        [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoff"];
        [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoffRunID"];
        if (refreshRunID.length) [defaults setObject:refreshRunID forKey:@"liveContainerAutoRefreshExpectedRunID"];
        else [defaults removeObjectForKey:@"liveContainerAutoRefreshExpectedRunID"];
    }}
    [self performRefreshForRealWithIdentifier:''')
    host = replace(host, "client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName)", 'client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName, refreshRunID: V3SharedAppGroup.sharedUserDefaults()?.string(forKey: "liveContainerAutoRefreshExpectedRunID"))')
    host = replace(host, "    func finish(_ error: String?) {", HOST + "\n    func finish(_ error: String?) {")
    client = replace(client, "    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)", CLIENT + "\n    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)")
    client = replace(client, "                server.finish(nil)", "                reportRefreshResult(nil, server: server)")
    client = replace(client, "                server.finish(error.localizedDescription)", "                reportRefreshResult(error.localizedDescription, server: server)")
    for path, text in zip(paths, (header, objc, host, client)):
        path.write_text(text, encoding="utf-8")
