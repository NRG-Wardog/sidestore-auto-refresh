#!/usr/bin/env python3
"""Port the validated transport to the pinned LiveContainer SideStore dependency.

The Swift gateway/FFI fixes are shared with the standalone builder. This adapter
owns only the newer minimuxer policy, base-class API and batch lifecycle changes.
It does not alter the combined IPA layout, deployment targets, or host IPC.
"""
from pathlib import Path
import sys

from patch_sidestore_integration import patch_gateway, replace_once, replace_region


POLICY = '''import Foundation
public import MinimuxerCommon

public enum RefreshTransport: String, Sendable {
    case coreDevice = "COREDEVICE_LOCALDEVVPN"
    case lockdownIPSec = "LOCKDOWN_IPSEC"
    case lockdownLegacy = "LOCKDOWN_LEGACY"
    case remotePairing = "REMOTE_PAIRING"
    case proxy = "UPSTREAM_PROXY"
    case unavailable = "FAILED_NO_VALID_TRANSPORT"
}

public struct RefreshTransportDecision: Sendable {
    public let transport: RefreshTransport
    public let reason: String
}

public enum RefreshTransportPolicy {
    public static func select(modernOS: Bool, mode: DeviceConnectionMode,
                              pairing: PairingProtocol, utun: Bool, ipsec: Bool,
                              coreDeviceSupported: Bool) -> RefreshTransportDecision {
        if pairing == .unknown {
            return .init(transport: .unavailable, reason: "No valid pairing record")
        }
        if mode == .remoteServer {
            return .init(transport: .proxy, reason: "Explicit upstream remote-server configuration")
        }
        guard mode == .localVPN, utun else {
            return .init(transport: .unavailable, reason: "Local VPN requires a reachable utun route; IPSec alone is not an upstream local-VPN route")
        }
        if pairing == .rppairing {
            return .init(transport: .remotePairing, reason: "RemotePairing-only record; not CoreDevice proof")
        }
        if modernOS && coreDeviceSupported {
            return .init(transport: .coreDevice, reason: "iOS>=26.4 local VPN with Lockdown record and patched CoreDevice backend; initialization pending")
        }
        if ipsec {
            return .init(transport: .lockdownIPSec, reason: "Upstream Lockdown with utun and IPSec interfaces")
        }
        if !modernOS {
            return .init(transport: .lockdownLegacy, reason: "Preserve upstream Lockdown below iOS26.4")
        }
        return .init(transport: .unavailable, reason: "Selected backend has no CoreDevice implementation and IPSec is absent")
    }
}
'''

GATEWAY_STATE = r'''
    // COMBINED_COREDEVICE_BATCH_V1: no connection is opened by configuration.
    private var coreDeviceEnabled = false
    private var batchCount = 0
    private var usesCoreDevice: Bool { coreDeviceEnabled && pairingFileType == .lockdown }
    public var supportsCoreDeviceTransport: Bool { true }
    public var coreDeviceTransportEnabled: Bool { onFFIQueue { usesCoreDevice } }
    public var hasActiveTransportBatch: Bool { onFFIQueue { batchCount > 0 } }

    public func configureCoreDeviceTransport(_ enabled: Bool) {
        onFFIQueue {
            guard coreDeviceEnabled != enabled else { return }
            releaseTransport()
            coreDeviceEnabled = enabled
        }
    }

    public func beginTransportBatch() async {
        await withCheckedContinuation { continuation in
            ffiQueue.async {
                self.batchCount += 1
                debugLog("[SIDESTORE_COREDEVICE] BATCH_BEGIN active_batches=\(self.batchCount)")
                continuation.resume()
            }
        }
    }

    public func endTransportBatch() async {
        await withCheckedContinuation { continuation in
            ffiQueue.async {
                self.batchCount = max(0, self.batchCount - 1)
                if self.batchCount == 0 {
                    self.releaseTransport()
                    self.stagedBundleIdentities.removeAll()
                }
                debugLog("[SIDESTORE_COREDEVICE] BATCH_END active_batches=\(self.batchCount)")
                continuation.resume()
            }
        }
    }

    public override func setDeviceEndpointIp(_ ip: String?) {
        onFFIQueue { super.setDeviceEndpointIp(ip) }
    }

    public override func setPort(_ port: UInt16, for protocol: PairingProtocol) {
        onFFIQueue { super.setPort(port, for: `protocol`) }
    }
'''

CONFIGURE = r'''
    @discardableResult
    private func configureRefreshTransport() async -> RefreshTransportDecision {
        let modernOS: Bool
        if #available(iOS 26.4, *) { modernOS = true } else { modernOS = false }
        let mode = await getConnectionMode()
        let utun = network.isUTunAvailable
        let ipsec = network.isIKEv2IPSecAvailable
        let decision = RefreshTransportPolicy.select(
            modernOS: modernOS, mode: mode, pairing: gateway.pairingFileType,
            utun: utun, ipsec: ipsec, coreDeviceSupported: gateway.supportsCoreDeviceTransport)
        gateway.configureCoreDeviceTransport(decision.transport == .coreDevice)
        // Interface presence cannot establish the identity of the App Store VPN.
        debugLog("[SIDESTORE_COREDEVICE] TRANSPORT_SELECTION os_version=\(ProcessInfo.processInfo.operatingSystemVersionString) pairing_mode=\(gateway.pairingFileType) localdevvpn_detected=unverified utun_detected=\(utun) ipsec_interface_detected=\(ipsec) selected_transport=\(decision.transport.rawValue) reason=\(decision.reason)")
        return decision
    }

    func beginTransportBatch() async {
        await gateway.beginTransportBatch()
        await configureRefreshTransport()
    }

    func endTransportBatch() async { await gateway.endTransportBatch() }
'''


def edit(path, marker, operation):
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        text = operation(text)
        if marker not in text:
            raise SystemExit(f"Missing postcondition {marker}: {path}")
        path.write_text(text, encoding="utf-8")


def patch(minimuxer: Path):
    if not (minimuxer / "DeviceGateway/BaseDeviceGateway.swift").is_file():
        raise SystemExit("Combined adapter requires the pinned BaseDeviceGateway revision")
    patch_gateway(minimuxer)
    common = minimuxer / "Common"
    (minimuxer / "Sources/RefreshTransportPolicy.swift").write_text(POLICY, encoding="utf-8")

    def pairing(text):
        old = '''        let missingRP = RPPairingFile.missingKeys(in: plist)
        if missingRP.isEmpty {
            return .rppairing
        }

        let missingLockdown = LockdownPairingFile.missingKeys(in: plist)
        if missingLockdown.isEmpty {
            return .lockdown
        }'''
        new = '''        // Composite records must use Lockdown/CoreDevice, not RemotePairing.
        let missingLockdown = LockdownPairingFile.missingKeys(in: plist)
        if missingLockdown.isEmpty {
            return .lockdown
        }

        let missingRP = RPPairingFile.missingKeys(in: plist)
        if missingRP.isEmpty {
            return .rppairing
        }'''
        return replace_once(text, old, new, "composite pairing selection")
    edit(common / "PairingFile.swift", "Composite records must use", pairing)

    api = minimuxer / "DeviceGateway/DeviceGatewayAPI.swift"
    def gateway_api(text):
        text = replace_once(text, "public protocol DeviceGatewayAPI: AnyObject, Sendable {",
                            '''public protocol DeviceGatewayAPI: AnyObject, Sendable {
    var supportsCoreDeviceTransport: Bool { get }
    var coreDeviceTransportEnabled: Bool { get }
    var hasActiveTransportBatch: Bool { get }
    func configureCoreDeviceTransport(_ enabled: Bool)
    func beginTransportBatch() async
    func endTransportBatch() async''', "transport capabilities")
        return replace_once(text, "public extension DeviceGatewayAPI {", '''public extension DeviceGatewayAPI {
    var supportsCoreDeviceTransport: Bool { false }
    var coreDeviceTransportEnabled: Bool { false }
    var hasActiveTransportBatch: Bool { false }
    func configureCoreDeviceTransport(_ enabled: Bool) {}
    func beginTransportBatch() async {}
    func endTransportBatch() async {}''', "unchanged backend defaults")
    edit(api, "supportsCoreDeviceTransport", gateway_api)
    base = minimuxer / "DeviceGateway/BaseDeviceGateway.swift"
    edit(base, "open func setPort", lambda text: replace_once(
        replace_once(text, "public func setPort(", "open func setPort(", "port override"),
        "public func setDeviceEndpointIp(", "open func setDeviceEndpointIp(", "endpoint override"))

    gateway = minimuxer / "DeviceGateway/idevice/IdeviceGateway.swift"
    def gateway_state(text):
        text = replace_once(text, "internal import MinimuxerCommon", "public import MinimuxerCommon",
                            "public base setter parameter types")
        text = replace_once(text, "            setPairingFileType(parsedPairingFile.mode)",
                            r'''            setPairingFileType(parsedPairingFile.mode)
            debugLog("[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED mode=\(parsedPairingFile.mode)")''',
                            "pairing selection diagnostic")
        text = replace_once(text, "    private var usesCoreDevice: Bool { pairingFileType == .lockdown }",
                            GATEWAY_STATE.rstrip(), "batch state")
        for detail in ("AFC_WRITE_BEGIN", "AFC_WRITE_RETURN", "AFC_FILE_OPEN_START",
                       "AFC_FILE_OPEN_PASS", "AFC_FILE_CLOSE_START", "AFC_FILE_CLOSE_PASS",
                       "STAGED_FILE_SIZE=", "STAGED_FILE_SIZE_MATCH=", "STAGING_WRITE_LOOP_DONE",
                       "IPA_STAGE_START", "file_STAGE_START"):
            text = text.replace('debugLog("[SELF_REFRESH] ' + detail,
                                'verboseLog("[SELF_REFRESH] ' + detail)
        # Adding defer makes these multi-statement closures. Preserve their
        # values explicitly; otherwise Swift infers Void instead of the result.
        text = text.replace("withFFIDispatch(on: self.ffiQueue) {\n            try self.",
                            "withFFIDispatch(on: self.ffiQueue) {\n            return try self.")
        # Non-batch operations remain usable, but must not leave a heartbeat alive.
        return text.replace("withFFIDispatch(on: self.ffiQueue) {",
                            "withFFIDispatch(on: self.ffiQueue) {\n            defer { if self.batchCount == 0 { self.releaseTransport() } }")
    edit(gateway, "COMBINED_COREDEVICE_BATCH_V1", gateway_state)

    mux_api = minimuxer / "Sources/MinimuxerApi.swift"
    edit(mux_api, "func beginTransportBatch()", lambda text: replace_once(
        text, "public protocol MinimuxerAPI: AnyObject {", '''public protocol MinimuxerAPI: AnyObject {
    func beginTransportBatch() async
    func endTransportBatch() async''', "batch API"))
    impl = minimuxer / "Sources/MinimuxerImpl.swift"
    def implementation(text):
        text = replace_once(text, "    func isReady(withNetworkCheck:", CONFIGURE + "\n    func isReady(withNetworkCheck:", "capability selection")
        text = replace_region(text, "        switch connectionMode {\n            case .notConfigured:",
                              "        // check if pairing file is loaded", '''        let decision = await configureRefreshTransport()
        if decision.transport == .unavailable {
            return .failure(.invalidVPN(decision.reason))
        }
        if decision.transport == .coreDevice {
            verboseLog("[SIDESTORE_COREDEVICE] LOCALVPN_UTUN_ACCEPTED transport=lockdown-coredevice")
            // Network-change UI checks must not start CoreDevice/signing work.
            if !gateway.hasActiveTransportBatch { return .success(false) }
        }

''', "replace readiness policy with actual transport capability")
        return replace_once(text, "        // retarget usbmuxd to our fake usbmuxd server (over network)",
                            "        await configureRefreshTransport()\n        // retarget usbmuxd to our fake usbmuxd server (over network)", "configure after pairing load")
    edit(impl, "private func configureRefreshTransport", implementation)

    observer = minimuxer / "Sources/Services/NetworkObserverService.swift"
    def endpoint(text):
        return replace_region(text, "                    let overrideIp = await manager.overridePeerIp",
                              "                    if let peer = effectiveIp {", r'''                    let overrideIp = await manager.overridePeerIp
                    let overrideReachable = await manager.isOverridePeerIpReachable
                    let derivedIp = await manager.derivedPeerIp
                    let derivedReachable = await manager.isDerivedPeerIpReachable
                    let effectiveIp = overrideReachable ? overrideIp : (derivedReachable ? derivedIp : nil)
                    let effectivePeer = overrideReachable ? "overridePeer" : "derivedPeerIp"
                    debugLog("[SIDESTORE_COREDEVICE] ENDPOINT_SELECT selected_source=\(effectivePeer) reachable=\(effectiveIp != nil)")

''', "reachable peer fallback")
    edit(observer, "[SIDESTORE_COREDEVICE] ENDPOINT_SELECT", endpoint)

    heartbeat = minimuxer / "Sources/Services/HeartbeatService.swift"
    edit(heartbeat, "CoreDevice owns the operation-scoped heartbeat", lambda text: replace_once(
        text, "    func start() async {", '''    func start() async {
        // CoreDevice owns the operation-scoped heartbeat; never start a probe loop.
        if gateway.coreDeviceTransportEnabled { return }''', "disable duplicate heartbeat"))

    sidestore = minimuxer.parent.parent
    runner = sidestore / "SideStore/Core/Operations/PipelineRunner.swift"
    def pipeline(text):
        text = replace_once(text, '''            // run the operation pipeline
            try await withThrowingTaskGroup(of: Void.self) { taskGroup in
                for operation in operations {
                    taskGroup.addTask {
                        try await self.performOperation(for: operation, handler: handler, group: group)
                    }
                }
                while let _ = try await taskGroup.next() {}
            }''', '''            // Finish standalone apps before a host replacement can terminate us.
            let hostOperations = operations.filter {
                ($0.app as? ALTApplication)?.isAltStoreApp == true || $0.bundleIdentifier.isAltStoreAppID
            }
            let normalOperations = operations.filter {
                !(($0.app as? ALTApplication)?.isAltStoreApp == true || $0.bundleIdentifier.isAltStoreAppID)
            }
            try await withThrowingTaskGroup(of: Void.self) { taskGroup in
                for operation in normalOperations {
                    taskGroup.addTask {
                        try await self.performOperation(for: operation, handler: handler, group: group)
                    }
                }
                while let _ = try await taskGroup.next() {}
            }
            for operation in hostOperations {
                try Task.checkCancellation()
                try await self.performOperation(for: operation, handler: handler, group: group)
            }''', "enforce host last within shared transport batch")
        text = replace_once(text, "        try await Task.detached {\n            /* Minimuxer Readiness Check */", '''        // COMBINED_COREDEVICE_PIPELINE_BATCH_V1: one lease for all apps, host last.
        let transportCore = minimuxer.core
        await transportCore.beginTransportBatch()
        do {
        try await Task.detached {
            /* Minimuxer Readiness Check */''', "batch acquisition before readiness")
        return replace_once(text, "        }.value\n", '''        }.value
        } catch {
            await transportCore.endTransportBatch()
            throw error
        }
        await transportCore.endTransportBatch()
''', "batch release on all returning paths")
    edit(runner, "COMBINED_COREDEVICE_PIPELINE_BATCH_V1", pipeline)
    send = sidestore / "SideStore/Core/Operations/PipelineOperations/SendAppOperation.swift"
    edit(send, "Preserve the underlying AFC failure", lambda text: replace_once(
        text, "            throw OperationError.appNotFound(name: bundleIdentifier)",
        "            // Preserve the underlying AFC failure instead of reporting a missing app.\n            throw error",
        "preserve staging failure"))
    # A deferred probe is not proof of device connectivity. Preserve the cheap
    # idle path without turning Result.success(false) into a green Ready badge.
    my_apps = sidestore / "AltStore/My Apps/MyAppsViewController.swift"
    def readiness_indicator(text):
        for value in ("status", "result"):
            text = replace_once(text, f"updateStatusDot(isReady: {value}.isSuccess)", f'''switch {value} {{
                    case .success(let ready): updateStatusDot(isReady: ready ? true : nil)
                    case .failure: updateStatusDot(isReady: false)
                    }}''', "honor deferred readiness")
        text = replace_once(text, "private func updateStatusDot(isReady: Bool)",
                            "private func updateStatusDot(isReady: Bool?)", "unknown readiness indicator")
        return replace_once(text, "let targetColor: UIColor = isReady ? .systemGreen : .systemRed",
                            "let targetColor: UIColor = isReady == nil ? .systemGray : (isReady == true ? .systemGreen : .systemRed)",
                            "neutral idle indicator")
    edit(my_apps, "private func updateStatusDot(isReady: Bool?)", readiness_indicator)
    health = sidestore / "SideStore/Views/Settings/TechyThings/HealthCheck"
    edit(health / "HealthCheckView.swift", 'Text("Not Checked")', lambda text: replace_once(
        text, "                        case .success:", '''                        case .success(false):
                            Image(systemName: "clock")
                                .foregroundColor(.secondary)
                            Text("Not Checked")
                                .font(.title2)
                        case .success(true):''', "deferred health check is not success"))
    def health_requirements(text):
        text = replace_once(text, "let ipsecSat = isRp ? nil : m.ipsec",
                            "let ipsecSat = (isRp || minimuxer.gateway.coreDeviceTransportEnabled) ? nil : m.ipsec",
                            "IPSec not a CoreDevice requirement")
        return replace_once(text, '''            if !self.isRPPairing {
                self.ipsecSatisfied = network.isIKEv2IPSecAvailable
            }''', '''            self.ipsecSatisfied = (self.isRPPairing || minimuxer.gateway.coreDeviceTransportEnabled)
                ? nil : network.isIKEv2IPSecAvailable''', "consistent reactive IPSec indicator")
    edit(health / "HealthCheckViewModel.swift", "minimuxer.gateway.coreDeviceTransportEnabled", health_requirements)
    verify(minimuxer)


def verify(root):
    checks = {
        "DeviceGateway/idevice/IdeviceGateway.swift": ["tunnel_create_usb(provider, &adapter, &handshake)",
            "COMBINED_COREDEVICE_BATCH_V1", "usesCoreDevice", "afc_client_connect_rsd",
            "installation_proxy_connect_rsd", "syncInstallAppBundle", "STAGED_FILE_SIZE_MATCH"],
        "Common/PairingFile.swift": ["Composite records must use Lockdown/CoreDevice"],
        "Sources/MinimuxerImpl.swift": ["configureRefreshTransport", "hasActiveTransportBatch"],
    }
    for name, needles in checks.items():
        text = (root / name).read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                raise SystemExit(f"Incomplete combined transport: {name}: {needle}")
    if "no ipsec interface (required for lockdown" in (root / "Sources/MinimuxerImpl.swift").read_text(encoding="utf-8"):
        raise SystemExit("Obsolete readiness-only IPSec policy remains")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_combined_transport.py <embedded-minimuxer-root>")
    patch(Path(sys.argv[1]))
    print("Combined CoreDevice source integration verified; device proof still required")
