"""Exercise the combined adapter on disposable copies of the real pinned source."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from patch_combined_transport import POLICY, patch
from patch_sidestore_integration import patch_gateway


def source_root():
    override = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
    candidates = [Path(override)] if override else [
        ROOT / ".audit/upstream/SideStore", ROOT / "work/EmbeddedSideStore",
        ROOT.parent / "work/EmbeddedSideStore",
    ]
    for candidate in candidates:
        if (candidate / "Dependencies/minimuxer/DeviceGateway/BaseDeviceGateway.swift").is_file():
            return candidate
    message = "Pinned modern SideStore source unavailable; set EMBEDDED_SIDESTORE_TEST_SOURCE"
    if override:
        raise AssertionError(message + f" (invalid path: {override})")
    raise unittest.SkipTest(message)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def function(text, name):
    """Select a top-level gateway method, retaining nested closure bodies."""
    match = re.search(r"^    (?:public |private |override |static )*func "
                      + re.escape(name) + r"(?:\(|<)", text, re.M)
    if not match:
        raise AssertionError(f"Missing Swift function {name}")
    end = re.search(r"^    }", text[match.start():], re.M)
    if not end:
        raise AssertionError(f"Missing closing brace for {name}")
    return text[match.start():match.start() + end.end()]


class SourceFixture(unittest.TestCase):
    def setUp(self):
        source = source_root()
        self.temp = tempfile.TemporaryDirectory(prefix="combined-transport-")
        self.addCleanup(self.temp.cleanup)
        self.side = Path(self.temp.name) / "SideStore"
        # Prune .git and binaries rather than copying a recursive submodule checkout.
        for directory, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in {".git", ".build", "build"}]
            for name in files:
                if name.endswith(".swift") or name in {"Package.swift", "Package.resolved"}:
                    original = Path(directory) / name
                    target = self.side / original.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
        self.mux = self.side / "Dependencies/minimuxer"
        self.gateway = self.mux / "DeviceGateway/idevice/IdeviceGateway.swift"
        self.send = self.side / "SideStore/Core/Operations/PipelineOperations/SendAppOperation.swift"
        self.assertTrue(self.send.is_file(), "Real SendAppOperation.swift must be included in fixture")
        self.original = self.gateway.read_text(encoding="utf-8")
        self.assertNotIn("TRANSPORT_CREATE_START", self.original,
                         "Fixture must be clean upstream, not an already patched checkout")

    def read(self, relative):
        return (self.mux / relative).read_text(encoding="utf-8")

    def compile_run(self, source):
        compiler = shutil.which("swiftc")
        if compiler is None:
            self.skipTest("swiftc unavailable on PATH; executable Swift behavior not verified")
        main = Path(self.temp.name) / "main.swift"
        main.write_text(source, encoding="utf-8")
        executable = main.with_name("transport-test.exe" if os.name == "nt" else "transport-test")
        result = subprocess.run([compiler, str(main), "-o", str(executable)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CombinedTransportTests(SourceFixture):
    def test_modern_gateway_double_application(self):
        patch_gateway(self.mux)
        first = snapshot(self.side)
        patch_gateway(self.mux)
        self.assertEqual(first, snapshot(self.side))
        self.assertNotEqual(self.original, self.gateway.read_text(encoding="utf-8"))

    def test_combined_double_application(self):
        before = snapshot(self.side)
        patch(self.mux)
        first = snapshot(self.side)
        patch(self.mux)
        self.assertEqual(first, snapshot(self.side))
        self.assertNotEqual(before, first)
        self.assertEqual(self.read("Sources/RefreshTransportPolicy.swift"), POLICY)
        self.assertIn("public import MinimuxerCommon\n", POLICY)
        self.assertFalse((self.mux / "Common/RefreshTransportPolicy.swift").exists())
        self.assertFalse(any(p.name == ".git" for p in self.side.rglob("*")))

    def test_app_folder_staging_and_install_preserved(self):
        patch(self.mux)
        text = self.gateway.read_text(encoding="utf-8")
        for name in ("syncsendAppBundleAfc", "syncInstallAppBundle"):
            self.assertIn("func " + name, self.original)
            self.assertIn("try self." + name, text)
        stage = function(text, "syncsendAppBundleAfc")
        for token in ("fileManager.enumerator(", "for case let fileURL as URL in enumerator",
                      "appURL.lastPathComponent", "remoteItemPath", "afc_make_directory",
                      "writeVerifiedBundleFile", "format=app"):
            self.assertIn(token, stage)
        install = function(text, "syncInstallAppBundle")
        for token in ("appName", "MinimuxerConstants.pkgPath", '"PackageType"', '"Developer"', "installation_proxy_install",
                      "verifyInstalledBundle", "SIDESTORE_INSTALL_COMPLETE"):
            self.assertIn(token, install)
        self.assertNotIn("app.ipa", install)
        writer = function(text, "writeVerifiedBundleFile")
        for token in ("afc_file_write", "afc_file_close", "STAGED_FILE_SIZE_MATCH"):
            self.assertIn(token, writer)

    def test_signed_bundle_identity_mapping_and_exact_lookup(self):
        patch(self.mux)
        text = self.gateway.read_text(encoding="utf-8")
        stage = function(text, "syncsendAppBundleAfc")
        invalidate = "stagedBundleIdentities.removeValue(forKey: bundleId)"
        self.assertLess(stage.index(invalidate), stage.index('appURL.appendingPathComponent("Info.plist")'))
        self.assertIn("PropertyListSerialization.propertyList(from: infoData", stage)
        self.assertIn('let signedIdentifier = info["CFBundleIdentifier"] as? String', stage)
        self.assertIn("!signedIdentifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty", stage)
        mapping = "stagedBundleIdentities[bundleId] = (appName: appURL.lastPathComponent, signedIdentifier: signedIdentifier)"
        self.assertIn(mapping, stage)
        self.assertLess(stage.index("try writeVerifiedBundleFile("), stage.index(mapping))
        self.assertLess(stage.index(mapping), stage.index("SIDESTORE_STAGE_PASS"))
        install = function(text, "syncInstallAppBundle")
        self.assertIn("guard let stagedIdentity = stagedBundleIdentities[bundleId], stagedIdentity.appName == appName else {", install)
        self.assertLess(install.index("guard let stagedIdentity"), install.index("installation_proxy_install("))
        self.assertIn("try verifyInstalledBundle(client: client, bundleId: stagedIdentity.signedIdentifier)", install)
        self.assertNotIn("verifyInstalledBundle(client: client, bundleId: bundleId)", install)
        self.assertLess(install.index("installation_proxy_install succeeded"), install.index(invalidate))
        verify = function(text, "verifyInstalledBundle")
        self.assertIn('plist_dict_get_item(application, "CFBundleIdentifier")', verify)
        self.assertIn("if identifier == bundleId {", verify)
        self.assertIn("allowLegacyPrefix: Bool = false", verify)
        self.assertIn('else if allowLegacyPrefix && identifier.hasPrefix("\\(bundleId).") {', verify)
        self.assertEqual(verify.count("identifier.hasPrefix"), 1)
        self.assertNotIn("allowLegacyPrefix: true", install)
        self.assertNotIn("identifier.hasSuffix", verify)
        self.assertIn(invalidate, function(text, "syncsendIpaAfc"))

    def test_dispatch_and_transport_routing(self):
        patch(self.mux)
        text = self.gateway.read_text(encoding="utf-8")
        calls = re.findall(r"withFFIDispatch\s*(\([^\n]*?\))?\s*\{", text)
        self.assertIn("public import MinimuxerCommon\n", text)
        self.assertNotIn("internal import MinimuxerCommon", text)
        self.assertGreaterEqual(len(calls), 10)
        self.assertEqual(len(calls), self.original.count("withFFIDispatch {"))
        self.assertTrue(all(call == "(on: self.ffiQueue)" for call in calls))
        self.assertEqual(text.count("defer { if self.batchCount == 0 { self.releaseTransport() } }"), len(calls))
        for name in ("setDeviceEndpointIp", "setPort", "invalidateConnection"):
            self.assertIn("onFFIQueue", function(text, name))
        self.assertIn("DispatchQueue.getSpecific", function(text, "onFFIQueue"))
        self.assertIn("ffiQueue.sync", function(text, "onFFIQueue"))
        self.assertIn("coreDeviceEnabled && pairingFileType == .lockdown", text)
        self.assertIn("if usesCoreDevice {", function(text, "ensureRPConnection"))
        for name in ("performWithEitherService", "syncFetchUDID", "syncMountPersonalizedDdi"):
            self.assertIn("pairingFileType == .rppairing || usesCoreDevice", function(text, name))
        self.assertNotIn("isRPPairing", text)
        self.assertIn("connectLockdown", function(text, "performWithEitherService"))

    def test_coredevice_failure_and_readiness_require_real_tunnel(self):
        patch(self.mux)
        text = self.gateway.read_text(encoding="utf-8")
        connection = function(text, "ensureCoreDeviceConnection")
        self.assertIn("tunnel_create_usb(provider, &adapter, &handshake)", connection)
        self.assertRegex(connection, r'\} catch \{\s*releaseTransport\(\)\s*'
                         r'debugLog\("\[SIDESTORE_COREDEVICE\] selected_transport=FAILED_NO_VALID_TRANSPORT '
                         r'reason=[^\n]+\)\s*throw error\s*\}\s*\}$')
        self.assertNotIn("performWithTcpService", connection)
        route = function(text, "ensureRPConnection")
        self.assertRegex(route, r"if usesCoreDevice \{\s*try ensureCoreDeviceConnection\(\)\s*return\s*\}")
        fetch = function(text, "syncFetchUDID")
        self.assertRegex(fetch, r"if pairingFileType == .rppairing \|\| usesCoreDevice \{\s*do \{")
        self.assertLess(fetch.index("try ensureRPConnection()"), fetch.index("lockdownd_connect_rsd("))
        self.assertIn("try self.syncFetchUDID()", function(text, "fetchUDID"))
        self.assertIn("try ensureRPConnection()", function(text, "performWithService"))
        readiness = function(self.read("Sources/MinimuxerImpl.swift"), "isReady")
        self.assertIn("if !gateway.hasActiveTransportBatch { return .success(false) }", readiness)
        # Both DDI-enabled and DDI-disabled readiness must first fetch through the tunnel.
        fetch_call = readiness.index("try await fetchUDID()")
        valid_udid = readiness.index("guard deviceUDID != nil else {")
        ddi_branch = readiness.index("if withDDIMountCheck {")
        self.assertLess(fetch_call, valid_udid)
        self.assertLess(valid_udid, ddi_branch)
        self.assertLess(ddi_branch, readiness.index("return .success(true)"))
        self.assertEqual(readiness.count("return .success(true)"), 1)

    def test_readiness_and_batch_lease(self):
        patch(self.mux)
        impl = self.read("Sources/MinimuxerImpl.swift")
        self.assertIn("if #available(iOS 26.4, *)", impl)
        self.assertIn("gateway.supportsCoreDeviceTransport", impl)
        self.assertIn("if !gateway.hasActiveTransportBatch { return .success(false) }", impl)
        self.assertNotIn("no ipsec interface (required for lockdown", impl.lower())
        runner = (self.side / "SideStore/Core/Operations/PipelineRunner.swift").read_text(encoding="utf-8")
        begin = runner.index("await transportCore.beginTransportBatch()")
        task = runner.index("try await Task.detached {", begin)
        readiness = runner.index("/* Minimuxer Readiness Check */", task)
        end = runner.index("}.value", readiness)
        self.assertLess(begin, task)
        self.assertLess(task, readiness)
        normal = runner.index("for operation in normalOperations {", readiness)
        drained = runner.index("while let _ = try await taskGroup.next() {}", normal)
        host = runner.index("for operation in hostOperations {", drained)
        self.assertLess(readiness, normal)
        self.assertLess(normal, drained)
        self.assertLess(drained, host)
        self.assertLess(host, end)
        self.assertRegex(runner[drained:host],
                         r"^while let _ = try await taskGroup.next\(\) \{\}\s*\}\s*$")
        self.assertIn("taskGroup.addTask {", runner[normal:drained])
        self.assertIn("try await self.performOperation(for: operation, handler: handler, group: group)",
                      runner[normal:drained])
        self.assertRegex(runner[host:end],
                         r"for operation in hostOperations \{\s*try Task.checkCancellation\(\)\s*"
                         r"try await self.performOperation\(for: operation, handler: handler, group: group\)\s*\}")
        self.assertNotIn("taskGroup.addTask", runner[host:end])
        execution = runner.index("let hostOperations = operations.filter {", readiness)
        self.assertNotRegex(runner[execution:end], r"for operation in operations\s*\{")
        self.assertIn("let hostOperations = operations.filter {", runner[readiness:normal])
        self.assertIn("let normalOperations = operations.filter {", runner[readiness:normal])
        self.assertIn("!(($0.app as? ALTApplication)?.isAltStoreApp == true || $0.bundleIdentifier.isAltStoreAppID)",
                      runner[readiness:normal])
        self.assertEqual(runner.count("await transportCore.beginTransportBatch()"), 1)
        self.assertEqual(runner.count("await transportCore.endTransportBatch()"), 2)
        self.assertRegex(runner[end:], r"(?s)\} catch \{\s*await transportCore.endTransportBatch\(\)\s*throw error\s*\}\s*await transportCore.endTransportBatch\(\)")
        gateway = self.gateway.read_text(encoding="utf-8")
        self.assertIn("self.batchCount += 1", function(gateway, "beginTransportBatch"))
        cleanup = function(gateway, "endTransportBatch")
        self.assertIn("ffiQueue.async {", cleanup)
        self.assertRegex(cleanup, r"if self.batchCount == 0 \{\s*self.releaseTransport\(\)\s*"
                         r"self.stagedBundleIdentities.removeAll\(\)\s*\}")

    def test_idle_readiness_ui_is_neutral_and_coredevice_needs_no_ipsec(self):
        patch(self.mux)
        apps = (self.side / "AltStore/My Apps/MyAppsViewController.swift").read_text(encoding="utf-8")
        for value in ("status", "result"):
            self.assertNotIn(f"updateStatusDot(isReady: {value}.isSuccess)", apps)
            self.assertRegex(apps, r"switch " + value + r" \{\s*"
                             r"case .success\(let ready\): updateStatusDot\(isReady: ready \? true : nil\)\s*"
                             r"case .failure: updateStatusDot\(isReady: false\)\s*\}")
        dot = function(apps, "updateStatusDot")
        self.assertIn("isReady: Bool?", dot)
        self.assertIn("let targetColor: UIColor = isReady == nil ? .systemGray : (isReady == true ? .systemGreen : .systemRed)", dot)
        health = self.side / "SideStore/Views/Settings/TechyThings/HealthCheck"
        view = (health / "HealthCheckView.swift").read_text(encoding="utf-8")
        idle = view.index("case .success(false):")
        ready = view.index("case .success(true):", idle)
        failed = view.index("case .failure", ready)
        self.assertIn('Text("Not Checked")', view[idle:ready])
        self.assertIn(".foregroundColor(.secondary)", view[idle:ready])
        self.assertNotIn('Text("SideStore Ready")', view[idle:ready])
        self.assertNotIn(".foregroundColor(.green)", view[idle:ready])
        self.assertIn('Text("SideStore Ready")', view[ready:failed])
        self.assertIn(".foregroundColor(.green)", view[ready:failed])
        model = (health / "HealthCheckViewModel.swift").read_text(encoding="utf-8")
        self.assertIn("let ipsecSat = (isRp || minimuxer.gateway.coreDeviceTransportEnabled) ? nil : m.ipsec", model)
        self.assertNotIn("let ipsecSat = isRp ? nil : m.ipsec", model)
        self.assertIn("self.ipsecSatisfied = status.ipsecSat", model)

    def test_send_app_preserves_afc_error_and_cellular_cleanup(self):
        original = self.send.read_text(encoding="utf-8")
        self.assertIn("throw OperationError.appNotFound(name: bundleIdentifier)", original)
        patch(self.mux)
        sent = self.send.read_text(encoding="utf-8")
        self.assertIn("try await sendAppBundleAfc(bundleIdentifier, at: appURL)", sent)
        catch = sent[sent.index("} catch {"):sent.index("return resignedAppBundle")]
        self.assertIn("await CellularRefreshManager.shared.turnOnDataIfNeeded()", catch)
        self.assertRegex(catch, r"throw error\s*\}")
        self.assertLess(catch.index("turnOnDataIfNeeded()"), catch.index("throw error"))
        self.assertNotIn("OperationError.appNotFound", sent)

    def test_composite_parser_source(self):
        patch(self.mux)
        parser = self.read("Common/PairingFile.swift")
        body = parser[parser.index("public static func validatePairingFile"):]
        self.assertLess(body.index("return .lockdown"), body.index("return .rppairing"))

    def test_pairing_mode_diagnostic_uses_selected_parser_mode(self):
        patch(self.mux)
        text = self.gateway.read_text(encoding="utf-8")
        marker = "[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED"
        self.assertEqual(text.count(marker), 1)
        self.assertIn(
            'setPairingFileType(parsedPairingFile.mode)\n'
            '            debugLog("' + marker + r' mode=\(parsedPairingFile.mode)")', text)
        self.assertLess(text.index("PairingFileParser.parse(content: pairingFileContent)"),
                        text.index(marker))

    def test_composite_parser_executable(self):
        if not shutil.which("swiftc"):
            self.skipTest("swiftc unavailable on PATH; real pairing parser execution not verified")
        patch(self.mux)
        self.compile_run('''import Foundation
enum MinimuxerConstants {
    static let remotePairingPort: UInt16 = 49151
    static let lockdowndPort: UInt16 = 62078
}
''' + self.read("Common/PairingProtocol.swift") + self.read("Common/PairingFile.swift") + '''
let rp: [String: any Sendable] = ["private_key": Data(), "public_key": Data(), "identifier": "test"]
let keys = ["WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey", "HostID", "RootCertificate", "UDID", "EscrowBag", "HostCertificate", "DeviceCertificate"]
var lockdown: [String: any Sendable] = [:]
for key in keys { lockdown[key] = "test" }
let composite = lockdown.merging(rp) { old, _ in old }
for (record, expected) in [(rp, PairingProtocol.rppairing), (lockdown, .lockdown), (composite, .lockdown)] {
    let selected = try PairingFileParser.validatePairingFile(from: record)
    precondition(selected == expected)
    let data = try PropertyListSerialization.data(fromPropertyList: record, format: .xml, options: 0)
    let parsed = try PairingFileParser.parse(content: String(decoding: data, as: UTF8.self))
    precondition(parsed.mode == expected)
}
let invalidRecords: [[String: any Sendable]?] = [nil, [:], ["identifier": "test"]]
for invalid in invalidRecords {
    do { _ = try PairingFileParser.validatePairingFile(from: invalid); fatalError("accepted invalid record") }
    catch is PairingError { }
}
''')

    def test_policy_executable(self):
        self.compile_run('''public enum DeviceConnectionMode { case notConfigured, localVPN, remoteServer }
public enum PairingProtocol { case unknown, lockdown, rppairing }
''' + POLICY.replace("public import MinimuxerCommon\n", "") + '''
for modern in [false, true] {
 for mode in [DeviceConnectionMode.notConfigured, .localVPN, .remoteServer] {
  for pairing in [PairingProtocol.unknown, .lockdown, .rppairing] {
   for utun in [false, true] {
    for ipsec in [false, true] {
     for supported in [false, true] {
      let expected: RefreshTransport
      if pairing == .unknown { expected = .unavailable }
      else if mode == .remoteServer { expected = .proxy }
      else if mode != .localVPN || !utun { expected = .unavailable }
      else if pairing == .rppairing { expected = .remotePairing }
      else if modern && supported { expected = .coreDevice }
      else if ipsec { expected = .lockdownIPSec }
      else if !modern { expected = .lockdownLegacy }
      else { expected = .unavailable }
      let actual = RefreshTransportPolicy.select(modernOS: modern, mode: mode, pairing: pairing, utun: utun, ipsec: ipsec, coreDeviceSupported: supported)
      precondition(actual.transport == expected, "policy matrix mismatch")
      precondition(!actual.reason.isEmpty)
     }
    }
   }
  }
 }
}
''')


class CombinedWorkflowTests(unittest.TestCase):
    def test_local_binary_and_combined_patch_injected_before_build(self):
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
        checks = workflow[workflow.index("- name: Run repository checks before patches"):]
        checks = checks.split("\n      - name:", 1)[0]
        self.assertIn("EMBEDDED_SIDESTORE_TEST_SOURCE: ${{ github.workspace }}/work/EmbeddedSideStore", checks)
        self.assertIn("python3 -m unittest discover -s builder/tests -v", checks)
        self.assertIn("MUX=work/EmbeddedSideStore/Dependencies/minimuxer", workflow)
        copy = workflow.index('cp -R idevice/swift/IDevice.xcframework "$MUX/DeviceGateway/LocalBinary/IDevice.xcframework"')
        adapter = workflow.index('python3 builder/scripts/patch_combined_transport.py "$MUX"')
        package = workflow.index('python3 builder/scripts/patch_local_idevice_package.py "$MUX"')
        build = workflow.index("xcodebuild -project work/EmbeddedSideStore")
        self.assertLess(copy, adapter)
        self.assertLess(adapter, package)
        self.assertLess(package, build)
        self.assertGreaterEqual(workflow.count('patch_combined_transport.py "$MUX"'), 2)


if __name__ == "__main__":
    unittest.main()
