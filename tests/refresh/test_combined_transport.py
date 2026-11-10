"""Exercise the combined adapter on disposable copies of the real pinned source."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


from tests.support.product_sources import read_product_source

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from tests.owner_source import owner_source, owner_root

MUX_OWNER = "SideStore/Dependencies/minimuxer"


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


def verify_gateway_ownership(text):
    """Current Swift call-site contracts; not a claim of executing Rust FFI."""
    logging = function(text, "setLogging")
    callback = "idevice_set_transport_log_callback(sideStoreTransportLog)"
    assert logging.count(callback) == 1, "transport log callback must be installed"
    assert logging.index(callback) < logging.index("idevice_init_logger("), "callback must precede logger"
    for method, cast, release, use in (
        ("verifyInstalledBundle", "let applications = result.assumingMemoryBound(to: plist_t?.self)",
         "defer { idevice_plist_array_free(applications, UInt(count)) }", "for index in 0..<count {"),
        ("getAppPaths", "let plistArray = resultPtr.assumingMemoryBound(to: plist_t?.self)",
         "defer { idevice_plist_array_free(plistArray, UInt(outLen)) }", "for i in 0..<outLen {")):
        body = function(text, method)
        assert body.count(release) == 1, method + " must release the Rust array exactly once"
        assert re.search(re.escape(cast) + r"\s*" + re.escape(release), body), method + " must defer immediately after cast"
        assert body.index(release) < body.index(use), method + " must defer before enumeration"
        assert not re.search(r"(?<![\w])free\s*\(\s*(?:outResult|resultPtr|plistArray|applications|result)\s*\)", body), method + " must not libc-free Rust storage"
    # Preserve the former whole-file ban as well as scoped ownership checks.
    assert not re.search(r"(?<![\w])free\s*\(\s*outResult\s*\)", text), "outResult is Rust-owned"
    verify = function(text, "verifyInstalledBundle")
    marker = 'debugLog("[SELF_REFRESH] SIDESTORE_POST_INSTALL_VERIFY_PASS")'
    assert verify.count(marker) == 1, "post-install success evidence must be emitted once"
    assert re.search(r'guard let matchedIdentifier else \{\s*throw IdeviceGatewayError\([^\n]+\)\s*\}\s*'
                     r'debugLog\("\[SELF_REFRESH\] INSTALLED_APP_LOOKUP_PASS"\)\s*' + re.escape(marker), verify), "success evidence must follow exact lookup guard"


class SourceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owner-transport-")
        self.addCleanup(self.temp.cleanup)
        self.side = Path(self.temp.name) / "SideStore"
        self.mux = self.side / "Dependencies/minimuxer"
        # All inputs checked against the candidate graph and committed blob before copying.
        inputs = {
            "SideStore": ["SideStore/Core/Operations/PipelineRunner.swift",
                "SideStore/Core/Operations/PipelineOperations/SendAppOperation.swift",
                "SideStore/Core/DeviceApi/MinimuxerWrapper.swift", "AltStore/My Apps/MyAppsViewController.swift",
                "SideStore/Views/Settings/TechyThings/HealthCheck/HealthCheckView.swift",
                "SideStore/Views/Settings/TechyThings/HealthCheck/HealthCheckViewModel.swift"],
            MUX_OWNER: ["DeviceGateway/idevice/IdeviceGateway.swift", "DeviceGateway/DeviceGatewayError.swift",
                "Common/ConcurrencyUtils.swift", "Common/FFIDispatcher.swift", "Common/PairingFile.swift", "Common/PairingProtocol.swift",
                "Sources/MinimuxerImpl.swift", "Sources/MinimuxerError.swift", "Sources/RefreshTransportPolicy.swift"]}
        for owner, paths in inputs.items():
            target_root = self.side if owner == "SideStore" else self.mux
            for relative in paths:
                target = target_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(owner_source(relative, owner), encoding="utf-8")
        self.gateway = self.mux / "DeviceGateway/idevice/IdeviceGateway.swift"
        self.send = self.side / "SideStore/Core/Operations/PipelineOperations/SendAppOperation.swift"

    def read(self, relative):
        return (self.mux / relative).read_text(encoding="utf-8")

    def compile_run(self, source, *, expected_success=True):
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
        if expected_success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "negative control unexpectedly passed")


class CombinedTransportTests(SourceFixture):
    def test_gateway_native_ownership_and_evidence(self):
        verify_gateway_ownership(self.gateway.read_text(encoding="utf-8"))

    def test_gateway_native_ownership_rejects_counterfactuals(self):
        original = self.gateway.read_text(encoding="utf-8")
        callback = "idevice_set_transport_log_callback(sideStoreTransportLog)"
        application_free = "defer { idevice_plist_array_free(applications, UInt(count)) }"
        paths_free = "defer { idevice_plist_array_free(plistArray, UInt(outLen)) }"
        marker = 'debugLog("[SELF_REFRESH] SIDESTORE_POST_INSTALL_VERIFY_PASS")'
        cases = {
            "missing callback": (callback, ""),
            "wrong callback": (callback, "idevice_set_transport_log_callback(nil)"),
            "missing applications free": (application_free, ""),
            "wrong applications count": (application_free, application_free.replace("UInt(count)", "0")),
            "missing paths free": (paths_free, ""),
            "wrong paths count": (paths_free, paths_free.replace("UInt(outLen)", "0")),
            "libc paths free": (paths_free, "defer { free(outResult) }"),
            "extra libc paths free": (paths_free, paths_free + "\n            free ( outResult )"),
            "eager applications free": (application_free, "idevice_plist_array_free(applications, UInt(count))"),
            "missing success evidence": (marker, ""),
            "lookup guard removed": ("guard let matchedIdentifier else {", "if false {"),
        }
        for label, (before, after) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(original.count(before), 1)
                with self.assertRaises(AssertionError):
                    verify_gateway_ownership(original.replace(before, after, 1))
        # A marker at function entry is not proof of a successful installation.
        anchor = '        debugLog("[SELF_REFRESH] POST_INSTALL_BROWSE_START")'
        self.assertEqual(original.count(anchor), 1)
        early = original.replace(marker, "", 1).replace(anchor, anchor + "\n        " + marker, 1)
        with self.assertRaises(AssertionError):
            verify_gateway_ownership(early)

    def test_owned_readiness_runner_uses_verified_production(self):
        # This real owner harness covers the redesigned lease/configuration paths
        # that did not exist in the old generated readiness body.
        for relative in (".ci/test-owned-readiness.py", ".ci/owned-readiness/fixture.swift",
                         "Sources/MinimuxerApi.swift", "Sources/MinimuxerImpl.swift",
                         "Sources/Services/NetworkObserverService.swift",
                         "Sources/Services/EndpointRefreshTransaction.swift",
                         "Sources/Services/DeviceConnectionManager.swift", "Sources/DeviceProvider.swift"):
            owner_source(relative, MUX_OWNER)
        if not shutil.which("swiftc"):
            self.skipTest("Swift unavailable: owned readiness runtime not executed")
        result = subprocess.run([sys.executable, str(owner_root(MUX_OWNER) / ".ci/test-owned-readiness.py")],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "REQUIRE_SWIFT_OWNED_READINESS_TESTS": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("skipped", result.stderr)

    def test_app_folder_staging_and_install_preserved(self):
        text = self.gateway.read_text(encoding="utf-8")
        for name in ("syncsendAppBundleAfc", "syncInstallAppBundle"):
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
        text = self.gateway.read_text(encoding="utf-8")
        calls = re.findall(r"withFFIDispatch\s*(\([^\n]*?\))?\s*\{", text)
        self.assertIn("public import MinimuxerCommon\n", text)
        self.assertNotIn("internal import MinimuxerCommon", text)
        self.assertGreaterEqual(len(calls), 10)
        self.assertTrue(all(call == "(on: self.ffiQueue)" for call in calls))
        self.assertEqual(text.count("defer { if self.batchCount == 0 { self.releaseTransport() } }"), len(calls) - 1)
        # stop() invokes cleanup directly; every async operation closure releases its lease.
        self.assertIn("self.cleanup()", function(text, "stop"))
        self.assertIn("onFFIQueue { self.syncCleanup() }", function(text, "cleanup"))
        self.assertIn("releaseTransport()", function(text, "syncCleanup"))
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
        text = self.gateway.read_text(encoding="utf-8")
        connection = function(text, "ensureCoreDeviceConnection")
        self.assertIn("tunnel_create_usb(provider, &adapter, &handshake)", connection)
        self.assertRegex(connection, r'\} catch \{\s*releaseTransport\(\)\s*'
                         r'debugLog\("\[SIDESTORE_COREDEVICE\] selected_transport=FAILED_NO_VALID_TRANSPORT '
                         r'stage=coreDevice result=failed"\)\s*throw error\s*\}\s*\}$')
        self.assertNotIn("performWithTcpService", connection)
        route = function(text, "ensureRPConnection")
        self.assertRegex(route, r"if usesCoreDevice \{\s*try ensureCoreDeviceConnection\(\)\s*return\s*\}")
        fetch = function(text, "syncFetchUDID")
        self.assertRegex(fetch, r"if pairingFileType == .rppairing \|\| usesCoreDevice \{\s*do \{")
        self.assertLess(fetch.index("try ensureRPConnection()"), fetch.index("lockdownd_connect_rsd("))
        self.assertIn("try self.syncFetchUDID()", function(text, "fetchUDID"))
        self.assertIn("try ensureRPConnection()", function(text, "performWithService"))
        readiness = function(self.read("Sources/MinimuxerImpl.swift"), "readinessObservation")
        self.assertIn("if !gateway.hasActiveTransportBatch { return .init(result: .success(false)) }", readiness)
        # Both DDI-enabled and DDI-disabled readiness must first fetch through the tunnel.
        fetch_call = readiness.index("try await fetchUDID()")
        valid_udid = readiness.index("if let failure = await localObservationFailure()", fetch_call)
        ddi_branch = readiness.index("if withDDIMountCheck {")
        self.assertLess(fetch_call, valid_udid)
        self.assertLess(valid_udid, ddi_branch)
        self.assertLess(ddi_branch, readiness.index("return .init(result: .success(true))", ddi_branch))
        self.assertEqual(readiness[readiness.index("let deviceUDID: String"):].count("return .init(result: .success(true))"), 1)
        self.assertIn("owned: false).result", function(self.read("Sources/MinimuxerImpl.swift"), "isReady"))
        self.assertIn("!udid.isEmpty", fetch)

    def test_readiness_and_batch_lease(self):
        impl = self.read("Sources/MinimuxerImpl.swift")
        self.assertIn("if #available(iOS 26.4, *)", impl)
        self.assertIn("gateway.supportsCoreDeviceTransport", impl)
        self.assertIn("if !gateway.hasActiveTransportBatch { return .init(result: .success(false)) }", impl)
        self.assertNotIn("no ipsec interface (required for lockdown", impl.lower())
        runner = (self.side / "SideStore/Core/Operations/PipelineRunner.swift").read_text(encoding="utf-8")
        self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(typedError)", runner)
        begin = runner.index("await transportCore.beginTransportBatch()")
        task = runner.index("do {", begin)
        readiness = runner.index("/* Minimuxer Readiness Check */", task)
        end = runner.index("await transportCore.endTransportBatch()", readiness)
        self.assertLess(begin, task)
        self.assertLess(task, readiness)
        normal = runner.index("for owned in normalOperations {", readiness)
        drained = runner.index("while let _ = try await taskGroup.next() {}", normal)
        host = runner.index("for owned in hostOperations {", drained)
        self.assertLess(readiness, normal)
        self.assertLess(normal, drained)
        self.assertLess(drained, host)
        self.assertLess(host, end)
        self.assertRegex(runner[drained:host],
                         r"^while let _ = try await taskGroup.next\(\) \{\}\s*\}\s*$")
        self.assertIn("taskGroup.addTask {", runner[normal:drained])
        self.assertIn("try await self.performOperation(for: operation, handler: handler, group: group,",
                      runner[normal:drained])
        self.assertRegex(runner[host:end],
                         r"for owned in hostOperations \{\s*let operation = owned.operation\s*try checkReadinessOwnership\(\)\s*"
                         r"try await self.performOperation\(for: operation, handler: handler, group: group,")
        self.assertNotIn("taskGroup.addTask", runner[host:end])
        execution = runner.index("let hostOperations = ownedOperations.filter {", readiness)
        self.assertNotRegex(runner[execution:end], r"for operation in operations\s*\{")
        self.assertIn("let hostOperations = ownedOperations.filter {", runner[readiness:normal])
        self.assertIn("let normalOperations = ownedOperations.filter {", runner[readiness:normal])
        self.assertIn("!(($0.operation.app as? ALTApplication)?.isAltStoreApp == true || $0.operation.bundleIdentifier.isAltStoreAppID)",
                      runner[readiness:normal])
        self.assertEqual(runner.count("await transportCore.beginTransportBatch()"), 1)
        self.assertEqual(runner.count("await transportCore.endTransportBatch()"), 2)
        self.assertRegex(runner[end:], r"(?s)await transportCore.endTransportBatch\(\)\s*return group\s*\} catch \{\s*await transportCore.endTransportBatch\(\)\s*throw error")
        self.assertIn("CellularRefreshManager.shared.isEnabled", runner[readiness:end])
        self.assertIn("refreshAdmission: checkReadinessOwnership", runner[host:end])
        self.assertIn("if let native = error as? MinimuxerError { typedError = native.asOperationError }", runner)
        gateway = self.gateway.read_text(encoding="utf-8")
        self.assertIn("self.batchCount += 1", function(gateway, "beginTransportBatch"))
        cleanup = function(gateway, "endTransportBatch")
        self.assertIn("ffiQueue.async {", cleanup)
        self.assertRegex(cleanup, r"if self.batchCount == 0 \{\s*self.releaseTransport\(\)\s*"
                         r"self.stagedBundleIdentities.removeAll\(\)\s*\}")

    def test_idle_readiness_ui_is_neutral_and_coredevice_needs_no_ipsec(self):
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
        sent = self.send.read_text(encoding="utf-8")
        self.assertIn("try await sendAppBundleAfc(bundleIdentifier, at: appURL)", sent)
        catch = sent[sent.index("} catch {"):sent.index("return resignedAppBundle")]
        self.assertIn("await CellularRefreshManager.shared.turnOnDataIfNeeded()", catch)
        self.assertRegex(catch, r"throw error\s*\}")
        self.assertLess(catch.index("turnOnDataIfNeeded()"), catch.index("throw error"))
        self.assertNotIn("OperationError.appNotFound", sent)

    def test_composite_parser_source(self):
        parser = self.read("Common/PairingFile.swift")
        body = parser[parser.index("// Keep NRG composite records"):parser.index("public static func parse")]
        self.assertLess(body.index("return .lockdown"), body.index("return .rppairing"))

    def test_cleanup_closures_preserve_return_values(self):
        gateway = self.gateway.read_text(encoding="utf-8")
        gateway = gateway[gateway.index("// Async FFI Dispatcher Extensions"):]
        for name in ("fetchUDID", "getLockdownValue", "dumpProfiles", "isDDIMounted",
                     "startWirelessPair", "triggerWirelessPair", "afcListDirectory",
                     "afcReadFile", "afcGetFileInfo"):
            body = function(gateway, name)
            self.assertIn("defer { if self.batchCount == 0", body)
            self.assertIn("return try self.sync", body, name)
        heartbeat = function(gateway, "performHeartbeat")
        self.assertIn("return newInterval > 0 ? newInterval : 1000", heartbeat)
        self.assertNotIn("return try self.syncPerformHeartbeat", heartbeat)

    def test_owner_cleanup_closures_executable(self):
        if not shutil.which("swiftc"):
            self.skipTest("swiftc unavailable; owner closure typechecking not verified locally")
        gateway = self.gateway.read_text(encoding="utf-8")
        gateway = gateway[gateway.index("// Async FFI Dispatcher Extensions"):]
        methods = "\n".join(function(gateway, name) for name in
                            ("fetchUDID", "isDDIMounted", "afcReadFile", "afcGetFileInfo", "performHeartbeat"))
        self.compile_run(self.read("Common/FFIDispatcher.swift") + '''
final class IdeviceGateway: @unchecked Sendable {
    let ffiQueue = DispatchQueue(label: "test.ffi")
    var batchCount = 0
    var releases = 0
    var fail = false
    func releaseTransport() { releases += 1 }
    func syncFetchUDID() throws -> String {
        if fail { throw NSError(domain: "test", code: 1) }
        return "test-device"
    }
    func syncIsDDIMounted() throws -> Bool { true }
    func syncAfcReadFile(bundleId: String, path: String) throws -> Data { Data([1, 2, 3]) }
    func syncAfcGetFileInfo(bundleId: String, path: String) throws -> (isDirectory: Bool, fileSize: Int64) { (false, 3) }
    func syncPerformHeartbeat(interval: UInt64, newInterval: UnsafeMutablePointer<UInt64>) throws { newInterval.pointee = 60 }
''' + methods + '''
}
let finished = DispatchSemaphore(value: 0)
Task.detached {
    let gateway = IdeviceGateway()
    let udid = try await gateway.fetchUDID()
    precondition(udid == "test-device")
    let mounted = try await gateway.isDDIMounted()
    precondition(mounted)
    let bytes = try await gateway.afcReadFile(bundleId: "test", path: "file")
    precondition(bytes == Data([1, 2, 3]))
    let info = try await gateway.afcGetFileInfo(bundleId: "test", path: "file")
    precondition(!info.isDirectory && info.fileSize == 3)
    let interval = try await gateway.performHeartbeat(interval: 1)
    precondition(interval == 60 && gateway.releases == 5)
    gateway.batchCount = 1
    _ = try await gateway.fetchUDID()
    precondition(gateway.releases == 5)
    gateway.batchCount = 0
    gateway.fail = true
    do { _ = try await gateway.fetchUDID(); fatalError("Expected failure") }
    catch { precondition(gateway.releases == 6) }
    finished.signal()
}
precondition(finished.wait(timeout: .now() + 10) == .success)
''')

    def test_pairing_mode_diagnostic_uses_selected_parser_mode(self):
        text = self.gateway.read_text(encoding="utf-8")
        marker = "[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED"
        self.assertEqual(text.count(marker), 1)
        self.assertIn(
            'setPairingFileType(parsedPairingFile.mode)\n'
            '            debugLog("' + marker + r' mode=\(parsedPairingFile.mode)")', text)
        self.assertLess(text.index("PairingFileParser.parse(content: pairingFileContent, preferred: preferred)"),
                        text.index(marker))

    def test_composite_parser_executable(self):
        if not shutil.which("swiftc"):
            self.skipTest("swiftc unavailable on PATH; real pairing parser execution not verified")
        source = '''import Foundation
enum MinimuxerConstants {
    static let remotePairingPort: UInt16 = 49151
    static let lockdowndPort: UInt16 = 62078
}
''' + self.read("Common/ConcurrencyUtils.swift") + self.read("Common/PairingProtocol.swift") + self.read("Common/PairingFile.swift") + '''
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
for mode in [PairingProtocol.lockdown, .rppairing] {
 let selected = try PairingFileParser.validatePairingFile(from: composite, preferred: mode)
 precondition(selected == mode)
}
for (record, mode) in [(rp, PairingProtocol.lockdown), (lockdown, .rppairing), (composite, .unknown)] {
 do { _ = try PairingFileParser.validatePairingFile(from: record, preferred: mode); fatalError("accepted invalid preference") }
 catch is PairingError { }
}
let invalidRecords: [[String: any Sendable]?] = [nil, [:], ["identifier": "test"]]
for invalid in invalidRecords {
    do { _ = try PairingFileParser.validatePairingFile(from: invalid); fatalError("accepted invalid record") }
    catch is PairingError { }
}
'''
        self.compile_run(source)
        anchor = "if hasLockdown {\n            return .lockdown"
        self.assertEqual(source.count(anchor), 1)
        self.compile_run(source.replace(anchor, "if hasLockdown {\n            return .rppairing", 1), expected_success=False)

    def test_policy_executable(self):
        source = '''public enum DeviceConnectionMode { case notConfigured, localVPN, remoteServer }
public enum PairingProtocol { case unknown, lockdown, rppairing }
''' + self.read("Sources/RefreshTransportPolicy.swift").replace("public import MinimuxerCommon\n", "") + '''
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
      let cause: RefreshTransportUnavailableCause?
      if pairing == .unknown { cause = .pairingUnavailable }
      else if mode == .remoteServer { cause = nil }
      else if mode != .localVPN { cause = .connectionModeNotConfigured }
      else if !utun {
       cause = modern && pairing == .lockdown && !supported && !ipsec
          ? .unsupportedBackend : .missingRequiredVPNInterface
      } else if expected == .unavailable { cause = .unsupportedBackend }
      else { cause = nil }
      precondition(actual.unavailableCause == cause, "wrong recovery authority")
     }
    }
   }
  }
 }
}
'''

        self.compile_run(source)
        anchor = "transport: .coreDevice, reason:"
        self.assertEqual(source.count(anchor), 1)
        self.compile_run(source.replace(anchor, "transport: .unavailable, reason:", 1), expected_success=False)



class RuntimeAuthenticationSourceTests(unittest.TestCase):
    def test_runtime_uses_authoritative_authentication_graph(self):
        service = read_product_source('v3_sidestore_service.swift')
        runtime = read_product_source('v3_headless_runtime.swift')
        self.assertNotIn("AuthFlowHandler", service + runtime)
        self.assertNotIn("AuthenticatedOperationContext", service + runtime)
        self.assertNotIn("presentingViewController", service + runtime)
        self.assertNotIn("SideSignConfigurationView()", service + runtime)
        self.assertIn("SignInOperation(context:", runtime)
        self.assertIn("SignInHandler, AnisetteServerHandler", runtime)
        self.assertIn("SideSignConfigManager.shared", runtime)


if __name__ == "__main__":
    unittest.main()
