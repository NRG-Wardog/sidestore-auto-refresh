"""Regression coverage for the v3 host-owned navigation shell."""
from pathlib import Path
import importlib.util
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Tuple

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v3_patch", ROOT / "scripts/patch_v3_unified_shell.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


def fixture(root: Path) -> Tuple[Path, Path]:
    live = root / "LiveContainer"
    side = root / "SideStore"
    (live / "LiveContainerSwiftUI/Utilities").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/App").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Views/Settings").mkdir(parents=True)
    (side / "AltStore").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Utilities/Shared.swift").write_text("public enum LCTabIdentifier: Hashable {\n    case sources\n    case apps\n    case tweaks\n    case settings\n}\n\npublic struct SharedModel {\n    @Published var selectedTab: LCTabIdentifier = .apps\n}\n")
    (live / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift").write_text("struct Root {\n            LCTabView()\n}\n")
    (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").write_text('''struct Settings {
    @State private var certificateDataFound = false
    var body: some View {
        NavigationView {
            Form {
                if sharedModel.multiLCStatus != 2 {
                }
            }
            .navigationBarTitle("lc.tabView.settings".loc)
        }
    }
                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
}

    func onSideStoreCertificateCallback(certificateData: Data, password: String) {
        certificateDataFound = true
    }

    func removeCertificate() async {
    }

    func handleURL(url: URL) {
        if url.host == "certificate" {
            return
        }
    }

struct LCTweaksView: View {
    var body: some View { Text("tweaks") }
}
''')
    (live / "LiveContainerSwiftUI/Views/AppList").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").write_text('''struct Apps {
                ToolbarItem(placement: .topBarLeading) {
                    if(UserDefaults.sideStoreExist()) {
                        Button {
                            LCUtils.openSideStore(delegate: self)
                        } label: {
                            IconImageView(icon: BuiltInSideStoreAppInfo.shared.iconIsDarkIcon(darkModeIcon))
                                .frame(width: UIFont.preferredFont(forTextStyle: .body).lineHeight, height: UIFont.preferredFont(forTextStyle: .body).lineHeight)

                        }
                    } else {
                        Button("Help", systemImage: "questionmark") {
                            helpPresent = true
                        }
                    }
                    

                }
                
}
''')
    (side / "AltStore/AppDelegate.swift").write_text('''func boot() {
                debugLog("Started DatabaseManager.")
}
''')
    return live, side


class V3UnifiedShellTests(unittest.TestCase):
    def test_navigation_anchor_drift_fails_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live, side = fixture(root)
            path = live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
            path.write_text(path.read_text().replace("SideStore scheduled refresh", "Changed upstream refresh"))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                patch.patch(live, side)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_shell_is_idempotent_and_host_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            live, side = fixture(Path(directory))
            patch.patch(live, side)
            first = {p.relative_to(Path(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
            patch.patch(live, side)
            self.assertEqual(first, {p.relative_to(Path(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()})
            shell = (live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift").read_text()
            self.assertIn("struct V3UnifiedShell", shell)
            self.assertIn(".tag(LCTabIdentifier.home)", shell)
            self.assertNotIn(".tag(LCTabIdentifier.refresh)", shell)
            for tab in ("home", "apps", "sources", "settings"):
                self.assertIn(f".tag(LCTabIdentifier.{tab})", shell)
            homeSelections = [m.start() for m in re.finditer("sharedModel.selectedTab = .home", shell)]
            self.assertEqual(len(homeSelections), 2)
            for position in homeSelections:
                context = shell[max(0, position - 400):position]
                self.assertTrue('"refresh"' in context or '"Refresh"' in context)
            self.assertIn("status.refreshPresented = true", shell)
            self.assertNotIn("LCUtils.openSideStore", shell)
            self.assertIn("V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs.", (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertNotIn("SideStore scheduled refresh", (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text())
            self.assertIn("Refresh, Schedule and History", (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text())
            status = (side / "AltStore/AppDelegate.swift").read_text()
            self.assertIn("V3_SIDESTORE_STATUS_SNAPSHOT_V1", status)
            self.assertNotIn("appleIDPassword", status)
            self.assertNotIn("appleIDXcodeToken", status)

    def test_launch_tab_startup_preference_is_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            live, side = fixture(Path(directory))
            patch.patch(live, side)
            shared = (live / "LiveContainerSwiftUI/Utilities/Shared.swift").read_text()
            self.assertIn("LCLaunchTab.resolve(LCUtils.appGroupUserDefault.string(forKey: LCLaunchTab.storageKey)) == .apps ? .apps : .home", shared)

    def test_launch_tab_anchor_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live, side = fixture(root)
            path = live / "LiveContainerSwiftUI/Utilities/Shared.swift"
            path.write_text(path.read_text().replace(
                "    @Published var selectedTab: LCTabIdentifier = .apps",
                "    @Published var selectedTab: LCTabIdentifier = .tweaks"))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                patch.patch(live, side)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_files_install_is_separate_from_guest_import(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        integration = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('Button("Install with SideStore", systemImage: "arrow.down.app")', source)
        self.assertIn('UIDocumentPickerViewController(forOpeningContentTypes:', source)
        self.assertIn('func documentPickerWasCancelled', source)
        self.assertIn('status?.stagePickerIPA(url, attemptID: attemptID)', source)
        self.assertIn('status.stageSharedIPA(selected, bookmark: bookmark, title: "Install shared app")', source)
        self.assertIn("V3IPAStaging.stage(sourceURL: url", source)
        self.assertNotIn('"V3SharedIPA."', source)
        self.assertIn("V3InstallPipelineParity.makeOperation(route: route, app)", runtime)
        self.assertIn("AppOperation.install($0)", runtime)
        self.assertIn("installAttempt.staged(attemptID: attemptID", source)
        self.assertIn('"opStart"', source)
        self.assertIn('"kind": request.operation', source)
        self.assertIn('case "opStart"', service)
        self.assertIn('InstallTarget', runtime)
        self.assertIn('AnyApp(name:', runtime)
        self.assertIn('.performSingleOperation(operation, handler: handler, context: context)', runtime)
        self.assertIn('V3HeadlessPipelineHandler', runtime)
        self.assertIn('group.cancel(); group.progress.cancel()', runtime)
        self.assertIn('Button("Add to LiveContainer"', integration)
        self.assertIn('choosingIPA = true', integration)

    def test_native_rendering_scrolls_every_identity_with_fresh_probes(self):
        source = (ROOT / "tests/fixtures/issue25_v3_rendering_harness.swift").read_text(encoding="utf-8")
        self.assertIn("ScrollViewReader { reader in", source)
        self.assertIn("reader.scrollTo(state.scrollID, anchor: .center)", source)
        self.assertIn("state.scrollRequest += 1", source)
        self.assertIn("for id in identities {", source)
        self.assertIn("await observe(id, scroll: scroll)", source)
        self.assertIn("state.epoch += 1", source)
        self.assertIn("state.samples.filter { $0.epoch == epoch }", source)
        self.assertIn("current[id] = cell.content", source)
        self.assertNotIn("state.frames", source)
        self.assertIn("current.count == identities.count && collected.count == identities.count", source)
        self.assertIn("return near(prior, frame)", source)
        self.assertIn("prior.offset == scroll.contentOffset, prior.size == scroll.contentSize", source)
        self.assertIn("full-collection geometry did not converge", source)
        self.assertIn('"scrollVisits": visits', source)
        calls = [line.strip() for line in source.splitlines() if "measure(" in line and "func measure" not in line]
        self.assertEqual(len(calls), 11)
        self.assertTrue(all(line.startswith("await measure(") for line in calls))

    def test_native_rendering_coordinates_are_bounded_and_non_layout_affecting(self):
        source = (ROOT / "tests/fixtures/issue25_v3_rendering_harness.swift").read_text(encoding="utf-8")
        screen = source.split("struct V3RenderingScreen: View {", 1)[1].split("@MainActor final class V3RenderingRunner", 1)[0]
        self.assertIn('V3InstalledAppsSection(query: state.query)\n'
                      '                            .background(FixtureGeometryProbe(id: "content"))\n'
                      '                            .background(FixtureScrollMarker(state: state))', screen)
        self.assertIn('.coordinateSpace(name: "v3-content")', screen)
        self.assertIn('V3HomeServiceHeader(isConnected: true', screen)
        self.assertIn('"reload-status-phone-width-320"', source)
        self.assertIn('"reload-status-tablet-width-1024"', source)
        self.assertIn('}\n                .background(FixtureGeometryProbe(id: "viewport"))\n'
                      '                .coordinateSpace(name: "v3-viewport")', screen)
        for modifier in (".frame(", ".padding(", ".offset(", ".scaleEffect(", ".ignoresSafeArea("):
            self.assertNotIn(modifier, screen)
        self.assertIn("var ancestor = state.scrollMarker?.superview", source)
        self.assertIn("ancestor = view.superview", source)
        self.assertNotIn("host.view.subviews.compactMap", source)
        self.assertIn("scroll.adjustedContentInset", source)
        self.assertIn("probe.intersection(hostClip)", source)
        self.assertIn("near(content.viewport, nativeContent)", source)
        self.assertIn("contains(visible, cell.viewport), contains(content.content, cell.content)", source)
        self.assertIn("near(translated, cell.viewport)", source)
        self.assertIn("scroll.contentSize.width <= scroll.bounds.width + 0.5", source)
        self.assertIn("observation.content.viewport.maxX <= measuredViewport.maxX + 0.5", source)
        self.assertNotIn("is anchored outside", source)

    def test_native_rendering_validates_full_collection_order_and_overlap(self):
        source = (ROOT / "tests/fixtures/issue25_v3_rendering_harness.swift").read_text(encoding="utf-8")
        self.assertIn("value.append(contentsOf: nextValue())", source)
        self.assertIn("Set(cells.map(\\.id)).count == cells.count", source)
        self.assertIn("isSubset(of: Set(identities))", source)
        self.assertIn("abs(anchor.frame.minY - entry.frame.minY) <= 1", source)
        self.assertIn("$0.sorted { $0.frame.minX < $1.frame.minX }", source)
        self.assertIn("check(frames.map(\\.key) == identities", source)
        self.assertIn("collected.map { (key: $0.key, frame: $0.value) }", source)
        self.assertIn("frames[i].frame.intersection(frames[j].frame)", source)
        self.assertIn("empty collection retained native cells", source)
        self.assertIn(".accessibilityExtraExtraExtraLarge", source)
        self.assertIn("status.installedApps.reverse()", source)

    def test_native_geometry_predicates_when_swift_is_available(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; native geometry predicates not executed")
        source = (ROOT / "tests/fixtures/issue25_v3_rendering_harness.swift").read_text(encoding="utf-8")
        helpers = source[source.index("    func valid("):source.index("    func freshSamples(")]
        rect = next(line for line in source.splitlines() if "func rect(" in line)
        program = "import Foundation\nimport CoreGraphics\n" + rect + "\n" + helpers + '''
let viewport = CGRect(x: 0, y: 44, width: 320, height: 730)
let content = CGRect(x: 0, y: 0, width: 320, height: 1600)
let belowFold = CGRect(x: 16, y: 951, width: 288, height: 230)
precondition(contains(content, belowFold))
precondition(!contains(viewport, belowFold))
precondition(contains(viewport, belowFold.offsetBy(dx: 0, dy: -800)))
precondition(!contains(viewport, CGRect(x: 244, y: 100, width: 136, height: 108)))
precondition(!contains(viewport, CGRect(x: -10, y: 100, width: 288, height: 108)))
precondition(!contains(viewport, CGRect(x: 16, y: 750, width: 288, height: 108)))
precondition(!contains(viewport, CGRect(x: 16, y: 0, width: 288, height: 108)))
precondition(!contains(viewport, .zero))
precondition(!valid(CGRect(x: CGFloat.nan, y: 0, width: 10, height: 10)))
precondition(!near(belowFold, belowFold.offsetBy(dx: 0, dy: 2)))
precondition(near(belowFold, belowFold.offsetBy(dx: 0, dy: 0.25)))
let scrollBounds = CGRect(x: 0, y: -62, width: 390, height: 874)
let visibleHeight = scrollBounds.height - 62 - 34
precondition(visibleHeight == 778)
let nativeSection = CGRect(x: 0, y: 0, width: 390, height: 325)
let initialSection = nativeSection.offsetBy(dx: -scrollBounds.minX, dy: -scrollBounds.minY - 62)
precondition(near(initialSection, nativeSection))
let scrolledSection = nativeSection.offsetBy(dx: 0, dy: -200 - 62)
precondition(near(scrolledSection, CGRect(x: 0, y: -262, width: 390, height: 325)))
'''
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "main.swift"
            binary = Path(directory) / "geometry.exe"
            main.write_text(program, encoding="utf-8")
            result = __import__("subprocess").run([compiler, str(main), "-o", str(binary)], text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = __import__("subprocess").run([str(binary)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_harness_parses_when_swift_is_available(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; SwiftUI/UIKit compilation requires Xcode")
        result = __import__("subprocess").run([compiler, "-frontend", "-parse", str(ROOT / "tests/fixtures/issue25_v3_rendering_harness.swift")], text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_template_parses_when_swift_is_available(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        result = __import__("subprocess").run([compiler, "-frontend", "-parse", str(ROOT / "scripts/templates/v3_unified_shell.swift")], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normal_flows_render_no_sidesstore_ui(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for token in ("V3RemoteServiceView", "AppSceneViewController(servicePID",
                      "Self.presenter", "presentingViewController:",
                      "struct CertificatesView", "struct DeveloperServicesView",
                      'status.perform("panel"', 'status.perform("signIn"',
                      'status.perform("addSource"', 'status.perform("removeSource"',
                      'status.perform("importPairing"', 'status.perform("setSetting"'):
            self.assertNotIn(token, source)

    def test_headless_host_surfaces_exist(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for token in ("V3SignInView", "V3AuthStore", "V3CertificatesView",
                      "V3DeveloperServicesView", "V3PairingView", "V3PromptSection",
                      "V3ConnectionView", "V3AnisetteView", "V3SideSignView",
                      "V3CustomizationsView", "V3HealthView", "V3BackupsView",
                      "V3SideJITView", "V3ReleaseTrackHostView", "V3DiagnosticsView",
                      "V3LogsView", "V3ExperimentalView", "V3SettingsStore",
                      "V3OperationSheet", "signInPresented", "V3SignInLink",
                      "needsSignIn", "Begin Sign In", "V3RefreshDetailView",
                      "NRG-Wardog", "Choose how Apple sends", "Verify Code",
                      "Change Verification Method", "Requesting a verification"):
            self.assertIn(token, source)
        for gone in ("Quick Actions",
                     ".sheet(isPresented: $status.refreshPresented"):
            self.assertNotIn(gone, source)
        self.assertIn("NavigationLink(isActive: $status.refreshPresented)", source)
        self.assertIn('Button("Submit")', source)
        for operation in ("authBegin", "authPoll", "authRespond", "authCancel",
                          "opStart", "opPoll", "opAnswer", "opCancel",
                          "certList", "certSetActive", "certDelete", "certPortalList",
                          "certRevoke", "certCreate", "devTeams", "devDevices",
                          "devAppIDs", "devGroups", "devProfiles", "sourcePreview",
                          "sourceAddConfirmed", "sourceRemoveConfirmed",
                          "pairingImportData", "settingsGet", "settingsSet",
                          "anisetteList", "anisetteReset", "anisetteSync",
                          "sidesignGet", "sidesignSet", "sidesignReset",
                          "sidesignImport", "sidesignExport", "logTail",
                          "healthSnapshot", "accountExport", "accountImport"):
            self.assertIn(f'"{operation}"', source)


class V3SetupAssistantTests(unittest.TestCase):
    def test_deep_link_opens_setup_without_mutation(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        index = source.index('case "setup":')
        end = source.index("\n", source.index("status.setupPresented = true", index))
        block = source[index:end]
        self.assertIn("status.setupPresented = true", block)
        for token in ("perform(", ".request(operation", "NotificationCenter.default.post",
                      "stageSharedIPA", "authBegin", "signIn"):
            self.assertNotIn(token, block)

    def test_setup_entry_points(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn(".sheet(isPresented: $status.setupPresented, onDismiss: { routePendingCanonicalJITLessSetup() })", source)
        self.assertIn("V3SetupAssistantView", source)
        self.assertIn("routePendingSetup()", source)
        self.assertIn('"V3PendingSetupAssistant"', source)
        self.assertIn("final class V3SetupStore", source)
        self.assertIn("struct V3SetupAssistantView", source)

    def test_no_second_scheduler_or_auth_stack(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for token in ("BGTask", "requestRefreshNow", "AutoRefreshScheduler.schedule",
                      "SignInOperation(", "AuthManager", "Keychain",
                      "AppManager.shared.install", "AppManager.shared.refresh",
                      "AppManager.shared.update"):
            self.assertNotIn(token, source)
        self.assertEqual(source.count("SecureField"), 3)

    def test_setup_markers_and_diagnostics_privacy(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for marker in ("[V3_SETUP] OPEN", "[V3_SETUP] STATUS", "[V3_SETUP] ACTION",
                       "[V3_SETUP] TEST_REFRESH_START", "[V3_SETUP] TEST_REFRESH_TERMINAL",
                       "[V3_SETUP] FAILURE"):
            self.assertIn(marker, source)
        diagnostics = source[source.index("func buildDiagnostics"):]
        diagnostics = diagnostics[:diagnostics.index("\n    }\n")]
        for secret in ("password", "token", "secret", "private", "credential", "authToken"):
            self.assertNotIn(secret, diagnostics)

    def test_verified_refresh_semantics(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("testRequestID", source)
        self.assertIn("V3RefreshAllAttemptState.record(in: ledger, requestID: requestID)", source)
        self.assertIn('runRecord["state"]', source)
        self.assertIn("allSatisfy", source)
        self.assertIn("Task.checkCancellation", source)
        self.assertIn("Copy Setup Diagnostics", source)

    def test_setup_intent_is_safe_and_gated(self):
        intent = (ROOT / "scripts/templates/v3_setup_intent.swift").read_text(encoding="utf-8")
        self.assertIn("struct V3SetupAssistantIntent: AppIntent", intent)
        self.assertIn("openAppWhenRun", intent)
        self.assertIn('"V3PendingSetupAssistant"', intent)
        self.assertIn("#if canImport(AppIntents)", intent)
        self.assertIn("@available(iOS 16.0, *)", intent)
        code = "\n".join(line for line in intent.splitlines()
                         if not line.strip().startswith("//"))
        for token in ("password", "deviceID", "pairing", "token", "URL(string:"):
            self.assertNotIn(token, code)

    def test_patch_installs_setup_intent(self):
        patch = (ROOT / "scripts/patch_v3_unified_shell.py").read_text(encoding="utf-8")
        self.assertIn("V3SetupAssistantIntent.swift", patch)
        self.assertIn("v3 setup intent is missing", patch)


class V3SetupAcceptanceTests(unittest.TestCase):
    def test_setup_complete_requires_everything(self):
        # V3_SETUP_COMPLETION_POLICY_V1: the assistant no longer owns a private
        # rule. It supplies inputs to the one shared policy that Home also uses.
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        setup = source[source.index("final class V3SetupStore"):source.index("struct V3SetupAssistantView")]
        self.assertIn("func completionInputs(status: V3SideStoreStatusStore) -> V3SetupCompletionInputs {", setup)
        for required in ('accountComplete: account.state == "complete"',
                         'provisioningIncomplete: statusProvisioningIncomplete',
                         'pairingSatisfied: pairing.state == "complete"',
                         'V3JITLessCompletionPolicy.isRequired(',
                         'jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness)',
                         'networkComplete: network.state == "complete"',
                         'tunnelComplete: tunnel.state == "complete"',
                         'backgroundRefreshAvailable: background.state == "complete"',
                         'scheduleEnabled: schedule.state == "complete"',
                         'verifiedRefreshPresent: verification.state == "complete"'):
            self.assertIn(required, setup)
        # Neither surface may restate the platform requirement locally, which is
        # what let Home and the assistant disagree on iOS 26.
        self.assertNotIn("jitlessRequired: ProcessInfo.processInfo", setup)
        # The decision itself is delegated, never re-implemented.
        self.assertIn("func isComplete(status: V3SideStoreStatusStore) -> Bool", setup)
        self.assertNotIn('majorVersion < 26 || jitless.state == "complete"', setup)

    def test_generated_shell_has_no_collapsed_declarations(self):
        # A closing brace immediately followed by a declaration on the same line
        # is a syntax error that no source-text assertion would catch, and it is
        # easy to introduce with a scripted edit. The generated shell is checked
        # structurally, statement by statement.
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        staging = (ROOT / "scripts/templates/v3_ipa_staging.swift").read_text(encoding="utf-8")
        template = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        generated = primitives.splitlines() + [""] + staging.splitlines() + [""] + template.splitlines()
        pattern = re.compile(r"\}\s+(?:@Published|@State|@Environment|@FocusState|@AppStorage|"
                             r"@EnvironmentObject|private |public |internal |static |func |var |let |"
                             r"struct |enum |init)")
        offenders = [(index, line) for index, line in enumerate(generated, 1) if pattern.search(line)]
        self.assertEqual(offenders, [],
                         "a declaration was collapsed onto a closing brace: "
                         + "\n".join(f"{index}: {line}" for index, line in offenders))

    def test_generated_shell_balances_braces(self):
        # Every template is concatenated into a single generated Swift file, so a
        # single unbalanced brace anywhere makes the whole product unparseable.
        # String literals are stripped BEFORE comments, because a "//" inside a
        # literal (a URL, for example) would otherwise be read as a comment and
        # silently remove the rest of the line.
        for path in sorted((ROOT / "scripts/templates").glob("*.swift")):
            text = path.read_text(encoding="utf-8")
            body = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)
            body = re.sub(r"//[^\n]*", "", body)
            self.assertEqual(body.count("{"), body.count("}"),
                             f"{path.name} has unbalanced braces, so the generated file cannot parse")

    def test_history_never_satisfies_current_test(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertEqual(source.count('detail: "Refresh verified"'), 1)
        check = source[source.index("private func checkTestResult"):
                      source.index("private func checkTestResult") + 3000]
        self.assertIn('detail: "Refresh verified"', check)
        self.assertIn("testRequestID", check)
        self.assertIn("V3RefreshAllAttemptState.record(in: ledger, requestID: requestID)", check)
        self.assertIn('runState == "completed" || runState == "failed"', check)
        self.assertIn("hasCompleteTerminalResults", check)

    def test_partial_manifest_does_not_verify(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/combined_failure.swift").read_text() + "\n"
                               + 'let runID = UUID().uuidString\nlet partial: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2",\n    "run_id": runID, "expected_ids": ["A", "B"],\n    "results": [["bundle_id": "A", "success": true]]]\nprecondition(!CombinedVerification.hasCompleteTerminalResults(partial, runID: runID))\nlet short: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2",\n    "run_id": runID, "expected_ids": ["A"],\n    "results": [["bundle_id": "A", "success": true]]]\nprecondition(CombinedVerification.hasCompleteTerminalResults(short, runID: runID))\nlet mismatch: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2",\n    "run_id": UUID().uuidString, "expected_ids": ["A"],\n    "results": [["bundle_id": "A", "success": true]]]\nprecondition(!CombinedVerification.hasCompleteTerminalResults(mismatch, runID: runID))\nprint("V3 manifest coverage PASS")')
            executable = directory / "manifest-coverage-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 manifest coverage PASS", result.stdout)

    def test_state_refreshes_after_child_flows_return(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("destination.onDisappear", source)
        self.assertIn("await setup.recalculate(status: status)", source)
        self.assertIn("V3PairingView().environmentObject(status)", source)
        self.assertIn("V3SignInView().environmentObject(status)", source)
        self.assertIn("V3RefreshDetailView()", source)

    def test_home_banner_reflects_full_setup(self):
        # V3_SETUP_COMPLETION_POLICY_V1: Home and the assistant now consume the
        # same policy, so the banner can no longer stop while the assistant
        # still considers setup incomplete.
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        home = source[source.index("struct V3HomeServiceHeader"):]
        self.assertIn("completionInputs(status: status, defaults: defaults).isComplete", home)
        for required in ("accountComplete: !status.needsSignIn",
                         "provisioningIncomplete: status.provisioningIncomplete",
                         "pairingSatisfied: !V3RefreshPrerequisite.evaluate",
                         "networkComplete: status.wifiAvailable == true",
                         "tunnelComplete: LiveContainerNetworkPreflight.hasTunnelInterface()",
                         "backgroundRefreshAvailable: UIApplication.shared.backgroundRefreshStatus == .available",
                         "scheduleEnabled: defaults?.bool(forKey: \"liveContainerAutoRefreshEnabled\")",
                         "verifiedRefreshPresent: verifiedRunID?.isEmpty == false"):
            self.assertIn(required, home)
        # The old private rule must be gone.
        self.assertNotIn('if UIApplication.shared.backgroundRefreshStatus != .available { return true }', home)

    def test_tunnel_presence_never_proves_coredevice(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("not a CoreDevice proof", source)
        coredevice = source[source.index("private func coredeviceState"):]
        coredevice = coredevice[:coredevice.index("\n    }\n")]
        self.assertIn('verification.state == "complete"', coredevice)

    def test_generic_errors_keep_structure(self):
        # V3_FAILURE_GUIDANCE_V1: this test previously asserted the opposite of
        # the policy. It required the row caption to carry failure.technicalDetails
        # and the bridged NSError domain and code, which is a diagnostics line
        # rendered as product copy. It also pinned an `else if let native = error
        # as NSError?` arm that made the final `else` unreachable, because every
        # Swift Error bridges to NSError?.
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = source.index("func recordError")
        record = source[start:source.index("\n    }\n", start)]
        record = "\n".join(line for line in record.splitlines()
                           if not line.strip().startswith("//"))
        # A typed failure keeps its own product copy and its structured fields.
        self.assertIn("failure.safeMessage", record)
        self.assertIn("failure.recovery", record)
        self.assertIn("recordFailure(operation: operation, stage: failure.stage.rawValue", record)
        # An untyped failure invents nothing and shows no numeric code.
        self.assertNotIn("native.domain", record)
        self.assertNotIn("native.code", record)
        self.assertNotIn("localizedDescription", record)
        self.assertIn("cause is not known", record)
        self.assertIn("V3FailureGuidance.message(error)", record)
        # The unreachable arm is gone: there is one typed branch and one else.
        self.assertNotIn("as NSError?", record)
        self.assertEqual(record.count("recordFailure(operation: operation"), 2)

    def test_refresh_warning_copy_is_readable_and_diagnostics_stay_in_logs(self):
        # V3_FAILURE_GUIDANCE_V1: lastErrorKey is rendered as red product copy on
        # Home under a "Last Refresh Warning" heading. It held a bridged
        # localizedDescription, which for an NSError is a numeric domain and
        # code, and opaque snake_case tokens.
        scheduler = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        self.assertNotIn("defaults.set(error.localizedDescription, forKey: lastErrorKey)", scheduler)
        for message in ("hostRelaunchUnverifiedMessage", "hostBaselineUnavailableMessage",
                        "hostExpirationNotAdvancedMessage", "schedulerConfigurationMessage",
                        "backgroundSubmitFailedMessage"):
            self.assertIn(f"static let {message} =", scheduler)
        # No opaque token may be stored where the user reads it.
        for token in ('"installed_host_baseline_unavailable"',
                      '"installed_host_expiration_not_advanced"'):
            self.assertNotIn(f"defaults.set({token}, forKey: lastErrorKey)", scheduler)
        # The raw text is still recorded for a support reader.
        self.assertIn("detail: error.localizedDescription", scheduler)
        alarm = (ROOT / "scripts/templates/livecontainer_refresh_alarm.swift").read_text(encoding="utf-8")
        self.assertNotIn('set(error.localizedDescription, forKey: "liveContainerAutoRefreshDeadlineWarningError")', alarm)

    def test_untyped_guidance_does_not_claim_a_side_effect(self):
        # Nothing supports "nothing was changed" for an untyped failure: it can
        # arrive after the service applied the request, and the same helper runs
        # after settings writes, source confirmation, pairing import and staging.
        primitives_text = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        start = primitives_text.index("enum V3FailureGuidance")
        guidance = primitives_text[start:primitives_text.index("\n}", start)]
        # Comments are stripped so the prose describing the old wording is never
        # read as the wording itself.
        code = "\n".join(line for line in guidance.splitlines()
                         if not line.strip().startswith("//"))
        self.assertNotIn("nothing was changed", code)
        self.assertIn("whether it took effect is not known", code)
        # A typed failure still keeps its own recovery copy.
        self.assertIn("return combined.recovery", code)

    def test_manifest_diagnostics_are_not_used_as_a_row_caption(self):
        # The manifest's "error" field is a newline-joined block of message,
        # recovery and technical details. It is a diagnostic record, and it was
        # being rendered verbatim as the Test Refresh row caption.
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = source.index('var detail = "Refresh reported failures"')
        block = source[start:source.index("\n        }", start)]
        self.assertIn("CombinedFailure.decode", block)
        self.assertIn("detail = decoded.safeMessage", block)
        self.assertIn("verificationGuidance = decoded.recovery", block)
        # The full text is no longer the caption; only its first line may be, and
        # only when the manifest carries no structured failure.
        self.assertIn("message.split(separator: \"\\n\").first", block)
        self.assertNotIn("detail = message", block)


class V3RefreshFeedbackTests(unittest.TestCase):
    def test_targeted_section_survives_missing_store(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = source.index("struct V3TargetedRefreshSection")
        block = source[start:start + 1200]
        self.assertIn("V3StatusStoreKey", source)
        self.assertIn("@Environment(\\.v3StatusStore)", block)
        self.assertIn("if let status", block)

    def test_first_launch_notification_prompt(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn('"V3NotificationsPromptShown"', source)
        self.assertIn("Stay Informed About Refreshes", source)
        self.assertIn("requestNotificationPermission", source)
        self.assertIn("Allow Refresh Notifications", source)


if __name__ == "__main__":
    unittest.main()
