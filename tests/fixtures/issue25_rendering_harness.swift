import SwiftUI
import UIKit

@MainActor final class RenderingState: ObservableObject {
    @Published var apps = (0..<6).map(LCAppModel.init)
    @Published var textSize: ContentSizeCategory = .large
}

struct RenderingScreen: View, LCAppBannerDelegate {
    @ObservedObject var state: RenderingState
    @AppStorage("LCAppLayoutStyle") var style: AppLayoutStyle = .list
    @AppStorage("LCShowAppLabels") var labels = true
    private let columns = [GridItem(.adaptive(minimum: 76, maximum: 100), spacing: 16, alignment: .top)]
    var body: some View {
        ScrollView {
            if state.apps.isEmpty {
                Text("No fixture apps").accessibilityIdentifier("fixture-empty")
            } else {
                collection.padding()
            }
        }
        .environment(\.sizeCategory, state.textSize)
    }
    @ViewBuilder private var collection: some View {
        switch style {
        case .grid:
            LazyVGrid(columns: columns, spacing: 16) {
                ForEach(state.apps, id: \.self) { app in
                    LCGridAppCell(appModel: app, delegate: self, showLabels: labels)
                }
            }
        case .list, .compactList:
            LazyVStack(spacing: style == .compactList ? 8 : 8) {
                ForEach(state.apps, id: \.self) { app in
                    LCAppBanner(appModel: app, delegate: self, layoutStyle: style)
                }
            }
        }
    }
    func removeApp(app: LCAppModel) {}
    func installMdm(data: Data) {}
    func openNavigationView(view: AnyView) {}
    func promptForGeneratedIconStyle() async -> GeneratedIconStyle? { nil }
}

@MainActor final class RenderingRunner {
    let window: UIWindow
    let parent = UIViewController()
    let state = RenderingState()
    var host: UIHostingController<RenderingScreen>!
    var measurements: [[String: Any]] = []
    var failures: [String] = []
    let baseline: Bool
    let cold: Bool
    let suite: String
    init(window: UIWindow) {
        self.window = window
        baseline = ProcessInfo.processInfo.arguments.contains("--baseline")
        cold = ProcessInfo.processInfo.arguments.contains("--cold")
        suite = ProcessInfo.processInfo.arguments.contains("--tablet") ? "tablet" : "phone"
    }
    func check(_ value: Bool, _ message: String) { if !value { failures.append(message) } }
    func waitForLayout() async {
        for _ in 0..<8 {
            parent.view.setNeedsLayout()
            parent.view.layoutIfNeeded()
            host.view.setNeedsLayout()
            host.view.layoutIfNeeded()
            try? await Task.sleep(nanoseconds: 70_000_000)
        }
    }
    func resize(_ width: CGFloat, height: CGFloat? = nil, category: ContentSizeCategory = .large) async {
        host.view.frame = CGRect(x: 0, y: 0, width: min(width, window.bounds.width), height: min(height ?? window.bounds.height, window.bounds.height))
        parent.setOverrideTraitCollection(UITraitCollection(horizontalSizeClass: width < 600 ? .compact : .regular), forChild: host)
        state.textSize = category
        await waitForLayout()
    }
    func controllers(_ parent: UIViewController) -> [UIViewController] {
        [parent] + parent.children.flatMap(controllers)
    }
    func descendants(_ parent: UIView) -> [UIView] {
        [parent] + parent.subviews.flatMap(descendants)
    }
    func visible(_ view: UIView, in root: UIView) -> Bool {
        var current: UIView? = view
        while let candidate = current {
            if candidate.isHidden || candidate.alpha <= 0.01 { return false }
            if candidate === root { return true }
            current = candidate.superview
        }
        return false
    }
    func rect(_ bounds: CGRect) -> [Double] {
        [Double(bounds.minX), Double(bounds.minY), Double(bounds.width), Double(bounds.height)]
    }
    func constraintValue(_ item: Any?, attribute: NSLayoutConstraint.Attribute, in root: UIView) -> CGFloat? {
        guard let view = item as? UIView else { return nil }
        let frame: CGRect
        if view === root { frame = root.alignmentRect(forFrame: root.bounds) }
        else if let superview = view.superview {
            frame = superview.convert(view.alignmentRect(forFrame: view.frame), to: root)
        } else { return nil }
        switch attribute {
        case .top: return frame.minY
        case .bottom: return frame.maxY
        case .leading, .left: return frame.minX
        case .trailing, .right: return frame.maxX
        case .centerX: return frame.midX
        case .centerY: return frame.midY
        case .width: return frame.width
        case .height: return frame.height
        default: return nil
        }
    }
    func requiredConstraintEvidence(in root: UIView) -> [[String: Any]] {
        // UIKit may break an unsatisfiable required constraint while leaving it
        // active. Check the actual resolved equations, including hidden labels.
        let constraints = root.constraints + root.subviews.filter { !($0 is LCAppBannerRootView) }.flatMap(\.constraints)
        return constraints.compactMap { constraint in
            guard constraint.isActive, constraint.priority == .required,
                  let first = constraintValue(constraint.firstItem, attribute: constraint.firstAttribute, in: root) else { return nil }
            let second: CGFloat
            if constraint.secondItem == nil { second = 0 }
            else if let value = constraintValue(constraint.secondItem, attribute: constraint.secondAttribute, in: root) { second = value }
            else { return nil }
            let difference = first - second * constraint.multiplier - constraint.constant
            let violated: Bool
            switch constraint.relation {
            case .equal: violated = abs(difference) > 0.5
            case .lessThanOrEqual: violated = difference > 0.5
            case .greaterThanOrEqual: violated = difference < -0.5
            @unknown default: violated = true
            }
            let intrinsic = String(describing: type(of: constraint)) == "NSContentSizeLayoutConstraint"
            var effectivePriority = constraint.priority.rawValue
            if intrinsic, let item = constraint.firstItem as? UIView {
                // UIKit's intrinsic wrapper reports an equality/priority1000,
                // but natural size has two directional priorities. Stretching
                // uses hugging; compression uses resistance. A natural19pt
                // symbol is therefore allowed to fill an explicit60pt icon.
                let axis: NSLayoutConstraint.Axis = constraint.firstAttribute == .height ? .vertical : .horizontal
                effectivePriority = difference > 0 ? item.contentHuggingPriority(for: axis).rawValue : item.contentCompressionResistancePriority(for: axis).rawValue
            }
            return ["constraintClass": String(describing: type(of: constraint)),
                    "role": intrinsic ? "intrinsicContentSize" : "explicitConstraint",
                    "reportedPriority": constraint.priority.rawValue, "effectivePriority": effectivePriority,
                    "identifier": String((constraint.identifier ?? "").prefix(120)),
                    "firstClass": (constraint.firstItem as? UIView).map { String(describing: type(of: $0)) } ?? "none",
                    "secondClass": (constraint.secondItem as? UIView).map { String(describing: type(of: $0)) } ?? "none",
                    "firstHidden": (constraint.firstItem as? UIView)?.isHidden ?? false,
                    "secondHidden": (constraint.secondItem as? UIView)?.isHidden ?? false,
                    "firstAttribute": constraint.firstAttribute.rawValue, "secondAttribute": constraint.secondAttribute.rawValue,
                    "relation": constraint.relation.rawValue, "constant": Double(constraint.constant), "multiplier": Double(constraint.multiplier),
                    "firstValue": Double(first), "secondValue": Double(second), "residual": Double(difference), "violated": violated,
                    "requiredViolation": violated && effectivePriority >= UILayoutPriority.required.rawValue]
        }
    }
    func gridControllers() -> [LCGridAppCellViewController] {
        controllers(host).compactMap { $0 as? LCGridAppCellViewController }
            .filter { $0.view.isDescendant(of: host.view) && (baseline || hiddenReason($0.view) == nil) }.sorted {
            let left = $0.view.convert($0.view.bounds, to: host.view)
            let right = $1.view.convert($1.view.bounds, to: host.view)
            return abs(left.minY - right.minY) > 1 ? left.minY < right.minY : left.minX < right.minX
        }
    }
    func hiddenReason(_ root: UIView) -> String? {
        var region = root.convert(root.bounds, to: host.view)
        var current: UIView? = root
        while let candidate = current {
            if candidate.isHidden || candidate.layer.isHidden { return "hidden ancestor" }
            if candidate.alpha <= 0.01 || candidate.layer.opacity <= 0.01 { return "transparent ancestor" }
            if candidate !== root && (candidate.clipsToBounds || candidate.layer.masksToBounds) {
                region = region.intersection(candidate.convert(candidate.bounds, to: host.view))
                if region.isEmpty || region.isNull { return "clipped by ancestor bounds" }
            }
            if candidate === host.view { return nil }
            current = candidate.superview
        }
        return "detached from host"
    }
    func visibilityEvidence(_ root: UIView) -> [[String: Any]] {
        var result: [[String: Any]] = []
        var current: UIView? = root
        while let candidate = current, result.count < 12 {
            result.append(["class": String(describing: type(of: candidate)), "hidden": candidate.isHidden,
                           "layerHidden": candidate.layer.isHidden, "alpha": Double(candidate.alpha),
                           "layerOpacity": Double(candidate.layer.opacity), "clips": candidate.clipsToBounds,
                           "bounds": rect(candidate.bounds)])
            if candidate === host.view { break }
            current = candidate.superview
        }
        return result
    }
    @discardableResult func measure(_ name: String, requireValid: Bool = true) -> Bool {
        let cells = gridControllers()
        let names = cells.compactMap { $0.view.accessibilityLabel }
        let expected = state.apps.map(\.displayName)
        var local: [String] = []
        if names != expected { local.append("identities/order differ: input=\(expected.count), cells=\(names.count)") }
        let frames = cells.map { $0.view.convert($0.view.bounds, to: host.view) }
        var cellEvidence: [[String: Any]] = []
        for (index, cell) in cells.enumerated() {
            let root = cell.view!
            let frame = frames[index]
            if frame.width <= 0 || frame.height <= 0 { local.append("cell \(index) has non-positive bounds") }
            if !frame.intersects(host.view.bounds) || root.isHidden || root.alpha <= 0.01 { local.append("cell \(index) is invisible") }
            let images = descendants(root).compactMap { $0 as? UIImageView }.filter { visible($0, in: root) && $0.bounds.width >= 40 }
            var imageEvidence: [[String: Any]] = []
            for image in images {
                let imageFrame = image.convert(image.bounds, to: root)
                if !root.bounds.insetBy(dx: -0.5, dy: -0.5).contains(imageFrame) { local.append("cell \(index) does not contain its icon") }
                if image.image == nil || image.image?.size.width == 0 { local.append("cell \(index) has no visible icon/fallback") }
                imageEvidence.append(["bounds": rect(imageFrame), "hasImage": image.image != nil, "imageWidth": Double(image.image?.size.width ?? 0)])
            }
            if images.isEmpty { local.append("cell \(index) has no icon view") }
            let title = root.subviews.compactMap { $0 as? UILabel }.first { $0.text == root.accessibilityLabel }
            let labelsEnabled = UserDefaults.standard.object(forKey: "LCShowAppLabels") as? Bool ?? true
            if let title {
                if title.isHidden == labelsEnabled { local.append("cell \(index) label preference not applied") }
                if labelsEnabled {
                    let labelBounds = title.convert(title.bounds, to: root)
                    if title.bounds.height <= 0 || !root.bounds.insetBy(dx: -0.5, dy: -0.5).contains(labelBounds) { local.append("cell \(index) title clipped by cell bounds") }
                } else {
                    let zeroHeight = title.constraints.contains { ($0.firstItem as? UIView) === title && $0.firstAttribute == .height && $0.secondItem == nil && $0.isActive && $0.priority == .required && $0.constant == 0 }
                    let zeroGap = root.constraints.contains { ($0.firstItem as? UIView) === title && $0.firstAttribute == .top && ($0.secondItem as? UIView) === images.first && $0.secondAttribute == .bottom && $0.isActive && $0.priority == .required && $0.constant == 0 }
                    if !zeroHeight || !zeroGap || abs(title.bounds.height) > 0.5 { local.append("cell \(index) hidden-label height/gap contract is not resolved to zero") }
                }
            } else if labelsEnabled { local.append("cell \(index) lacks visual label") }
            if root.accessibilityLabel?.isEmpty != false { local.append("cell \(index) lacks accessibility name") }
            if cell.children.count != 1 || cell.children.first?.parent !== cell { local.append("cell \(index) action-router containment is invalid") }
            let constraints = requiredConstraintEvidence(in: root)
            let brokenConstraints = constraints.filter { $0["requiredViolation"] as? Bool == true }.count
            if brokenConstraints > 0 { local.append("cell \(index) violates \(brokenConstraints) required layout constraints") }
            cellEvidence.append(["index": index, "fixtureName": root.accessibilityLabel ?? "", "bounds": rect(frame), "preferredContentSize": [Double(cell.preferredContentSize.width), Double(cell.preferredContentSize.height)], "unsatisfiedRequiredConstraints": brokenConstraints, "constraintEquations": constraints, "visibility": visibilityEvidence(root), "icons": imageEvidence])
        }
        for i in frames.indices {
            for j in frames.indices where j > i {
                if frames[i].intersection(frames[j]).width > 0.5 && frames[i].intersection(frames[j]).height > 0.5 { local.append("cells \(i) and \(j) overlap") }
            }
        }
        let retainedButDetached = controllers(host).compactMap { $0 as? LCGridAppCellViewController }.filter { !$0.view.isDescendant(of: host.view) }.count
        let excluded = controllers(host).compactMap { $0 as? LCGridAppCellViewController }.filter { !cells.contains($0) }.map {
            ["fixtureName": $0.view.accessibilityLabel ?? "", "reason": hiddenReason($0.view) ?? "not attached", "visibility": visibilityEvidence($0.view)] as [String: Any]
        }
        measurements.append(["case": name, "inputCount": state.apps.count, "cellCount": cells.count, "retainedButDetachedControllers": retainedButDetached, "excludedControllers": excluded, "viewport": rect(host.view.bounds), "labels": UserDefaults.standard.object(forKey: "LCShowAppLabels") as? Bool ?? true, "cells": cellEvidence, "violations": local])
        if requireValid { failures += local.map { "\(name): \($0)" } }
        return local.isEmpty
    }
    @discardableResult func verifyList(_ name: String, compact: Bool, requireValid: Bool = true) -> Bool {
        let previousFailures = failures.count
        let cells = controllers(host).compactMap { $0 as? LCAppBannerViewController }.filter { !($0.parent is LCGridAppCellViewController) && $0.view.isDescendant(of: host.view) }.sorted {
            $0.view.convert($0.view.bounds, to: host.view).minY < $1.view.convert($1.view.bounds, to: host.view).minY
        }
        check(cells.map { $0.view.accessibilityLabel ?? "" } == state.apps.map(\.displayName), "\(name): list identities/order changed")
        check(cells.allSatisfy { $0.view.bounds.width > 0 && abs($0.view.bounds.height - (compact ? 56 : 88)) < 1 }, "\(name): banner representable sizing is incorrect")
        var iconEvidence: [[String: Any]] = []
        for cell in cells {
            let images = descendants(cell.view).compactMap { $0 as? UIImageView }.filter { visible($0, in: cell.view) && $0.bounds.width >= 40 }
            check(images.count == 1, "\(name): banner is missing its icon view")
            for image in images {
                check(cell.view.bounds.contains(image.convert(image.bounds, to: cell.view)), "\(name): production banner icon is clipped")
                // SF Symbols carry alignment insets: Auto Layout constrains the
                // alignment rect, so e.g. a 60pt icon can have a 57pt image frame.
                // Verify the actual constrained geometry, not an arbitrary tolerance.
                let alignment = image.alignmentRect(forFrame: image.frame)
                check(abs(alignment.height - (compact ? 40 : 60)) < 0.5, "\(name): production banner icon alignment size did not track style")
                iconEvidence.append(["bounds": rect(image.bounds), "alignmentRect": rect(alignment), "expectedAlignmentHeight": compact ? 40 : 60])
            }
        }
        let violations = Array(failures.dropFirst(previousFailures))
        measurements.append(["case": name, "inputCount": state.apps.count, "cellCount": cells.count, "scope": "production LCAppBanner representable and LCAppBannerRootView with controlled action-router dependency; baseline compact omits new applyLayoutStyle to reproduce old 60-point icon geometry", "bounds": cells.map { rect($0.view.convert($0.view.bounds, to: host.view)) }, "icons": iconEvidence, "violations": violations])
        if !requireValid { failures.removeSubrange(previousFailures..<failures.count) }
        return violations.isEmpty
    }
    func exerciseActions() {
        LCAppBannerViewController.primaryActions = []
        LCAppBannerViewController.contextMenus = []
        let cells = gridControllers()
        for cell in cells {
            (cell.view as? UIControl)?.sendActions(for: .touchUpInside)
            let interaction = UIContextMenuInteraction(delegate: cell)
            let configuration = cell.contextMenuInteraction(interaction, configurationForMenuAtLocation: .zero)
            // UIKit publicly exposes the provider through configuration only when asking the delegate;
            // invoking the provider via NSInvocation is deliberately avoided. The source forwards to
            // makeContextMenu; tap forwarding is executed, menu configuration presence is measured.
            check(configuration != nil, "context menu configuration was dropped")
#if CORRECTED_GRID
            _ = cell.makeContextMenu()
#endif
        }
        check(LCAppBannerViewController.primaryActions == state.apps.map(\.identity), "tap forwarding changed app identities/order")
#if CORRECTED_GRID
        check(LCAppBannerViewController.contextMenus == state.apps.map(\.identity), "context-menu forwarding changed app identities/order")
#endif
    }
    func capture(_ name: String) {
        let renderer = UIGraphicsImageRenderer(bounds: host.view.bounds)
        let image = renderer.image { context in host.view.layer.render(in: context.cgContext) }
        if let data = image.pngData() {
            try? data.write(to: URL.documentsDirectoryCompat.appendingPathComponent("\(suite)-\(name).png"))
        }
    }
    func run() async {
        if cold {
            check(UserDefaults.standard.string(forKey: "LCAppLayoutStyle") == "grid", "cold launch did not retain Grid preference")
            check(UserDefaults.standard.bool(forKey: "LCShowAppLabels"), "cold launch did not retain labels preference")
        } else {
            UserDefaults.standard.set("list", forKey: "LCAppLayoutStyle")
            UserDefaults.standard.set(true, forKey: "LCShowAppLabels")
        }
        host = UIHostingController(rootView: RenderingScreen(state: state))
        window.rootViewController = parent
        parent.addChild(host)
        parent.view.addSubview(host.view)
        host.didMove(toParent: parent)
        window.makeKeyAndVisible()
        await resize(min(window.bounds.width, 390))
        if cold {
            measure("cold-launch-saved-grid")
            capture("cold-grid")
        } else {
            verifyList("initial-list", compact: false)
            // Uses exactly the settings keys changed by the real Settings picker.
            UserDefaults.standard.set("grid", forKey: "LCAppLayoutStyle")
            await waitForLayout()
            let initiallyValid = measure("list-settings-grid", requireValid: !baseline)
            capture(baseline ? "baseline-grid" : "fixed-grid")
            if baseline {
                check(!initiallyValid, "baseline did not reproduce a measured rendering failure")
                let violations = measurements.last?["violations"] as? [String] ?? []
                check(violations.contains { $0.contains("non-positive bounds") || $0.contains("does not contain its icon") || $0.contains("overlap") || $0.contains("invisible") }, "baseline did not reproduce a geometry/visibility failure; missing-icon fallback alone is insufficient")
                UserDefaults.standard.set("compactList", forKey: "LCAppLayoutStyle")
                await waitForLayout()
                let compactValid = verifyList("baseline-compact-old-icon-geometry", compact: true, requireValid: false)
                check(!compactValid, "baseline compact did not reproduce clipping of original 60-point icon in 56-point row")
                capture("baseline-compact")
            } else {
                exerciseActions()
                UserDefaults.standard.set(false, forKey: "LCShowAppLabels")
                await waitForLayout()
                measure("labels-disabled")
                UserDefaults.standard.set(true, forKey: "LCShowAppLabels")
                await waitForLayout()
                measure("labels-restored")
                if !ProcessInfo.processInfo.arguments.contains("--diagnostic") {
                for width: CGFloat in [320, 375, 390, 600, 768, 844, 1024] where width <= window.bounds.width {
                    await resize(width)
                    measure("resize-\(Int(width))")
                    await resize(width, category: .accessibilityExtraExtraExtraLarge)
                    measure("accessibility-\(Int(width))")
                }
                await resize(min(window.bounds.width, 844), height: 320)
                measure("landscape-shaped-window")
                capture("landscape-shaped-window")
                await resize(min(window.bounds.width, 390))
                for cycle in 0..<3 {
                    for layout in ["compactList", "list", "grid"] {
                        UserDefaults.standard.set(layout, forKey: "LCAppLayoutStyle")
                        await waitForLayout()
                        if layout == "grid" { measure("transition-\(cycle)-grid") }
                        else { verifyList("transition-\(cycle)-\(layout)", compact: layout == "compactList") }
                    }
                }
                state.apps.remove(at: 2)
                state.apps.insert(LCAppModel(9), at: 1)
                state.apps.reverse()
                await waitForLayout()
                measure("live-collection-reorder-replace")
                exerciseActions()
                capture("reorder-replace-grid")
                state.apps = []
                await waitForLayout()
                check(gridControllers().isEmpty, "empty collection retains stale grid cells")
                // The harness owns the controlled empty state; actual product empty-state source is
                // separately checked in repository tests, not claimed to execute here.
                measurements.append(["case": "empty-collection", "inputCount": 0, "cellCount": gridControllers().count])
                state.apps = (0..<6).map(LCAppModel.init)
                await waitForLayout()
                measure("repopulate-after-empty")
                capture("final-grid")
                }
            }
        }
        let report: [String: Any] = [
            "schemaVersion": 1, "mode": baseline ? "baseline" : (ProcessInfo.processInfo.arguments.contains("--fallback") ? "fallback-contract" : "corrected"), "phase": cold ? "cold" : "suite", "deviceClass": suite,
            "os": UIDevice.current.systemVersion, "screen": rect(window.bounds), "deploymentTarget": "iOS 15.0",
            "passed": failures.isEmpty, "failures": failures, "measurements": measurements,
            "diagnosticOnly": ProcessInfo.processInfo.arguments.contains("--diagnostic"),
            "evidenceKind": "simulator execution of production grid and banner representables with controlled model/action-router dependencies",
            "limitations": ["Not the reporter's physical device", "No production guest launch/signing/transport is performed", "Menu configuration and real controller forwarding helper are executed; UIKit menu presentation remains a device acceptance check", "Full Apps screen navigation is not hosted; settings transitions use the production preference keys", "Fallback-contract mode removes the iOS16 sizeThatFits hook on the available simulator; it is not execution on iOS15"]
        ]
        let url = URL.documentsDirectoryCompat.appendingPathComponent("\(suite)-\(cold ? "cold" : "suite").json")
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: .atomic)
            print("ISSUE25_RENDERING_RESULT \(failures.isEmpty ? "PASS" : "FAIL") \(measurements.count) cases")
        } catch { print("ISSUE25_REPORT_WRITE_FAILED") }
    }
}

private extension URL {
    static var documentsDirectoryCompat: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }
}

@main final class RenderingAppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var runner: RenderingRunner?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        self.window = window
        let runner = RenderingRunner(window: window)
        self.runner = runner
        Task { @MainActor in await runner.run() }
        return true
    }
}
