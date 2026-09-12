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
