"""Artifact source inventory shared by packaging validation and legacy replay.

These values are preserved verbatim from the reviewed headless contract.
PINS identifies the historical artifact basis, not the current owner graph.
No mutation or product-code generation runs when importing this module.
"""

PINS = ("12377cf3b91d51739a33f14a302e5f522b238593", "ff25922e5c13ccfafd83bda5092910d848ebd409")

HEADLESS_SIDESTORE_VIEW_FILES = (
    "Views/Components/AppInfoView.swift",
    "Views/Components/BundleResourceBrowserView.swift",
    "Views/Components/CodeResourcesViewer.swift",
    "Views/Components/CustomAppIDAlertViewController.swift",
    "Views/Components/InfoPlistContainerView.swift",
    "Views/Components/MachOResourceViewer.swift",
    "Views/Components/UIKit/CollapsingMarkdownView.swift",
    "Views/MyApps/DeleteAppAlertViewController.swift",
    "Views/Settings/Advanced/Anisette/AnisetteDataView.swift",
    "Views/Settings/Advanced/BackupRestore/BackupAndRestoreView.swift",
    "Views/Settings/Advanced/Certificates/ActiveCertSectionView.swift",
    "Views/Settings/Advanced/Certificates/CertificateDetailView.swift",
    "Views/Settings/Advanced/Certificates/CertificateExporter.swift",
    "Views/Settings/Advanced/Certificates/CertificateRowView.swift",
    "Views/Settings/Advanced/Certificates/CertificateTypes.swift",
    "Views/Settings/Advanced/Certificates/CertificatesListView.swift",
    "Views/Settings/Advanced/Certificates/CertificatesView.swift",
    "Views/Settings/Advanced/Certificates/CertificatesViewModel.swift",
    "Views/Settings/Advanced/Certificates/PrivateKeyTextEditor.swift",
    "Views/Settings/Advanced/Certificates/PrivateKeyTextInputView.swift",
    "Views/Settings/Advanced/Certificates/RevokeAlertViewController.swift",
    "Views/Settings/Advanced/Certificates/SetCertificateAlertViewController.swift",
    "Views/Settings/Advanced/Certificates/SignableCertificatesListViewController.swift",
    "Views/Settings/Advanced/Connection/ConnectionConfigView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppGroups/AppGroupsListView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppIDs/AppIDDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/AppIDs/AppIDsListView.swift",
    "Views/Settings/Advanced/DeveloperServices/Certificates/CertificatePortalDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/Certificates/CertificatesPortalListView.swift",
    "Views/Settings/Advanced/DeveloperServices/DeveloperServicesView.swift",
    "Views/Settings/Advanced/DeveloperServices/DeveloperServicesViewModel.swift",
    "Views/Settings/Advanced/DeveloperServices/Devices/DevicesListView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/CreateManualProfileView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/ProfilePortalDetailView.swift",
    "Views/Settings/Advanced/DeveloperServices/Profiles/ProfilesListView.swift",
    "Views/Settings/Advanced/JIT/SideJITServerConfigView.swift",
    "Views/Settings/Advanced/NetworkDiscovery/BonjourDiscoveryView.swift",
    "Views/Settings/Advanced/NetworkDiscovery/BonjourDiscoveryViewModel.swift",
    "Views/Settings/Advanced/SideSign/SideSignConfigurationView.swift",
    "Views/Settings/Advanced/UserCustomizations/ThemePickerView.swift",
    "Views/Settings/Advanced/UserCustomizations/UserCustomizationsView.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairTargetDialog.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairView.swift",
    "Views/Settings/Advanced/WirelessPair/WirelessPairViewModel.swift",
    "Views/Settings/Auth/ExportAccountAlertViewController.swift",
    "Views/Settings/Auth/ImportAccountAlertController.swift",
    "Views/Settings/Auth/ResetAdiAlertViewController.swift",
    "Views/Settings/Auth/RevokeCertificatesAlertViewController.swift",
    "Views/Settings/Auth/SignOutAlertViewController.swift",
    "Views/Settings/Diagnostics/DeveloperOptionsView.swift",
    "Views/Settings/Diagnostics/ExperimentalFeaturesView.swift",
    "Views/Settings/Diagnostics/OperationsLoggingControlView.swift",
    "Views/Settings/TechyThings/ErrorLog/ConsoleLogView.swift",
    "Views/Settings/TechyThings/HealthCheck/HealthCheckView.swift",
    "Views/Settings/TechyThings/HealthCheck/HealthCheckViewModel.swift",
    "Views/Settings/TechyThings/StorageExplorer/DirectoryExplorerView.swift",
    "Views/Settings/TechyThings/StorageExplorer/StorageExplorerView.swift",
    "Views/Settings/TechyThings/StorageExplorer/StorageExplorerViewModel.swift",
    "Views/SplashView.swift",
)

HEADLESS_SIDESTORE_AUX_UI_FILES = (
    "DeepLinks/ExportCertificateDialog.swift",
    "DeepLinks/InstallAppDialog.swift",
    "Views/Settings/Advanced/CacheMgmt/CacheManagementView.swift",
    "Views/Settings/Advanced/CacheMgmt/CacheViewModel.swift",
    "Views/Settings/Advanced/Connection/ConnectionConfig.swift",
)

HEADLESS_SIDESTORE_PIPELINE_UI_FILES = (
    "Managing Apps/AppExtensionView.swift",
    "Permissions/ReviewPermissionsViewController.swift",
)

HEADLESS_SIDESTORE_HANDLER_UI_FILES = (
    "Handlers/PresenterProvider.swift",
)

HEADLESS_BACKEND_CONNECTION_CONFIG = '''// V3_HEADLESS_BACKEND_CONNECTION_CONFIG_V1: backend-owned transport configuration.
import Foundation
import Minimuxer

final class ConnectionConfig {
    static let shared = ConnectionConfig()

    private static var defaultOverrideIP: String { "" }
    private static var defaultRemoteServerIP: String { AppConstants.Connection.defaultRemoteServerIP }
    private static var defaultWireGuardServerHost: String { AppConstants.Proxy.address }
    private static var defaultWireGuardServerPort: UInt16 { AppConstants.Proxy.defaultPort }

    var tunnelIfaceIp: String?
    var tunnelIfaceSubnetMask: String?
    var tunnelPeerIp: String?
    var tunnelPeerSubnetMask: String?
    var tunnelPeerReachable = false
    var overrideTunnelPeerReachable = false
    var remotePeerIp: String?
    var remoteReachable = false

    // Read persisted settings on every access. LiveContainer writes these keys
    // directly through settingsSet, so a cached value would leave the already-
    // bound Minimuxer connection-mode callback stale for the process lifetime.
    var overrideTunnelPeerIp: String {
        get { UserDefaults.standard.tunnelOverridePeerIp ?? Self.defaultOverrideIP }
        set { UserDefaults.standard.tunnelOverridePeerIp = newValue }
    }

    var remoteServerIp: String {
        get { UserDefaults.standard.remoteServerIp ?? Self.defaultRemoteServerIP }
        set { UserDefaults.standard.remoteServerIp = newValue }
    }

    var useLocalVPN: Bool {
        get { UserDefaults.standard.useLocalVPN }
        set { UserDefaults.standard.useLocalVPN = newValue }
    }

    var wireguardServerHost: String {
        get { UserDefaults.standard.wireGuardServerHost ?? Self.defaultWireGuardServerHost }
        set { UserDefaults.standard.wireGuardServerHost = newValue }
    }

    var wireguardServerPort: UInt16 {
        get { UserDefaults.standard.wireGuardServerPort ?? Self.defaultWireGuardServerPort }
        set { UserDefaults.standard.wireGuardServerPort = newValue }
    }

    var connectionMode: DeviceConnectionMode {
        useLocalVPN ? .localVPN : .remoteServer
    }
}

extension UserDefaults {
    @objc var tunnelOverridePeerIp: String? {
        get { self.string(forKey: "TunnelOverridePeerIp") }
        set { self.set(newValue, forKey: "TunnelOverridePeerIp") }
    }

    @objc var remoteServerIp: String? {
        get { self.string(forKey: "RemoteServerIp") }
        set { self.set(newValue, forKey: "RemoteServerIp") }
    }

    @objc var wireGuardServerHost: String? {
        get { self.string(forKey: "WireGuardServerHost") }
        set { self.set(newValue, forKey: "WireGuardServerHost") }
    }

    var wireGuardServerPort: UInt16? {
        get {
            guard self.object(forKey: "WireGuardServerPort") != nil else { return nil }
            let val = self._wireGuardServerPort
            return (val > 0 && val <= 65535) ? UInt16(val) : nil
        }
        set {
            if let newValue {
                self._wireGuardServerPort = Int(newValue)
            } else {
                self.removeObject(forKey: "WireGuardServerPort")
            }
        }
    }

    @objc(wireGuardServerPort) private var _wireGuardServerPort: Int {
        get { self.integer(forKey: "WireGuardServerPort") }
        set { self.set(newValue, forKey: "WireGuardServerPort") }
    }
}'''


def verify_keychain_selection_contract(executable: bytes) -> None:
    """Check stable link markers, not the layout of interpolated Swift strings.

    The runtime diagnostic emits the selected scope, but Swift may encode its
    short `` scope=`` fragment as an immediate rather than a contiguous string
    in an optimized executable. The long prefix and migration key are link
    evidence only; they do not prove selection on a particular device.
    """
    if b"[LC_KEYCHAIN] GROUP_SELECTED" not in executable:
        raise ValueError("Keychain selection diagnostic missing from embedded executable")
    if b"LCSharedKeychainReadyV1" not in executable:
        raise ValueError("Legacy Keychain migration contract missing")
