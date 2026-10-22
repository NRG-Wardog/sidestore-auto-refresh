// LifetimeProbe — a measurement app, not an installer or a trust verifier.
// Paste into a new SwiftUI iOS App target; replace that target's generated @main.
// Intended baseline: iOS 15+, Swift 5 language mode, nonisolated default isolation.
// Native compile/device: NOT RUN.

import SwiftUI
import UIKit
import Foundation
import CryptoKit
import Network
import UniformTypeIdentifiers

@main
@MainActor
struct LifetimeProbeApp: App {
    var body: some Scene {
        WindowGroup {
            LifetimeProbeView()
        }
    }
}

private enum ProbeProcess {
    // Created once per process, not once per receipt or view appearance.
    static let launchID = UUID().uuidString.lowercased()
}

private struct PlatformObservation: Encodable, Sendable {
    let system_version: String
}

private struct TypeObservation: Encodable, Sendable {
    let queried_filename_extension: String
    let resolved_identifier: String?
    let preferred_mime_type: String?
    let is_declared: Bool?
    let is_dynamic: Bool?
    let interpretation: String

    static func mobileProvision() -> TypeObservation {
        let resolved = UTType(filenameExtension: "mobileprovision")
        return TypeObservation(
            queried_filename_extension: "mobileprovision",
            resolved_identifier: resolved?.identifier,
            preferred_mime_type: resolved?.preferredMIMEType,
            is_declared: resolved?.isDeclared,
            is_dynamic: resolved?.isDynamic,
            interpretation: "Type registry observation only; does not establish import, installation, or renewal support."
        )
    }
}

private struct PathObservation: Encodable, Sendable {
    let observed_at_utc: String
    let status: String
    let uses_interface_types: [String]
    let is_expensive: Bool?
    let is_constrained: Bool?
    let supports_ipv4: Bool?
    let supports_ipv6: Bool?
    let interpretation: String

    static func from(_ path: NWPath) -> PathObservation {
        let status: String
        switch path.status {
        case .satisfied: status = "satisfied"
        case .unsatisfied: status = "unsatisfied"
        case .requiresConnection: status = "requires_connection"
        @unknown default: status = "unknown"
        }
        let candidates: [(NWInterface.InterfaceType, String)] = [
            (.wifi, "wifi"), (.cellular, "cellular"),
            (.wiredEthernet, "wired_ethernet"), (.loopback, "loopback"),
            (.other, "other")
        ]
        return PathObservation(
            observed_at_utc: ReceiptCapture.utc(Date()),
            status: status,
            uses_interface_types: candidates.compactMap { candidate in
                path.usesInterfaceType(candidate.0) ? candidate.1 : nil
            },
            is_expensive: path.isExpensive,
            is_constrained: path.isConstrained,
            supports_ipv4: path.supportsIPv4,
            supports_ipv6: path.supportsIPv6,
            interpretation: "One foreground NWPath observation; not a radio-state audit or proof of the route used by another process."
        )
    }

    static func timedOut() -> PathObservation {
        PathObservation(
            observed_at_utc: ReceiptCapture.utc(Date()), status: "timed_out",
            uses_interface_types: [], is_expensive: nil, is_constrained: nil,
            supports_ipv4: nil, supports_ipv6: nil,
            interpretation: "The nominal two-second timeout completed before a path callback; scheduling or suspension may delay completion."
        )
    }
}

// NWPath callbacks and timeout completion are serialized on this private queue.
// No packet, remote request, VPN activation, or network-setting change is made.
private final class OneShotPathProbe: @unchecked Sendable {
    private let queue = DispatchQueue(label: "LifetimeProbe.pathObservation")
    private let monitor = NWPathMonitor()
    private var continuation: CheckedContinuation<PathObservation, Never>?

    private init(_ continuation: CheckedContinuation<PathObservation, Never>) {
        self.continuation = continuation
    }

    static func observe() async -> PathObservation {
        await withCheckedContinuation { continuation in
            let probe = OneShotPathProbe(continuation)
            probe.start()
        }
    }

    private func start() {
        monitor.pathUpdateHandler = { [self] path in
            finish(PathObservation.from(path))
        }
        monitor.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 2.0) { [self] in
            finish(PathObservation.timedOut())
        }
    }

    private func finish(_ observation: PathObservation) {
        guard let pending = continuation else { return }
        continuation = nil
        monitor.pathUpdateHandler = nil
        monitor.cancel()
        pending.resume(returning: observation)
    }
}

private struct LifetimeReceipt: Encodable, Sendable {
    let schema_version: Int
    let captured_at_utc: String
    let uptime_seconds: Double
    let boot_epoch_estimate_utc: String
    let launch_id: String
    let bundle_id_sha256: String
    let version_sha256: String
    let executable_sha256: String
    let embedded_profile_sha256: String?
    let canary_sha256: String
    let platform: PlatformObservation
    let canary_was_created: Bool
    let uti_observation: TypeObservation
    let network_observation: PathObservation?
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case schema_version, captured_at_utc, uptime_seconds, boot_epoch_estimate_utc
        case launch_id, bundle_id_sha256, version_sha256, executable_sha256
        case embedded_profile_sha256, canary_sha256, platform, canary_was_created
        case uti_observation, network_observation, limitations
    }

    func encode(to encoder: Encoder) throws {
        var output = encoder.container(keyedBy: CodingKeys.self)
        try output.encode(schema_version, forKey: .schema_version)
        try output.encode(captured_at_utc, forKey: .captured_at_utc)
        try output.encode(uptime_seconds, forKey: .uptime_seconds)
        try output.encode(boot_epoch_estimate_utc, forKey: .boot_epoch_estimate_utc)
        try output.encode(launch_id, forKey: .launch_id)
        try output.encode(bundle_id_sha256, forKey: .bundle_id_sha256)
        try output.encode(version_sha256, forKey: .version_sha256)
        try output.encode(executable_sha256, forKey: .executable_sha256)
        if let profileHash = embedded_profile_sha256 {
            try output.encode(profileHash, forKey: .embedded_profile_sha256)
        } else {
            try output.encodeNil(forKey: .embedded_profile_sha256)
        }
        try output.encode(canary_sha256, forKey: .canary_sha256)
        try output.encode(platform, forKey: .platform)
        try output.encode(canary_was_created, forKey: .canary_was_created)
        try output.encode(uti_observation, forKey: .uti_observation)
        try output.encodeIfPresent(network_observation, forKey: .network_observation)
        try output.encode(limitations, forKey: .limitations)
    }
}

private struct ReceiptArtifact: Sendable {
    let json: Data
    let url: URL
    let capturedAt: String
}

private enum CaptureFailure: Error {
    case identityUnavailable
    case executableUnavailable
    case canaryInvalid
}

private enum ReceiptCapture {
    private static let canaryLock = NSLock()

    static func utc(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    static func hash(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func hashFile(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var digest = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            digest.update(data: chunk)
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func canary() throws -> (hash: String, created: Bool) {
        // Also protect against concurrent captures if a project enables scenes.
        canaryLock.lock()
        defer { canaryLock.unlock() }
        let manager = FileManager.default
        let base = try manager.url(
            for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        )
        let directory = base.appendingPathComponent("LifetimeProbe", isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let location = directory.appendingPathComponent("persistent-canary-v1.bin")
        if manager.fileExists(atPath: location.path) {
            let data = try Data(contentsOf: location)
            guard data.count == 32 else { throw CaptureFailure.canaryInvalid }
            return (hash(data), false)
        }
        var generator = SystemRandomNumberGenerator()
        let data = Data((0..<32).map { _ in UInt8.random(in: .min ... .max, using: &generator) })
        try data.write(to: location, options: [.atomic, .completeFileProtection])
        // Read back the persisted bytes rather than reporting only the candidate.
        let persisted = try Data(contentsOf: location)
        guard persisted == data else { throw CaptureFailure.canaryInvalid }
        return (hash(persisted), true)
    }

    static func create(systemVersion: String, network: PathObservation?) throws -> ReceiptArtifact {
        let bundle = Bundle.main
        guard let bundleID = bundle.bundleIdentifier,
              let shortVersion = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
              let buildVersion = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        else { throw CaptureFailure.identityUnavailable }
        guard let executable = bundle.executableURL else {
            throw CaptureFailure.executableUnavailable
        }

        let executableHash = try hashFile(executable)
        let profileURL = bundle.bundleURL.appendingPathComponent("embedded.mobileprovision")
        let profileHash: String?
        if FileManager.default.fileExists(atPath: profileURL.path) {
            profileHash = try hashFile(profileURL)
        } else {
            profileHash = nil
        }
        let dataCanary = try canary()
        let capturedAt = Date()
        let uptime = ProcessInfo.processInfo.systemUptime
        let receipt = LifetimeReceipt(
            schema_version: 1,
            captured_at_utc: utc(capturedAt),
            uptime_seconds: uptime,
            boot_epoch_estimate_utc: utc(capturedAt.addingTimeInterval(-uptime)),
            launch_id: ProbeProcess.launchID,
            bundle_id_sha256: hash(Data(bundleID.utf8)),
            version_sha256: hash(Data((shortVersion + "\n" + buildVersion).utf8)),
            executable_sha256: executableHash,
            embedded_profile_sha256: profileHash,
            canary_sha256: dataCanary.hash,
            platform: PlatformObservation(system_version: systemVersion),
            canary_was_created: dataCanary.created,
            uti_observation: TypeObservation.mobileProvision(),
            network_observation: network,
            limitations: [
                "This is an untrusted self-report, not cryptographic attestation or an installation result.",
                "Only this running app's own bundle and own data container were inspected.",
                "Executable hash covers one on-disk file, not all frameworks, extensions, or a separate debug dylib.",
                "Embedded profile bytes are hashed, not parsed, authenticated, or checked for revocation.",
                "Effective system profile-store state and installed profiles for extensions are not inspected.",
                "A separate profile-store renewal may leave embedded profile bytes unchanged.",
                "Wall clock and uptime are observations; their difference does not prove a reboot or clock integrity.",
                "NWPath cannot prove Wi-Fi is off or that no computer, USB, relay, VPN, or radio toggle participated.",
                "One fresh launch does not establish validity through the whole proposed renewal period.",
                "Hashes enable comparison and are not anonymization of predictable identifiers."
            ]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        var payload = try encoder.encode(receipt)
        payload.append(0x0a)

        let manager = FileManager.default
        let cache = try manager.url(
            for: .cachesDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        ).appendingPathComponent("LifetimeProbeReceipts", isDirectory: true)
        try manager.createDirectory(at: cache, withIntermediateDirectories: true)
        let file = cache.appendingPathComponent("receipt-" + UUID().uuidString.lowercased() + ".json")
        try payload.write(to: file, options: [.atomic, .completeFileProtection])
        return ReceiptArtifact(json: payload, url: file, capturedAt: receipt.captured_at_utc)
    }
}

@MainActor
private final class LifetimeProbeModel: ObservableObject {
    @Published var isCapturing = false
    @Published var status = "No receipt captured."
    @Published var preview = ""
    @Published var latestURL: URL?

    func capture(includePath: Bool) {
        guard !isCapturing else { return }
        guard UIApplication.shared.applicationState == .active else {
            status = "Bring the app to the foreground before capturing."
            return
        }
        isCapturing = true
        latestURL = nil
        preview = ""
        status = "Reading this app's own files…"
        let systemVersion = UIDevice.current.systemVersion

        Task {
            defer { isCapturing = false }
            do {
                let network: PathObservation?
                if includePath {
                    network = await OneShotPathProbe.observe()
                } else {
                    network = nil
                }
                let artifact = try await Task.detached(priority: .userInitiated) {
                    try ReceiptCapture.create(systemVersion: systemVersion, network: network)
                }.value
                preview = String(data: artifact.json, encoding: .utf8) ?? ""
                latestURL = artifact.url
                status = "Captured at " + artifact.capturedAt
            } catch {
                // Avoid exposing own filesystem paths through arbitrary error text.
                status = "Capture failed. No complete receipt was created. Check the app's own file access; an existing invalid canary is never replaced automatically."
            }
        }
    }
}

private struct ShareReceipt: Identifiable {
    let id = UUID()
    let url: URL
}

private struct ReceiptShareSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

@MainActor
private struct LifetimeProbeView: View {
    @StateObject private var model = LifetimeProbeModel()
    @State private var includePath = false
    @State private var share: ShareReceipt?

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("Own-app evidence")) {
                    Text("Capture this running app's executable, embedded profile bytes and persistent data canary. Compare receipts with the separate experiment record.")
                    Text("This app does not install profiles or apps, authenticate CMS data, or infer renewal success.")
                        .font(.footnote)
                }
                Section(header: Text("Capture")) {
                    Toggle("Include a foreground network-path observation", isOn: $includePath)
                        .disabled(model.isCapturing)
                    Text("The optional observation has a nominal two-second timeout while the app runs. It cannot establish that Wi-Fi is off. No network request is sent.")
                        .font(.footnote)
                    Button("Capture receipt") {
                        model.capture(includePath: includePath)
                    }
                    .disabled(model.isCapturing)
                    if model.isCapturing { ProgressView() }
                    Text(model.status).font(.footnote)
                    if let url = model.latestURL {
                        Button("Share JSON…") { share = ShareReceipt(url: url) }
                    }
                }
                if !model.preview.isEmpty {
                    Section(header: Text("Receipt JSON")) {
                        Text(model.preview)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
            }
            .navigationTitle("Lifetime Probe")
        }
        .navigationViewStyle(.stack)
        .sheet(item: $share) { item in
            ReceiptShareSheet(url: item.url)
        }
    }
}
