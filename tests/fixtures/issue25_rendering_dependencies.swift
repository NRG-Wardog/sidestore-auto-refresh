// Controlled dependencies only. LCGridAppCell and LCAppBanner come from generated production source.
import SwiftUI
import UIKit

enum LCUtils {
    static let appGroupUserDefault = UserDefaults.standard
}
enum GeneratedIconStyle { case standard }
final class DataManager {
    static let shared = DataManager()
    let model = NSObject()
}
extension String { var loc: String { self } }
final class LCAppInfo {
    var isLocked = false
    var missingIcon = false
    var cachedColor: UIColor?
    var cachedColorDark: UIColor?
    func iconIsDarkIcon(_ dark: Bool) -> UIImage? {
        missingIcon ? nil : UIImage(systemName: "app.fill")
    }
}
final class LCAppModel: ObservableObject, Hashable {
    let identity: String
    @Published var displayName: String
    let appInfo = LCAppInfo()
    let version = "1.0"
    var bundleIdentifier: String { identity }
    let uiRemark = "Fixture metadata"
    struct Container { let name: String }
    let uiSelectedContainer: Container? = Container(name: "Fixture container")
    let uiIsShared = false
    let uiIsJITNeeded = false
    let uiIs32bit = false
    var uiIsLocked: Bool { appInfo.isLocked }
    let uiIsHidden = false
    let isSigningInProgress = false
    let signProgress = 0.0
    let isAppRunning = false
    init(_ index: Int) {
        identity = "fixture-\(index)"
        displayName = index == 1 ? "Fixture 1 — A deliberately long application name for accessibility layout" : "Fixture \(index)"
        appInfo.isLocked = index == 2
        appInfo.missingIcon = index == 3
    }
    static func == (lhs: LCAppModel, rhs: LCAppModel) -> Bool { lhs === rhs }
    func hash(into hasher: inout Hasher) { hasher.combine(identity) }
}
struct LCAppBannerConfiguration {
    let model: LCAppModel
    let dynamicColors: Bool
    let darkModeIcon: Bool
    var layoutStyle: AppLayoutStyle = .list
}
final class LCAppBannerViewController: UIViewController {
    static var primaryActions: [String] = []
    static var contextMenus: [String] = []
    private var configuration: LCAppBannerConfiguration
    private let bannerView = LCAppBannerRootView()
    init(delegate: LCAppBannerDelegate, config: LCAppBannerConfiguration) {
        configuration = config
        super.init(nibName: nil, bundle: nil)
        update(model: config.model, dynamicColors: config.dynamicColors, darkModeIcon: config.darkModeIcon, layoutStyle: config.layoutStyle)
    }
    required init?(coder: NSCoder) { fatalError("fixture does not use storyboards") }
    override func loadView() {
        view = bannerView
    }
    func update(model: LCAppModel, dynamicColors: Bool, darkModeIcon: Bool, layoutStyle: AppLayoutStyle = .list) {
        configuration = LCAppBannerConfiguration(model: model, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon, layoutStyle: layoutStyle)
        loadViewIfNeeded()
        bannerView.update(model: model, appInfo: model.appInfo, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon, traitCollection: traitCollection)
#if CORRECTED_BANNER
        bannerView.applyLayoutStyle(layoutStyle)
#endif
        view.accessibilityLabel = model.displayName
        preferredContentSize = CGSize(width: 0, height: layoutStyle == .compactList ? 56 : 88)
    }
    func performPrimaryAction() { Self.primaryActions.append(configuration.model.identity) }
    func makeContextMenu() -> UIMenu {
        Self.contextMenus.append(configuration.model.identity)
        return UIMenu(children: [UIAction(title: "Fixture details") { _ in }])
    }
}
