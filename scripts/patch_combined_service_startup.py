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
    (1, "AltStore/AppDelegate.swift"), (1, "SideStore/Core/Operations/PipelineExecutor.swift"),
    (1, "SideStore/Core/Operations/PipelineRunner.swift")}
V3_OUTPUTS = OUTPUTS | {(1, "SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift")}

SIDESIGN_PORTAL_OBSERVER = '''
// LC_PORTAL_RESPONSE_OBSERVER_V1: scalar-only, task-scoped observation. Requests,
// response parsing and the original thrown SideSign error remain unchanged.
public enum SideSignPortalDiagnostics {
    @TaskLocal public static var responseObserver: (@Sendable (Int?, String?) -> Void)? = nil
    public static func safeProviderCode(_ value: String?) -> String? {
        let known: Set<String> = ["ENTITY_ERROR", "ENTITY_ERROR.INVALID", "ENTITY_ERROR.ATTRIBUTE.INVALID",
            "ENTITY_ERROR.ATTRIBUTE.REQUIRED", "ENTITY_ERROR.ATTRIBUTE.UNKNOWN",
            "ENTITY_ERROR.RELATIONSHIP.INVALID", "ENTITY_ERROR.RELATIONSHIP.INVALID_NOT_ALLOWED",
            "ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE", "FORBIDDEN_ERROR", "NOT_FOUND",
            "PARAMETER_ERROR.INVALID", "PARAMETER_ERROR.REQUIRED", "RATE_LIMIT_EXCEEDED",
            "SERVICE_UNAVAILABLE", "UNEXPECTED_ERROR", "UNKNOWN_ERROR"]
        guard let value, known.contains(value) else { return nil }
        return value
    }
}
'''

def patch_sidesign_portal_observer(text):
    if "LC_PORTAL_RESPONSE_OBSERVER_V1" in text:
        if text.count("SideSignPortalDiagnostics.responseObserver?(httpResponse?.statusCode, nil)") != 2 or text.count("SideSignPortalDiagnostics.safeProviderCode(firstError.code)") != 2 or SIDESIGN_PORTAL_OBSERVER.strip() not in text:
            raise SystemExit("SideSign portal observer is incomplete")
        return text
    anchor = "        let httpResponse = response as? HTTPURLResponse"
    if text.count(anchor) != 2:
        raise SystemExit("pinned SideSign HTTP response anchors changed")
    text = text.replace(anchor, anchor + "\n        SideSignPortalDiagnostics.responseObserver?(httpResponse?.statusCode, nil)")
    error_anchor = "            if let errors = status.errors, let firstError = errors.first, let detail = firstError.detail {"
    if text.count(error_anchor) != 2:
        raise SystemExit("pinned SideSign structured error anchors changed")
    return text.replace(error_anchor, error_anchor + "\n                SideSignPortalDiagnostics.responseObserver?(httpResponse?.statusCode, SideSignPortalDiagnostics.safeProviderCode(firstError.code))") + SIDESIGN_PORTAL_OBSERVER

SIGNING_CAUSE_HELPER = '''
import CryptoKit
// LC_SIGNING_CAUSE_CLASSIFIER_V1: only typed upstream errors gain a semantic cause.
func lcSafeSigningCause(_ error: Error, portalResponse: Bool = false) -> String {
    if let urlError = error as? URLError {
        switch urlError.code {
        case .networkConnectionLost: return "signingNetworkConnectionLost"
        case .timedOut: return "signingNetworkTimedOut"
        case .notConnectedToInternet, .cannotConnectToHost, .cannotFindHost:
            return "signingNetworkUnavailable"
        default: break
        }
    }
    let native = error as NSError
    if native.domain == NSURLErrorDomain {
        switch native.code {
        case NSURLErrorNetworkConnectionLost: return "signingNetworkConnectionLost"
        case NSURLErrorTimedOut: return "signingNetworkTimedOut"
        case NSURLErrorNotConnectedToInternet, NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost:
            return "signingNetworkUnavailable"
        default: break
        }
    }
    if let serverError = error as? ServerError {
        switch serverError {
        case .underlyingError: return portalResponse ? "developerPortalRejectedRequest" : "unknownSigningCause"
        case .badServerResponse, .invalidResponseFormat, .missingKey:
            return portalResponse ? "developerPortalInvalidResponse" : "unknownSigningCause"
        }
    }
    if let portalError = error as? DeveloperPortalError {
        switch portalError {
        case .provisioningProfileDoesNotExist: return "provisioningProfileUnavailable"
        case .certificateDoesNotExist: return "certificateUnavailable"
        default: break
        }
    }
    return "unknownSigningCause"
}

func lcSigningHash(_ value: String) -> String {
    SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
}

private final class LCSigningHTTPObservation: @unchecked Sendable {
    private let lock = NSLock()
    private var status: Int?
    private var providerCode: String?
    func record(_ value: Int?, code: String?) {
        lock.lock(); defer { lock.unlock() }
        status = value.flatMap { (100...599).contains($0) ? $0 : nil }
        providerCode = SideSignPortalDiagnostics.safeProviderCode(code)
    }
    func snapshot() -> (status: Int?, providerCode: String?) {
        lock.lock(); defer { lock.unlock() }
        return (status, providerCode)
    }
}

// Request facts are captured before calling the existing upstream API. No shared
// last-step state: parallel extension failures retain their own request identity.
func lcPortalSigningContext(teamID: String, generation: UInt64,
                            bundleID: String? = nil, features: [String: String]? = nil,
                            groupCount: Int? = nil, groupID: String? = nil, profileMode: String? = nil) -> [String: String] {
    var facts = ["account_binding": "verified", "team_binding": "verified",
                 "team_sha256": lcSigningHash(teamID), "session_generation": String(generation)]
    if let bundleID { facts["requested_bundle_sha256"] = lcSigningHash(bundleID) }
    if let features {
        facts["capability_count"] = String(features.count)
        facts["capabilities_sha256"] = lcSigningHash(features.sorted { $0.key < $1.key }
            .map { $0.key + "=" + $0.value }.joined(separator: "\\n"))
        facts["capability_names"] = features.keys.filter { CombinedFailure.signingCapabilityNames.contains($0) }.sorted().joined(separator: ",")
        facts["enabled_capability_names"] = features.filter { CombinedFailure.signingCapabilityNames.contains($0.key) && $0.value == "true" }.keys.sorted().joined(separator: ",")
    }
    if let groupCount { facts["app_group_count"] = String(groupCount) }
    if let groupID { facts["requested_app_group_sha256"] = lcSigningHash(groupID) }
    if let profileMode {
        facts["profile_mode"] = profileMode
        // The team-profile endpoint chooses devices server-side. Do not claim
        // that a saved device or a UI certificate was explicitly sent to it.
        facts["device_registration"] = "unobserved"
    }
    return facts
}

func lcStructuredSigningFailure(_ error: Error, stage: String, sourceStep: String?,
                                facts: [String: String] = [:], portalResponse: Bool = false) -> NSError {
    let native = error as NSError
    var info: [String: Any] = ["LCStructuredFailureStageV1": stage,
        NSUnderlyingErrorKey: native, NSLocalizedDescriptionKey: "SideStore could not complete this pipeline step."]
    if let sourceStep { info["LCStructuredFailureSourceV1"] = sourceStep }
    var context = facts
    if stage == "signing" { info["LCStructuredFailureCauseV1"] = lcSafeSigningCause(error, portalResponse: portalResponse) }
    if let server = error as? ServerError {
        switch server {
        case .underlyingError(let code, _):
            context["typed_error"] = "sideSignServerReportedError"
            // -1 is SideSign's sentinel for a detail-only response, not an
            // observed numeric Apple result code. NSError's ordinal is never used.
            context["server_code"] = code == -1 ? "unknown" : String(code)
        case .badServerResponse: context["typed_error"] = "sideSignBadResponse"
        case .invalidResponseFormat: context["typed_error"] = "sideSignInvalidResponse"
        case .missingKey: context["typed_error"] = "sideSignMissingKey"
        }
        if context["http_status"] == nil { context["http_status"] = "unavailable" }
    }
    if let prior = native.userInfo["LCStructuredSigningContextV1"] as? [String: String] {
        context.merge(prior) { _, requestFact in requestFact }
    }
    for key in ["LCStructuredFailureStageV1", "LCStructuredFailureSourceV1", "LCStructuredFailureCauseV1"] {
        if let prior = native.userInfo[key] as? String { info[key] = prior }
    }
    if let safe = CombinedFailure.validatedSigningContext(context), !safe.isEmpty {
        info["LCStructuredSigningContextV1"] = safe
    }
    return NSError(domain: native.domain, code: native.code, userInfo: info)
}

func lcPortalSigningRequest<T>(sourceStep: String, facts: [String: String],
                               operation: () async throws -> T) async throws -> T {
    let observation = LCSigningHTTPObservation()
    do {
        return try await SideSignPortalDiagnostics.$responseObserver.withValue({ observation.record($0, code: $1) }) {
            try await operation()
        }
    }
    catch let server as ServerError {
        var observed = facts
        let response = observation.snapshot()
        observed["http_status"] = response.status.map { String($0) } ?? "unavailable"
        observed["provider_code"] = response.providerCode ?? "unavailable"
        // Keep business handling of other typed upstream errors unchanged.
        throw lcStructuredSigningFailure(server, stage: "signing", sourceStep: sourceStep,
                                         facts: observed, portalResponse: true)
    }
}

func lcProvisioningBundleRequest<T>(role: String, originalBundleID: String, preferredParentMatch: Bool,
                                    operation: () async throws -> T) async throws -> T {
    do { return try await operation() }
    catch {
        guard (error as? ServerError) != nil ||
              (error as NSError).userInfo["LCStructuredSigningContextV1"] != nil else { throw error }
        throw lcStructuredSigningFailure(error, stage: "signing", sourceStep: "provisioningProfileFetch",
            facts: ["provisioning_bundle_role": role,
                    "provisioning_bundle_sha256": lcSigningHash(originalBundleID),
                    "preferred_parent_id_match": String(preferredParentMatch)])
    }
}
'''

PIPELINE_FAILURE_HANDLER = r'''            result = error
            if error is CancellationError { throw error }
            // LC_STRUCTURED_FAILURE_V1: preserve step responsibility and the underlying error.
            var stage: String
            switch step {
            case .resignApp, .fetchProvisioningProfiles, .verifyCertificate: stage = "signing"
            case .sendApp, .installApp: stage = "installation"
            default: stage = "command"
            }
            var sourceStep: String?
            switch step {
            case .fetchProvisioningProfiles: sourceStep = "provisioningProfileFetch"
            case .verifyCertificate: sourceStep = "certificateValidation"
            case .resignApp: sourceStep = "localCodeSigning"
            default: break
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
            var facts: [String: String] = [:]
            if stage == "signing" {
                facts["extension_count"] = String(context.targetAppBundle?.appExtensions.count ?? 0)
                facts["signing_certificate_present"] = context.targetSigningCertificate == nil ? "false" : "true"
                if let certificate = context.targetSigningCertificate {
                    facts["signing_certificate_serial_sha256"] = lcSigningHash(certificate.serialNumber)
                }
            }
            throw lcStructuredSigningFailure(error, stage: stage, sourceStep: sourceStep, facts: facts)'''


def replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("combined startup anchor drift: " + old[:90])
    return text.replace(old, new, 1)


def patch_pipeline_executor(text, product="v3"):
    if "LC_SIGNING_CAUSE_CLASSIFIER_V1" in text or "LC_STRUCTURED_FAILURE_V1" in text:
        raise SystemExit("pinned pipeline already contains the structured signing adapter")
    if product == "v3":
        text = replace(text, "        do {\n            switch step {", '''        // V3_PIPELINE_PHASE_REPORTING_V1: report the authoritative step before it runs.
        if let headlessHandler = context.handler as? V3HeadlessPipelineHandler {
            await headlessHandler.recordPipelinePhase(step,
                downloadUsesNetwork: downloadingApp.url?.isFileURL == false)
        }
        do {
            switch step {''')
    helper = SIGNING_CAUSE_HELPER
    if product != "v3":
        # The legacy product has no identity-bound v3 proxy/HTTP observer.
        helper = helper[:helper.index("\nfunc lcPortalSigningRequest")]
    return replace(text, "            result = error\n            throw error",
                   PIPELINE_FAILURE_HANDLER) + helper


def patch_provisioning_profile_requests(text):
    """Focused parent/extension identity backport, retaining the upstream pipeline."""
    marker = "LC_PROVISIONING_PARENT_ID_UPSTREAM_V1"
    if marker in text:
        raise SystemExit("provisioning parent ID backport already applied")
    start = text.index("        let preferredBundleID = await self.getPreferredBundleID", text.index("private func provisionAndFetchProfile"))
    end = text.index("        let preferredName: String", start)
    # Exact parentID/suffix algorithm from SideStore develop 0dd743f75afc358b0ba4a002feb5f19474492371.
    # Keeping the optional lookup result also permits a truthful failure observation.
    replacement = '''        // LC_PROVISIONING_PARENT_ID_UPSTREAM_V1
        let preferredBundleID = await self.getPreferredBundleID(for: targetAppBundle, team: team)
        let parentID: String
        if let preferredBundleID {
            parentID = preferredBundleID
        } else if self.context.appendTeamID {
            parentID = "\\(self.context.targetBundleIdentifier).\\(team.identifier)"
        } else {
            parentID = self.context.targetBundleIdentifier
        }

        let bundleID: String
        if let parentAppBundle {
            guard targetAppBundle.bundleIdentifier.hasPrefix(parentAppBundle.bundleIdentifier + ".") else {
                throw OperationError.invalidApp(reason: "Extension bundle ID does not start with its parent bundle ID.")
            }
            let suffix = String(targetAppBundle.bundleIdentifier.dropFirst(parentAppBundle.bundleIdentifier.count))
            bundleID = parentID + suffix
        } else {
            bundleID = parentID
        }

        return try await lcProvisioningBundleRequest(
            role: parentAppBundle == nil ? "main" : "extension",
            originalBundleID: targetAppBundle.bundleIdentifier,
            preferredParentMatch: preferredBundleID != nil
        ) {
'''
    text = text[:start] + replacement + text[end:]
    # The existing App ID/features/groups/profile calls remain inside this
    # observation scope, with the existing explicit team and operation context.
    return replace(text, "        return profile\n    }\n}", "        return profile\n        }\n    }\n}")


def patch_pipeline_runner(text):
    marker = "V3_PROGRESS_BASELINE_FIX_V1"
    if marker in text:
        raise SystemExit("pinned runner already contains the progress baseline fix")
    return replace(text, "        group.progress.completedUnitCount = 1",
        "        // V3_PROGRESS_BASELINE_FIX_V1: child weights already span the full total.\n"
        "        group.progress.completedUnitCount = 0")


def patch(live, side, product):
    if product not in ("v2", "v3"):
        raise SystemExit("expected v2 or v3")
    expected_outputs = V3_OUTPUTS if product == "v3" else OUTPUTS
    for root, pin in zip((live, side), PINS):
        if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != pin:
            raise SystemExit("combined startup requires pinned source")
    manifest = live / ".combined-service-startup.json"
    # Every template whose text lands in an output is covered, so a changed
    # template can never be silently skipped on replay. The shared App Group
    # identity is one of them: it is prepended into SideStore.swift.
    templates = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(TEMPLATES.glob("combined_*"))}
    templates["v3_shared_app_group.swift"] = hashlib.sha256(
        (TEMPLATES / "v3_shared_app_group.swift").read_bytes()).hexdigest()
    templates["patch_combined_service_startup.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous["templates"] != templates or previous["product"] != product:
            raise SystemExit("combined startup templates changed; use fresh pinned sources")
        if (previous.get("pins") != list(PINS) or len(previous["files"]) != len(expected_outputs)
                or {(i, name) for i, name, _ in previous["files"]} != expected_outputs):
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
        old_result = 'return .result(dialog: "All apps have been refreshed.")'
        requested_result = 'return .result(dialog: "Refresh All was requested in LiveContainer. Check Refresh History for the run result.")'
        if old_result in text:
            text = replace(text, old_result,
                requested_result if product == "v3" else
                'return .result(dialog: "Refresh request completed. Check Refresh for verified installation results.")')
        elif product == "v3" and requested_result not in text:
            raise SystemExit("combined startup anchor drift: Refresh All request result copy")
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
        handler = handler.replace("/*REFRESH_READINESS*/", "")
        handler = handler.replace("/*SERVICE_PROBE*/", '''
        let until = Date().addingTimeInterval(30)
        var backoff = V3ServiceReadinessBackoff()
        var ready = false
        var pending = false
        var invalid = false
        var terminalFailure: V3ServiceReadinessFailure?
        while true {
            try Task.checkCancellation()
            guard launchID == id else { throw CancellationError() }
            switch V3ServiceReadinessProbeState.resolve(
                ready: ready, invalid: invalid, hasTerminalFailure: terminalFailure != nil,
                expired: Date() >= until) {
            case .ready:
                NSLog("[V3_SERVICE_START] SNAPSHOT_READY id=%@", id.uuidString)
                return
            case .invalid:
                NSLog("[V3_SERVICE_START] READINESS_INVALID_RESPONSE id=%@", id.uuidString)
                throw CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .invalidResponse, id: id.uuidString)
            case .failed:
                guard let failure = terminalFailure?.combinedFailure(
                        id: refreshRunID ?? id.uuidString,
                        operation: refreshRunID == nil ? nil : "refresh") else {
                    throw CombinedFailure(operation: "connect", stage: .serviceReadiness,
                        code: .invalidResponse, id: id.uuidString)
                }
                throw failure
            case .timedOut:
                switch await V3ServiceReadinessProbeState.recheckAfterYield(
                    ready: { ready }, invalid: { invalid }, hasTerminalFailure: { terminalFailure != nil }) {
                case .ready:
                    NSLog("[V3_SERVICE_START] SNAPSHOT_READY id=%@", id.uuidString)
                    return
                case .invalid:
                    NSLog("[V3_SERVICE_START] READINESS_INVALID_RESPONSE id=%@", id.uuidString)
                    throw CombinedFailure(operation: "connect", stage: .serviceReadiness,
                        code: .invalidResponse, id: id.uuidString)
                case .failed:
                    guard let failure = terminalFailure?.combinedFailure(
                            id: refreshRunID ?? id.uuidString,
                            operation: refreshRunID == nil ? nil : "refresh") else {
                        throw CombinedFailure(operation: "connect", stage: .serviceReadiness,
                            code: .invalidResponse, id: id.uuidString)
                    }
                    throw failure
                case .pending, .timedOut:
                    NSLog("[V3_SERVICE_START] READINESS_TIMEOUT id=%@", id.uuidString)
                    throw CombinedFailure(operation: "connect", stage: .serviceReadiness,
                        code: .timedOut, id: id.uuidString, retryable: true)
                }
            case .pending:
                break
            }
            if !pending, let client {
                let requestID = UUID().uuidString
                let message: [String: Any] = ["version": 1, "id": requestID, "operation": "snapshot", "target": "", "deadline": Date().addingTimeInterval(30), "payload": ["readinessOnly": true]]
                let data = try PropertyListSerialization.data(fromPropertyList: message, format: .binary, options: 0)
                pending = true
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        guard self.launchID == id else { return }
                        pending = false
                        switch V3ServiceReadinessReply.decode(response, requestID: requestID) {
                        case .invalid: invalid = true
                        case .notReady: break
                        case .failed(let failure): terminalFailure = failure
                        case .ready: ready = true
                        }
                    }
                }
            }
            guard let delay = backoff.nextDelay(remaining: until.timeIntervalSinceNow) else { continue }
            try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }
''' if product == "v3" else '''
        // v2 has no command catalog. App launch readiness is distinct from database readiness,
        // which remains owned by the subsequent explicit refresh intent.
        try Task.checkCancellation()
''')
        return text[:start] + template("combined_failure.swift") + template("v3_shared_app_group.swift") + template("combined_service_connection.swift") + handler + text[end:]
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
    edit(live, "SideStoreSupport/XPCServer.m", lambda s: '#import "../LiveContainer/FoundationPrivate.h"\n#import "../LiveContainer/LCContainerStorage.h"\n' + replace(s,
        '    [newConnection resume];',
        '    // V3_XPC_PEER_ADMISSION_V1: RefreshHandler resumes only the launched extension peer.') + '''
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
        // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the expected run ID is written by
        // the host scheduler into the one runtime App Group, and read back by the
        // embedded service. A fixed suite name correlates the failure to a run
        // the service never began.
        let id = V3SharedAppGroup.sharedUserDefaults()?.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? UUID().uuidString
        reportRefreshResult(CombinedFailure.capture(error, operation: "refresh", stage: .command, id: id).encodedString, server: server)
    }
}
'''
    edit(live, "SideStoreSupport/SideStoreClient.swift", client)
    edit(side, "AltStore/AppDelegate.swift", lambda s: s + template("combined_failure.swift"))
    edit(side, "SideStore/Core/Operations/PipelineExecutor.swift",
         lambda s: patch_pipeline_executor(s, product))
    edit(side, "SideStore/Core/Operations/PipelineRunner.swift", patch_pipeline_runner)
    if product == "v3":
        edit(side, "SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift",
             patch_provisioning_profile_requests)
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
    if len(sys.argv) == 3 and sys.argv[1] == "--portal":
        root = Path(sys.argv[2]).resolve()
        if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != "a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d":
            raise SystemExit("portal diagnostics require the pinned SideSign revision")
        target = root / "Sources/DeveloperPortal/DeveloperPortalAPI.swift"
        target.write_bytes(patch_sidesign_portal_observer(target.read_text(encoding="utf-8")).encode())
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--transport":
        patch_transport(Path(sys.argv[2]).resolve())
        raise SystemExit(0)
    if len(sys.argv) != 4: raise SystemExit("usage: patch_combined_service_startup.py LIVE SIDE v2|v3 | --portal SIDESIGN")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3])
