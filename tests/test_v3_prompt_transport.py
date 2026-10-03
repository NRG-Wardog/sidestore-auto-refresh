"""Execute the production peer admission and bridge against external IPC stubs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def declaration(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError("unbalanced production declaration")


class PromptTransportTests(unittest.TestCase):
    def execute(self, source, marker=None, should_pass=True):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; required macOS CI executes this family")
        with tempfile.TemporaryDirectory() as temporary:
            program = Path(temporary) / "main.swift"
            executable = Path(temporary) / "test"
            program.write_text(source, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)],
                                      text=True, capture_output=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=15)
            if should_pass:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(marker, result.stdout)
            else:
                self.assertNotEqual(result.returncode, 0, "removing the peer check must break the requirement")

    def test_real_bridge_binds_prompt_answers_to_creating_service(self):
        fixture = (ROOT / "tests/fixtures/v3_bridge_harness.swift").read_text(encoding="utf-8")
        mocks = fixture[:fixture.index("@main")]
        names = ["combined_failure.swift", "v3_behavioral_primitives.swift", "combined_service_connection.swift",
                 "v3_wire_contract.swift", "v3_service_bridge.swift"]
        source = mocks + "\n".join((ROOT / "scripts/templates" / name).read_text(encoding="utf-8") for name in names)
        source += (ROOT / "tests/fixtures/v3_prompt_transport_harness.swift").read_text(encoding="utf-8")
        self.execute(source, "V3_PROMPT_TRANSPORT_PASS")

    def test_peer_admission_checks_actual_launched_pid_in_both_callback_orders(self):
        production = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        methods = declaration(production, "    fileprivate func accepted(") + "\n" + declaration(
            production, "    private func confirmLaunchedPeer(")
        source = '''import Foundation
protocol RefreshClient: AnyObject {}
final class DummyClient: RefreshClient {}
final class NSXPCInterface { init(with type: Any.Type) {} }
final class NSXPCConnection {
    let processIdentifier: Int32
    var invalidated = false; var resumes = 0; var proxies = 0
    var remoteObjectInterface: NSXPCInterface?
    var invalidationHandler: (() -> Void)?; var interruptionHandler: (() -> Void)?
    init(_ pid: Int32) { processIdentifier = pid }
    func invalidate() { invalidated = true }
    func resume() { resumes += 1 }
    func remoteObjectProxyWithErrorHandler(_ handler: @escaping (Error) -> Void) -> Any {
        proxies += 1; return DummyClient()
    }
}
@MainActor final class Owner {
    var launchID: UUID? = UUID(); var sideStorePid: Int32 = 0
    var connection: NSXPCConnection?; var client: RefreshClient?
    var pendingPeerConnections: [NSXPCConnection] = []
    var signals = 0
    var service: Owner { self }
    enum Stage { case xpcConnection }; enum Signal { case connected }
    func signal(_ signal: Signal, attempt: UUID) { signals += 1 }
    func failed(_ id: UUID, stage: Stage, underlying: Error? = nil, code: Int = 0) {}
''' + methods.replace("code: .interrupted", "code: 1").replace(
    "private func confirmLaunchedPeer", "fileprivate func confirmLaunchedPeer") + '''
}
@main struct Test {
    @MainActor static func main() {
        for early in [false, true] {
            let owner = Owner(); let id = owner.launchID!
            let rogue = NSXPCConnection(999); let correct = NSXPCConnection(42)
            if !early { owner.sideStorePid = 42 }
            owner.accepted(rogue, id: id); owner.accepted(correct, id: id)
            if early {
                precondition(rogue.resumes == 0 && correct.resumes == 0 && correct.proxies == 0)
                owner.sideStorePid = 42; owner.confirmLaunchedPeer(id)
            }
            precondition(rogue.invalidated && rogue.resumes == 0 && rogue.proxies == 0)
            precondition(correct.resumes == 1 && owner.connection === correct && owner.signals == 1)
            let duplicate = NSXPCConnection(42); owner.accepted(duplicate, id: id)
            precondition(duplicate.invalidated && duplicate.resumes == 0)
            let stale = NSXPCConnection(42); owner.accepted(stale, id: UUID())
            precondition(stale.invalidated && stale.resumes == 0)
        }
        print("V3_PEER_ADMISSION_PASS")
    }
}
'''
        self.execute(source, "V3_PEER_ADMISSION_PASS")
        mutant = source.replace(
            "guard incoming.processIdentifier == sideStorePid else { incoming.invalidate(); return }", "")
        self.assertNotEqual(source, mutant)
        self.execute(mutant, should_pass=False)
