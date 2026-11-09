// Owned synthetic UI. Production localization/state/bridge are compiled unmodified.
import UIKit
import SwiftUI

@available(iOS 16.0, *)
final class VirtualWindowsHostView: UIView {}
// Exact production Objective-C class boundaries, without any guest execution.
@available(iOS 16.0, *)
class AppSceneViewController: UIViewController {}
@available(iOS 16.0, *)
class DecoratedAppSceneViewController: UIViewController {}

@MainActor final class FixtureModel: ObservableObject {
    @Published var tab = 0
    @Published var path: [Int] = [1]
    @Published var draft = "user draft stays"
    @Published var showSheet = false
    let operationIdentity = UUID()
    let message = V3DisplayMessage.localized("Version %@", "שם משתמש")
}

struct RenderedProbe: UIViewRepresentable {
    var identifier = "rendered-language-probe"
    @Environment(\.locale) private var locale
    @Environment(\.layoutDirection) private var direction
    func makeUIView(context: Context) -> UILabel {
        let label = UILabel(); label.accessibilityIdentifier = identifier
        return label
    }
    func updateUIView(_ label: UILabel, context: Context) {
        label.text = V3Localization.string("Checking", language: .resolve(locale.identifier))
        label.semanticContentAttribute = direction == .rightToLeft ? .forceRightToLeft : .forceLeftToRight
        label.accessibilityValue = locale.identifier
    }
}

struct EagerLegacyLabel: UIViewRepresentable {
    let text: String
    func makeUIView(context: Context) -> UILabel { let label=UILabel();label.accessibilityIdentifier="legacy-eager-label";return label }
    func updateUIView(_ label: UILabel, context: Context) { label.text=text }
}
struct EagerLegacyProbe: View {
    @Environment(\.locale) private var displayLocale
    var body: some View {
        let _ = displayLocale.identifier
        // Matches legacy String/NSString call sites: lookup reads host preference
        // eagerly; explicit environment observation invalidates this same view.
        EagerLegacyLabel(text: V3Localization.string("Checking"))
    }
}

struct OpaqueDiagnosticProbe: UIViewRepresentable {
    func makeUIView(context: Context) -> UILabel {
        let label = UILabel(); label.text = "SS-LANG-TECH-1"; label.accessibilityIdentifier = "opaque-diagnostic"
        label.semanticContentAttribute = .forceLeftToRight; return label
    }
    func updateUIView(_ label: UILabel, context: Context) {}
}

@MainActor enum DiagnosticTableTrace {
    static var enabled = false
    static var events = [[String:Any]]()
    static func record(_ phase: String, view: UITableView) {
        guard enabled, events.count < 100 else { return }
        events.append(["phase":phase,"identifier":view.accessibilityIdentifier ?? "missing",
                       "selected_language":V3AppLanguage.selected.rawValue,
                       "semantic":view.semanticContentAttribute.rawValue,
                       "effective":view.effectiveUserInterfaceLayoutDirection.rawValue])
    }
}
struct DiagnosticTableProbe: UIViewRepresentable {
    var identifier = "explicit-ltr-diagnostic-table"
    func makeUIView(context: Context) -> UITableView {
        let table=UITableView();table.accessibilityIdentifier=identifier
        table.semanticContentAttribute = .forceLeftToRight
        DiagnosticTableTrace.record("make_after_explicit_ltr", view:table)
        return table
    }
    func updateUIView(_ view: UITableView, context: Context) {
        DiagnosticTableTrace.record("update_no_assignment", view:view)
    }
}

struct UIKitOnlyDiagnosticProbe: UIViewRepresentable {
    func makeUIView(context: Context) -> UIView {
        let container=UIView()
        let table=UITableView(frame:.zero)
        table.accessibilityIdentifier="standalone-uikit-technical-table"
        table.semanticContentAttribute = .forceLeftToRight
        container.addSubview(table)
        return container
    }
    func updateUIView(_ view: UIView, context: Context) {}
}

struct GuestBoundaryProbe: UIViewRepresentable {
    func makeUIView(context: Context) -> VirtualWindowsHostView {
        let view = VirtualWindowsHostView(); view.accessibilityIdentifier = "guest-boundary"
        let bar = UINavigationBar();bar.semanticContentAttribute = .forceRightToLeft
        bar.accessibilityIdentifier = "nested-guest-navigation";view.addSubview(bar)
        return view
    }
    func updateUIView(_ view: VirtualWindowsHostView, context: Context) {}
}

struct FixtureRoot: View {
    @ObservedObject var language: V3HostLanguageState
    @ObservedObject var model: FixtureModel
    var bridgeEnabled = true
    var observesCounterfactual = false
    var body: some View {
        TabView(selection: $model.tab) {
            NavigationStack(path: $model.path) {
                Text("Root").navigationDestination(for: Int.self) { _ in
                    Form {
                        RenderedProbe()
                        EagerLegacyProbe()
                        Text(V3Localization.string("Checking", language: language.selected))
                        TextField("Draft", text: $model.draft)
                        OpaqueDiagnosticProbe().environment(\.layoutDirection, .leftToRight)
                        if observesCounterfactual {
                            // Preserve the initially failing implicit boundary for
                            // attribution. Creation-only UIKit semantics are not
                            // a persistent SwiftUI environment override.
                            DiagnosticTableProbe().frame(height: 1)
                            DiagnosticTableProbe(identifier: "swiftui-explicit-ltr-diagnostic-table")
                                .frame(height: 1).environment(\.layoutDirection, .leftToRight)
                        } else {
                            // Production technical details declare LTR in the
                            // SwiftUI environment, not only in makeUIView.
                            DiagnosticTableProbe().frame(height: 1)
                                .environment(\.layoutDirection, .leftToRight)
                        }
                        UIKitOnlyDiagnosticProbe().frame(height: 1)
                        GuestBoundaryProbe().frame(height: 1)
                    }.navigationTitle("Language fixture")
                }
            }.tabItem { Label("First", systemImage: "1.circle") }.tag(0)
            Text("Second").tabItem { Label("Second", systemImage: "2.circle") }.tag(1)
        }
        .sheet(isPresented: $model.showSheet) {
            NavigationStack { Form { RenderedProbe(identifier: "sheet-language-probe"); OpaqueDiagnosticProbe().environment(\.layoutDirection, .leftToRight) }.navigationTitle("Sheet") }
        }
        .environment(\.locale, Locale(identifier: language.selected.rawValue))
        .environment(\.layoutDirection, language.selected.isRightToLeft ? .rightToLeft : .leftToRight)
        .background {
            if bridgeEnabled { V3HostLanguageUIKitBridge(language: language.selected) }
        }
    }
}

struct FixtureFailure: Error { let reason: String }
@MainActor func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw FixtureFailure(reason: message) }
}
@MainActor func descendants(_ view: UIView) -> [UIView] { [view] + view.subviews.flatMap(descendants) }
@MainActor func settle() async { try? await Task.sleep(nanoseconds: 250_000_000) }

@main final class FixtureAppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var guestWindow: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        V3Localization.hostDefaults.set("he", forKey: V3AppLanguage.storageKey)
        let language = V3HostLanguageState(defaults: V3Localization.hostDefaults)
        let model = FixtureModel()
        let arguments = ProcessInfo.processInfo.arguments
        let withoutBridge = arguments.contains("--counterfactual-no-bridge")
        let withBridge = arguments.contains("--counterfactual-with-bridge")
        let counterfactual = withoutBridge || withBridge
        DiagnosticTableTrace.enabled = counterfactual
        let counterfactualFilename = withoutBridge ? "counterfactual-without-bridge.json" : "counterfactual-with-bridge.json"
        let root = FixtureRoot(language: language, model: model, bridgeEnabled: !withoutBridge,
                               observesCounterfactual: counterfactual)
        let host = UIHostingController(rootView: root)
        let window = UIWindow(frame: UIScreen.main.bounds); self.window = window
        window.rootViewController = host; window.makeKeyAndVisible()
        let guest = UIWindow(frame: CGRect(x: 0, y: 0, width: 10, height: 10))
        guest.semanticContentAttribute = .forceRightToLeft
        guest.rootViewController = UIViewController();guest.rootViewController!.view.semanticContentAttribute = .forceRightToLeft
        self.guestWindow = guest
        Task { @MainActor in
            do {
                try require(!(withoutBridge && withBridge), "Ambiguous counterfactual mode")
                if counterfactual {
                    try await self.observeCounterfactual(language:language, host:host,
                        bridgeEnabled:!withoutBridge, filename:counterfactualFilename)
                } else {
                    try await self.exercise(language: language, model: model, host: host, guest: guest)
                }
            }
            catch { self.finish(["status":"FAILED", "failure": (error as? FixtureFailure)?.reason ?? String(describing:error), "synthetic_ui_hierarchy": self.diagnosticHierarchy(host)],
                filename:counterfactual ? counterfactualFilename : "language-switch-result.json") }
        }
        return true
    }
    @MainActor func observeCounterfactual(language:V3HostLanguageState,
        host:UIHostingController<FixtureRoot>, bridgeEnabled:Bool, filename:String) async throws {
        await settle();await settle()
        var observations=[[String:Any]]()
        for selected in [V3AppLanguage.hebrew, .english, .hebrew] {
            language.select(selected)
            await settle();host.view.layoutIfNeeded();await settle()
            let views=descendants(host.view)
            for identifier in ["explicit-ltr-diagnostic-table", "swiftui-explicit-ltr-diagnostic-table"] {
                guard let table=views.first(where:{$0.accessibilityIdentifier == identifier}) as? UITableView else {
                    throw FixtureFailure(reason:"Counterfactual table missing: " + identifier)
                }
                observations.append(["language":selected.rawValue,"identifier":identifier,
                    "semantic":table.semanticContentAttribute.rawValue,
                    "effective":table.effectiveUserInterfaceLayoutDirection.rawValue])
            }
        }
        finish(["status":"OBSERVED_COUNTERFACTUAL_NOT_QUALIFICATION", "bridge_enabled":bridgeEnabled,
            "observations":observations,"table_lifecycle_events":DiagnosticTableTrace.events,
            "synthetic_ui_hierarchy":diagnosticHierarchy(host),"physical_device_execution":false], filename:filename)
    }
    @MainActor func exercise(language: V3HostLanguageState, model: FixtureModel, host: UIHostingController<FixtureRoot>, guest: UIWindow) async throws {
        let stateID = ObjectIdentifier(language), hostID = ObjectIdentifier(host), operation = model.operationIdentity
        let originalMessage = model.message.original
        let appleLanguages = V3Localization.hostDefaults.object(forKey:"AppleLanguages") as? [String]
        await settle(); await settle()
        // Model the reported pre-existing RTL chrome, not only unspecified views.
        for chrome in descendants(host.view) where (chrome is UINavigationBar || chrome is UITabBar) && chrome.accessibilityIdentifier != "nested-guest-navigation" {
            chrome.semanticContentAttribute = .forceRightToLeft
        }
        var records = [[String:Any]]()
        let transitions: [V3AppLanguage] = [.hebrew,.english,.hebrew,.russian,.hebrew,.simplifiedChinese,.hebrew,.traditionalChinese,.english,.hebrew,.russian,.english]
        for selected in transitions {
            language.select(selected)
            await settle();host.view.layoutIfNeeded();await settle()
            let views = descendants(host.view)
            guard let label = views.first(where:{$0.accessibilityIdentifier == "rendered-language-probe"}) as? UILabel else { throw FixtureFailure(reason:"Live rendered label missing") }
            let expected = V3Localization.string("Checking",language:selected)
            try require(label.text == expected,"Stale rendered string after "+selected.rawValue)
            let eager=views.first(where:{$0.accessibilityIdentifier=="legacy-eager-label"}) as? UILabel
            try require(eager?.text==expected,"Legacy eager host lookup retained stale language")
            try require(label.accessibilityValue == selected.rawValue,"SwiftUI locale did not update")
            let direction: UIUserInterfaceLayoutDirection = selected.isRightToLeft ? .rightToLeft : .leftToRight
            try require(label.effectiveUserInterfaceLayoutDirection == direction,"Rendered label retained wrong direction")
            let chrome=views.filter { view in
                (view is UINavigationBar || view is UITabBar || view is UITableView || view is UICollectionView) && view.accessibilityIdentifier != "nested-guest-navigation" && view.accessibilityIdentifier != "explicit-ltr-diagnostic-table" && view.accessibilityIdentifier != "standalone-uikit-technical-table"
            }
            try require(chrome.contains(where:{$0 is UINavigationBar}) && chrome.contains(where:{$0 is UITabBar}),"Missing actual live navigation/tab chrome")
            try require(chrome.contains(where:{$0 is UITableView || $0 is UICollectionView}),"Missing actual live Form chrome")
            for item in chrome { try require(item.effectiveUserInterfaceLayoutDirection == direction,"UIKit chrome retained wrong direction: "+String(describing:type(of:item))) }
            guard let diagnostic=views.first(where:{$0.accessibilityIdentifier == "opaque-diagnostic"}) else { throw FixtureFailure(reason:"Technical diagnostic view missing") }
            try require(diagnostic.effectiveUserInterfaceLayoutDirection == .leftToRight,"Explicit technical direction changed")
            guard let technicalTable=views.first(where:{$0.accessibilityIdentifier == "explicit-ltr-diagnostic-table"}) else { throw FixtureFailure(reason:"Technical table missing") }
            try require(technicalTable.semanticContentAttribute == .forceLeftToRight,"Explicit technical table direction changed")
            guard let standaloneTable=views.first(where:{$0.accessibilityIdentifier == "standalone-uikit-technical-table"}) else { throw FixtureFailure(reason:"UIKit-only technical table missing") }
            try require(standaloneTable.semanticContentAttribute == .forceLeftToRight,"UIKit-only explicit technical direction changed")
            guard let guestBar=views.first(where:{$0.accessibilityIdentifier == "nested-guest-navigation"}) else { throw FixtureFailure(reason:"Guest boundary probe missing") }
            try require(guestBar.semanticContentAttribute == .forceRightToLeft,"Nested guest chrome changed")
            try require(guest.semanticContentAttribute == .forceRightToLeft && guest.rootViewController!.view.semanticContentAttribute == .forceRightToLeft,"Separate guest window changed")
            try require(ObjectIdentifier(language)==stateID && ObjectIdentifier(host)==hostID,"Root/state owner replaced")
            try require(model.operationIdentity==operation && model.tab==0 && model.path==[1] && model.draft=="user draft stays","Live session state reset")
            try require(V3AppLanguage.selected==selected,"Persisted language differs")
            try require(model.message.original==originalMessage && model.message.rendered(language:selected).contains("שם משתמש"),"Message/user content changed")
            try require(V3Localization.hostDefaults.object(forKey:"AppleLanguages") as? [String] == appleLanguages,"Global preferred languages changed")
            records.append(["language":selected.rawValue,"visible_string":label.text!,"direction":direction == .rightToLeft ? "rtl":"ltr","chrome_count":chrome.count])
        }
        // An already-presented sheet is a distinct UIKit controller subtree.
        model.showSheet=true;await settle();await settle()
        for selected in [V3AppLanguage.hebrew,.english,.russian,.simplifiedChinese] {
            language.select(selected);await settle();await settle()
            guard let presented=host.presentedViewController else { throw FixtureFailure(reason:"Sheet missing") }
            let views=descendants(presented.view)
            let label=views.first(where:{$0.accessibilityIdentifier=="sheet-language-probe"}) as? UILabel
            try require(label?.text==V3Localization.string("Checking",language:selected),"Presented sheet retained stale language")
            guard let technical=views.first(where:{$0.accessibilityIdentifier=="opaque-diagnostic"}) else { throw FixtureFailure(reason:"Sheet technical probe missing") }
            try require(technical.effectiveUserInterfaceLayoutDirection == .leftToRight,"Sheet explicit technical direction changed")
            let nav=views.compactMap{$0 as? UINavigationBar}
            try require(!nav.isEmpty,"Sheet navigation chrome missing")
            for bar in nav { try require(bar.effectiveUserInterfaceLayoutDirection == (selected.isRightToLeft ? .rightToLeft : .leftToRight),"Presented sheet retained stale native direction") }
        }
        model.showSheet=false;await settle();await settle()
        // Later navigation must inherit the final direction without reset.
        model.path=[];await settle();model.path=[1];await settle();await settle()
        let reopened=descendants(host.view).first(where:{$0.accessibilityIdentifier=="rendered-language-probe"}) as? UILabel
        try require(reopened?.text==V3Localization.string("Checking",language:.simplifiedChinese),"Reopened screen retained stale language")
        let reopenedChrome=descendants(host.view).filter { ($0 is UINavigationBar || $0 is UITabBar || $0 is UITableView || $0 is UICollectionView) && $0.accessibilityIdentifier != "nested-guest-navigation" && $0.accessibilityIdentifier != "explicit-ltr-diagnostic-table" && $0.accessibilityIdentifier != "standalone-uikit-technical-table" }
        try require(reopenedChrome.count>=3,"Reopened chrome missing")
        for chrome in reopenedChrome { try require(chrome.effectiveUserInterfaceLayoutDirection == .leftToRight,"Reopened native chrome stale direction") }
        // Rapid selection changes must converge without replacing the live root.
        language.select(.hebrew);language.select(.english);language.select(.hebrew);language.select(.russian)
        await settle();await settle()
        guard let label=descendants(host.view).first(where:{$0.accessibilityIdentifier=="rendered-language-probe"}) as? UILabel else { throw FixtureFailure(reason:"Rapid transition label missing") }
        try require(language.selected == .russian && label.text == V3Localization.string("Checking",language:.russian),"Rapid switches did not converge")
        // A presented navigation wrapper containing a guest controller belongs
        // to the guest even though it shares the host window/presentation chain.
        for guestController in [AppSceneViewController(), DecoratedAppSceneViewController()] as [UIViewController] {
            let wrapper=UINavigationController(rootViewController:guestController)
            wrapper.navigationBar.semanticContentAttribute = .forceRightToLeft
            host.present(wrapper,animated:false);await settle();await settle()
            try require(wrapper.view.window === host.view.window,"Guest wrapper must exercise same-window boundary")
            for selected in [V3AppLanguage.hebrew,.english] {
                language.select(selected);await settle();await settle()
                try require(wrapper.navigationBar.semanticContentAttribute == .forceRightToLeft,"Presented guest wrapper chrome changed")
                try require(host.presentedViewController === wrapper,"Guest presentation was dismissed or replaced")
            }
            host.dismiss(animated:false);await settle();await settle()
        }
        finish(["status":"PASS","transition_count":transitions.count,"same_hosting_controller":true,"same_state_owner":true,"guest_window_unchanged":true,"presented_guest_wrapper_unchanged":true,"rapid_switch_converged":true,"presented_sheet_transitions":4,"later_navigation_pass":true,"records":records,"physical_device_execution":false])
    }
    @MainActor func diagnosticHierarchy(_ host: UIViewController) -> [[String:Any]] {
        // Bounded class/direction metadata from this synthetic fixture only.
        // No labels, textfield values, account data or production logs.
        var result=[[String:Any]]()
        func visit(_ controller:UIViewController,_ depth:Int) {
            guard depth<8 && result.count<64 else { return }
            let chrome=(controller.viewIfLoaded.map { Array(descendants($0).prefix(256)) } ?? []).filter {
                $0 is UINavigationBar || $0 is UITabBar || $0 is UITableView || $0 is UICollectionView
            }.prefix(32).map { ["class":String(describing:type(of:$0)),"semantic":$0.semanticContentAttribute.rawValue,"effective":$0.effectiveUserInterfaceLayoutDirection.rawValue] as [String:Any] }
            result.append(["controller":String(describing:type(of:controller)),"parent":controller.parent.map { String(describing:type(of:$0)) } ?? "none","depth":depth,"chrome":Array(chrome)])
            for child in controller.children { visit(child,depth+1) }
            if let presented=controller.presentedViewController { visit(presented,depth+1) }
        }
        visit(host,0);return result
    }
    @MainActor func finish(_ report:[String:Any], filename:String = "language-switch-result.json") {
        let url=FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent(filename)
        do { try JSONSerialization.data(withJSONObject:report,options:[.sortedKeys,.prettyPrinted]).write(to:url,options:.atomic) }
        catch { NSLog("Fixture evidence write failed") }
    }
}
