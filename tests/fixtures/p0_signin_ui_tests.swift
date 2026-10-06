import XCTest

final class P0SignInUITests: XCTestCase {
    @MainActor private lazy var app = XCUIApplication()
    private var failures: [String] = []
    private var measurements: [[String: Any]] = []
    private var screenshots: [String] = []
    private var caseName = ""
    private var largest = false
    private var submitting = false
    private var submissionInvoked = false
    private var priorFailureCleared = false
    private var clipboardMatched = false
    private var safeDiagnosticOnly = false
    private var oneCredentialsPanel = false
    private var cancelInvoked = false
    private var noRedundantPanel = false

    @MainActor private func require(_ condition: Bool, _ message: String, file: StaticString = #filePath, line: UInt = #line) {
        if !condition { failures.append(message) }
        XCTAssertTrue(condition, message, file: file, line: line)
    }
    @MainActor private func wait(_ predicate: @escaping () -> Bool) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in predicate() }, object: nil)
        return XCTWaiter.wait(for: [expectation], timeout: 5) == .completed
    }
    @MainActor private func reveal(_ element: XCUIElement) {
        // Coordinates stay inside the measured 320-point window on iPad too.
        let viewport = app.descendants(matching: .any)["p0-viewport"].firstMatch
        for direction in [true, false] {
            for _ in 0..<8 {
                if element.exists && element.isHittable { return }
                let start = viewport.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: direction ? 0.7 : 0.3))
                let end = viewport.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: direction ? 0.3 : 0.7))
                start.press(forDuration: 0.05, thenDragTo: end)
            }
        }
        require(element.exists && element.isHittable, "Control unreachable after bounded scrolling: \(element.identifier)")
    }
    @MainActor private func record(_ element: XCUIElement, name: String) {
        reveal(element)
        let frame = element.frame
        let viewport = app.descendants(matching: .any)["p0-viewport"].firstMatch.frame
        require(abs(viewport.width - 320) <= 1, "Actual fixture viewport is not 320 points")
        require(!frame.isEmpty && !frame.isInfinite && !frame.isNull, "Invalid control geometry: \(name)")
        require(frame.minX >= viewport.minX - 1 && frame.maxX <= viewport.maxX + 1,
                "Control exceeds measured 320-point viewport: \(name)")
        require(frame.minY >= viewport.minY - 1 && frame.maxY <= viewport.maxY + 1,
                "Control is vertically clipped: \(name)")
        if name == "copy-details" || name == "cancel" {
            require(frame.height >= 43, "Touch target is shorter than 44 points: \(name)")
        }
        measurements.append(["control": name, "label": element.label,
            "bounds": [frame.minX, frame.minY, frame.width, frame.height],
            "viewportBounds": [viewport.minX, viewport.minY, viewport.width, viewport.height],
            "viewportWidth": viewport.width, "largestDynamicType": largest, "hittable": element.isHittable])
    }
    @MainActor private func screenshot(_ suffix: String) {
        let name = "p0-" + caseName + "-" + suffix
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways
        add(attachment); screenshots.append(name)
    }
    @MainActor private func finish() {
        let data: [String: Any] = ["schema": "p0-signin-case-v1", "case": caseName,
            "passed": failures.isEmpty, "failures": failures, "measurements": measurements,
            "screenshots": screenshots, "clipboardExactMatch": clipboardMatched,
            "cancelInvoked": cancelInvoked, "submitting": submitting,
            "submissionInvoked": submissionInvoked, "priorFailureCleared": priorFailureCleared,
            "safeDiagnosticOnly": safeDiagnosticOnly, "oneCredentialsPanel": oneCredentialsPanel,
            "largestDynamicType": largest, "noRedundantStatusPanel": noRedundantPanel,
            "scope": "unsigned-in credentials/no recovery; fixture cancel callback only",
            "accessibility": "Runtime accessibility labels; spoken VoiceOver output is not asserted"]
        let attachment = XCTAttachment(data: try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]),
                                       uniformTypeIdentifier: "public.json")
        attachment.name = "p0-" + caseName + "-report.json"; attachment.lifetime = .keepAlways
        add(attachment)
        app.terminate()
    }
    @MainActor private func credentials(largest: Bool, submitting: Bool) {
        continueAfterFailure = false
        self.largest = largest; self.submitting = submitting
        caseName = (submitting ? "submitting" : "credentials") + (largest ? "-largest" : "-default")
        failures = []; measurements = []; screenshots = []
        clipboardMatched = false; cancelInvoked = false; noRedundantPanel = false
        safeDiagnosticOnly = false; oneCredentialsPanel = false
        submissionInvoked = false; priorFailureCleared = false
        XCUIDevice.shared.orientation = .portrait
        app.launchArguments = largest ? ["--largest"] : []
        app.launch()
        defer { finish() }
        require(app.staticTexts["p0-ready"].waitForExistence(timeout: 10), "Fixture failed to launch")
        let copy = app.buttons["signin.prompt.copy-details"]
        let cancel = app.buttons["signin.prompt.cancel"]
        let username = app.textFields["Apple ID"]
        let password = app.secureTextFields["Password"]
        let expected = app.staticTexts["p0-expected"].value as? String ?? ""
        require(expected.contains("kind=unknown") && expected.contains("stage=authentication") &&
                expected.contains("correlation=00000000-0000-0000-0000-000000000025") &&
                expected.contains("typed_error=unknownAccountFailure"), "Fixture diagnostics lost known safe provenance")
        require(!expected.contains("p0-user@example.invalid") && !expected.contains("p0-synthetic-password"),
                "Production diagnostic formatter included credential values")
        var fields: [String: String] = [:]
        let tokens = expected.split(separator: " ")
        for token in tokens {
            let parts = token.split(separator: "=", maxSplits: 1)
            if parts.count == 2 { fields[String(parts[0])] = String(parts[1]) }
        }
        let allowed = ["kind": "unknown", "stage": "authentication", "code": "failed",
            "correlation": "00000000-0000-0000-0000-000000000025", "underlying": "redacted/0",
            "retryable": "unknown", "source_step": "authenticate", "typed_error": "unknownAccountFailure",
            "server_code": "unknown", "http_status": "unavailable"]
        safeDiagnosticOnly = fields.count == tokens.count && allowed.allSatisfy { fields[$0.key] == $0.value }
        for (key, value) in fields where allowed[key] == nil {
            if key == "diagnostic_code" {
                safeDiagnosticOnly = safeDiagnosticOnly && value.range(of: "^SS-AUTH-C11(-[A-Z][0-9]{2})*-A00$", options: .regularExpression) != nil
            } else if key == "builder_commit" {
                safeDiagnosticOnly = safeDiagnosticOnly && value.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil
            } else { safeDiagnosticOnly = false }
        }
        require(safeDiagnosticOnly, "Clipboard expectation contains data beyond the known safe fixture diagnostic")
        oneCredentialsPanel = app.staticTexts.matching(NSPredicate(format: "label ==[c] %@", "Apple ID Sign In")).count == 1
        require(oneCredentialsPanel, "Expected one credentials panel header")
        noRedundantPanel = !app.staticTexts["Status"].exists && !app.staticTexts["Needs your input"].exists
        require(noRedundantPanel, "Redundant Status/Needs your input top panel is present")
        require(app.buttons.matching(identifier: "signin.prompt.cancel").count == 1,
                "Expected one session-owned Cancel control")
        require(!app.buttons["Cancel"].exists, "Prompt option duplicated session-owned cancellation")
        // Diagnostic body is collapsed at launch; Copy must be its visible sibling.
        require(!app.staticTexts[expected].exists, "Technical details unexpectedly expanded at launch")
        let error = app.staticTexts["signin.prompt.previous-error"]
        require(error.exists && error.label.hasPrefix("Sign-in failed before completion."),
                "Unknown failure guidance is absent from the credentials panel")
        if let diagnostic = fields["diagnostic_code"] {
            require(error.label.contains("Error ID: " + diagnostic), "Visible Error ID differs from Copy Details")
        }
        screenshot("prompt-top")
        record(username, name: "username")
        username.tap(); username.typeText("p0-user@example.invalid")
        record(password, name: "password")
        password.tap(); password.typeText("p0-synthetic-password\n")
        require(wait { self.app.keyboards.count == 0 }, "Native Return did not dismiss the credentials keyboard")
        require(username.value as? String == "p0-user@example.invalid", "Native username input was not retained")
        record(copy, name: "copy-details")
        require(copy.isEnabled, "Copy Details is disabled before credential submission")
        screenshot("copy-details")
        copy.tap()
        clipboardMatched = wait { self.app.staticTexts["p0-clipboard"].value as? String == expected }
        require(clipboardMatched, "Actual Copy Details tap did not write the exact safe fixture diagnostic")
        let copied = app.staticTexts["p0-clipboard"].value as? String ?? ""
        require(!copied.contains("p0-user@example.invalid") && !copied.contains("p0-synthetic-password"),
                "Copy Details leaked synthetic username or password")
        let proceed = app.buttons["Submit"]
        reveal(proceed)
        require(proceed.isEnabled, "Credential submission is unexpectedly disabled")
        if submitting {
            proceed.tap()
            submissionInvoked = wait { self.app.staticTexts["p0-answer-count"].label == "1" && !proceed.isEnabled }
            require(submissionInvoked, "Actual Submit tap did not enter fixture submission exactly once")
            priorFailureCleared = wait { !copy.exists && !error.exists }
            require(priorFailureCleared, "Production failure policy retained stale credentials failure during submission")
        }
        record(cancel, name: "cancel")
        require(cancel.isEnabled, "Cancel disabled while prompt submission is active")
        screenshot("cancel-reachable")
        cancel.tap()
        cancelInvoked = wait { self.app.staticTexts["p0-cancel-count"].label == "1" }
        require(cancelInvoked, "Cancel tap did not invoke the fixture cancellation callback exactly once")
        require(app.staticTexts["p0-cancelled-submission"].label == (submitting ? "yes" : "no"),
                "Cancellation callback lost active submission state")
        require(app.staticTexts["p0-answer-count"].label == (submitting ? "1" : "0"), "Cancel was routed as a credential answer")
        require(wait { !cancel.isEnabled && cancel.label == "Cancelling..." },
                "Production Cancel did not disable/show cancellation progress after the callback")
    }
    @MainActor func testCredentialsDefault() { credentials(largest: false, submitting: false) }
    @MainActor func testCredentialsLargest() { credentials(largest: true, submitting: false) }
    @MainActor func testSubmittingDefault() { credentials(largest: false, submitting: true) }
    @MainActor func testSubmittingLargest() { credentials(largest: true, submitting: true) }
}
