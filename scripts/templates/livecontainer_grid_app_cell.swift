import SwiftUI
import UIKit

struct LCGridAppCell: UIViewControllerRepresentable {
    @ObservedObject var appModel: LCAppModel
    var delegate: LCAppBannerDelegate
    var showLabels: Bool

    @AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault) private var dynamicColors = true
    @AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) private var darkModeIcon = false

    init(appModel: LCAppModel, delegate: LCAppBannerDelegate, showLabels: Bool) {
        _appModel = ObservedObject(wrappedValue: appModel)
        self.delegate = delegate
        self.showLabels = showLabels
    }

    func makeUIViewController(context: Context) -> LCGridAppCellViewController {
        LCGridAppCellViewController(delegate: delegate, configuration: LCAppBannerConfiguration(model: appModel, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon), showLabels: showLabels)
    }

    func updateUIViewController(_ controller: LCGridAppCellViewController, context: Context) {
        controller.update(model: appModel, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon, showLabels: showLabels)
    }

}

final class LCGridAppCellViewController: UIViewController, UIContextMenuInteractionDelegate {
    private let actionRouter: LCAppBannerViewController
    private let gridView = LCGridAppCellView()

    init(delegate: LCAppBannerDelegate, configuration: LCAppBannerConfiguration, showLabels: Bool) {
        actionRouter = LCAppBannerViewController(delegate: delegate, config: configuration)
        super.init(nibName: nil, bundle: nil)
        update(model: configuration.model, dynamicColors: configuration.dynamicColors, darkModeIcon: configuration.darkModeIcon, showLabels: showLabels)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func loadView() {
        view = gridView
        addChild(actionRouter)
        actionRouter.view.translatesAutoresizingMaskIntoConstraints = false
        actionRouter.view.isHidden = true
        gridView.addSubview(actionRouter.view)
        NSLayoutConstraint.activate([
            actionRouter.view.widthAnchor.constraint(equalToConstant: 1),
            actionRouter.view.heightAnchor.constraint(equalToConstant: 1),
            actionRouter.view.leadingAnchor.constraint(equalTo: gridView.leadingAnchor),
            actionRouter.view.topAnchor.constraint(equalTo: gridView.topAnchor)
        ])
        actionRouter.didMove(toParent: self)
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        gridView.addTarget(self, action: #selector(performPrimaryAction), for: .touchUpInside)
        gridView.addInteraction(UIContextMenuInteraction(delegate: self))
    }

    func update(model: LCAppModel, dynamicColors: Bool, darkModeIcon: Bool, showLabels: Bool) {
        actionRouter.update(model: model, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon)
        gridView.update(model: model, darkModeIcon: darkModeIcon, showLabels: showLabels)
    }

    @objc private func performPrimaryAction() { actionRouter.performPrimaryAction() }

    func contextMenuInteraction(_ interaction: UIContextMenuInteraction, configurationForMenuAtLocation location: CGPoint) -> UIContextMenuConfiguration? {
        UIContextMenuConfiguration(identifier: nil, previewProvider: nil) { [actionRouter] _ in actionRouter.makeContextMenu() }
    }
}

private final class LCGridAppCellView: UIControl {
    private let iconImageView = UIImageView()
    private let titleLabel = UILabel()
    private let lockView = UIImageView(image: UIImage(systemName: "lock.fill"))

    override init(frame: CGRect) {
        super.init(frame: frame)
        isAccessibilityElement = true
        accessibilityTraits = .button
        iconImageView.translatesAutoresizingMaskIntoConstraints = false
        iconImageView.contentMode = .scaleAspectFit
        iconImageView.layer.cornerRadius = 14
        iconImageView.layer.cornerCurve = .continuous
        iconImageView.clipsToBounds = true
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.font = .systemFont(ofSize: 12, weight: .medium)
        titleLabel.textAlignment = .center
        titleLabel.numberOfLines = 2
        titleLabel.lineBreakMode = .byTruncatingTail
        lockView.translatesAutoresizingMaskIntoConstraints = false
        lockView.tintColor = .white
        lockView.backgroundColor = UIColor.black.withAlphaComponent(0.6)
        lockView.layer.cornerRadius = 9
        lockView.contentMode = .center
        addSubview(iconImageView)
        addSubview(lockView)
        addSubview(titleLabel)
        NSLayoutConstraint.activate([
            iconImageView.topAnchor.constraint(equalTo: topAnchor, constant: 4),
            iconImageView.centerXAnchor.constraint(equalTo: centerXAnchor),
            iconImageView.widthAnchor.constraint(equalToConstant: 60),
            iconImageView.heightAnchor.constraint(equalToConstant: 60),
            lockView.widthAnchor.constraint(equalToConstant: 18),
            lockView.heightAnchor.constraint(equalToConstant: 18),
            lockView.trailingAnchor.constraint(equalTo: iconImageView.trailingAnchor, constant: 4),
            lockView.topAnchor.constraint(equalTo: iconImageView.topAnchor, constant: -4),
            titleLabel.topAnchor.constraint(equalTo: iconImageView.bottomAnchor, constant: 6),
            titleLabel.leadingAnchor.constraint(equalTo: leadingAnchor),
            titleLabel.trailingAnchor.constraint(equalTo: trailingAnchor),
            titleLabel.bottomAnchor.constraint(lessThanOrEqualTo: bottomAnchor)
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(model: LCAppModel, darkModeIcon: Bool, showLabels: Bool) {
        iconImageView.image = model.appInfo.iconIsDarkIcon(darkModeIcon) ?? UIImage()
        titleLabel.text = model.displayName
        titleLabel.isHidden = !showLabels
        lockView.isHidden = !model.appInfo.isLocked
        accessibilityLabel = model.displayName
    }
}
