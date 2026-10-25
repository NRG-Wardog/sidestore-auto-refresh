"""Execute the production install customization adapter with headless prompts."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


def customization_adapter():
    source = RUNTIME.read_text(encoding="utf-8")
    start = source.index("    func resolveBundleIDOverride(initialBundleID: String)")
    end = source.index("    func resolveAppGroupMismatch(", start)
    return source[start:end]


def extension_adapter():
    source = RUNTIME.read_text(encoding="utf-8")
    start = source.index("    func selectAppExtensionsToRemove(")
    end = source.index("    func resolveUnsupportediOSVersion(", start)
    return source[start:end]


def compatibility_adapter():
    source = RUNTIME.read_text(encoding="utf-8")
    start = source.index("    func resolveUnsupportediOSVersion(")
    end = source.index("    func requestBackgroundSuspension()", start)
    return source[start:end]


def extension_policies():
    source = RUNTIME.read_text(encoding="utf-8")
    start = source.index("enum V3PromptSelectionPolicy {")
    end = source.index("// MARK: - Authentication failure classification", start)
    policies = source[start:end]
    source = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
    start = source.index("enum V3ExtensionRemovalPromptPolicy {")
    end = source.index("enum V3RefreshAllPhase:", start)
    return policies + source[start:end]


HARNESS = r'''
import Foundation

final class ALTApplication: Hashable {
    let bundleIdentifier: String
    let appExtensions: Set<ALTApplication>
    init(_ bundleIdentifier: String, extensions: Set<ALTApplication> = []) {
        self.bundleIdentifier = bundleIdentifier
        self.appExtensions = extensions
    }
    static func == (lhs: ALTApplication, rhs: ALTApplication) -> Bool {
        lhs.bundleIdentifier == rhs.bundleIdentifier
    }
    func hash(into hasher: inout Hasher) { hasher.combine(bundleIdentifier) }
}

enum ExtensionRemovalDecision {
    case cancel
    case keepAll(useMainProfile: Bool)
    case removeAll
    case removeSelected(Set<ALTApplication>)
}

// PRODUCTION_POLICIES

@MainActor
final class FixtureHandler {
    var answer: [String: String] = [:]
    var offeredOptions: [[String: String]] = []
    var offeredMessage = ""
    var promptCalls = 0

    func ask(kind: String, title: String, message: String,
             fields: [[String: String]] = [], options: [[String: String]]) async throws -> [String: String] {
        precondition(["bundleIDOverride", "extensions", "unsupportedVersion"].contains(kind))
        offeredOptions = options
        offeredMessage = message
        promptCalls += 1
        return answer
    }

    // PRODUCTION_ADAPTER
}

enum FixtureError: Error { case cancelled }

@main
struct InstallPromptParityHarness {
    @MainActor
    static func main() async throws {
        let handler = FixtureHandler()
        let initialID = "com.example.original"

        // This is the retained upstream UserCustomizationOperation contract:
        // nil means cancellation, not “use the default”.
        func apply() async throws -> (customID: String, appendTeamID: Bool) {
            guard let result = try await handler.resolveBundleIDOverride(initialBundleID: initialID) else {
                throw FixtureError.cancelled
            }
            return result
        }

        handler.answer = ["choice": "default", "customID": "com.example.edited", "appendTeamID": "false"]
        let defaultResult = try await apply()
        precondition(defaultResult.customID == initialID && defaultResult.appendTeamID,
                     "Use Default must continue with the original identifier and upstream team suffix")
        precondition(handler.offeredOptions.contains { $0["id"] == "default" && $0["label"] == "Use Default" })

        handler.answer = ["choice": "custom", "customID": " \ncom.example.custom\t", "appendTeamID": "false"]
        let custom = try await apply()
        precondition(custom.customID == "com.example.custom" && !custom.appendTeamID,
                     "Custom input must match upstream trimming and the selected suffix behavior")

        for empty in ["", " \n\t"] {
            handler.answer = ["choice": "custom", "customID": empty, "appendTeamID": "true"]
            let result = try await apply()
            precondition(result.customID == initialID && result.appendTeamID,
                         "Cleared custom input must fall back to the original identifier")
        }

        for choice in ["cancel", "forged", ""] {
            handler.answer = ["choice": choice, "customID": "com.example.custom"]
            do {
                _ = try await apply()
                fatalError("Cancellation or an unknown choice must never continue installation")
            } catch is CancellationError {} catch { throw error }
        }
        handler.answer = [:]
        do {
            _ = try await apply()
            fatalError("A missing choice must not approve customization")
        } catch is CancellationError {} catch { throw error }

        let existingExtension = ALTApplication("com.example.original.widget")
        let newExtension = ALTApplication("com.example.original.share")
        let target = ALTApplication(initialID, extensions: [existingExtension, newExtension])
        let beforeFreshInstall = handler.promptCalls
        handler.answer = ["choice": "keepAllMainProfile"]
        // Upstream reports empty excess extensions on every fresh install.
        switch try await handler.selectAppExtensionsToRemove(appBundle: target,
            localAppExtensions: [], excessExtensions: []) {
        case .keepAll(let useMainProfile): precondition(useMainProfile)
        default: fatalError("Explicit main-profile selection must retain the extensions")
        }
        precondition(handler.promptCalls == beforeFreshInstall + 1,
                     "Fresh installs must offer customization despite zero excess extensions")
        precondition(Set(handler.offeredOptions.compactMap { $0["id"] }).isSuperset(of:
            ["keepAll", "keepAllMainProfile", "removeAll", "cancel",
             "remove:" + existingExtension.bundleIdentifier, "remove:" + newExtension.bundleIdentifier]))

        handler.answer = ["choice": "keepAll"]
        switch try await handler.selectAppExtensionsToRemove(appBundle: target,
            localAppExtensions: [existingExtension, newExtension], excessExtensions: []) {
        case .keepAll(let useMainProfile): precondition(!useMainProfile)
        default: fatalError("Explicit registration choice must preserve separate profiles")
        }
        precondition(handler.promptCalls == beforeFreshInstall + 2,
                     "An unchanged update must still allow customization")

        handler.answer = ["choice": "selected", "ids": "remove:" + existingExtension.bundleIdentifier]
        switch try await handler.selectAppExtensionsToRemove(appBundle: target,
            localAppExtensions: [existingExtension], excessExtensions: [newExtension]) {
        case .removeSelected(let selection): precondition(selection == [existingExtension])
        default: fatalError("Upstream allows choosing an existing extension, not only a newly added one")
        }

        handler.answer = ["choice": "removeAll"]
        switch try await handler.selectAppExtensionsToRemove(appBundle: target,
            localAppExtensions: [], excessExtensions: []) {
        case .removeAll: break
        default: fatalError("Remove All requires an explicit removal decision")
        }

        let beforeEmptyTarget = handler.promptCalls
        // A stale destructive answer must have no effect when no extensions exist.
        switch try await handler.selectAppExtensionsToRemove(appBundle: ALTApplication(initialID),
            localAppExtensions: [], excessExtensions: []) {
        case .keepAll(let useMainProfile): precondition(!useMainProfile)
        default: fatalError("An empty target must preserve the upstream keep-all/no-op behavior")
        }
        precondition(handler.promptCalls == beforeEmptyTarget)

        for answer in [
            ["choice": "cancel"],
            ["choice": "unknown"],
            ["choice": "selected", "ids": "remove:com.example.forged"],
            ["choice": "selected", "ids": ""],
            ["choice": "selected", "ids": "remove:" + existingExtension.bundleIdentifier +
                ",remove:" + existingExtension.bundleIdentifier]
        ] {
            handler.answer = answer
            do {
                _ = try await handler.selectAppExtensionsToRemove(appBundle: target,
                    localAppExtensions: [], excessExtensions: [])
                fatalError("Cancelled, unknown, or invalid extension choices must fail closed")
            } catch is CancellationError {} catch { throw error }
        }

        handler.answer = ["choice": "proceed"]
        let downloadCompatible = try await handler.resolveUnsupportediOSVersion(
            errorDescription: "Latest version requires a newer iOS release.",
            appName: "Example", compatibleVersion: "1.2")
        precondition(downloadCompatible,
                     "Existing affirmative wire choice must still permit the compatible-version download")
        precondition(handler.offeredOptions.contains { $0["id"] == "proceed" && $0["label"] == "Download Example 1.2" })
        precondition(handler.offeredMessage.contains("last version compatible with this device instead"))
        precondition(!handler.offeredMessage.contains("Proceed anyway"))
        for choice in ["cancel", "unknown", ""] {
            handler.answer = ["choice": choice]
            let download = try await handler.resolveUnsupportediOSVersion(
                errorDescription: "Unsupported", appName: "Example", compatibleVersion: "1.2")
            precondition(!download, "Only the explicit affirmative choice permits the fallback download")
        }

        print("V3_INSTALL_PROMPT_PARITY_PASS")
    }
}
'''


class V3InstallPromptParityTests(unittest.TestCase):
    def test_default_and_custom_values_preserve_upstream_contract(self):
        method = customization_adapter()
        self.assertIn('case "default": return (initialBundleID, true)', method)
        self.assertNotIn('case "default": return nil', method)
        self.assertIn('.trimmingCharacters(in: .whitespacesAndNewlines)', method)
        self.assertIn('default: throw CancellationError()', method)

    def test_extension_adapter_uses_complete_target_and_keeps_choices_distinct(self):
        method = extension_adapter()
        self.assertIn("targetExtensions: appBundle.appExtensions", method)
        self.assertIn("let sorted = appBundle.appExtensions.sorted", method)
        self.assertIn('case .keepAll: return .keepAll(useMainProfile: false)', method)
        self.assertIn('case .keepAllMainProfile: return .keepAll(useMainProfile: true)', method)
        self.assertIn('guard selected.count == bundleIDs.count, !selected.isEmpty', method)

    def test_compatibility_prompt_explains_the_actual_download_choice(self):
        method = compatibility_adapter()
        self.assertIn("Download the last version compatible with this device instead?", method)
        self.assertIn('"label": "Download \\(appName) \\(compatibleVersion)"', method)
        self.assertIn('return answer["choice"] == "proceed"', method)
        self.assertNotIn("Proceed anyway?", method)

    def test_production_install_adapters_execute_upstream_contract(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; production install adapter runs in macOS CI")
        source_text = HARNESS.replace("    // PRODUCTION_ADAPTER",
                                      customization_adapter() + extension_adapter() + compatibility_adapter())
        source_text = source_text.replace("// PRODUCTION_POLICIES", extension_policies())
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "install-prompt-parity"
            source.write_text(source_text, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_INSTALL_PROMPT_PARITY_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
