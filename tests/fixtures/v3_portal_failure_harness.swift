import Foundation
import CoreFoundation
import CryptoKit

enum Constants { static let defaultAccountRepairMessage = "" }
$SIDESIGN_ERROR_TYPES$
$DEVELOPER_PORTAL_STATUS_RESPONSE$
$DEVELOPER_PORTAL_RESULT_CODES$
$PORTAL_OBSERVER$
$COMBINED_FAILURE$
$BEHAVIORAL_POLICIES$
$SIGNING_CAUSE_HELPER$

private func debugLog(_ text: String) {}
private let resultCodeHandler: ((Int, String) -> Error?)? = nil

private func dispatchActualSideSignStatus(data: Data, response: URLResponse) throws {
$ACTUAL_HTTP_OBSERVER_AND_STATUS_DECODER$
}

struct ALTTeam: Sendable { let identifier: String }
struct ALTAppID: Sendable { let bundleIdentifier: String }
struct BoundSession: Sendable {
    let session: String
    let appleID: String
    let generation: UInt64
}
enum OperationError: Error { case notAuthenticated }

actor AuthManagerBindingBoundary {
    static let shared = AuthManagerBindingBoundary()
    var allowDispatch = true
    private var pauseNextBind = false
    private var bindingStarted = false
    func setAllowed(_ allowed: Bool) { allowDispatch = allowed }
    func pauseNextBinding() { pauseNextBind = true }
    func isBindingPaused() -> Bool { bindingStarted }
    func bind() async throws -> BoundSession {
        if pauseNextBind {
            pauseNextBind = false
            bindingStarted = true
            try? await Task.sleep(nanoseconds: 100_000_000)
            bindingStarted = false
        }
        guard allowDispatch, !Task.isCancelled else { throw OperationError.notAuthenticated }
        return BoundSession(session: "opaque-session", appleID: "account@example.invalid", generation: 7)
    }
    func verify(_ context: BoundSession) throws {
        guard allowDispatch, !Task.isCancelled, context.generation == 7 else {
            throw OperationError.notAuthenticated
        }
    }
}

struct PortalResponsePlan: Sendable {
    let body: Data
    let status: Int
    let delayMilliseconds: UInt64
}

actor ALTAppleAPI {
    static let shared = ALTAppleAPI()
    private var plans: [String: PortalResponsePlan] = [:]
    private var callCounts: [String: Int] = [:]
    func install(_ newPlans: [String: PortalResponsePlan]) { plans = newPlans; callCounts = [:] }
    func counts() -> [String: Int] { callCounts }

    func addAppID(withName name: String, bundleIdentifier: String,
                  team: ALTTeam, session: String) async throws -> ALTAppID {
        callCounts[bundleIdentifier, default: 0] += 1
        guard let plan = plans[bundleIdentifier] else { throw OperationError.notAuthenticated }
        if plan.delayMilliseconds > 0 {
            try await Task.sleep(nanoseconds: plan.delayMilliseconds * 1_000_000)
        }
        let response = HTTPURLResponse(url: URL(string: "https://developer.invalid/addAppID")!,
            statusCode: plan.status, httpVersion: "HTTP/1.1", headerFields: nil)!
        try dispatchActualSideSignStatus(data: plan.body, response: response)
        return ALTAppID(bundleIdentifier: bundleIdentifier)
    }
}

final class DeveloperPortalProxyHarness {
    func getBoundSession() async throws -> BoundSession {
        try await AuthManagerBindingBoundary.shared.bind()
    }
    func getBoundTeam(_ requested: ALTTeam? = nil, context: BoundSession) async throws -> ALTTeam {
        try await AuthManagerBindingBoundary.shared.verify(context)
        return requested ?? ALTTeam(identifier: "TEAM-PRIVATE-123")
    }
    func awaitBound<T>(_ context: BoundSession,
                       operation: () async throws -> T) async throws -> T {
        try await AuthManagerBindingBoundary.shared.verify(context)
        let value = try await operation()
        try await AuthManagerBindingBoundary.shared.verify(context)
        return value
    }
$ACTUAL_PATCHED_ADD_APP_ID_METHOD$
}

enum PipelineStep {
    case resignApp, fetchProvisioningProfiles, verifyCertificate, sendApp, installApp, other
}
struct PipelineCertificate { let serialNumber: String }
struct PipelineBundle { let appExtensions: [String] }
struct PipelineContext {
    let targetAppBundle: PipelineBundle?
    let targetSigningCertificate: PipelineCertificate?
}

private func pipelineFailure(_ failure: Error, step: PipelineStep) throws -> Never {
    var result: Error?
    let context = PipelineContext(targetAppBundle: PipelineBundle(appExtensions: []),
        targetSigningCertificate: PipelineCertificate(serialNumber: "PRIVATE-CERTIFICATE-SERIAL"))
    do {
        throw failure
    } catch {
$ACTUAL_PIPELINE_FAILURE_HANDLER$
    }
}

private func roundTrip(_ failure: Error, operation: String = "install") throws -> CombinedFailure {
    let identifier = UUID().uuidString
    do {
        try pipelineFailure(failure, step: .resignApp)
    } catch {
        let captured = CombinedFailure.capture(error, operation: operation,
            stage: .installation, id: identifier)
        let bytes = try PropertyListSerialization.data(fromPropertyList: captured.wire,
            format: .binary, options: 0)
        for privateValue in ["PRIVATE-CERTIFICATE-SERIAL", "TEAM-PRIVATE-123"] {
            precondition(bytes.range(of: Data(privateValue.utf8)) == nil)
        }
        let decoded = try PropertyListSerialization.propertyList(from: bytes, format: nil) as! [String: Any]
        guard let bridged = CombinedFailure.decode(decoded, expectedID: identifier) else {
            throw NSError(domain: "PortalHarness", code: 1)
        }
        return bridged
    }
    throw NSError(domain: "PortalHarness", code: 2)
}

@main struct PortalFailureHarness {
    static func main() async throws {
        let proxy = DeveloperPortalProxyHarness()
        let actualCode = ALTAppleAPI.shared
        let oneBundle = "com.private.one"
        let twoBundle = "com.private.two"
        let threeBundle = "com.private.three"
        let detailBody = Data(#"{"errors":[{"code":"ENTITY_ERROR.ATTRIBUTE.INVALID","detail":"private provider response and identity"}]}"#.utf8)
        let privateCodeBody = Data(#"{"errors":[{"code":"PRIVATE_PROVIDER_SENTINEL_92","detail":"private numeric provider body"}]}"#.utf8)
        // Code 42 is not one of SideSign addAppID's custom-mapped result codes,
        // so its real dispatcher preserves it as ServerError.underlyingError.
        let numericBody = Data(#"{"resultCode":42,"userString":"private numeric provider body"}"#.utf8)
        await actualCode.install([
            oneBundle: PortalResponsePlan(body: detailBody, status: 409, delayMilliseconds: 35),
            twoBundle: PortalResponsePlan(body: numericBody, status: 422, delayMilliseconds: 1),
            threeBundle: PortalResponsePlan(body: privateCodeBody, status: 429, delayMilliseconds: 0)
        ])

        // A success dispatch reaches SideSign once and returns normally.
        let successBundle = "com.private.success"
        await actualCode.install([successBundle: PortalResponsePlan(
            body: Data(#"{"resultCode":0}"#.utf8), status: 200, delayMilliseconds: 0)])
        let created = try await proxy.addAppID(name: "private name", bundleIdentifier: successBundle)
        precondition(created.bundleIdentifier == successBundle)
        let successCounts = await actualCode.counts()
        precondition(successCounts[successBundle] == 1)

        // Auth/session admission rejection and a cancelled call do not dispatch.
        let rejectedBundle = "com.private.rejected"
        await actualCode.install([rejectedBundle: PortalResponsePlan(
            body: numericBody, status: 422, delayMilliseconds: 0)])
        await AuthManagerBindingBoundary.shared.setAllowed(false)
        do { _ = try await proxy.addAppID(name: "x", bundleIdentifier: rejectedBundle); preconditionFailure() }
        catch is OperationError {}
        let rejectedCounts = await actualCode.counts()
        precondition(rejectedCounts[rejectedBundle] == nil)
        await AuthManagerBindingBoundary.shared.setAllowed(true)
        let cancelledBundle = "com.private.cancelled"
        await actualCode.install([cancelledBundle: PortalResponsePlan(
            body: numericBody, status: 422, delayMilliseconds: 0)])
        await AuthManagerBindingBoundary.shared.pauseNextBinding()
        let cancelled = Task { try await proxy.addAppID(name: "x", bundleIdentifier: cancelledBundle) }
        for _ in 0..<1000 {
            if await AuthManagerBindingBoundary.shared.isBindingPaused() { break }
            await Task.yield()
        }
        let bindingPaused = await AuthManagerBindingBoundary.shared.isBindingPaused()
        precondition(bindingPaused)
        cancelled.cancel()
        do { _ = try await cancelled.value; preconditionFailure() }
        catch {}
        let cancelledCounts = await actualCode.counts()
        precondition(cancelledCounts[cancelledBundle] == nil)

        // Parallel responses are reordered by latency but keep each request's
        // actual SideSign code, observed HTTP status and hashed call metadata.
        await actualCode.install([
            oneBundle: PortalResponsePlan(body: detailBody, status: 409, delayMilliseconds: 35),
            twoBundle: PortalResponsePlan(body: numericBody, status: 422, delayMilliseconds: 1),
            threeBundle: PortalResponsePlan(body: privateCodeBody, status: 429, delayMilliseconds: 0)
        ])
        async let first = capture(proxy, bundle: oneBundle)
        async let second = capture(proxy, bundle: twoBundle)
        let (one, two) = try await (first, second)
        assertPortalFailure(one, bundle: oneBundle, status: "409", serverCode: "unknown",
            providerCode: "ENTITY_ERROR.ATTRIBUTE.INVALID")
        assertPortalFailure(two, bundle: twoBundle, status: "422", serverCode: "42")
        let three = try await capture(proxy, bundle: threeBundle)
        assertPortalFailure(three, bundle: threeBundle, status: "429", serverCode: "unknown")
        let concurrentCounts = await actualCode.counts()
        precondition(concurrentCounts[oneBundle] == 1)
        precondition(concurrentCounts[twoBundle] == 1)
        precondition(concurrentCounts[threeBundle] == 1)

        // A typed certificate error retains its authoritative certificate route.
        let typed = try roundTrip(DeveloperPortalError.certificateDoesNotExist(serial: "PRIVATE-CERT"))
        let typedDetails = V3OperationFailureDetails(typed)
        let typedIssue = V3UserFacingIssue.make(typed)
        precondition(typedDetails.recoveryDestination == "certificates")
        precondition(typedIssue.recoveryDestination == "certificates")
        print("PINNED_PORTAL_FAILURE_CONTEXT_PASS")
    }

    private static func capture(_ proxy: DeveloperPortalProxyHarness,
                                bundle: String) async throws -> CombinedFailure {
        do { _ = try await addAndClassify(proxy, bundle: bundle) }
        catch {
            let identifier = UUID().uuidString
            let captured = CombinedFailure.capture(error, operation: "install",
                stage: .installation, id: identifier)
            let bytes = try PropertyListSerialization.data(fromPropertyList: captured.wire,
                format: .binary, options: 0)
            for privateValue in [bundle, "TEAM-PRIVATE-123", "PRIVATE-CERTIFICATE-SERIAL",
                                 "private provider response and identity", "private numeric provider body",
                                 "PRIVATE_PROVIDER_SENTINEL_92"] {
                precondition(bytes.range(of: Data(privateValue.utf8)) == nil)
            }
            let decoded = try PropertyListSerialization.propertyList(from: bytes, format: nil) as! [String: Any]
            guard let value = CombinedFailure.decode(decoded, expectedID: identifier) else {
                throw NSError(domain: "PortalHarness", code: 3)
            }
            return value
        }
        throw NSError(domain: "PortalHarness", code: 4)
    }

    private static func addAndClassify(_ proxy: DeveloperPortalProxyHarness,
                                       bundle: String) async throws -> ALTAppID {
        do {
            return try await lcProvisioningBundleRequest(role: "extension", originalBundleID: bundle,
                                                         preferredParentMatch: true) {
                try await proxy.addAppID(name: "private display name", bundleIdentifier: bundle)
            }
        }
        catch { try pipelineFailure(error, step: .resignApp) }
    }

    private static func assertPortalFailure(_ failure: CombinedFailure, bundle: String,
                                            status: String, serverCode: String,
                                            providerCode: String = "unavailable") {
        let details = V3OperationFailureDetails(failure)
        let issue = V3UserFacingIssue.make(failure)
        precondition(failure.stage == .signing)
        precondition(failure.sourceStep?.rawValue == "appIDRegistration")
        precondition(failure.safeCause == .developerPortalRejectedRequest)
        precondition(failure.signingContext["server_code"] == serverCode)
        precondition(failure.signingContext["http_status"] == status)
        precondition(failure.signingContext["provider_code"] == providerCode)
        precondition(failure.signingContext["typed_error"] == "sideSignServerReportedError")
        precondition(failure.signingContext["account_binding"] == "verified")
        precondition(failure.signingContext["team_binding"] == "verified")
        precondition(failure.signingContext["requested_bundle_sha256"] == lcSigningHash(bundle))
        precondition(failure.signingContext["provisioning_bundle_role"] == "extension")
        precondition(failure.signingContext["provisioning_bundle_sha256"] == lcSigningHash(bundle))
        precondition(failure.signingContext["preferred_parent_id_match"] == "true")
        precondition(details.recoveryDestination == nil)
        precondition(issue.recoveryDestination == nil)
        let safe = failure.technicalDetails + " " + issue.technicalDetails
        precondition(!safe.contains("PRIVATE-CERT"))
        precondition(!safe.contains("private provider"))
        precondition(!safe.contains("private numeric provider body"))
        precondition(!safe.contains("PRIVATE_PROVIDER_SENTINEL_92"))
        precondition(!safe.contains("TEAM-PRIVATE-123"))
        precondition(!safe.contains(bundle))
    }
}
