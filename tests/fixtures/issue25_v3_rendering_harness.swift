import SwiftUI
import UIKit

enum LCUtils { static let appGroupUserDefault = UserDefaults.standard }
@MainActor final class V3ServiceBridge {
    static let shared = V3ServiceBridge()
    func request(operation: String, target: String) async throws -> [String: Any] {
        let image = UIGraphicsImageRenderer(size: CGSize(width: 192, height: 192)).image { context in
            UIColor.systemOrange.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 192, height: 192))
        }
        return ["icon": image.pngData()!]
    }
}
struct V3SideStoreApp: Identifiable {
    let identifier: String
    var id: String { identifier }
    let name: String
    let bundleID: String
    let isActive: Bool
    let version = "1.0"
    let expirationDate: Date? = nil
    let hasUpdate = false
    init(_ index: Int) {
        identifier = "sidestore:fixture-\(index)"
        name = index == 1 ? "SideStore fixture 1 with a deliberately long app name" : "SideStore fixture \(index)"
        bundleID = "fixture.app.\(index)"
        isActive = index % 2 == 0
    }
}
@MainActor final class V3SideStoreStatusStore: ObservableObject {
    @Published var installedApps = (0..<6).map(V3SideStoreApp.init)
    var installedAppCount: Int { installedApps.count }
    let isStale = false
    let loading = false
    func reload() {}
}
struct V3SideStoreAppDetail: View {
    let identifier: String
    var body: some View { Text(identifier) }
}
struct V3AppActions: View {
    let app: V3SideStoreApp
    var body: some View { Button("Fixture action") {} }
}
struct FixtureFrameKey: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}
struct FixtureGeometryProbe: View {
    let id: String
    var body: some View {
        GeometryReader { proxy in
            Color.clear.preference(key: FixtureFrameKey.self, value: [id: proxy.frame(in: .named("v3-rendering-harness"))])
        }
    }
}
@MainActor final class V3RenderingState: ObservableObject {
    @Published var query = ""
    @Published var textSize: ContentSizeCategory = .large
    var frames: [String: CGRect] = [:]
}
struct V3RenderingScreen: View {
    @ObservedObject var status: V3SideStoreStatusStore
    @ObservedObject var state: V3RenderingState
    var body: some View {
        NavigationView {
            ScrollView {
                V3InstalledAppsSection(query: state.query)
            }
            .coordinateSpace(name: "v3-rendering-harness")
            .onPreferenceChange(FixtureFrameKey.self) { state.frames = $0 }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .environmentObject(status)
        .environment(\.sizeCategory, state.textSize)
    }
}
@MainActor final class V3RenderingRunner {
    let window: UIWindow
    let parent = UIViewController()
    let state = V3RenderingState()
    let status = V3SideStoreStatusStore()
    var host: UIHostingController<V3RenderingScreen>!
    var failures: [String] = []
    var measurements: [[String: Any]] = []
    var cold: Bool { ProcessInfo.processInfo.arguments.contains("--cold") }
    var suite: String { ProcessInfo.processInfo.arguments.contains("--tablet") ? "tablet" : "phone" }
    init(window: UIWindow) { self.window = window }
    func check(_ condition: Bool, _ message: String) { if !condition { failures.append(message) } }
    func rect(_ frame: CGRect) -> [Double] { [Double(frame.minX), Double(frame.minY), Double(frame.width), Double(frame.height)] }
    func settle() async {
        for _ in 0..<4 {
            parent.view.layoutIfNeeded()
            host.view.layoutIfNeeded()
            try? await Task.sleep(nanoseconds: 80_000_000)
        }
    }
    func resize(_ width: CGFloat, height: CGFloat? = nil, category: ContentSizeCategory = .large) async {
        host.view.frame = CGRect(x: 0, y: 0, width: min(width, window.bounds.width), height: min(height ?? window.bounds.height, window.bounds.height))
        parent.setOverrideTraitCollection(UITraitCollection(horizontalSizeClass: width < 600 ? .compact : .regular), forChild: host)
        state.textSize = category
        await settle()
    }
    func measure(_ name: String) {
        let expected = status.installedApps.filter { state.query.isEmpty || $0.name.localizedCaseInsensitiveContains(state.query) || $0.bundleID.localizedCaseInsensitiveContains(state.query) }
        let frames = state.frames.sorted {
            // LazyVGrid's default vertical alignment is center: different label
            // heights share a row center, not a top edge. JSON records all raw
            // frames so this ordering assertion can be independently checked.
            abs($0.value.midY - $1.value.midY) > 1 ? $0.value.midY < $1.value.midY : $0.value.minX < $1.value.minX
        }
        check(frames.map(\.key) == expected.map(\.identifier), "\(name): native SideStore cell identity/order mismatch")
        for (id, frame) in frames {
            check(frame.width > 0 && frame.height > 0, "\(name): \(id) has non-positive bounds")
            check(frame.intersects(host.view.bounds), "\(name): \(id) is not visible")
        }
        for i in frames.indices {
            for j in frames.indices where j > i {
                let intersection = frames[i].value.intersection(frames[j].value)
                check(intersection.width <= 0.5 || intersection.height <= 0.5, "\(name): native SideStore cells overlap")
            }
        }
        measurements.append(["case": name, "inputCount": expected.count, "cellCount": frames.count,
                             "viewport": rect(host.view.bounds), "frames": frames.map { ["identity": $0.key, "bounds": rect($0.value)] },
                             "labels": UserDefaults.standard.bool(forKey: "LCShowAppLabels")])
    }
    func run() async {
        if cold {
            check(UserDefaults.standard.string(forKey: "LCAppLayoutStyle") == "grid", "native cold launch lost Grid preference")
        } else {
            UserDefaults.standard.set("list", forKey: "LCAppLayoutStyle")
            UserDefaults.standard.set(true, forKey: "LCShowAppLabels")
        }
        host = UIHostingController(rootView: V3RenderingScreen(status: status, state: state))
        window.rootViewController = parent
        parent.addChild(host)
        parent.view.addSubview(host.view)
        host.didMove(toParent: parent)
        window.makeKeyAndVisible()
        await resize(min(window.bounds.width, 390))
        measure(cold ? "native-cold-grid" : "native-initial-list")
        if !cold {
            for layout in ["grid", "compactList", "list", "grid", "list", "compactList", "grid"] {
                UserDefaults.standard.set(layout, forKey: "LCAppLayoutStyle")
                await settle()
                measure("native-transition-\(layout)")
            }
            for labels in [false, true] {
                UserDefaults.standard.set(labels, forKey: "LCShowAppLabels")
                await settle()
                measure("native-labels-\(labels)")
            }
            for width in [CGFloat(320), 375, 390, 600, 768, 844, 1024] where width <= window.bounds.width {
                await resize(width)
                measure("native-resize-\(Int(width))")
                await resize(width, category: .accessibilityExtraExtraExtraLarge)
                measure("native-accessibility-\(Int(width))")
            }
            await resize(min(window.bounds.width, 844), height: 320)
            measure("native-landscape-shaped-window")
            await resize(min(window.bounds.width, 390))
            state.query = "fixture 1"
            await settle()
            measure("native-filter-name")
            state.query = "fixture.app.3"
            await settle()
            measure("native-filter-bundle")
            state.query = ""
            status.installedApps.reverse()
            await settle()
            measure("native-live-reorder")
            status.installedApps = []
            await settle()
            measure("native-empty-state")
            status.installedApps = (0..<6).map(V3SideStoreApp.init)
            await settle()
            measure("native-repopulate")
        }
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let renderer = UIGraphicsImageRenderer(bounds: host.view.bounds)
        let screenshot = renderer.image { context in host.view.layer.render(in: context.cgContext) }
        try? screenshot.pngData()?.write(to: documents.appendingPathComponent("\(suite)-native-\(cold ? "cold" : "suite").png"))
        let report: [String: Any] = ["schemaVersion": 1, "mode": "v3-native", "phase": cold ? "cold" : "suite", "deviceClass": suite,
                                   "os": UIDevice.current.systemVersion, "passed": failures.isEmpty, "failures": failures, "measurements": measurements,
                                   "evidenceKind": "production V3InstalledAppsSection with non-layout-affecting background geometry probes and controlled app/status dependencies",
                                   "limitations": ["Navigation destinations and mutation menus are controlled stubs", "No physical device or service/database operations", "Not the full v3 application"]]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: documents.appendingPathComponent("\(suite)-\(cold ? "cold" : "suite").json"), options: .atomic)
        } catch { print("V3_RENDERING_REPORT_FAILED") }
    }
}
@main final class V3RenderingAppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var runner: V3RenderingRunner?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        self.window = window
        let runner = V3RenderingRunner(window: window)
        self.runner = runner
        Task { @MainActor in await runner.run() }
        return true
    }
}
