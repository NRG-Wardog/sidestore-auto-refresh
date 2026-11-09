"""Execute the distinct command/readiness failure decoder boundaries.

The frozen readiness vocabulary is intentionally narrower than CombinedFailure.
No production decoder or allowlist is rewritten by this test.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tests.support.product_sources import read_product_source, declaration_end

READINESS_CAUSES = ('databaseLayoutBlocked', 'databaseLayoutConflict', 'databaseAppGroupUnavailable', 'databasePersistenceFailed', 'databaseStartupFailed', 'databaseHostBundleUnavailable', 'databaseHostProfileUnavailable', 'databaseModelUnrecognized', 'databaseMigrationPathUnavailable', 'databaseMigrationFailed', 'databaseSaveFailed', 'networkConnectionLost', 'networkTimedOut', 'networkUnavailable', 'anisetteServerUnavailable', 'anisetteServerRejected', 'anisetteRequestTimedOut', 'anisetteRateLimited', 'anisetteInvalidResponse', 'anisetteUnknownFailure', 'signingNetworkConnectionLost', 'signingNetworkTimedOut', 'signingNetworkUnavailable', 'developerPortalRejectedRequest', 'developerPortalInvalidResponse', 'appIDLimitReached', 'provisioningProfileUnavailable', 'certificateUnavailable', 'signingStorageUnverified', 'wifiUnavailable', 'localDevVPNUnavailable', 'unknownSigningCause', 'sourceNetworkFailure', 'sourceInvalidManifest', 'sourcePersistenceUnverified', 'sourceInvalidURL', 'sourceBlocked', 'sourceChangedID', 'sourceDuplicate', 'sourceUnsupported', 'sourceValidationFailed', 'sourceRemoveFailed', 'sourceRemoveBusy', 'sourceAddBusy', 'operationInProgress', 'responseCapacityUnavailable', 'staleRefreshAttempt', 'knownSourcePolicyNetworkFailure', 'knownSourcePolicyInvalidResponse', 'catalogUnavailable', 'catalogSourceUnavailable', 'responseEncodingFailed', 'responseTooLarge', 'pairingRequired', 'invalidPairingFile', 'pairingFilePreparationFailed', 'authAttemptNotDispatched', 'authProvisioningRetryNotDispatched', 'authSessionUnavailable', 'authResponseCapacityUnavailable', 'operationPersistenceFailed', 'keychainSignOutFailed', 'credentialCommitFailed', 'credentialCommitOutcomeUnknown', 'accountActivationFailed', 'provisioningStorageFailed', 'keychainSignOutOutcomeUnknown', 'recoveryMalformedRecord', 'recoveryIncompatibleRecord', 'recoveryStorageUnavailable', 'recoveryLockUnavailable', 'recoveryReadFailure', 'recoveryDeleteFailure', 'sharedStoreUnavailable', 'secretHandoffUnavailable')
READINESS_STEPS = ('authenticate', 'anisetteFetch', 'appleAuthentication', 'accountLookup', 'credentialCommit', 'fetchTeams', 'saveAccount', 'fetchCertificate', 'activateCertificate', 'registerDevice', 'activateAccount', 'provisioningUnknown', 'provisioningProfileFetch', 'certificateValidation', 'localCodeSigning', 'appIDLookup', 'appIDRegistration', 'appIDCapabilitiesUpdate', 'appGroupLookup', 'appGroupRegistration', 'appGroupAssignment', 'provisioningProfileRetrieval', 'provisioningProfileCreation', 'provisioningProfileUpdate', 'sourceDownload', 'manifestParsing', 'sourceValidation', 'knownSourcePolicyFetch', 'knownSourcePolicyParsing', 'catalogRead')
DIRECT_RECOVERY = ('directRecoveryRecordMissing', 'directRecoveryTargetMismatch',
                   'directRecoveryModeInvalid', 'directRecoveryPhaseNotEligible')

COMMAND_ONLY_STEPS = ('refreshRecordUpdate', 'certificateSelection',
    'provisioningProfileInstallation', 'refreshReadiness', 'refreshPreflight',
    'refreshMetadataCache', 'refreshApplication')


HARNESS = r"""
@main struct DecoderBoundaryTests {
    static let id = "00000000-0000-4000-8000-000000000001"
    static let other = "00000000-0000-4000-8000-000000000002"
    static func envelope() -> [String: Any] {
        ["version": 1, "operation": "snapshot", "stage": "serviceReadiness",
         "code": "failed", "correlationID": id, "underlyingDomain": "redacted",
         "underlyingCode": 0, "retryable": false]
    }
    static func readiness(_ failure: [String: Any], outerID: String = id) throws -> V3ServiceReadinessReply {
        let reply: [String: Any] = ["version": 1, "id": outerID, "failure": failure]
        let data = try PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
        return V3ServiceReadinessReply.decode(data, requestID: id)
    }
    static func rejectBoth(_ wire: [String: Any]) throws {
        precondition(CombinedFailure.decode(wire, expectedID: id) == nil)
        let decoded = try readiness(wire)
        precondition(decoded == .invalid)
    }
    static func main() throws {
        let causes: Set<String> = __CAUSES__
        let steps: Set<String> = __STEPS__
        let direct: [String] = __DIRECT__
        precondition(V3ServiceReadinessReply.knownSafeCauseValues == causes)
        precondition(V3ServiceReadinessReply.knownSourceStepValues == steps)
        let commandOnlySteps: Set<String> = __COMMAND_STEPS__
        precondition(Set(CombinedFailure.SafeCause.allCases.map { $0.rawValue }) == causes.union(direct))
        precondition(Set(CombinedFailure.SourceStep.allCases.map { $0.rawValue }) == steps.union(commandOnlySteps))
        for (field, values) in [("safeCause", Set(direct)), ("sourceStep", commandOnlySteps)] {
            for value in values {
                var wire = envelope(); wire[field] = value
                guard let command = CombinedFailure.decode(wire, expectedID: id) else {
                    preconditionFailure("Command decoder rejected command-only value")
                }
                precondition((field == "safeCause" ? command.safeCause?.rawValue : command.sourceStep?.rawValue) == value)
                let decoded = try readiness(wire)
                precondition(decoded == .invalid)
            }
        }
        for (field, values) in [("safeCause", causes), ("sourceStep", steps)] {
            for value in values {
                var wire = envelope(); wire[field] = value
                guard let command = CombinedFailure.decode(wire, expectedID: id) else {
                    preconditionFailure("Command decoder rejected accepted value")
                }
                precondition((field == "safeCause" ? command.safeCause?.rawValue : command.sourceStep?.rawValue) == value)
                guard case .failed(let failure) = try readiness(wire) else {
                    preconditionFailure("Readiness decoder rejected accepted value")
                }
                precondition((field == "safeCause" ? failure.safeCause : failure.sourceStep) == value)
                precondition(failure.correlationID == id)
                precondition(CombinedFailure.decode(command.wire, expectedID: id) != nil)
            }
            for bad in ["unknown-private-value", 7, true, ["nested"], ["nested": "value"]] as [Any] {
                var wire = envelope(); wire[field] = bad
                try rejectBoth(wire)
            }
        }
        for (key, wrong) in [("version", true as Any), ("version", "1" as Any),
                             ("underlyingCode", true as Any), ("underlyingCode", "0" as Any),
                             ("retryable", 1 as Any), ("correlationID", other as Any),
                             ("correlationID", 7 as Any)] {
            var wire = envelope(); wire[key] = wrong; try rejectBoth(wire)
        }
        var unknownKey = envelope(); unknownKey["unrecognized"] = "value"
        try rejectBoth(unknownKey)
        let wrongOuterID = try readiness(envelope(), outerID: other)
        precondition(wrongOuterID == .invalid)
        print("FAILURE_DECODER_BOUNDARIES_PASS")
    }
}
"""


def swift_program():
    import json
    harness = HARNESS.replace('__CAUSES__', json.dumps(READINESS_CAUSES))
    harness = harness.replace('__STEPS__', json.dumps(READINESS_STEPS))
    harness = harness.replace('__DIRECT__', json.dumps(DIRECT_RECOVERY))
    harness = harness.replace('__COMMAND_STEPS__', json.dumps(COMMAND_ONLY_STEPS))
    return (read_product_source('combined_failure.swift') + '\n' +
            read_product_source('v3_wire_contract.swift') + '\n' + harness)


class FailureDecoderBoundariesTests(unittest.TestCase):
    def test_current_owner_vocabulary_is_explicitly_scoped(self):
        source = read_product_source('v3_wire_contract.swift')
        for name, expected in [('knownSafeCauseValues', READINESS_CAUSES),
                               ('knownSourceStepValues', READINESS_STEPS)]:
            body = source.split(name + ': Set<String> = [', 1)[1].split(']', 1)[0]
            observed = re.findall(r'"(\w+)"', body)
            self.assertEqual(len(observed), len(set(observed)))
            self.assertEqual(set(observed), set(expected))
        common = read_product_source('combined_failure.swift')
        for name, readiness, command_only in [('SafeCause', READINESS_CAUSES, DIRECT_RECOVERY),
                                             ('SourceStep', READINESS_STEPS, COMMAND_ONLY_STEPS)]:
            start = common.index('public enum ' + name + ':')
            body = common[start:declaration_end(common, start)]
            cases = re.findall(r'\bcase\s+(\w+(?:\s*,\s*\w+)*)', body)
            values = [value.strip() for case in cases for value in case.split(',')]
            self.assertEqual(len(values), len(set(values)))
            self.assertEqual(set(values), set(readiness) | set(command_only))
            self.assertEqual(set(values) - set(readiness), set(command_only))
        self.assertTrue(set(COMMAND_ONLY_STEPS).isdisjoint(READINESS_STEPS))
        self.assertTrue(set(DIRECT_RECOVERY).isdisjoint(READINESS_CAUSES))
        program = swift_program()
        self.assertNotIn('__CAUSES__', program)
        self.assertNotIn('__STEPS__', program)
        self.assertNotIn('__DIRECT__', program)
        self.assertNotIn('__COMMAND_STEPS__', program)

    def test_scope_guard_detects_deletion_and_accidental_expansion(self):
        original = read_product_source
        for scope, accepted, command_only in [('knownSafeCauseValues', 'databaseLayoutBlocked', DIRECT_RECOVERY[0]),
                                              ('knownSourceStepValues', 'authenticate', COMMAND_ONLY_STEPS[0])]:
            for mutation in ('delete', 'expand'):
                def altered(name):
                    source = original(name)
                    if name != 'v3_wire_contract.swift':
                        return source
                    anchor = scope + ': Set<String> = ['
                    start = source.index(anchor)
                    before, body = source[:start], source[start:]
                    if mutation == 'delete':
                        body = body.replace('"' + accepted + '",', '', 1)
                    else:
                        body = body.replace(anchor, anchor + '"' + command_only + '",', 1)
                    return before + body
                with self.subTest(scope=scope, mutation=mutation), patch(
                        __name__ + '.read_product_source', side_effect=altered), self.assertRaises(AssertionError):
                    self.test_current_owner_vocabulary_is_explicitly_scoped()

    def test_actual_command_and_readiness_decoders(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler required; execute on macOS CI')
        with tempfile.TemporaryDirectory(prefix='failure-decoder-') as name:
            root = Path(name)
            source, binary = root / 'main.swift', root / 'decoder'
            source.write_text(swift_program())
            result = subprocess.run([compiler, '-parse-as-library', str(source), '-o', str(binary)],
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('FAILURE_DECODER_BOUNDARIES_PASS', result.stdout)
