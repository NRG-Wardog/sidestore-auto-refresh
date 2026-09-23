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
struct V3RefreshAllButton: View {
    var body: some View { Button("Refresh All") {} }
}
struct V3SideStoreAppDetail: View {
    let identifier: String
    var body: some View { Text(identifier) }
}
struct V3AppActions: View {
    let app: V3SideStoreApp
    var body: some View { Button("Fixture action") {} }
}
struct FixtureFrame: Equatable {
    let id: String
    let epoch: Int
    let content: CGRect
    let viewport: CGRect
}
struct FixtureFrameKey: PreferenceKey {
    static var defaultValue: [FixtureFrame] = []
    static func reduce(value: inout [FixtureFrame], nextValue: () -> [FixtureFrame]) {
        value.append(contentsOf: nextValue())
    }
}
struct FixtureGeometryProbe: View {
    @EnvironmentObject private var state: V3RenderingState
    let id: String
    var body: some View {
        GeometryReader { proxy in
            Color.clear.preference(key: FixtureFrameKey.self, value: [FixtureFrame(
                id: id, epoch: state.epoch,
                content: proxy.frame(in: .named("v3-content")),
                viewport: proxy.frame(in: .named("v3-viewport")))])
        }
        .allowsHitTesting(false)
    }
}
struct FixtureScrollMarker: UIViewRepresentable {
    let state: V3RenderingState
    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        state.scrollMarker = view
        return view
    }
    func updateUIView(_ uiView: UIView, context: Context) {}
}
@MainActor final class V3RenderingState: ObservableObject {
    @Published var query = ""
    @Published var textSize: ContentSizeCategory = .large
    @Published var epoch = 0
    @Published var scrollRequest = 0
    var scrollID = ""
    var samples: [FixtureFrame] = []
    weak var scrollMarker: UIView?
}
struct V3RenderingScreen: View {
    @ObservedObject var status: V3SideStoreStatusStore
    @ObservedObject var state: V3RenderingState
    var body: some View {
        NavigationView {
            ScrollViewReader { reader in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        V3HomeServiceHeader(isConnected: true, isLoading: false, onReload: {})
                            .background(FixtureGeometryProbe(id: "home-header"))
                        V3InstalledAppsSection(query: state.query)
                            .background(FixtureGeometryProbe(id: "content"))
                            .background(FixtureScrollMarker(state: state))
                    }
                    .coordinateSpace(name: "v3-content")
                }
                .background(FixtureGeometryProbe(id: "viewport"))
                .coordinateSpace(name: "v3-viewport")
                .onPreferenceChange(FixtureFrameKey.self) { state.samples = $0 }
                .onChange(of: state.scrollRequest) { _ in
                    var transaction = Transaction(animation: nil)
                    transaction.disablesAnimations = true
                    withTransaction(transaction) { reader.scrollTo(state.scrollID, anchor: .center) }
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .environmentObject(status)
        .environmentObject(state)
        .environment(\.sizeCategory, state.textSize)
    }
}
@MainActor final class V3RenderingRunner {
    let window: UIWindow
    let parent = UIViewController()
    let state = V3RenderingState()
    let status = V3SideStoreStatusStore()
    var host: UIHostingController<V3RenderingScreen>!
    var scrollView: UIScrollView? {
        var ancestor = state.scrollMarker?.superview
        while let view = ancestor {
            if let scroll = view as? UIScrollView { return scroll }
            ancestor = view.superview
        }
        return nil
    }
    var failures: [String] = []
    var measurements: [[String: Any]] = []
    var observationDiagnostics: [String: Any] = [:]
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
    func valid(_ frame: CGRect) -> Bool {
        [frame.origin.x, frame.origin.y, frame.size.width, frame.size.height].allSatisfy { $0.isFinite }
            && frame.width > 0 && frame.height > 0
    }
    func near(_ lhs: CGRect, _ rhs: CGRect) -> Bool {
        zip(rect(lhs), rect(rhs)).allSatisfy { abs($0.0 - $0.1) <= 0.5 }
    }
    func contains(_ outer: CGRect, _ inner: CGRect) -> Bool {
        valid(outer) && valid(inner) && inner.minX >= outer.minX - 0.5 && inner.maxX <= outer.maxX + 0.5
            && inner.minY >= outer.minY - 0.5 && inner.maxY <= outer.maxY + 0.5
    }
    func freshSamples() async -> [FixtureFrame] {
        state.epoch += 1
        let epoch = state.epoch
        parent.view.layoutIfNeeded()
        host.view.layoutIfNeeded()
        try? await Task.sleep(nanoseconds: 60_000_000)
        return state.samples.filter { $0.epoch == epoch }
    }
    func viewport(_ scroll: UIScrollView, probe: CGRect) -> CGRect {
        let hostClip = scroll.convert(host.view.bounds, from: host.view)
            .offsetBy(dx: -scroll.bounds.minX - scroll.adjustedContentInset.left,
                      dy: -scroll.bounds.minY - scroll.adjustedContentInset.top)
        return probe.intersection(hostClip)
    }
    func observe(_ id: String, scroll: UIScrollView) async -> (cell: FixtureFrame, content: FixtureFrame, viewport: CGRect, samples: [FixtureFrame])? {
        var previous: (cell: FixtureFrame, content: FixtureFrame, viewport: CGRect, offset: CGPoint, size: CGSize)?
        for attempt in 0..<12 {
            if attempt % 4 == 0 {
                state.scrollID = id
                state.scrollRequest += 1
            }
            let samples = await freshSamples()
            let cells = samples.filter { $0.id == id }
            let contents = samples.filter { $0.id == "content" }
            let viewports = samples.filter { $0.id == "viewport" }
            observationDiagnostics = ["attempt": attempt, "epoch": state.epoch,
                                      "cellProbes": cells.count, "contentProbes": contents.count,
                                      "viewportProbes": viewports.count]
            guard cells.count == 1, contents.count == 1, viewports.count == 1 else { previous = nil; continue }
            let cell = cells[0], content = contents[0]
            let visible = viewport(scroll, probe: viewports[0].viewport)
            let translated = cell.content.offsetBy(dx: content.viewport.minX - content.content.minX,
                                                   dy: content.viewport.minY - content.content.minY)
            guard let marker = state.scrollMarker else { previous = nil; continue }
            let nativeContent = scroll.convert(marker.bounds, from: marker)
                .offsetBy(dx: -scroll.bounds.minX - scroll.adjustedContentInset.left,
                          dy: -scroll.bounds.minY - scroll.adjustedContentInset.top)
            let visibleWidth = scroll.bounds.width - scroll.adjustedContentInset.left - scroll.adjustedContentInset.right
            let visibleHeight = scroll.bounds.height - scroll.adjustedContentInset.top - scroll.adjustedContentInset.bottom
            let minimumY = -scroll.adjustedContentInset.top
            let maximumY = max(minimumY, scroll.contentSize.height - scroll.bounds.height + scroll.adjustedContentInset.bottom)
            observationDiagnostics["checks"] = [
                "minimumOffset": scroll.contentOffset.y >= minimumY - 0.5,
                "maximumOffset": scroll.contentOffset.y <= maximumY + 0.5,
                "horizontalOffset": abs(scroll.contentOffset.x + scroll.adjustedContentInset.left) <= 0.5,
                "viewportWidth": abs(viewports[0].viewport.width - visibleWidth) <= 0.5,
                "viewportHeight": abs(viewports[0].viewport.height - visibleHeight) <= 0.5,
                "nativeContentAgreement": near(content.viewport, nativeContent),
                "visibleCell": contains(visible, cell.viewport),
                "containedCell": contains(content.content, cell.content),
                "coordinateAgreement": near(translated, cell.viewport),
                "notDragging": !scroll.isDragging,
                "notDecelerating": !scroll.isDecelerating
            ]
            observationDiagnostics["nativeContent"] = rect(nativeContent)
            observationDiagnostics["scrollBounds"] = rect(scroll.bounds)
            observationDiagnostics["visibleBounds"] = valid(visible) ? rect(visible) : []
            observationDiagnostics["adjustedInsets"] = [Double(scroll.adjustedContentInset.top), Double(scroll.adjustedContentInset.left), Double(scroll.adjustedContentInset.bottom), Double(scroll.adjustedContentInset.right)]
            observationDiagnostics["contentSize"] = [Double(scroll.contentSize.width), Double(scroll.contentSize.height)]
            guard scroll.contentOffset.y >= minimumY - 0.5, scroll.contentOffset.y <= maximumY + 0.5,
                  abs(scroll.contentOffset.x + scroll.adjustedContentInset.left) <= 0.5,
                  abs(viewports[0].viewport.width - visibleWidth) <= 0.5,
                  abs(viewports[0].viewport.height - visibleHeight) <= 0.5,
                  near(content.viewport, nativeContent),
                  contains(visible, cell.viewport), contains(content.content, cell.content),
                  near(translated, cell.viewport), !scroll.isDragging, !scroll.isDecelerating else { previous = nil; continue }
            if let prior = previous, near(prior.cell.content, cell.content), near(prior.cell.viewport, cell.viewport),
               near(prior.content.content, content.content), near(prior.content.viewport, content.viewport),
               near(prior.viewport, visible), prior.offset == scroll.contentOffset, prior.size == scroll.contentSize {
                return (cell: cell, content: content, viewport: visible, samples: samples)
            }
            previous = (cell: cell, content: content, viewport: visible, offset: scroll.contentOffset, size: scroll.contentSize)
        }
        return nil
    }
    func measureHomeHeader(_ name: String) async {
        let samples = await freshSamples()
        let headers = samples.filter { $0.id == "home-header" }
        let labels = samples.filter { $0.id == "reload-label" }
        let viewports = samples.filter { $0.id == "viewport" }
        guard headers.count == 1, labels.count == 1, viewports.count == 1 else {
            check(false, "\(name): missing fresh Home/Reload Status geometry")
            measurements.append(["case": name, "headerProbeCount": headers.count,
                                 "reloadLabelProbeCount": labels.count, "passed": false])
            return
        }
        let header = headers[0]
        let label = labels[0]
        let viewportBounds = viewports[0].viewport
        check(valid(header.content), "\(name): invalid Home header bounds")
        check(valid(label.content), "\(name): invalid Reload Status label bounds")
        check(contains(header.content, label.content), "\(name): Reload Status escaped its dedicated row")
        check(contains(viewportBounds, header.viewport), "\(name): Home header was clipped by the phone/tablet viewport")
        check(contains(viewportBounds, label.viewport), "\(name): Reload Status was clipped by the phone/tablet viewport")
        measurements.append(["case": name, "headerBounds": rect(header.content),
                             "reloadStatusLabelBounds": rect(label.content),
                             "viewportBounds": rect(viewportBounds), "passed": true])
    }
    func saveScreenshot(_ name: String) {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let renderer = UIGraphicsImageRenderer(bounds: host.view.bounds)
        let screenshot = renderer.image { context in host.view.layer.render(in: context.cgContext) }
        try? screenshot.pngData()?.write(to: documents.appendingPathComponent("\(suite)-\(name).png"))
    }
    func measure(_ name: String) async {
        let expected = status.installedApps.filter { state.query.isEmpty || $0.name.localizedCaseInsensitiveContains(state.query) || $0.bundleID.localizedCaseInsensitiveContains(state.query) }
        let identities = expected.map(\.identifier)
        guard let scroll = scrollView else {
            check(false, "\(name): native scroll viewport not found")
            measurements.append(["case": name, "inputCount": expected.count, "cellCount": 0, "scrollViewFound": false])
            return
        }
        let top = CGPoint(x: -scroll.adjustedContentInset.left, y: -scroll.adjustedContentInset.top)
        scroll.setContentOffset(top, animated: false)
        await settle()
        var collected: [String: CGRect] = [:]
        var visits: [[String: Any]] = []
        var measuredViewport = CGRect.zero
        var measuredContent = CGRect.zero
        var converged = identities.isEmpty
        for pass in 0..<3 where !identities.isEmpty {
            var current: [String: CGRect] = [:]
            for id in identities {
                let before = scroll.contentOffset
                guard let observation = await observe(id, scroll: scroll) else {
                    check(false, "\(name): \(id) did not become fully visible with fresh stable geometry after scrollTo")
                    visits.append(["identity": id, "pass": pass, "reached": false,
                                   "offsetBefore": [Double(before.x), Double(before.y)],
                                   "offsetAfter": [Double(scroll.contentOffset.x), Double(scroll.contentOffset.y)],
                                   "diagnostics": observationDiagnostics,
                                   "samples": state.samples.filter { $0.epoch == state.epoch && valid($0.viewport) && valid($0.content) }.map {
                                       ["identity": $0.id, "epoch": $0.epoch, "bounds": rect($0.content), "viewportBounds": rect($0.viewport)]
                                   }])
                    continue
                }
                let cell = observation.cell
                measuredViewport = observation.viewport
                measuredContent = observation.content.content
                let cells = observation.samples.filter {
                    !["content", "viewport", "home-header", "reload-label"].contains($0.id)
                }
                check(Set(cells.map(\.id)).count == cells.count, "\(name): duplicate native cell probes")
                check(Set(cells.map(\.id)).isSubset(of: Set(identities)), "\(name): unexpected native cell identity")
                check(scroll.contentSize.width <= scroll.bounds.width + 0.5,
                      "\(name): native content overflows horizontally")
                check(observation.content.viewport.minX >= measuredViewport.minX - 0.5
                      && observation.content.viewport.maxX <= measuredViewport.maxX + 0.5,
                      "\(name): native section exceeds horizontal viewport bounds")
                current[id] = cell.content
                visits.append(["identity": id, "pass": pass, "reached": true, "epoch": cell.epoch,
                               "bounds": rect(cell.content), "viewportBounds": rect(cell.viewport),
                               "viewport": rect(measuredViewport), "contentBounds": rect(measuredContent),
                               "contentInViewport": rect(observation.content.viewport),
                               "offsetBefore": [Double(before.x), Double(before.y)],
                               "offsetAfter": [Double(scroll.contentOffset.x), Double(scroll.contentOffset.y)],
                               "contentSize": [Double(scroll.contentSize.width), Double(scroll.contentSize.height)]])
            }
            converged = current.count == identities.count && collected.count == identities.count
                && identities.allSatisfy { id in
                    guard let prior = collected[id], let frame = current[id] else { return false }
                    return near(prior, frame)
                }
            collected = current
            if converged || current.count != identities.count { break }
        }
        if identities.isEmpty {
            let samples = await freshSamples()
            check(samples.filter { !["content", "viewport", "home-header", "reload-label"].contains($0.id) }.isEmpty,
                  "\(name): empty collection retained native cells")
            if let probe = samples.first(where: { $0.id == "viewport" }), let content = samples.first(where: { $0.id == "content" }) {
                measuredViewport = viewport(scroll, probe: probe.viewport)
                measuredContent = content.content
                check(valid(measuredViewport), "\(name): empty collection has invalid viewport")
                check(content.viewport.minX >= measuredViewport.minX - 0.5 && content.viewport.maxX <= measuredViewport.maxX + 0.5,
                      "\(name): empty section exceeds horizontal viewport bounds")
            } else {
                check(false, "\(name): missing fresh empty-state geometry")
            }
        }
        check(converged, "\(name): full-collection geometry did not converge across scroll sweeps")
        let probes: [(key: String, frame: CGRect)] = collected.map { (key: $0.key, frame: $0.value) }
        var rows: [[(key: String, frame: CGRect)]] = []
        for entry in probes.sorted(by: { ($0.frame.minY, $0.frame.minX) < ($1.frame.minY, $1.frame.minX) }) {
            if let anchor = rows.last?.first, abs(anchor.frame.minY - entry.frame.minY) <= 1 {
                rows[rows.count - 1].append(entry)
            } else {
                rows.append([entry])
            }
        }
        let frames = rows.flatMap { $0.sorted { $0.frame.minX < $1.frame.minX } }
        check(frames.map(\.key) == identities, "\(name): native SideStore cell identity/order mismatch")
        for i in frames.indices {
            for j in frames.indices where j > i {
                let intersection = frames[i].frame.intersection(frames[j].frame)
                check(intersection.isNull || intersection.width <= 0.5 || intersection.height <= 0.5,
                      "\(name): native SideStore cells overlap: \(frames[i].key), \(frames[j].key)")
            }
        }
        measurements.append(["case": name, "inputCount": expected.count, "cellCount": frames.count,
                             "viewport": rect(measuredViewport), "contentBounds": rect(measuredContent),
                             "frameCoordinateSpace": "v3-content", "converged": converged, "scrollVisits": visits,
                             "frames": frames.map { ["identity": $0.key, "bounds": rect($0.frame)] },
                             "labels": UserDefaults.standard.bool(forKey: "LCShowAppLabels")])
        scroll.setContentOffset(top, animated: false)
        await settle()
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
        await measureHomeHeader("home-header-initial")
        if suite == "phone" {
            await resize(320)
            await measureHomeHeader("reload-status-phone-width-320")
            saveScreenshot("reload-status-phone-width-320")
            await resize(min(window.bounds.width, 390))
        } else {
            await resize(min(window.bounds.width, 1024))
            await measureHomeHeader("reload-status-tablet-width-1024")
            saveScreenshot("reload-status-tablet-width-1024")
            await resize(min(window.bounds.width, 390))
        }
        await measure(cold ? "native-cold-grid" : "native-initial-list")
        if !cold {
            for layout in ["grid", "compactList", "list", "grid", "list", "compactList", "grid"] {
                UserDefaults.standard.set(layout, forKey: "LCAppLayoutStyle")
                await settle()
                await measure("native-transition-\(layout)")
            }
            for labels in [false, true] {
                UserDefaults.standard.set(labels, forKey: "LCShowAppLabels")
                await settle()
                await measure("native-labels-\(labels)")
            }
            for width in [CGFloat(320), 375, 390, 600, 768, 844, 1024] where width <= window.bounds.width {
                await resize(width)
                await measure("native-resize-\(Int(width))")
                await resize(width, category: .accessibilityExtraExtraExtraLarge)
                await measure("native-accessibility-\(Int(width))")
            }
            await resize(min(window.bounds.width, 844), height: 320)
            await measure("native-landscape-shaped-window")
            await resize(min(window.bounds.width, 390))
            state.query = "fixture 1"
            await settle()
            await measure("native-filter-name")
            state.query = "fixture.app.3"
            await settle()
            await measure("native-filter-bundle")
            state.query = ""
            status.installedApps.reverse()
            await settle()
            await measure("native-live-reorder")
            status.installedApps = []
            await settle()
            await measure("native-empty-state")
            status.installedApps = (0..<6).map(V3SideStoreApp.init)
            await settle()
            await measure("native-repopulate")
        }
        saveScreenshot("native-\(cold ? "cold" : "suite")")
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
