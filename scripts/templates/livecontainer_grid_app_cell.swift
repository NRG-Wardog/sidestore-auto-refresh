import SwiftUI
import UIKit

struct LCGridAppCell: UIViewControllerRepresentable {
    @ObservedObject var appModel: LCAppModel
    var delegate: LCAppBannerDelegate
    var showLabels: Bool
    var gridSize: LCGridSize?

    @AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault) private var dynamicColors = true
    @AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) private var darkModeIcon = false
    @Environment(\.sizeCategory) private var sizeCategory

    init(appModel: LCAppModel, delegate: LCAppBannerDelegate, showLabels: Bool, gridSize: LCGridSize? = nil) {
        _appModel = ObservedObject(wrappedValue: appModel)
        self.delegate = delegate
        self.showLabels = showLabels
        self.gridSize = gridSize
    }

    func makeUIViewController(context: Context) -> LCGridAppCellViewController {
        LCGridAppCellViewController(delegate: delegate, configuration: LCAppBannerConfiguration(model: appModel, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon), showLabels: showLabels, gridSize: gridSize, sizeCategory: Self.uiContentSizeCategory(sizeCategory))
    }

    func updateUIViewController(_ controller: LCGridAppCellViewController, context: Context) {
        controller.update(model: appModel, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon, showLabels: showLabels, gridSize: gridSize, sizeCategory: Self.uiContentSizeCategory(sizeCategory))
    }

    // On iOS 15 UIViewControllerRepresentable.sizeThatFits is unavailable, so
    // SwiftUI sizes the cell from preferredContentSize. Text-size changes arrive
    // here before UIKit trait propagation, so the SwiftUI category is bridged
    // explicitly; otherwise intrinsicContentSize lags one update behind.
    static func uiContentSizeCategory(_ category: ContentSizeCategory) -> UIContentSizeCategory {
        switch category {
        case .extraSmall: return .extraSmall
        case .small: return .small
        case .medium: return .medium
        case .large: return .large
        case .extraLarge: return .extraLarge
        case .extraExtraLarge: return .extraExtraLarge
        case .extraExtraExtraLarge: return .extraExtraExtraLarge
        case .accessibilityMedium: return .accessibilityMedium
        case .accessibilityLarge: return .accessibilityLarge
        case .accessibilityExtraLarge: return .accessibilityExtraLarge
        case .accessibilityExtraExtraLarge: return .accessibilityExtraExtraLarge
        case .accessibilityExtraExtraExtraLarge: return .accessibilityExtraExtraExtraLarge
        @unknown default: return .large
        }
    }

    @available(iOS 16.0, *)
    func sizeThatFits(_ proposal: ProposedViewSize, uiViewController: LCGridAppCellViewController, context: Context) -> CGSize? {
        uiViewController.fittingSize(width: proposal.width)
    }
}

final class LCGridAppCellViewController: UIViewController, UIContextMenuInteractionDelegate {
    private let actionRouter: LCAppBannerViewController
    private let gridView: LCGridAppCellView

    init(delegate: LCAppBannerDelegate, configuration: LCAppBannerConfiguration, showLabels: Bool, gridSize: LCGridSize? = nil, sizeCategory: UIContentSizeCategory) {
        actionRouter = LCAppBannerViewController(delegate: delegate, config: configuration)
        gridView = LCGridAppCellView(sizeCategory: sizeCategory)
        super.init(nibName: nil, bundle: nil)
        update(model: configuration.model, dynamicColors: configuration.dynamicColors, darkModeIcon: configuration.darkModeIcon, showLabels: showLabels, gridSize: gridSize, sizeCategory: sizeCategory)
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

    func update(model: LCAppModel, dynamicColors: Bool, darkModeIcon: Bool, showLabels: Bool, gridSize: LCGridSize? = nil, sizeCategory: UIContentSizeCategory) {
        actionRouter.update(model: model, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon)
        gridView.update(model: model, darkModeIcon: darkModeIcon, showLabels: showLabels, gridSize: gridSize, sizeCategory: sizeCategory)
        // preferredContentSize and intrinsicContentSize are also used on iOS 15,
        // where UIViewControllerRepresentable.sizeThatFits is unavailable.
        preferredContentSize = fittingSize(width: nil)
    }

    func fittingSize(width: CGFloat?) -> CGSize {
        CGSize(width: width.flatMap { $0.isFinite && $0 > 0 ? $0 : nil } ?? gridView.minimumWidth,
               height: gridView.intrinsicContentSize.height)
    }

    @objc private func performPrimaryAction() { actionRouter.performPrimaryAction() }

    func makeContextMenu() -> UIMenu { actionRouter.makeContextMenu() }

    func contextMenuInteraction(_ interaction: UIContextMenuInteraction, configurationForMenuAtLocation location: CGPoint) -> UIContextMenuConfiguration? {
        UIContextMenuConfiguration(identifier: nil, previewProvider: nil) { [weak self] _ in self?.makeContextMenu() }
    }
}

private final class LCGridAppCellView: UIControl {
    // The grid's vertical contract is derived from its actual icon, spacing and
    // scaled two-line label, not the parent scroll view's unbounded proposal.
    private var gridSize: LCGridSize?
    private var sizeCategory: UIContentSizeCategory
    private var iconSide: CGFloat { gridSize?.iconSize ?? 60 }
    var minimumWidth: CGFloat { gridSize?.minimumWidth ?? 76 }
    private lazy var iconWidthConstraint = iconImageView.widthAnchor.constraint(equalToConstant: iconSide)
    private lazy var iconHeightConstraint = iconImageView.heightAnchor.constraint(equalToConstant: iconSide)
    private static let topInset: CGFloat = 4
    private static let labelSpacing: CGFloat = 6
    private static let bottomInset: CGFloat = 4
    private let iconImageView = UIImageView()
    private let titleLabel = UILabel()
    private let lockView = UIImageView(image: UIImage(systemName: "lock.fill"))
    private lazy var titleTopConstraint = titleLabel.topAnchor.constraint(equalTo: iconImageView.bottomAnchor, constant: Self.labelSpacing)
    private lazy var hiddenTitleHeightConstraint = titleLabel.heightAnchor.constraint(equalToConstant: 0)

    override var intrinsicContentSize: CGSize {
        let labelHeight = titleLabel.isHidden ? 0 : Self.labelSpacing + ceil(titleLabel.font.lineHeight * 2)
        return CGSize(width: UIView.noIntrinsicMetric,
                      height: Self.topInset + iconSide + labelHeight + Self.bottomInset)
    }

    private func updateMetrics() {
        let traits = UITraitCollection(preferredContentSizeCategory: sizeCategory)
        titleLabel.font = UIFontMetrics(forTextStyle: .caption1).scaledFont(
            for: .systemFont(ofSize: 12, weight: .medium), compatibleWith: traits)
        invalidateIntrinsicContentSize()
        setNeedsLayout()
    }

    init(sizeCategory: UIContentSizeCategory) {
        self.sizeCategory = sizeCategory
        super.init(frame: .zero)
        isAccessibilityElement = true
        accessibilityTraits = .button
        iconImageView.translatesAutoresizingMaskIntoConstraints = false
        iconImageView.contentMode = .scaleAspectFit
        iconImageView.layer.cornerRadius = 14
        iconImageView.layer.cornerCurve = .continuous
        iconImageView.clipsToBounds = true
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.adjustsFontForContentSizeCategory = false
        updateMetrics()
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
            iconImageView.topAnchor.constraint(equalTo: topAnchor, constant: Self.topInset),
            iconImageView.centerXAnchor.constraint(equalTo: centerXAnchor),
            iconWidthConstraint,
            iconHeightConstraint,
            lockView.widthAnchor.constraint(equalToConstant: 18),
            lockView.heightAnchor.constraint(equalToConstant: 18),
            lockView.trailingAnchor.constraint(equalTo: iconImageView.trailingAnchor, constant: 4),
            lockView.topAnchor.constraint(equalTo: iconImageView.topAnchor, constant: -4),
            titleTopConstraint,
            titleLabel.leadingAnchor.constraint(equalTo: leadingAnchor),
            titleLabel.trailingAnchor.constraint(equalTo: trailingAnchor),
            titleLabel.bottomAnchor.constraint(lessThanOrEqualTo: bottomAnchor, constant: -Self.bottomInset)
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(model: LCAppModel, darkModeIcon: Bool, showLabels: Bool, gridSize: LCGridSize?, sizeCategory: UIContentSizeCategory) {
        self.sizeCategory = sizeCategory
        self.gridSize = gridSize
        iconWidthConstraint.constant = iconSide
        iconHeightConstraint.constant = iconSide
        iconImageView.layer.cornerRadius = iconSide * 14 / 60
        iconImageView.image = model.appInfo.iconIsDarkIcon(darkModeIcon) ?? UIImage(systemName: "app.fill")
        titleLabel.text = model.displayName
        titleLabel.isHidden = !showLabels
        titleTopConstraint.constant = showLabels ? Self.labelSpacing : 0
        hiddenTitleHeightConstraint.isActive = !showLabels
        lockView.isHidden = !model.appInfo.isLocked
        accessibilityLabel = model.displayName
        updateMetrics()
    }
}
