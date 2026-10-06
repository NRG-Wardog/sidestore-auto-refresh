import Foundation
import SwiftUI
import UIKit
import CoreFoundation
enum V3ServiceBridge {
    public static func strictBool(_ value: Any?) -> Bool? {
        V3WireContract.strictBool(value)
    }

    public static func strictInt(_ value: Any?) -> Int? {
        V3WireContract.strictInt(value)
    }
}
