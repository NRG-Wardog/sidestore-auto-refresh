"""Shared combined-only storage/startup/error adapters; pinned and transactional."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

TEMPLATES = Path(__file__).with_name("templates")
PINS = ("12377cf3b91d51739a33f14a302e5f522b238593", "ff25922e5c13ccfafd83bda5092910d848ebd409")
MARKER = "LC_SERVICE_CONNECTION_V1"
OUTPUTS = {(0, name) for name in ("SideStoreSupport/SideStore.swift", "LiveContainer/LCContainerStorage.h",
    "LiveContainer/LCBootstrap.m", "SideStoreSupport/XPCServer.h", "SideStoreSupport/XPCServer.m",
    "SideStoreSupport/SideStoreClient.swift", "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift")} | {
    (1, "AltStore/AppDelegate.swift"), (1, "SideStore/Core/Operations/PipelineExecutor.swift")}


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("combined startup anchor drift: " + old[:90])
    return text.replace(old, new, 1)


def patch(live, side, product):
    if product not in ("v2", "v3"):
        raise SystemExit("expected v2 or v3")
    for root, pin in zip((live, side), PINS):
        if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != pin:
            raise SystemExit("combined startup requires pinned source")
    manifest = live / ".combined-service-startup.json"
    templates = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in TEMPLATES.glob("combined_*")}
    templates["patch_combined_service_startup.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous["templates"] != templates or previous["product"] != product:
            raise SystemExit("combined startup templates changed; use fresh pinned sources")
        if (previous.get("pins") != list(PINS) or len(previous["files"]) != len(OUTPUTS)
                or {(i, name) for i, name, _ in previous["files"]} != OUTPUTS):
            raise SystemExit("combined startup manifest source/output drift")
        for index, relative, digest in previous["files"]:
            if hashlib.sha256(((live, side)[index] / relative).read_bytes()).hexdigest() != digest:
                raise SystemExit("combined startup replay drift: " + relative)
        return
    changes = {}
    def edit(root, relative, transform):
        path = root / relative
        changes[path] = transform(changes.get(path, path.read_text(encoding="utf-8") if path.exists() else ""))
    def template(name):
        return (TEMPLATES / name).read_text(encoding="utf-8")
    def host(text):
        text = replace(text, 'return .result(dialog: "All apps have been refreshed.")',
            'return .result(dialog: "Refresh request completed. Check Refresh for verified installation results.")')
        text = text.replace("        RefreshHandler.shared.progress = intentProgress", "        await MainActor.run { RefreshHandler.shared.progress = intentProgress }")
        start = text.index("class RefreshHandler:")
        if text[max(0, start-11):start] == "@MainActor\n": start -= 11
        brace = text.index("{", start)
        depth = 1; end = brace + 1
        while depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        handler = template("combined_refresh_handler.swift")
        handler = handler.replace("/*MUTATION_GUARD*/", ", !V3ServiceBridge.shared.isMutating" if product == "v3" else "")
        handler = handler.replace("/*DISCONNECTED*/", "V3ServiceBridge.shared.disconnected()" if product == "v3" else "")
        handler = handler.replace("/*REFRESH_READINESS*/", '''
        let status = try await V3ServiceBridge.shared.request(operation: "snapshot")
        guard status["busy"] as? Bool == false else {
            throw CombinedFailure(operation: "refresh", stage: .serviceReadiness, code: .busy, id: token.uuidString, retryable: true)
        }''' if product == "v3" else "")
        handler = handler.replace("/*SERVICE_PROBE*/", '''
        let until = Date().addingTimeInterval(30)
        var ready = false
        var pending = false
        var invalid = false
        var lastSnapshotError = ""
        while Date() < until {
            try Task.checkCancellation()
            guard launchID == id else { throw CancellationError() }
            if ready {
                NSLog("[V3_SERVICE_START] SNAPSHOT_READY id=%@", id.uuidString)
                return
            }
            if invalid {
                NSLog("[V3_SERVICE_START] READINESS_INVALID_RESPONSE id=%@ error=%@", id.uuidString, lastSnapshotError)
                throw CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .invalidResponse, id: id.uuidString)
            }
            if !pending, let client {
                let requestID = UUID().uuidString
                let message: [String: Any] = ["version": 1, "id": requestID, "operation": "snapshot", "target": "", "deadline": Date().addingTimeInterval(30)]
                let data = try PropertyListSerialization.data(fromPropertyList: message, format: .binary, options: 0)
                pending = true
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        guard self.launchID == id else { return }
                        pending = false
                        guard response.count <= V3WireContract.responseLimit,
                              let result = try? PropertyListSerialization.propertyList(from: response, format: nil) as? [String: Any],
                              result["id"] as? String == requestID else { invalid = true; return }
                        if let replyError = result["error"] as? String { lastSnapshotError = replyError }
                        else if result["ok"] as? Bool != true { lastSnapshotError = "missing-ok" }
                        ready = result["ok"] as? Bool == true
                    }
                }
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        NSLog("[V3_SERVICE_START] READINESS_TIMEOUT id=%@ lastError=%@", id.uuidString, lastSnapshotError)
        throw CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .timedOut, id: id.uuidString, retryable: true)
''' if product == "v3" else '''
        // v2 has no command catalog. App launch readiness is distinct from database readiness,
        // which remains owned by the subsequent explicit refresh intent.
        try Task.checkCancellation()
''')
        return text[:start] + template("combined_failure.swift") + template("combined_service_connection.swift") + handler + text[end:]
    edit(live, "SideStoreSupport/SideStore.swift", host)
    edit(live, "LiveContainer/LCContainerStorage.h", lambda _: template("combined_container_storage.h"))
    def bootstrap(text):
        old = '''    NSArray *dirList = @[@"Library/Caches", @"Library/Cookies", @"Documents", @"SystemData"];
    for (NSString *dir in dirList) {
        NSString *dirPath = [newHomePath stringByAppendingPathComponent:dir];
        [fm createDirectoryAtPath:dirPath withIntermediateDirectories:YES attributes:nil error:nil];
    }'''
        return '#import "LCContainerStorage.h"\n' + replace(text, old, '''    if (!LCPrepareContainerDirectories(newHomePath, &error)) {
        return @"The application container directories could not be prepared. Existing data was preserved.";
    }''')
    edit(live, "LiveContainer/LCBootstrap.m", bootstrap)
    edit(live, "SideStoreSupport/XPCServer.h", lambda s: s + '''
// LC_SERVICE_CONNECTION_V1: preserve NSError and nullable launch results.
@class NSExtension;
BOOL LCPrepareServiceStorage(NSURL * _Nonnull url, NSError * _Nullable * _Nullable error) __attribute__((swift_error(none)));
NSData * _Nullable LCCreateServiceBookmark(NSURL * _Nonnull url, NSError * _Nullable * _Nullable error) __attribute__((swift_error(none)));
void LCLaunchServiceExtension(NSExtension * _Nonnull extension, NSExtensionItem * _Nonnull item,
    void (^ _Nonnull completion)(NSUUID * _Nullable identifier, NSError * _Nullable error));
''')
    edit(live, "SideStoreSupport/XPCServer.m", lambda s: '#import "../LiveContainer/FoundationPrivate.h"\n#import "../LiveContainer/LCContainerStorage.h"\n' + s + '''
BOOL LCPrepareServiceStorage(NSURL *url, NSError **error) {
    return LCPrepareContainerDirectories(url.path, error);
}
NSData *LCCreateServiceBookmark(NSURL *url, NSError **error) {
    return [url bookmarkDataWithOptions:(1<<11) includingResourceValuesForKeys:nil relativeToURL:nil error:error];
}
void LCLaunchServiceExtension(NSExtension *extension, NSExtensionItem *item, void (^completion)(NSUUID *, NSError *)) {
    [extension beginExtensionRequestWithInputItems:@[item] completion:^(NSUUID *identifier) {
        completion(identifier, identifier ? nil : [NSError errorWithDomain:NSCocoaErrorDomain code:NSExecutableLoadError userInfo:nil]);
    }];
}
''')
    def client(text):
        text = replace(text, '            let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .binary, options: 0)',
            '            payload = CombinedVerification.sanitized(payload, runID: runID)\n            let data = try PropertyListSerialization.data(fromPropertyList: payload, format: .binary, options: 0)')
        text = replace(text, '"SideStore could not encode installation results: " + error.localizedDescription',
            'CombinedFailure.capture(error, operation: "refresh", stage: .refreshVerification, id: runID).encodedString')
        for old in ['reportRefreshResult(error.localizedDescription, server: server)',
                    'reportRefreshResult("SideStore refresh failed. Check account, pairing and operation diagnostics.", server: server)']:
            if old in text:
                text = replace(text, old, 'reportStructuredRefreshFailure(error, server: server)')
                break
        else: raise SystemExit("structured refresh failure anchor missing")
        return text + '''
@available(iOS 17.0, *)
extension SideStoreClient {
    func reportStructuredRefreshFailure(_ error: Error, server: any RefreshServer) {
        let id = UserDefaults(suiteName: "group.com.SideStore.SideStore")?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? UUID().uuidString
        reportRefreshResult(CombinedFailure.capture(error, operation: "refresh", stage: .command, id: id).encodedString, server: server)
    }
}
'''
    edit(live, "SideStoreSupport/SideStoreClient.swift", client)
    edit(side, "AltStore/AppDelegate.swift", lambda s: s + template("combined_failure.swift"))
    edit(side, "SideStore/Core/Operations/PipelineExecutor.swift", lambda s: replace(s,
        "            result = error\n            throw error", '''            result = error
            // LC_STRUCTURED_FAILURE_V1: preserve step responsibility and the underlying error.
            var stage: String
            switch step {
            case .resignApp, .fetchProvisioningProfiles, .verifyCertificate: stage = "signing"
            case .sendApp, .installApp: stage = "installation"
            default: stage = "command"
            }
            if let operationError = error as? OperationError, operationError == .notAuthenticated { stage = "authentication" }
            if let portalError = error as? DeveloperPortalError {
                switch portalError {
                case .incorrectCredentials, .appSpecificPasswordRequired, .requiresTwoFactorAuthentication,
                     .incorrectVerificationCode, .authenticationHandshakeFailed, .invalidAnisetteData,
                     .tooManyAttempts, .accountRepairRequired, .invalid2FAResponse: stage = "authentication"
                default: break
                }
            }
            let native = error as NSError
            throw NSError(domain: native.domain, code: native.code,
                userInfo: ["LCStructuredFailureStageV1": stage, NSUnderlyingErrorKey: native,
                           NSLocalizedDescriptionKey: native.localizedDescription])'''))
    edit(live, "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift", lambda s: replace(s,
        "                if sharedModel.developerMode {", '''                Section("Build Candidate") {
                    Text("Product: " + (Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String ?? "unknown"))
                    Text(Bundle.main.object(forInfoDictionaryKey: "LCBuilderCommit") as? String ?? "unknown commit").font(.caption).textSelection(.enabled)
                    Button("Copy Build Diagnostics") {
                        UIPasteboard.general.string = ["LCProductLine", "LCBuilderCommit", "LCBuildRunURL"].map {
                            $0 + "=" + (Bundle.main.object(forInfoDictionaryKey: $0) as? String ?? "unknown")
                        }.joined(separator: "\\n")
                    }
                }

                if sharedModel.developerMode {'''))
    records = []
    for path, text in changes.items():
        index = 0 if live in path.parents else 1
        records.append([index, path.relative_to((live, side)[index]).as_posix(), hashlib.sha256(text.encode()).hexdigest()])
    for path, text in changes.items(): path.write_bytes(text.encode())
    manifest.write_text(json.dumps({"templates": templates, "product": product, "pins": PINS, "files": records}, indent=2))


def patch_transport(root):
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != "98c3c79982f813878e922ab42f9545314a700f0c":
        raise SystemExit("transport diagnostics require pinned minimuxer")
    path = root / "DeviceGateway/idevice/IdeviceGateway.swift"
    text = path.read_text(encoding="utf-8")
    marker = root / ".combined-errors.sha256"
    patch_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if marker.exists():
        if marker.read_text() != patch_digest + ":" + hashlib.sha256(path.read_bytes()).hexdigest():
            raise SystemExit("transport diagnostic replay drift")
        return
    stages = {
        "CoreDevice provider creation failed:": "coreDevice",
        "CoreDevice provider creation returned nil": "coreDevice",
        "CoreDevice tunnel failed:": "cdTunnel",
        "CoreDevice tunnel returned incomplete handles": "cdTunnel",
        "CoreDevice transport returned incomplete handles": "coreDevice",
        "CoreDevice heartbeat is inactive": "heartbeat",
        "Lockdownd RSD connection failed": "rsdService",
        "Lockdownd client is nil after connect": "lockdownConnection",
        "Querying UniqueDeviceID failed": "uniqueDeviceID",
        "UniqueDeviceID plist value is nil": "uniqueDeviceID",
        "UniqueDeviceID string is empty": "uniqueDeviceID",
        "Lockdown pairing parse failed:": "pairing",
    }
    for fragment, stage in stages.items():
        if fragment not in text: raise SystemExit("missing native failure anchor: " + fragment)
        text = text.replace('reason: "' + fragment, 'reason: "lc_stage=' + stage + ' ' + fragment)
    text = text.replace("throw IdeviceGatewayError(.deviceEndpointIpNotAvailable)",
        'throw IdeviceGatewayError(.deviceEndpointIpNotAvailable, reason: "lc_stage=endpointSelection Endpoint unavailable")')
    for fragment in ("CoreDevice tunnel failed:", "Lockdownd RSD connection failed", "Querying UniqueDeviceID failed"):
        text = text.replace(" " + fragment, " lc_native_code=\\(code) " + fragment)
    text = replace(text, 'lc_stage=cdTunnel lc_native_code=\\(code) CoreDevice tunnel failed:',
        'lc_stage=\\(lcTransportFailureStage(message)) lc_native_code=\\(code) CoreDevice tunnel failed:')
    text += '''
// LC_NATIVE_STAGE_V1: inspect known pinned Rust failure labels locally, never export raw descriptions.
private func lcTransportFailureStage(_ message: String) -> String {
    if message.contains("RSD connect") || message.contains("RSD handshake") { return "rsdDiscovery" }
    if message.contains("heartbeat") { return "heartbeat" }
    if message.contains("software tunnel") || message.contains("CDTunnel") { return "cdTunnel" }
    return "coreDevice"
}
'''
    # The old wrappers still receive typed errors, not nil, and the FFI code is read before free.
    text += "\n// LC_STRUCTURED_FAILURE_V1\n"
    path.write_bytes(text.encode())
    marker.write_text(patch_digest + ":" + hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--transport":
        patch_transport(Path(sys.argv[2]).resolve())
        raise SystemExit(0)
    if len(sys.argv) != 4: raise SystemExit("usage: patch_combined_service_startup.py LIVE SIDE v2|v3")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3])
