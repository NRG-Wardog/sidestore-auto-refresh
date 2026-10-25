//
//  LCSettingsView.swift
//  LiveContainerSwiftUI
//
//  Created by s s on 2024/8/21.
//

import Foundation
import SideStoreSupport
import SwiftUI
import UserNotifications

enum JITEnablerType : Int, CaseIterable, Identifiable {
    var id: Int { rawValue }
    case SideJITServer = 0
    case StikJIT = 1
    case JITStreamerEBLegacy = 2
    case StikJITLC = 3
    case SideStore = 4
    case StosDebug = 5
    case StosDebugLC = 6
    
    var displayName: String {
        switch self {
        case .StikJIT: "StikDebug"
        case .StikJITLC: "StikDebug (Another LiveContainer/Multitask)"
        case .StosDebug: "StosDebug"
        case .StosDebugLC: "StosDebug (Another LiveContainer/Multitask)"
        case .SideStore: "SideStore"
        case .JITStreamerEBLegacy: "JitStreamer-EB (Relaunch)"
        case .SideJITServer: "SideJITServer/JITStreamer 2.0"
        }
    }
}

struct LCSettingsView: View {
    @Environment(\.locale) private var selectedDisplayLocale
    @Environment(\.layoutDirection) private var selectedDisplayDirection
    @EnvironmentObject private var sideStoreStatus: V3SideStoreStatusStore
    // These write trackers must survive the Form's direction-only reconstruction.
    @StateObject private var betaUpdateState = V3BoolSettingState()
    @StateObject private var idleTimeoutState = V3BoolSettingState()
    @AppStorage("LCHideReturnControl", store: UserDefaults.lcShared()) private var hideReturnControl = false
    @AppStorage("LCGuestReturnStartsCollapsed", store: UserDefaults.lcShared()) private var returnStartsCollapsed = false
    @AppStorage("LCGuestReturnCustomColors", store: UserDefaults.lcShared()) private var returnCustomColors = false
    @AppStorage("LCGuestReturnTintRGB", store: UserDefaults.lcShared()) private var returnTintRGB = 0x007AFF
    @AppStorage("LCGuestReturnBackgroundRGB", store: UserDefaults.lcShared()) private var returnBackgroundRGB = 0xF2F2F7
    @State var errorShow = false
    @State var errorDisplay = V3DisplayMessage.raw("")
    var errorInfo: String {
        get { errorDisplay.original }
        nonmutating set { errorDisplay = .raw(newValue) }
    }
    @State var successShow = false
    @State var successDisplay = V3DisplayMessage.raw("")
    var successInfo: String {
        get { successDisplay.original }
        nonmutating set { successDisplay = .raw(newValue) }
    }

    @State private var certificateDataFound = false
    @State private var v3OpenJITLessDiagnose = false // V3_CANONICAL_JITLESS_ROUTE_V1
    // V3_CERTIFICATE_IMPORT_OWNERSHIP_V1: persist only a short-lived opaque request id.
    private enum V3CertificateImportOwnership {
        private static let requestKey = "V3PendingCertificateImportRequestID"
        private static let expiryKey = "V3PendingCertificateImportExpiry"
        private static let lifetime: TimeInterval = 300
        private static let lock = NSLock()
        private static func invalidateLocked(_ defaults: UserDefaults) {
            defaults.removeObject(forKey: requestKey)
            defaults.removeObject(forKey: expiryKey)
        }
        private static func isActiveLocked(_ requestID: String, defaults: UserDefaults, now: Date) -> Bool {
            guard UUID(uuidString: requestID) != nil,
                  defaults.string(forKey: requestKey) == requestID,
                  let expiry = defaults.object(forKey: expiryKey) as? NSNumber else { return false }
            return expiry.doubleValue > now.timeIntervalSince1970
        }
        static func begin(defaults: UserDefaults = .standard, now: Date = Date()) -> String {
            lock.lock(); defer { lock.unlock() }
            let requestID = UUID().uuidString
            defaults.set(requestID, forKey: requestKey)
            defaults.set(now.addingTimeInterval(lifetime).timeIntervalSince1970, forKey: expiryKey)
            defaults.synchronize()
            return requestID
        }
        static func isActive(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            return isActiveLocked(requestID, defaults: defaults, now: now)
        }
        static func consume(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            guard isActiveLocked(requestID, defaults: defaults, now: now) else { return false }
            invalidateLocked(defaults)
            defaults.synchronize()
            return true
        }
        // Cancellation is scoped to the exact current request. A late cancel
        // from a superseded or expired prompt must not invalidate a newer import.
        @discardableResult
        static func cancel(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            guard isActiveLocked(requestID, defaults: defaults, now: now) else { return false }
            invalidateLocked(defaults)
            defaults.synchronize()
            return true
        }
        static func invalidate(defaults: UserDefaults = .standard) {
            lock.lock(); defer { lock.unlock() }
            invalidateLocked(defaults)
            defaults.synchronize()
        }
    }
    
    @StateObject private var certificateImportAlert = YesNoHelper()
    @StateObject private var certificateImportFromBuiltInSideStoreAlert = YesNoHelper()
    @StateObject private var certificateRemoveAlert = YesNoHelper()
    @StateObject private var certificateImportFileAlert = AlertHelper<URL>()
    @StateObject private var certificateImportPasswordAlert = InputHelper()
    
    @AppStorage("LCFrameShortcutIcons") var frameShortIcon = false
    @AppStorage("LCSwitchAppWithoutAsking") var silentSwitchApp = false
    @AppStorage("LCOpenWebPageWithoutAsking") var silentOpenWebPage = false
    @AppStorage("LCDontSignApp", store: LCUtils.appGroupUserDefault) var dontSignApp = false
    @AppStorage("LCStrictHiding", store: LCUtils.appGroupUserDefault) var strictHiding = false
    @AppStorage("dynamicColors", store: LCUtils.appGroupUserDefault) var dynamicColors = true
    @AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) var darkModeIcon = false
    @AppStorage(LCGridSize.storageKey, store: LCUtils.appGroupUserDefault) private var gridSize: LCGridSize = .medium
    @AppStorage(LCLaunchTab.storageKey, store: LCUtils.appGroupUserDefault) private var launchTab: LCLaunchTab = .home
    @AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var showAppLabels: Bool = true
    
    @AppStorage("LCSideJITServerAddress", store: LCUtils.appGroupUserDefault) var sideJITServerAddress : String = ""
    @AppStorage("LCDeviceUDID", store: LCUtils.appGroupUserDefault) var deviceUDID: String = ""
    @AppStorage("LCJITEnablerType", store: LCUtils.appGroupUserDefault) var JITEnabler: JITEnablerType = .SideJITServer
    
    @State var store : Store = .Unknown
    
    @AppStorage("LCLoadTweaksToSelf") var injectToLCItelf = false
    @AppStorage("LCIgnoreJITOnLaunch") var ignoreJITOnLaunch = false
    @AppStorage("LCSelected32BitEmulator", store: LCUtils.appGroupUserDefault) var selected32BitEmulator : String = ""
    @AppStorage("LCKeepSelectedWhenQuit") var keepSelectedWhenQuit = false
    @AppStorage("LCWaitForDebugger") var waitForDebugger = false
    @AppStorage("LCSharePrivateDataWithLiveProcess") var sharePrivateDataWithLiveProcess = false
    @AppStorage("BKNoWatchdogs") var disableLiveProcessWatchdog = false
    
    @EnvironmentObject private var sharedModel : SharedModel
    
    @State private var isViewAppeared = false
    
    let storeName = LCUtils.getStoreName()
    
    init() {
        _certificateDataFound = State(initialValue: LCSharedUtils.certificatePassword() != nil)
        _store = State(initialValue: LCUtils.store())
    }
    
    private func returnColorBinding(_ rgb: Binding<Int>) -> Binding<Color> {
        Binding(get: {
            let value = rgb.wrappedValue
            return Color(.sRGB, red: Double((value >> 16) & 0xFF) / 255,
                         green: Double((value >> 8) & 0xFF) / 255,
                         blue: Double(value & 0xFF) / 255, opacity: 1)
        }, set: { color in
            var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
            guard UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha) else { return }
            func channel(_ value: CGFloat) -> Int {
                Int((min(1, max(0, value)) * 255).rounded())
            }
            rgb.wrappedValue = (channel(red) << 16) | (channel(green) << 8) | channel(blue)
        })
    }

    var body: some View {
        let _ = selectedDisplayLocale.identifier
        NavigationView {
            Form {
                V3LanguageSettingsSection()
                V3AccountSettings(betaUpdateState: betaUpdateState, idleTimeoutState: idleTimeoutState)
                Section {
                    Toggle("Show Return Button", isOn: Binding(get: { !hideReturnControl }, set: { hideReturnControl = !$0 }))
                    Group {
                        Toggle("Start Collapsed", isOn: $returnStartsCollapsed)
                        Toggle("Use Custom Colors", isOn: $returnCustomColors)
                        if returnCustomColors {
                            ColorPicker("Icon Color", selection: returnColorBinding($returnTintRGB), supportsOpacity: false)
                            ColorPicker("Button Background", selection: returnColorBinding($returnBackgroundRGB), supportsOpacity: false)
                        }
                    }
                    .disabled(hideReturnControl)
                } header: {
                    Text("Guest Controls")
                } footer: {
                    Text("Start Collapsed shows an edge tab when a guest opens and after using Return. Tap the tab to expand, then tap Return to go back. Long-press Return to collapse it again. Icon Color also applies to the tab; its background stays transparent. Turn off Use Custom Colors to restore system colors.")
                }
                Section {
                    NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("Refresh, Schedule and History") }
                }
                if sharedModel.multiLCStatus != 2 {
                    Section{
                        if !certificateDataFound {
                            Button {
                                Task{ await importCertificate() }
                            } label: {
                                Text("lc.settings.importCertificate".loc)
                            }
                        } else {
                            Button {
                                Task{ await removeCertificate() }
                            } label: {
                                Text("lc.settings.removeCertificate".loc)
                            }
                        }
                        if store == .AltStore || store == .SideStore {
                            Button {
                                Task{ await importCertificateFromSideStore() }
                            } label: {
                                if certificateDataFound {
                                    Text("lc.settings.refreshCertificateFromStore %@".localizeWithFormat(storeName))
                                } else {
                                    Text("lc.settings.importCertificateFromStore %@".localizeWithFormat(storeName))
                                }
                            }
                        }
                        
                        NavigationLink {
                            LCJITLessDiagnoseView()
                        } label: {
                            Text("lc.settings.jitlessDiagnose".loc)
                        }

                    } header: {
                        Text("lc.settings.jitLess".loc)
                    } footer: {
                        Text("lc.settings.jitLessDesc".loc)
                    }
                }
                if (store != .Unknown && store != .ADP) || LCUtils.isAppGroupAltStoreLike() {
                    Section{
                        NavigationLink {
                            LCMultiLCManagementView()
                        } label: {
                            if sharedModel.multiLCStatus == 0 {
                                Text("lc.settings.multiLC".loc)
                            } else if sharedModel.multiLCStatus == 2 {
                                Text("lc.settings.multiLCIsSecond".loc)
                            }
                            
                        }
                        .disabled(sharedModel.multiLCStatus == 2)
                        
                        if(sharedModel.multiLCStatus == 2) {
                            NavigationLink {
                                LCJITLessDiagnoseView()
                            } label: {
                                Text("lc.settings.jitlessDiagnose".loc)
                            }
                        }
                    } footer: {
                        Text("lc.settings.multiLCDesc".loc)
                    }
                }
                
                if #available(iOS 16.1, *) {
                    Section {
                        NavigationLink {
                            LCMultitaskSettingView()
                        } label: {
                            Text("lc.appBanner.multitask".loc)
                        }
                    } footer: {
                        Text("lc.settings.multitaskDesc".loc)
                    }
                }
                
                Section {
                    if JITEnabler == .SideJITServer || JITEnabler == .JITStreamerEBLegacy {
                        HStack {
                            Text("lc.settings.JitAddress".loc)
                            Spacer()
                            TextField(JITEnabler == .SideJITServer ? "http://x.x.x.x:8080" : "http://[fd00::]:9172", text: $sideJITServerAddress)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                    if JITEnabler == .SideJITServer {
                        HStack {
                            Text("lc.settings.JitUDID".loc)
                            Spacer()
                            TextField("", text: $deviceUDID)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                    Picker(selection: $JITEnabler) {
                        ForEach(JITEnablerType.allCases) { enablerType in
                            Group {
                                switch enablerType {
                                case .StikJITLC, .StosDebugLC, .JITStreamerEBLegacy:
                                    V3LocalizedText(enablerType.displayName)
                                default:
                                    Text(verbatim: enablerType.displayName)
                                }
                            }.tag(enablerType)
                        }
                    } label: {
                        Text("lc.settings.jitEnabler".loc)
                    }

                } header: {
                    Text("JIT")
                } footer: {
                    Text("lc.settings.JitDesc".loc)
                }
                
                Section {
                    Picker(selection: $selected32BitEmulator) {
                        ForEach(sharedModel.arm32EmuApps, id: \.self) { app in
                            Text("lc.common.none".loc).tag("")
                            Text(app.appInfo.displayName()).tag(app.appInfo.relativeBundlePath!)
                        }
                    } label: {
                        Text("lc.settings.selected32BitEmulator".loc)
                    }
                }
                
                Section{
                    // LC_APP_LAYOUT_PATCH_V1
                    Picker("Grid Size", selection: $gridSize) {
                        ForEach(LCGridSize.allCases) { size in
                            V3LocalizedText(size.displayName).tag(size)
                        }
                    }
                    Toggle("Show app labels", isOn: $showAppLabels)
                    Picker("Default Launch Screen", selection: $launchTab) {
                        ForEach(LCLaunchTab.allCases) { tab in
                            V3LocalizedText(tab.displayName).tag(tab)
                        }
                    }
                    Toggle(isOn: $dynamicColors) {
                        Text("lc.settings.dynamicColors".loc)
                    }
                    if #available(iOS 18.0, *) {
                        Toggle(isOn: $darkModeIcon) {
                            Text("lc.settings.darkModeIcon".loc)
                        }
                    }
                    
                } header: {
                    Text("lc.settings.interface".loc)
                } footer: {
                    Text("lc.settings.dynamicColors.desc".loc)
                }
                Section{
                    Toggle(isOn: $frameShortIcon) {
                        Text("lc.settings.FrameIcon".loc)
                    }
                } header: {
                    Text("lc.common.miscellaneous".loc)
                } footer: {
                    Text("lc.settings.FrameIconDesc".loc)
                }
                
                Section {
                    Toggle(isOn: $silentSwitchApp) {
                        Text("lc.settings.silentSwitchApp".loc)
                    }
                } footer: {
                    Text("lc.settings.silentSwitchAppDesc".loc)
                }
                
                Section {
                    Toggle(isOn: $silentOpenWebPage) {
                        Text("lc.settings.silentOpenWebPage".loc)
                    }
                } footer: {
                    Text("lc.settings.silentOpenWebPageDesc".loc)
                }
                
                if sharedModel.isHiddenAppUnlocked {
                    Section {
                        Toggle(isOn: $strictHiding) {
                            Text("lc.settings.strictHiding".loc)
                        }
                    } footer: {
                        Text("lc.settings.strictHidingDesc".loc)
                    }
                }
                
                Section {
                    Toggle(isOn: $dontSignApp) {
                        Text("lc.settings.dontSign".loc)
                    }
                } footer: {
                    Text("lc.settings.dontSignDesc".loc)
                }

                Section {
                    Button {
                        clearNotifications()
                    } label: {
                        Text("lc.settings.clearNotifications".loc)
                    }
                }

                Section {
                    if sharedModel.multiLCStatus != 2 {
                        NavigationLink {
                            LCStorageManagementView()
                        } label: {
                            Text("lc.settings.storageManagement".loc)
                        }
                    }
                    NavigationLink {
                        LCDataManagementView()
                    } label: {
                        Text("lc.settings.dataManagement".loc)
                    }
                }
                
                Section {
                    HStack {
                        Image("GitHub")
                        Button("LiveContainer/LiveContainer") {
                            openGitHub()
                        }
                    }
                    HStack {
                        Image("Twitter")
                        Button("khanhduytran0") {
                            openTwitter()
                        }
                    }
                    HStack {
                        Image("GitHub")
                        Button("Huge_Black") {
                            openGitHub2()
                        }
                    }
                } header: {
                    Text("lc.settings.about".loc)
                } footer: {
                    Text("lc.settings.warning".loc)
                }
                
                VStack{
                    Text(LCUtils.getVersionInfo())
                        .foregroundStyle(.gray)
                        .onTapGesture(count: 5) {
                            sharedModel.developerMode = true
                        }
                }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
                    .background(Color(UIColor.systemGroupedBackground))
                    .listRowInsets(EdgeInsets())
                
                Section("Build Candidate") {
                    Text(V3Localization.format("Product %@", Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String ?? "unknown"))
                    Text(Bundle.main.object(forInfoDictionaryKey: "LCBuilderCommit") as? String ?? "unknown commit").font(.caption).textSelection(.enabled)
                    Button("Copy Build Diagnostics") {
                        UIPasteboard.general.string = ["LCProductLine", "LCBuilderCommit", "LCBuildRunURL"].map {
                            $0 + "=" + (Bundle.main.object(forInfoDictionaryKey: $0) as? String ?? "unknown")
                        }.joined(separator: "\n")
                    }
                }

                if sharedModel.developerMode {
                    Section {
                        Toggle(isOn: $injectToLCItelf) {
                            Text("lc.settings.injectLCItself".loc)
                        }
                        Toggle(isOn: $ignoreJITOnLaunch) {
                            Text("Ignore JIT on Launching App")
                        }
                        Toggle(isOn: $keepSelectedWhenQuit) {
                            Text("Keep Selected App when Quit")
                        }
                        Toggle(isOn: $waitForDebugger) {
                            Text("Wait For Debugger")
                        }
                        Toggle(isOn: $sharePrivateDataWithLiveProcess) {
                            Text("Allow Private Data access from LiveProcess")
                        }
                        Toggle(isOn: $disableLiveProcessWatchdog) {
                            Text("Disable LiveProcess watchdog termination")
                        }
                        Button {
                            export()
                        } label: {
                            Text("Export Cert")
                        }
                        Button {
                            exportDyld()
                        } label: {
                            Text("Export Dyld")
                        }
                        Button {
                            Task { await nukeSideStore() }
                        } label: {
                            Text("Nuke SideStore")
                        }
                        Button {
                            exportMainBundle()
                        } label: {
                            Text("Export Main Bundle")
                        }
                        Button {
                            resetSymbolOffsets()
                        } label: {
                            Text("Reset Symbol Offsets")
                        }
                        Button {
                            presentFLEXOverlay()
                        } label: {
                            Text("Show FLEX Overlay")
                        }
                        .disabled(NSClassFromString("FLEXManager") == nil)
                    } header: {
                        Text("Developer Settings")
                    } footer: {
                        Text("lc.settings.injectLCItselfDesc".loc)
                    }
                }
            }
            // Recreate only native Form layout when crossing the RTL/LTR boundary.
            // Settings values and asynchronous write ownership live above this identity.
            // Presentation and navigation modifiers below deliberately remain outside it.
            .id(selectedDisplayDirection == .rightToLeft)
            // V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1: a background is laid out
            // outside the Form row structure, so this programmatic route cannot
            // produce an empty Settings row at any text size or device width, and
            // leaves no accessibility ghost element.
            .background(
                NavigationLink(destination: LCJITLessDiagnoseView(), isActive: $v3OpenJITLessDiagnose) { EmptyView() }
                    .hidden()
            )
            .navigationBarTitle("lc.tabView.settings".loc)
            .alert("lc.common.error".loc, isPresented: $errorShow){
            Button("lc.common.ok".loc, role: .cancel) {}
            Button("lc.common.copy".loc) {
                UIPasteboard.general.string = LCNativeErrorPresentation.details(errorInfo, site: .settings)
            }
            } message: {
                V3LocalizedText(errorDisplay.appendingDiagnostic(LCNativeErrorSite.settings.rawValue))
            }
            .alert("lc.common.success".loc, isPresented: $successShow){
            } message: {
                V3LocalizedText(successDisplay)
            }
            .alert("lc.settings.importCertificate".loc, isPresented: $certificateImportAlert.show) {
                Button {
                    certificateImportAlert.close(result: true)
                } label: {
                    Text("lc.common.ok".loc)
                }

                Button("lc.common.cancel".loc, role: .cancel) {
                    certificateImportAlert.close(result: false)
                }
            } message: {
                Text("lc.settings.importCertificateDesc".loc)
            }
            .alert("lc.settings.removeCertificate".loc, isPresented: $certificateRemoveAlert.show) {
                Button(role: .destructive) {
                    certificateRemoveAlert.close(result: true)
                } label: {
                    Text("lc.common.ok".loc)
                }

                Button("lc.common.cancel".loc, role: .cancel) {
                    certificateRemoveAlert.close(result: false)
                }
            } message: {
                Text("lc.settings.removeCertificateDesc".loc)
            }
            .alert("lc.settings.importCertFromBuiltinSideStore".loc, isPresented: $certificateImportFromBuiltInSideStoreAlert.show) {
                Button {
                    certificateImportFromBuiltInSideStoreAlert.close(result: true)
                } label: {
                    Text("lc.common.ok".loc)
                }
                Button("lc.common.cancel".loc, role: .cancel) {
                    certificateImportFromBuiltInSideStoreAlert.close(result: false)
                }
            } message: {
                Text("lc.settings.importCertFromBuiltinSideStoreDesc".loc)
            }
            .betterFileImporter(isPresented: $certificateImportFileAlert.show, types: [.p12], multiple: false, callback: { fileUrls in
                certificateImportFileAlert.close(result: fileUrls[0])
            }, onDismiss: {
                certificateImportFileAlert.close(result: nil)
            })
            .textFieldAlert(
                isPresented: $certificateImportPasswordAlert.show,
                title: "lc.settings.importCertificateInputPassword",
                text: $certificateImportPasswordAlert.initVal,
                placeholder: "",
                action: { newText in
                    certificateImportPasswordAlert.close(result: newText)
                },
                actionCancel: {_ in
                    certificateImportPasswordAlert.close(result: nil)
                    certificateImportPasswordAlert.show = false
                }
            )
        }
        .navigationViewStyle(StackNavigationViewStyle())
        // Load lifetime follows Settings, not reconstruction of its native Form.
        .task { await betaUpdateState.load(key: "isBetaUpdatesEnabled", status: sideStoreStatus) }
        .task { await idleTimeoutState.load(key: "isIdleTimeoutDisableEnabled", status: sideStoreStatus) }
        .onAppear() {
            if !isViewAppeared {
                guard sharedModel.selectedTab == .settings, let link = sharedModel.deepLink else { return }
                sharedModel.deepLink = nil
                handleURL(url: link)
                isViewAppeared = true
            }
        }
        .onChange(of: sharedModel.deepLink) { link in
            guard sharedModel.selectedTab == .settings, let link else { return }
            sharedModel.deepLink = nil
            handleURL(url: link)
        }
    }
    
    func openGitHub() {
        UIApplication.shared.open(URL(string: "https://github.com/LiveContainer/LiveContainer")!)
    }
    
    func openGitHub2() {
        UIApplication.shared.open(URL(string: "https://github.com/hugeBlack")!)
    }
    
    func openTwitter() {
        UIApplication.shared.open(URL(string: "https://twitter.com/khanhduytran0")!)
    }

    func clearNotifications() {
        let notificationCenter = UNUserNotificationCenter.current()
        notificationCenter.removeAllDeliveredNotifications()
        notificationCenter.removeAllPendingNotificationRequests()
        if #available(iOS 16.0, *) {
            notificationCenter.setBadgeCount(0)
        } else {
            UIApplication.shared.applicationIconBadgeNumber = 0
        }
    }

    func export() {
        let fileManager = FileManager.default
        let documentsURL = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first!
        
        // 1. Copy embedded.mobileprovision from the main bundle to Documents
        if let embeddedURL = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision") {
            let destinationURL = documentsURL.appendingPathComponent("embedded.mobileprovision")
            do {
                try fileManager.copyItem(at: embeddedURL, to: destinationURL)
                print("Successfully copied embedded.mobileprovision to Documents.")
            } catch {
                print("Error copying embedded.mobileprovision: \(error)")
            }
        } else {
            print("embedded.mobileprovision not found in the main bundle.")
        }
        
        // 2. Read "certData" from UserDefaults and save to cert.p12 in Documents
        if let certData = LCUtils.certificateData() {
            let certFileURL = documentsURL.appendingPathComponent("cert.p12")
            do {
                try certData.write(to: certFileURL)
                print("Successfully wrote certData to cert.p12 in Documents.")
            } catch {
                print("Error writing certData to cert.p12: \(error)")
            }
        } else {
            print("certData not found in UserDefaults.")
        }
        
        // 3. Read "certPassword" from UserDefaults and save to pass.txt in Documents
        if let certPassword = LCSharedUtils.certificatePassword() {
            let passwordFileURL = documentsURL.appendingPathComponent("pass.txt")
            do {
                try certPassword.write(to: passwordFileURL, atomically: true, encoding: .utf8)
                print("Successfully wrote certPassword to pass.txt in Documents.")
            } catch {
                print("Error writing certPassword to pass.txt: \(error)")
            }
        } else {
            print("certPassword not found in UserDefaults.")
        }
    }
    
    func exportMainBundle() {
        let url = Bundle.main.bundleURL
        let fileManager = FileManager.default
        let documentsURL = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first!
        do {
            let destinationURL = documentsURL.appendingPathComponent(url.lastPathComponent)
            try fileManager.copyItem(at: url, to: destinationURL)
            print("Successfully copied main bundle to Documents.")
        } catch {
            print("Error copying main bundle \(error)")
        }
    }
    
    func resetSymbolOffsets() {
        LCUtils.appGroupUserDefault.removeObject(forKey: "symbolOffsetCache")
    }
    
    func presentFLEXOverlay() {
        let manager = (NSClassFromString("FLEXManager") as? NSObject.Type)?.perform(NSSelectorFromString("sharedManager"))
            .takeUnretainedValue() as? NSObject
        manager?.perform(NSSelectorFromString("showExplorer"))
    }
    
    func importCertificate() async {
        guard let doImport = await certificateImportAlert.open(), doImport else {
            return
        }
        guard let certificateURL = await certificateImportFileAlert.open() else {
            return
        }
        guard let certificatePassword = await certificateImportPasswordAlert.open() else {
            return
        }
        let certificateData : Data
        do {
            certificateData = try Data(contentsOf: certificateURL)
        } catch {
            errorDisplay = .preserving(error)
            errorShow = true
            return
        }
        
        guard let _ = LCUtils.getCertTeamId(withKeyData: certificateData, password: certificatePassword) else {
            errorDisplay = .localized("lc.settings.invalidCertError")
            errorShow = true
            return
        }

        // V3_CANONICAL_JITLESS_MANUAL_IMPORT_INVALIDATES_PENDING_V1
        V3CertificateImportOwnership.invalidate()
        LCUtils.appGroupUserDefault.set(certificateData, forKey: "LCCertificateData")
        LCUtils.appGroupUserDefault.set(certificatePassword, forKey: "LCCertificatePassword")
        LCUtils.appGroupUserDefault.set(NSDate.now, forKey: "LCCertificateUpdateDate")
        certificateDataFound = true

        UserDefaults.standard.set(LCSharedUtils.appGroupID(), forKey: "LCAppGroupID")
        // V3_CANONICAL_JITLESS_MANUAL_IMPORT_EVENT_V1
        NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)
    }
    
    // V3_SERVICE_CERTIFICATE_EXPORT_V1: only the SideStore process can read its
    // active Keychain group. The returned PKCS#12 is transient and enters the
    // existing explicitly-confirmed, request-owned callback path.
    func importCertificateFromSideStore() async {
        // V3_SERVICE_CERTIFICATE_EXPORT_V1
        let requestID = V3CertificateImportOwnership.begin()
        if UserDefaults.sideStoreExist() {
            guard let accepted = await certificateImportFromBuiltInSideStoreAlert.open(), accepted else {
                _ = V3CertificateImportOwnership.cancel(requestID)
                return
            }
            guard V3CertificateImportOwnership.isActive(requestID) else { return }

            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "certExportActive")
                guard V3CertificateImportOwnership.isActive(requestID),
                      Set(reply.keys) == Set(["data", "password", "teamIdentifier", "identitySHA256"]),
                      let data = reply["data"] as? Data, !data.isEmpty, data.count <= 1_048_576,
                      let password = reply["password"] as? String,
                      password.utf8.count <= 512,
                      let team = reply["teamIdentifier"] as? String,
                      !team.isEmpty, team.utf8.count <= 64,
                      let fingerprint = reply["identitySHA256"] as? String,
                      fingerprint.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                      LCUtils.getCertTeamId(withKeyData: data, password: password) == team else {
                    throw NSError(domain: "V3CertificateImport", code: 2)
                }
                let status = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
                guard V3CertificateImportOwnership.isActive(requestID),
                      let current = status["certificateState"] as? [String: Any],
                      V3ServiceBridge.strictBool(current["active"]) == true,
                      current["team"] as? String == team,
                      current["certificateIdentitySHA256"] as? String == fingerprint else {
                    throw NSError(domain: "V3CertificateImport", code: 3)
                }
                v3CompleteSideStoreCertificateImport(certificateData: data, password: password,
                    requestID: requestID)
            } catch {
                guard V3CertificateImportOwnership.cancel(requestID) else { return }
                errorInfo = "The active SideStore certificate could not be imported. Check Certificates and try again."
                errorShow = true
            }
        } else {
            _ = V3CertificateImportOwnership.cancel(requestID)
            errorInfo = "Embedded SideStore is unavailable in this LiveContainer build."
            errorShow = true
        }
    }

    // Only an exact, live, one-use request may reach the existing three-key writer.
    private func v3CompleteSideStoreCertificateImport(certificateData: Data, password: String, requestID: String) {
        guard V3CertificateImportOwnership.consume(requestID) else { return }
        onSideStoreCertificateCallback(certificateData: certificateData, password: password)
    }
    func onSideStoreCertificateCallback(certificateData: Data, password: String) {
        LCUtils.appGroupUserDefault.set(certificateData, forKey: "LCCertificateData")
        LCUtils.appGroupUserDefault.set(password, forKey: "LCCertificatePassword")
        LCUtils.appGroupUserDefault.set(NSDate.now, forKey: "LCCertificateUpdateDate")
        certificateDataFound = true
        NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)
    }
    
    func removeCertificate() async {
        guard let doRemove = await certificateRemoveAlert.open(), doRemove else {
            return
        }

        V3CertificateImportOwnership.invalidate()
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateData")
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificatePassword")
        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateUpdateDate")
        certificateDataFound = false

        UserDefaults.standard.set(nil, forKey: "LCAppGroupID")
        NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)
    }
    
    func nukeSideStore() async {
        guard let doRemove = await certificateRemoveAlert.open(), doRemove else {
            return
        }
        do {
            let fm = FileManager.default
            let sidestoreAppGroupURL = LCPath.lcGroupDocPath.deletingLastPathComponent()
            try fm.removeItem(at: sidestoreAppGroupURL.appendingPathComponent("Database"))
            try fm.removeItem(at: sidestoreAppGroupURL.appendingPathComponent("Apps"))
        } catch {
            print("wtf \(error)")
        }
    }
    
    func exportDyld() {
        let url = URL(fileURLWithPath: "/usr/lib/dyld")
        let fileManager = FileManager.default
        let documentsURL = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first!
        do {
            let destinationURL = documentsURL.appendingPathComponent(url.lastPathComponent)
            try fileManager.copyItem(at: url, to: destinationURL)
            print("Successfully copied dyld to Documents.")
        } catch {
            print("Error copying dyld \(error)")
        }
    }
    
    func handleURL(url: URL) {
        if url.host == "jitless-setup" {
            Task { await importCertificateFromSideStore() }
            return
        }
        if url.host == "jitless-diagnose" {
            v3OpenJITLessDiagnose = true
            return
        }
        if url.host == "certificate" {
            if let components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
                let queryItems = components.queryItems?.reduce(into: [String: String]()) { $0[$1.name.lowercased()] = $1.value } ?? [:]
                guard let encodedCert = queryItems["cert"]?.removingPercentEncoding,
                      let password = queryItems["password"],
                      let certData = Data(base64Encoded: encodedCert)
                else { return }
                
                guard let requestID = queryItems["request_id"],
                      V3CertificateImportOwnership.isActive(requestID) else { return }
                v3CompleteSideStoreCertificateImport(certificateData: certData, password: password, requestID: requestID)
                
            }
        }
    }
}

// LC_NATIVE_ERROR_PRESENTERS_V1
