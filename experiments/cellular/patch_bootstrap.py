"""Opt-in embedded bootstrap patches. Does not dispatch CI or change releases."""
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parent


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Pinned source anchor changed: {old[:90]}")
    return text.replace(old, new, 1)


def patch_rust(root):
    """Run after the validated CoreDevice patch, before header generation."""
    path = root / "ffi/src/lockdown.rs"
    text = path.read_text(encoding="utf-8")
    addition = (ROOT / "bootstrap/lockdown_validation.rs").read_text(encoding="utf-8")
    if "fn cellular_lockdown_validate(" not in text:
        path.write_text(text + addition, encoding="utf-8")
    elif not text.endswith(addition):
        raise ValueError("Existing cellular Lockdown helper differs from template")

    path = root / "ffi/src/tunnel_provider.rs"
    text = path.read_text(encoding="utf-8")
    if "fn cellular_tunnel_create(" in text:
        return
    # One implementation serves production and diagnostics. The original ABI
    # passes null and has no behavior change. Never intercept the global logger.
    old = '''pub unsafe extern "C" fn tunnel_create_usb(
    lockdown_provider: *mut IdeviceProviderHandle,
    out_adapter: *mut *mut AdapterHandle,
    out_handshake: *mut *mut RsdHandshakeHandle,
) -> *mut IdeviceFfiError {'''
    new = old + '''
    unsafe { cellular_tunnel_create(lockdown_provider, out_adapter, out_handshake, std::ptr::null_mut()) }
}

/// Same production tunnel with optional diagnostic stage observation.
/// stage: 0 CoreDevice not complete, 1 CoreDevice complete, 2 RSD complete.
/// # Safety
/// The provider and outputs follow tunnel_create_usb ownership requirements.
/// stage may be null, otherwise it must be writable for the entire call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cellular_tunnel_create(
    lockdown_provider: *mut IdeviceProviderHandle,
    out_adapter: *mut *mut AdapterHandle,
    out_handshake: *mut *mut RsdHandshakeHandle,
    stage: *mut u32,
) -> *mut IdeviceFfiError {
    if !stage.is_null() { unsafe { *stage = 0 }; }
'''
    text = once(text, old, new)
    text = once(text, '                transport_log("[SIDESTORE_COREDEVICE] TUNNEL_COREDEVICE_CONNECT_PASS");',
                '''                if !stage.is_null() { unsafe { *stage = 1 }; }
                transport_log("[SIDESTORE_COREDEVICE] TUNNEL_COREDEVICE_CONNECT_PASS");''')
    text = once(text, '        transport_log("[SIDESTORE_COREDEVICE] TUNNEL_RSD_HANDSHAKE_PASS");',
                '''        if !stage.is_null() { unsafe { *stage = 2 }; }
        transport_log("[SIDESTORE_COREDEVICE] TUNNEL_RSD_HANDSHAKE_PASS");''')
    path.write_text(text, encoding="utf-8")


def edit(path, marker, transform):
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        path.write_text(transform(text), encoding="utf-8")


def template(name):
    return (ROOT / "bootstrap" / name).read_text(encoding="utf-8")


def patch_side(side):
    mux = side / "Dependencies/minimuxer"
    gateway = mux / "DeviceGateway/idevice/IdeviceGateway.swift"
    if "COMBINED_COREDEVICE_BATCH_V1" not in gateway.read_text(encoding="utf-8"):
        raise ValueError("Apply the combined production transport patch first")
    for name in ("BootstrapState.swift", "ProbeTypes.swift", "NetworkUtils.swift"):
        (mux / "Common" / name).write_text(template(name), encoding="utf-8")
    edit(mux / "Common/Package.swift", '"BootstrapState.swift"', lambda t: once(
        t, '"NetworkUtils.swift",', '"NetworkUtils.swift",\n                "BootstrapState.swift",\n                "ProbeTypes.swift",'))
    edit(gateway, "CELLULAR_BOOTSTRAP_GATEWAY_V1", lambda t: once(
        t, "    private func ensureCoreDeviceConnection() throws {",
        template("gateway.swift") + "\n    private func ensureCoreDeviceConnection() throws {"))
    api = mux / "DeviceGateway/DeviceGatewayAPI.swift"
    def gateway_api(t):
        t = once(t, "public protocol DeviceGatewayAPI: AnyObject, Sendable {", '''public protocol DeviceGatewayAPI: AnyObject, Sendable {
    func cellularValidatePairing(_ content: String) async -> Bool
    func cellularReadOnlyProbe(_ content: String, discovery: CellularProbeDiscovery, expectedConfiguration: String?) async -> CellularProbeResult''')
        return once(t, "public extension DeviceGatewayAPI {", '''public extension DeviceGatewayAPI {
    func cellularValidatePairing(_ content: String) async -> Bool { false }
    func cellularReadOnlyProbe(_ content: String, discovery: CellularProbeDiscovery, expectedConfiguration: String?) async -> CellularProbeResult {
        var result = CellularProbeResult()
        result.failureStage = "UNSUPPORTED_GATEWAY"
        return result
    }''')
    edit(api, "func cellularValidatePairing", gateway_api)
    manager = mux / "Sources/Services/DeviceConnectionManager.swift"
    edit(manager, "func cellularProbeDiscovery", lambda t: once(
        t, "    private struct CandidatePeer:", template("discovery.swift") + "\n    private struct CandidatePeer:"))
    edit(mux / "Sources/MinimuxerApi.swift", "func cellularProbeDiscovery", lambda t: once(
        t, "public protocol MinimuxerAPI: AnyObject {", '''public protocol MinimuxerAPI: AnyObject {
    func cellularProbeDiscovery() async -> CellularProbeDiscovery'''))
    edit(mux / "Sources/MinimuxerImpl.swift", "func cellularProbeDiscovery", lambda t: once(
        t, "    func getConnectionMode()", '''    func cellularProbeDiscovery() async -> CellularProbeDiscovery {
        await connectionManager.cellularProbeDiscovery()
    }

    func getConnectionMode()'''))
    manager = side / "SideStore/Core/Pairing/PairingFileManager.swift"
    edit(manager, "func saveDiagnosticPairing", lambda t: once(
        t, "    func savePairingFile(contents: String) throws {", '''    // Canonical destination, validated before replacement. Never delete the old
    // record first. Rename is atomic within this directory; errors retain it.
    func saveDiagnosticPairing(contents: String) throws {
        let parsed = try PairingFileParser.parse(content: contents)
        guard parsed.mode == .lockdown else { throw CocoaError(.fileReadCorruptFile) }
        let fm = FileManager.default
        let destination = fm.documentsDirectory.appendingPathComponent(Self.pairingFileName)
        var temporary = destination.deletingLastPathComponent().appendingPathComponent(".pairing-import-" + UUID().uuidString)
        defer { try? fm.removeItem(at: temporary) }
        try parsed.rawData.write(to: temporary, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try temporary.setResourceValues(values)
        let renamed = temporary.path.withCString { source in
            destination.path.withCString { target in Darwin.rename(source, target) }
        }
        guard renamed == 0 else { throw CocoaError(.fileWriteUnknown) }
        UserDefaults.standard.isPairingReset = false
    }

    func savePairingFile(contents: String) throws {''').replace(
            "import UniformTypeIdentifiers", "import UniformTypeIdentifiers\nimport Darwin", 1))
    view = side / "SideStore/Views/Settings/Diagnostics/ExperimentalFeaturesView.swift"
    def screen(t):
        t = once(t, "                // Section 1: STANDALONE FEATURES", '''                NavigationLink(destination: CellularBootstrapView()) {
                    Label("Cellular Diagnostics", systemImage: "antenna.radiowaves.left.and.right")
                }
                // Section 1: STANDALONE FEATURES''')
        return t + template("screen.swift")
    edit(view, "CELLULAR_BOOTSTRAP_SCREEN_V1", screen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idevice", type=Path)
    parser.add_argument("--sidestore", type=Path)
    args = parser.parse_args()
    if not args.idevice and not args.sidestore:
        parser.error("Specify --idevice and/or --sidestore")
    if args.idevice:
        patch_rust(args.idevice)
    if args.sidestore:
        patch_side(args.sidestore)
