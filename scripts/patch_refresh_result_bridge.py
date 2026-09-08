"""Carry refresh metadata across the existing XPC boundary, not guest preferences."""
from pathlib import Path

MARKER = "LC_REFRESH_RESULT_XPC_V1"


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"refresh result bridge: expected one anchor {old[:80]!r}")
    return text.replace(old, new, 1)


HOST = r'''
    // LC_REFRESH_RESULT_XPC_V1: accept only this outstanding host request.
    func finishRefresh(_ error: String?, runID: String, verification: Data?) {
        guard let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") else {
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
    func reportRefreshResult(_ error: String?, server: any RefreshServer) {
        guard let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") else {
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


def patch(root: Path):
    paths = [root / "SideStoreSupport" / name for name in
             ("XPCServer.h", "XPCClient.m", "SideStore.swift", "SideStoreClient.swift")]
    header, objc, host, client = [p.read_text(encoding="utf-8") for p in paths]
    if MARKER in header:
        assert HOST in host and CLIENT in client and "refreshRunID" in objc
        return
    header = replace(header, "- (void)finish:(NSString*)error;", "- (void)finish:(NSString*)error;\n// LC_REFRESH_RESULT_XPC_V1\n- (void)finishRefresh:(NSString* _Nullable)error runID:(NSString* _Nonnull)runID verification:(NSData* _Nullable)verification NS_SWIFT_NAME(finishRefresh(_:runID:verification:));")
    signature = "mangledTypeName:(NSString *)mangledTypeName"
    header = replace(header, signature + ";", signature + " refreshRunID:(NSString* _Nullable)refreshRunID;")
    objc = replace(objc, signature + " {", signature + " refreshRunID:(NSString* _Nullable)refreshRunID {")
    objc = replace(objc, "    [self performRefreshForRealWithIdentifier:", '''    NSUserDefaults *defaults = [[NSUserDefaults alloc] initWithSuiteName:@"group.com.SideStore.SideStore"];
    [defaults removeObjectForKey:@"liveContainerAutoRefreshVerification"];
    [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoff"];
    [defaults removeObjectForKey:@"liveContainerAutoRefreshHostHandoffRunID"];
    if (refreshRunID.length) [defaults setObject:refreshRunID forKey:@"liveContainerAutoRefreshExpectedRunID"];
    else [defaults removeObjectForKey:@"liveContainerAutoRefreshExpectedRunID"];
    [self performRefreshForRealWithIdentifier:''')
    host = replace(host, "client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName)", 'client.refreshAllApps(withIdentifier: identifier, mangledTypeName: mangledName, refreshRunID: UserDefaults(suiteName: "group.com.SideStore.SideStore")?.string(forKey: "liveContainerAutoRefreshExpectedRunID"))')
    host = replace(host, "    func finish(_ error: String?) {", HOST + "\n    func finish(_ error: String?) {")
    client = replace(client, "    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)", CLIENT + "\n    @objc(performRefreshForRealWithIdentifier:mangledTypeName:server:)")
    client = replace(client, "                server.finish(nil)", "                reportRefreshResult(nil, server: server)")
    client = replace(client, "                server.finish(error.localizedDescription)", "                reportRefreshResult(error.localizedDescription, server: server)")
    for path, text in zip(paths, (header, objc, host, client)):
        path.write_text(text, encoding="utf-8")
