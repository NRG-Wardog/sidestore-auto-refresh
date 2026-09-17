import Foundation

public enum AppLayoutStyle: String, CaseIterable, Identifiable, Codable {
    case list = "list"
    case grid = "grid"
    case compactList = "compactList"

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .list:
            return "List"
        case .grid:
            return "Grid"
        case .compactList:
            return "Compact List"
        }
    }
}

public enum LCGridSize: String, CaseIterable, Identifiable, Codable {
    case small
    case medium
    case large
    case extraLarge

    public static let storageKey = "LCGridSize"
    public static let defaultValue: LCGridSize = .medium
    public var id: String { rawValue }

    public static func resolve(_ rawValue: String?) -> LCGridSize {
        rawValue.flatMap(Self.init(rawValue:)) ?? defaultValue
    }

    public var displayName: String {
        switch self {
        case .small: return "Small"
        case .medium: return "Medium"
        case .large: return "Large"
        case .extraLarge: return "Extra Large"
        }
    }

    public var iconSize: CGFloat {
        switch self {
        case .small: return 48
        case .medium: return 60
        case .large: return 76
        case .extraLarge: return 96
        }
    }

    public var minimumWidth: CGFloat {
        switch self {
        case .small: return 84
        case .medium: return 100
        case .large: return 120
        case .extraLarge: return 144
        }
    }
}

public enum LCLaunchTab: String, CaseIterable, Identifiable, Codable {
    case home
    case apps

    public static let storageKey = "LCLaunchTab"
    public static let defaultValue: LCLaunchTab = .home
    public var id: String { rawValue }

    public static func resolve(_ rawValue: String?) -> LCLaunchTab {
        rawValue.flatMap(Self.init(rawValue:)) ?? defaultValue
    }

    public var displayName: String {
        switch self {
        case .home: return "Home"
        case .apps: return "My Apps"
        }
    }
}
