import XCTest

enum P0SignInViewport {
    static func available(viewport: CGRect, navigation: CGRect?, keyboard: CGRect?, footer: CGRect?) -> CGRect {
        guard !viewport.isEmpty, !viewport.isNull, !viewport.isInfinite else { return .zero }
        var top = viewport.minY
        var bottom = viewport.maxY
        if let navigation, navigation.intersects(viewport) { top = max(top, navigation.maxY) }
        if let keyboard, keyboard.intersects(viewport) { bottom = min(bottom, keyboard.minY) }
        if let footer, footer.intersects(viewport) { bottom = min(bottom, footer.minY) }
        return CGRect(x: viewport.minX + 4, y: top + 4,
                      width: max(0, viewport.width - 8), height: max(0, bottom - top - 8))
    }
    static func contains(_ frame: CGRect, in region: CGRect) -> Bool {
        !region.isEmpty && !region.isInfinite && !region.isNull &&
        !frame.isEmpty && !frame.isInfinite && !frame.isNull &&
        frame.minX >= region.minX - 1 && frame.maxX <= region.maxX + 1 &&
        frame.minY >= region.minY - 1 && frame.maxY <= region.maxY + 1
    }
}

final class P0SignInUITests: XCTestCase {
    @MainActor private lazy var app = XCUIApplication()
    private var failures: [String] = []
    private var measurements: [[String: Any]] = []
    private var screenshots: [String] = []
    private var diagnosticScreenshots: [String] = []
    private var finished = false
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
    @MainActor private func visibleScrollRegion() -> CGRect {
        let viewport = app.descendants(matching: .any)["p0-viewport"].firstMatch.frame
        let keyboard = app.keyboards.firstMatch
        let navigation = app.navigationBars.firstMatch
        let footer = app.descendants(matching: .any)["p0-telemetry"].firstMatch
        let available = P0SignInViewport.available(viewport: viewport,
            navigation: navigation.exists ? navigation.frame : nil,
            keyboard: keyboard.exists ? keyboard.frame : nil,
            footer: footer.exists ? footer.frame : nil)
        let candidates = app.collectionViews.allElementsBoundByIndex +
            app.tables.allElementsBoundByIndex + app.scrollViews.allElementsBoundByIndex
        let intersections = candidates.map { $0.frame.intersection(available) }
            .filter { !$0.isNull && !$0.isEmpty }
        return intersections.max(by: { $0.width * $0.height < $1.width * $1.height }) ?? available
    }
    @MainActor private func reveal(_ element: XCUIElement, name: String = "requested control", towardTop: Bool = false) {
        for attempt in 0..<16 {
            let region = visibleScrollRegion()
            require(region.width >= 44 && region.height >= 80,
                    "No safe list area remains outside keyboard/navigation for scrolling")
            if element.exists && element.isHittable &&
                P0SignInViewport.contains(element.frame, in: region) { return }
            let upward: Bool
            if element.exists && !element.frame.isEmpty {
                upward = element.frame.maxY > region.maxY
            } else {
                // Offscreen lazy rows have no usable frame. Start toward the
                // known control order, then search the other direction too.
                upward = attempt < 8 ? !towardTop : towardTop
            }
            let window = app.windows.firstMatch
            let origin = window.coordinate(withNormalizedOffset: .zero)
            let startY = upward ? region.maxY - region.height * 0.2 : region.minY + region.height * 0.2
            let endY = upward ? region.minY + region.height * 0.2 : region.maxY - region.height * 0.2
            let start = origin.withOffset(CGVector(dx: region.midX - window.frame.minX, dy: startY - window.frame.minY))
            let end = origin.withOffset(CGVector(dx: region.midX - window.frame.minX, dy: endY - window.frame.minY))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        require(element.exists && element.isHittable &&
                P0SignInViewport.contains(element.frame, in: visibleScrollRegion()),
                "Control is not fully visible after bounded safe scrolling: \(name)")
    }
    @MainActor private func record(_ element: XCUIElement, name: String) {
        reveal(element, name: name, towardTop: name == "copy-details")
        let frame = element.frame
        let viewport = app.descendants(matching: .any)["p0-viewport"].firstMatch.frame
        require(abs(viewport.width - 320) <= 1, "Actual fixture viewport is not 320 points")
        require(!frame.isEmpty && !frame.isInfinite && !frame.isNull, "Invalid control geometry: \(name)")
        require(frame.minX >= viewport.minX - 1 && frame.maxX <= viewport.maxX + 1,
                "Control exceeds measured 320-point viewport: \(name)")
        require(frame.minY >= viewport.minY - 1 && frame.maxY <= viewport.maxY + 1,
                "Control is vertically clipped: \(name)")
        require(P0SignInViewport.contains(frame, in: visibleScrollRegion()),
                "Control is covered by navigation, keyboard or fixture telemetry: \(name)")
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
    @MainActor private func diagnosticSnapshot(_ suffix: String) {
        let name = "p0-" + caseName + "-diagnostic-" + suffix
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways
        add(attachment); diagnosticScreenshots.append(name)
    }
    @MainActor private func finish() {
        guard !finished else { return }
        finished = true
        diagnosticSnapshot("teardown")
        if app.state == .runningForeground {
            let hierarchy = XCTAttachment(string: String(app.debugDescription.prefix(131072)))
            hierarchy.name = "p0-" + caseName + "-hierarchy.txt"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
        }
        let frameworkFailures = testRun?.totalFailureCount ?? 1
        if frameworkFailures > 0 { failures.append("XCTest recorded \(frameworkFailures) assertion failures or exceptions.") }
        let data: [String: Any] = ["schema": "p0-signin-case-v1", "case": caseName,
            "passed": failures.isEmpty && frameworkFailures == 0, "failures": failures,
            "xctestFailureCount": frameworkFailures, "teardownCaptured": true,
            "diagnosticScreenshots": diagnosticScreenshots, "measurements": measurements,
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
        failures = []; measurements = []; screenshots = []; diagnosticScreenshots = []; finished = false
        clipboardMatched = false; cancelInvoked = false; noRedundantPanel = false
        safeDiagnosticOnly = false; oneCredentialsPanel = false
        submissionInvoked = false; priorFailureCleared = false
        XCUIDevice.shared.orientation = .portrait
        app.launchArguments = largest ? ["--largest"] : []
        // XCTest invokes teardown after assertion failure/exception; Swift
        // defer is not reliable when continueAfterFailure=false aborts a test.
        addTeardownBlock { @MainActor [self] in
            await Task.yield()
            finish()
        }
        app.launch()
        diagnosticSnapshot("launch")
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
        safeDiagnosticOnly = fields.count == 12 && fields.count == tokens.count &&
            allowed.allSatisfy { fields[$0.key] == $0.value } &&
            fields["diagnostic_code"] == "SS-AUTH-C11-S01-T31-A00" &&
            fields["builder_commit"]?.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil &&
            fields["builder_commit"]?.utf8.count == 40
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
        // Diagnostic body is collapsed at launch; Copy must be its visible sibling.
        require(!app.staticTexts["signin.prompt.previous-error-body"].exists,
                "Technical details unexpectedly expanded at launch")
        let error = app.staticTexts["signin.prompt.previous-error"]
        require(error.exists && error.label.hasPrefix("Sign-in failed before completion."),
                "Unknown failure guidance is absent from the credentials panel")
        require(error.label == "Sign-in failed before completion. Copy Details to help identify the cause.\nError ID: SS-AUTH-C11-S01-T31-A00",
                "Visible unknown-failure guidance or canonical ID differs from Copy Details")
        screenshot("prompt-top")
        record(username, name: "username")
        username.tap(); username.typeText("p0-user@example.invalid")
        require(username.value as? String == "p0-user@example.invalid", "Native username input was not retained")
        username.typeText("\n")
        require(wait { self.app.keyboards.count == 0 }, "Native Return did not dismiss the username keyboard")
        record(password, name: "password")
        password.tap(); password.typeText("p0-synthetic-password\n")
        require(wait { self.app.keyboards.count == 0 }, "Native Return did not dismiss the credentials keyboard")
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
        reveal(proceed, name: "Submit")
        require(proceed.isEnabled, "Credential submission is unexpectedly disabled")
        if submitting {
            proceed.tap()
            submissionInvoked = wait { self.app.staticTexts["p0-answer-count"].label == "1" && proceed.exists && !proceed.isEnabled }
            require(submissionInvoked, "Actual Submit tap did not enter fixture submission exactly once")
            priorFailureCleared = wait { self.app.staticTexts["p0-prior-failure"].value as? String == "cleared" }
            require(priorFailureCleared, "Production failure policy retained stale credentials failure during submission")
            let header = app.staticTexts.matching(NSPredicate(format: "label ==[c] %@", "Apple ID Sign In")).firstMatch
            reveal(header, name: "credentials header", towardTop: true)
            require(!copy.exists && !error.exists,
                    "Stale error or Copy Details remains after returning to the previous-error area")
        }
        record(cancel, name: "cancel")
        let cancelCount = app.buttons.matching(identifier: "signin.prompt.cancel").count
        require(cancelCount == 1, "Expected one revealed session-owned Cancel control, found \(cancelCount)")
        require(!app.buttons["Cancel"].exists, "Prompt option duplicated session-owned cancellation")
        require(cancel.isEnabled, "Cancel disabled while prompt submission is active")
        screenshot("cancel-reachable")
        cancel.tap()
        cancelInvoked = wait { self.app.staticTexts["p0-cancel-count"].label == "1" }
        require(cancelInvoked, "Cancel tap did not invoke the fixture cancellation callback exactly once")
        require(app.staticTexts["p0-cancelled-submission"].label == (submitting ? "yes" : "no"),
                "Cancellation callback lost active submission state")
        require(app.staticTexts["p0-answer-count"].label == (submitting ? "1" : "0"), "Cancel was routed as a credential answer")
        require(wait { cancel.exists && !cancel.isEnabled && cancel.label == "Cancelling..." },
                "Production Cancel did not disable/show cancellation progress after the callback")
    }
    @MainActor func testCredentialsDefault() { credentials(largest: false, submitting: false) }
    @MainActor func testCredentialsLargest() { credentials(largest: true, submitting: false) }
    @MainActor func testSubmittingDefault() { credentials(largest: false, submitting: true) }
    @MainActor func testSubmittingLargest() { credentials(largest: true, submitting: true) }
}
