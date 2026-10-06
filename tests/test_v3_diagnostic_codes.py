"""Stable categories are explicit finite evidence, independent of attempt IDs."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / 'scripts/templates/combined_failure.swift'


def declaration(source, anchor):
    start = source.index(anchor)
    opening = source.index('{', start)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == '{': depth += 1
        elif source[end] == '}':
            depth -= 1
            if depth == 0: return source[start:end + 1]
    raise AssertionError(anchor)


class DiagnosticCodeTests(unittest.TestCase):
    def test_frozen_registry_matches_explicit_switches_without_collisions(self):
        registry = json.loads((ROOT / 'docs/ERROR_CODES_V1.json').read_text())
        source = COMMON.read_text()
        for family, name in [('stage', 'Stage'), ('code', 'Code'), ('cause', 'SafeCause'),
                             ('step', 'SourceStep'), ('launch', 'LaunchContext.Step')]:
            body = declaration(source, 'extension CombinedFailure.' + name + ' {')
            actual = dict(re.findall(r'case \.(\w+): return "([A-Z0-9]+)"', body))
            self.assertEqual(actual, registry[family])
            enum_name = 'Step' if name == 'LaunchContext.Step' else name
            enum_body = declaration(source, 'public enum ' + enum_name + ':')
            enum_body = enum_body.split('var portalUserLabel')[0].split('fileprivate var inferredRetryable')[0]
            declared = set()
            for group in re.findall(r'\bcase\s+([A-Za-z]\w*(?:\s*,\s*[A-Za-z]\w*)*)', enum_body):
                declared.update(part.strip() for part in group.split(','))
            self.assertEqual(set(actual), declared)
            self.assertEqual(len(actual), len(set(actual.values())))
        self.assertIn('case .authentication: return "AUTH"', source)
        self.assertIn('case .failed: return "C11"', source)
        self.assertIn('case .fetchTeams: return "S06"', source)
        body = declaration(source, 'public var diagnosticCode: String {')
        for prohibited in ['hashValue', 'Hasher', 'localizedDescription', 'correlationID', 'UUID()', 'requestContext']:
            self.assertNotIn(prohibited, body)

    def test_every_inventoried_local_condition_has_its_frozen_label(self):
        registry = json.loads((ROOT / 'docs/ERROR_CODES_V1.json').read_text())
        coverage = json.loads((ROOT / 'docs/ERROR_PRESENTATION_COVERAGE.json').read_text())
        codes = []
        for item in coverage['local_conditions']:
            code = item['diagnostic_code']
            self.assertEqual(registry['localPresentation'][item['key']], code)
            codes.append(code)
            self.assertTrue(item['files'], item['key'])
            for filename in item['files']:
                text = (ROOT / filename).read_text()
                literal = json.dumps(item['literal'], ensure_ascii=False)
                self.assertIn(literal + ' + "\\nError ID: ' + code + '"', text)
        self.assertEqual(len(codes), len(set(codes)))

    def test_guest_alert_does_not_display_or_copy_arbitrary_provider_text(self):
        source = (ROOT / 'scripts/patch_guest_return.py').read_text()
        body = source.split('VIRTUAL_LAUNCH_INITIALIZED =', 1)[1].split('CANCELLATION_OLD', 1)[0]
        self.assertNotIn('launchError.localizedDescription', body)
        self.assertIn('SS-GUEST-EXIT', body)
        self.assertIn('SS-GUEST-UNKNOWN', body)
        self.assertIn('safeNative ? launchError.domain : @"redacted"', body)
        self.assertIn('UIPasteboard.generalPasteboard.string = details', body)

    def test_actual_swift_categories_display_copy_and_privacy(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; stable diagnostic IDs execute in macOS CI')
        primitives = (ROOT / 'scripts/templates/v3_behavioral_primitives.swift').read_text()
        source = COMMON.read_text() + '\n' + declaration(primitives, 'enum V3AuthFailureDiagnosticsPolicy {')
        source += r'''
@main struct StableDiagnosticTests {
 static func main() {
  let first = "00000000-0000-0000-0000-000000000001"
  let second = "00000000-0000-0000-0000-000000000002"
  let secret = "SECRET-account-token@example.invalid"
  let failure = CombinedFailure(operation: "signIn", stage: .authentication, id: first,
      underlying: NSError(domain: secret, code: 73, userInfo: [NSLocalizedDescriptionKey: secret]),
      sourceStep: .appleAuthentication, signingContext: ["typed_error": "sideSignServerReportedError"])
  precondition(failure.diagnosticCode == "SS-AUTH-C11-S03-T01")
  precondition(failure.correlating(to: second).diagnosticCode == failure.diagnosticCode)
  precondition(failure.correlating(to: second).correlationID != failure.correlationID)
  precondition(failure.safeMessage.contains(failure.diagnosticLabel))
  precondition(failure.technicalDetails.contains("diagnostic_code=" + failure.diagnosticCode))
  precondition(!failure.safeMessage.contains(secret) && !failure.technicalDetails.contains(secret))
  precondition(CombinedFailure.decode(failure.wire, expectedID: first)?.diagnosticCode == failure.diagnosticCode)
  for stage in CombinedFailure.Stage.allCases {
   var ids = Set<String>()
   for code in CombinedFailure.Code.allCases {
    let item = CombinedFailure(operation: "command", stage: stage, code: code, id: first)
    precondition(ids.insert(item.diagnosticCode).inserted)
   }
  }
  var causeIDs = Set<String>(), stepIDs = Set<String>()
  for cause in CombinedFailure.SafeCause.allCases {
   precondition(causeIDs.insert(CombinedFailure(operation: "command", stage: .command,
       id: first, safeCause: cause).diagnosticCode).inserted)
  }
  for step in CombinedFailure.SourceStep.allCases {
   precondition(stepIDs.insert(CombinedFailure(operation: "command", stage: .command,
       id: first, sourceStep: step).diagnosticCode).inserted)
  }
  let portal = CombinedFailure(operation: "signIn", stage: .provisioning, id: first,
      sourceStep: .fetchTeams, signingContext: ["typed_error": "sideSignServerReportedError", "server_code": "1100"])
  precondition(portal.diagnosticCode == "SS-PROV-C11-S06-T01-P01")
  let terminalReply: [String: Any] = ["state": "authenticatedProvisioningIncomplete", "failure": portal.wire]
  let provisioning = V3AuthFailureDiagnosticsPolicy.provisioning(reply: terminalReply,
      message: "Apple rejected the saved session.", technical: portal.technicalDetails)
  let provisioningID = V3AuthFailureDiagnosticsPolicy.diagnosticCode(for: portal.wire)
  precondition(provisioning.message == "Apple rejected the saved session.\nError ID: " + provisioningID)
  precondition(provisioning.technical.hasPrefix("diagnostic_code=" + provisioningID + " "))
  precondition(provisioning.technical.contains("underlying_diagnostic_code=" + portal.diagnosticCode))
  precondition(provisioning.technical.contains("underlying_builder_commit="))
  precondition(provisioning.technical.components(separatedBy: .whitespacesAndNewlines)
      .filter { $0.hasPrefix("builder_commit=") }.count == 1)
  var auth = failure.wire; auth["kind"] = "unknown"
  let authID = V3AuthFailureDiagnosticsPolicy.diagnosticCode(for: auth)
  let displayed = V3AuthFailureDiagnosticsPolicy.display(failure.safeMessage, failure: auth)
  let displayedTwice = V3AuthFailureDiagnosticsPolicy.display(displayed, failure: auth)
  precondition(displayed == displayedTwice)
  let recoveryPresentation = V3AuthFailureDiagnosticsPolicy.display(failure.safeMessage + " " + failure.recovery, failure: auth)
  precondition(recoveryPresentation.contains(failure.recovery))
  precondition(recoveryPresentation.components(separatedBy: "Error ID:").count == 2)
  precondition(displayed.components(separatedBy: "Error ID:").count == 2)
  precondition(displayed.contains(authID))
  precondition(V3AuthFailureDiagnosticsPolicy.render(auth, underlyingCode: 0, retryableValue: nil)
      .contains("diagnostic_code=" + authID))
  let hostile: [String: Any] = ["kind": secret, "stage": secret, "sourceStep": secret,
      "code": secret, "signingContext": ["typed_error": secret]]
  precondition(V3AuthFailureDiagnosticsPolicy.diagnosticCode(for: hostile) == "SS-AUTH-C11-A00")
  precondition(V3DiagnosticBuild.validatedCommit(secret) == "unknown")
  precondition(V3DiagnosticBuild.validatedCommit(String(repeating: "A", count: 40)) == String(repeating: "a", count: 40))
  precondition(V3DiagnosticBuild.validatedCommit(nil) == "unknown")
  precondition(V3DiagnosticBuild.validatedCommit(String(repeating: "a", count: 40) + "\n") == "unknown")
  let copy = V3DiagnosticCopy.details(visibleMessage: "Error ID: " + secret + "\nError ID: SS-CAT-D001",
      technical: "correlation=" + first)
  precondition(copy.contains("visible_error_id=SS-CAT-D001") && !copy.contains(secret))
  print("STABLE_DIAGNOSTIC_CODES_PASS")
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'; binary = Path(directory) / 'diagnostic-codes'
            path.write_text(source)
            built = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn('STABLE_DIAGNOSTIC_CODES_PASS', ran.stdout)
