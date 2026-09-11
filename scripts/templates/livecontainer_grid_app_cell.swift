import Foundation
import SwiftUI
import UIKit

struct LCGridAppCell: View {
    @ObservedObject var appModel: LCAppModel
    var delegate: LCAppBannerDelegate
    var showLabels: Bool

    @AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault) private var dynamicColors = true
    @AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) private var darkModeIcon = false

    init(appModel: LCAppModel, delegate: LCAppBannerDelegate, showLabels: Bool) {
        self.appModel = appModel
        self.delegate = delegate
        self.showLabels = showLabels
    }

    private var appIcon: UIImage {
        appModel.appInfo.iconIsDarkIcon(darkModeIcon) ?? UIImage()
    }

    var body: some View {
        Button {
            Task {
                await launchApp()
            }
        } label: {
            VStack(spacing: 6) {
                ZStack(alignment: .topTrailing) {
                    Image(uiImage: appIcon)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 60, height: 60)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .shadow(color: Color.black.opacity(0.12), radius: 4, x: 0, y: 2)

                    if appModel.appInfo.isLocked {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.white)
                            .padding(4)
                            .background(Circle().fill(Color.black.opacity(0.6)))
                            .offset(x: 4, y: -4)
                    }
                }

                if showLabels {
                    Text(appModel.displayName)
                        .font(.system(size: 12, weight: .medium))
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                        .foregroundColor(.primary)
                        .frame(maxWidth: 80)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(appModel.displayName)
        .contextMenu {
            contextMenuItems
        }
    }

    @ViewBuilder
    private var contextMenuItems: some View {
        if appModel.uiContainers.count > 1 {
            Menu("Containers") {
                ForEach(appModel.uiContainers, id: \.self) { container in
                    Button {
                        appModel.uiSelectedContainer = container
                    } label: {
                        if container == appModel.uiSelectedContainer {
                            Label(container.name, systemImage: "checkmark")
                        } else {
                            Text(container.name)
                        }
                    }
                }
            }
        }

        if !appModel.uiIsShared, appModel.uiSelectedContainer != nil {
            Button {
                if let dataFolder = appModel.uiSelectedContainer?.dataUUID {
                    let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                    let folderURL = documentsURL.appendingPathComponent("Data").appendingPathComponent(dataFolder)
                    delegate.openNavigationView(view: AnyView(LCDataManagementView(currentUrl: folderURL, isEditing: false)))
                }
            } label: {
                Label("lc.appBanner.openDataFolder".loc, systemImage: "folder")
            }
        }

        if #available(iOS 16.0, *) {
            let shouldLaunchInMultitaskMode = appModel.shouldLaunchInMultitaskMode
            Button {
                Task {
                    await launchApp(multitask: !shouldLaunchInMultitaskMode)
                }
            } label: {
                Label(
                    shouldLaunchInMultitaskMode ? "lc.appBanner.run".loc : "lc.appBanner.multitask".loc,
                    systemImage: shouldLaunchInMultitaskMode ? "play.fill" : "macwindow.badge.plus"
                )
            }
        }

        Menu {
            Button {
                UIPasteboard.general.string = appModel.launchUrlStr
            } label: {
                Label("lc.appBanner.copyLaunchUrl".loc, systemImage: "link")
            }

            Button {
                Task {
                    if let icon = appModel.appInfo.iconIsDarkIcon(darkModeIcon) {
                        UIImageWriteToSavedPhotosAlbum(icon, nil, nil, nil)
                    }
                }
            } label: {
                Label("lc.appBanner.saveAppIcon".loc, systemImage: "square.and.arrow.down")
            }
        } label: {
            Label("lc.appBanner.addToHomeScreen".loc, systemImage: "plus.app")
        }

        Button {
            delegate.openNavigationView(view: AnyView(LCAppSettingsView(appModel: appModel, delegate: delegate)))
        } label: {
            Label("lc.tabView.settings".loc, systemImage: "gear")
        }

        Button(role: .destructive) {
            delegate.removeApp(app: appModel)
        } label: {
            Label("lc.common.delete".loc, systemImage: "trash")
        }
    }

    private func launchApp(multitask: Bool? = nil) async {
        if appModel.appInfo.isLocked && !DataManager.shared.model.isHiddenAppUnlocked {
            do {
                if !(try await LCUtils.authenticateUser()) {
                    return
                }
            } catch {
                return
            }
        }

        do {
            try await appModel.runApp(multitask: multitask)
        } catch {
        }
    }
}
