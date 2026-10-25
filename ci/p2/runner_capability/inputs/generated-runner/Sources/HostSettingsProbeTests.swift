import Foundation
import XCTest

// This code runs only in a separately generated XCTest runner. It imports no
// product module, never evaluates product source, and never creates a host app.
final class HostSettingsProbeTests: XCTestCase {
    private let identity: [String: String] = [
        "bundleIdentifier": "com.kdt.livecontainer",
        "executableSHA256": "72eae07bb0f34fed85e47fa5d40b61383f2a774f5ffe9433c5d296f6e77d9f3f",
        "sourceCommit": "eb10cedb772bc0496d67edd7a269679e24826b9f",
        "sourceTree": "d6960a99a2f022a333c364fa4d28e264568a28ca",
        "builderCommit": "4cb67e4d252ba1b1f7a6fa6431560f75264f337c"
    ]
    private var app: XCUIApplication!
    private var observations: [[String: Any]] = []
    private var navigation: [[String: Any]] = []
    private var status = "blocked"
    private var stage = "before-launch"
    private var launchCount = 0

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func attachText(_ text: String, name: String) {
        let attachment = XCTAttachment(string: text)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func capture(_ name: String) {
        guard app != nil else { return }
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "host-probe-\(name).png"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        let tree = app.debugDescription
        // The actual app snapshot is retained, bounded to 512 KiB. A truncated
        // snapshot cannot satisfy a successful observation receipt.
        let bytes = Array(tree.utf8.prefix(512 * 1024))
        attachText(String(decoding: bytes, as: UTF8.self), name: "host-probe-\(name).txt")
        XCTAssertLessThanOrEqual(tree.utf8.count, 512 * 1024, "AX evidence exceeds bound")
    }

    override func tearDownWithError() throws {
        if status != "observed", app != nil { capture("failure") }
        let report: [String: Any] = [
            "identity": identity, "status": status, "stage": stage,
            "launchCount": launchCount, "navigation": navigation,
            "observations": observations
        ]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        let receipt = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
        receipt.name = "host-probe-observations.json"
        receipt.lifetime = .keepAlways
        add(receipt)
        // No teardown termination/relaunch, preference clearing or device reset.
    }

    private func noUnexpectedAlerts() {
        XCTAssertEqual(app.alerts.count, 0, "Unexpected app/entitlement/authentication prompt blocks probe")
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        XCTAssertEqual(springboard.alerts.count, 0, "OS permission/system prompt blocks probe")
    }

    private func cancelKnownNotificationExplanationIfPresent() {
        // The source at the pinned commit defines Later as role: .cancel with
        // an empty closure. Only Allow Notifications requests OS permission.
        // Exact title, body and complete action inventory must all match.
        guard app.alerts.firstMatch.waitForExistence(timeout: 3) else { return }
        stage = "startup-prompt-inventory"
        XCTAssertEqual(app.alerts.count, 1)
        let alert = app.alerts.firstMatch
        let title = "Stay Informed About Refreshes"
        let body = "LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this."
        let later = "Later"
        let allow = "Allow Notifications"
        capture("notice-before")
        let labels = alert.staticTexts.allElementsBoundByIndex.map(\.label)
        XCTAssertLessThanOrEqual(labels.count, 64)
        XCTAssertTrue(labels.allSatisfy { $0.utf8.count <= 16_384 })
        XCTAssertEqual(labels.filter { Array($0.utf8) == Array(title.utf8) }.count, 1, "Unknown/ambiguous startup title")
        XCTAssertEqual(labels.filter { Array($0.utf8) == Array(body.utf8) }.count, 1, "Unknown/ambiguous startup message")
        let buttons = alert.buttons.allElementsBoundByIndex.map(\.label).sorted()
        XCTAssertEqual(buttons, [allow, later].sorted(), "Unexpected startup actions")
        let cancel = alert.buttons[later]
        XCTAssertTrue(cancel.isHittable, "Known Later action is not hittable")
        stage = "ordinary-known-later-cancellation"
        cancel.tap()
        let absent = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: alert)
        XCTAssertEqual(XCTWaiter.wait(for: [absent], timeout: 5), .completed)
        noUnexpectedAlerts()
        capture("notice-after")
        navigation.append(["action": "known-notification-later", "title": title,
                           "body": body, "actualStaticTextLabels": labels, "buttons": buttons, "tapped": later,
                           "hittableBeforeTap": true, "noAlertsAfterTap": true])
    }

    private func picker() -> XCUIElement {
        let elements = app.descendants(matching: .any).matching(identifier: "settings.language")
        XCTAssertEqual(elements.count, 1, "Missing or ambiguous genuine Settings picker")
        let value = elements.element(boundBy: 0)
        XCTAssertTrue(value.isHittable, "Genuine Settings picker is not hittable")
        return value
    }

    private func selected(_ element: XCUIElement, is nativeName: String) -> Bool {
        if (element.value as? String) == nativeName { return true }
        if element.staticTexts.matching(NSPredicate(format: "label == %@", nativeName)).count == 1 { return true }
        let labelParts = element.label.components(separatedBy: ", ")
        return labelParts.contains(nativeName)
    }

    private func record(_ name: String, language: String, nativeName: String, settingsTitle: String) {
        stage = name
        noUnexpectedAlerts()
        let row = picker()
        let selectedValue = selected(row, is: nativeName)
        let settings = app.tabBars.buttons[settingsTitle]
        let visibleTab = settings.exists && settings.isHittable
        capture(name)
        observations.append([
            "stage": name, "language": language, "selectedNativeName": nativeName,
            "selectedValueVisible": selectedValue, "settingsTabVisible": visibleTab,
            "applicationForeground": app.state == .runningForeground,
            "pickerLabel": row.label, "pickerValue": row.value as? String ?? "",
            "pickerChildLabels": row.staticTexts.allElementsBoundByIndex.map(\.label),
            "settingsTabLabel": settings.label,
            "pickerFrame": ["x": row.frame.origin.x, "y": row.frame.origin.y,
                            "width": row.frame.size.width, "height": row.frame.size.height]
        ])
        XCTAssertTrue(selectedValue, "Selected language is not observable in the real picker row")
        XCTAssertTrue(visibleTab, "Actual Settings tab did not reflect selected catalog language")
        XCTAssertEqual(app.state, .runningForeground)
    }

    private func choose(_ nativeName: String) {
        stage = "open-genuine-language-picker"
        noUnexpectedAlerts()
        picker().tap()
        // Only a uniquely named, real hittable menu/navigation choice is used.
        // No accessibilityActivate, coordinate guesses or direct defaults writes.
        let choices = app.buttons.matching(NSPredicate(format: "label == %@", nativeName))
        XCTAssertTrue(choices.firstMatch.waitForExistence(timeout: 5))
        XCTAssertEqual(choices.count, 1, "Missing or ambiguous genuine language choice")
        XCTAssertTrue(choices.firstMatch.isHittable)
        choices.firstMatch.tap()
        let settled = XCTNSPredicateExpectation(predicate: NSPredicate { [weak self] _, _ in
            guard let self else { return false }
            let rows = self.app.descendants(matching: .any).matching(identifier: "settings.language")
            return rows.count == 1 && self.selected(rows.element(boundBy: 0), is: nativeName)
        }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [settled], timeout: 5), .completed)
    }

    func testUnchangedHostSettingsLanguageRoundTrip() {
        // These exact installed bytes were authenticated before this test.
        // The application receives no test launch arguments or environment.
        app = XCUIApplication(bundleIdentifier: identity["bundleIdentifier"]!)
        stage = "genuine-host-launch"
        launchCount += 1
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 20))
        capture("launch")
        cancelKnownNotificationExplanationIfPresent()
        noUnexpectedAlerts()
        stage = "ordinary-settings-tab-navigation"
        let settings = app.tabBars.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10), "Genuine Settings tab unreachable")
        XCTAssertTrue(settings.isHittable)
        settings.tap()
        record("en-before", language: "en", nativeName: "English", settingsTitle: "Settings")
        choose("עברית")
        record("he-selected", language: "he", nativeName: "עברית", settingsTitle: "הגדרות")
        choose("English")
        record("en-returned", language: "en", nativeName: "English", settingsTitle: "Settings")
        status = "observed"
    }
}
