#!/usr/bin/env python3
"""Preserve typed 2FA verification results for the v3 host prompt flow."""
from __future__ import annotations

from pathlib import Path
import sys


MARKER = "V3_TFA_TYPED_STATE_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"patch_sidesign_2fa_state: {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    api = root / "Sources/DeveloperPortal/DeveloperPortalAPI.swift"
    auth = root / "Sources/DeveloperPortal/Authentication.swift"
    api_text = api.read_text(encoding="utf-8")
    auth_text = auth.read_text(encoding="utf-8")

    if MARKER in api_text and MARKER in auth_text:
        return

    old_request = '''public enum TwoFactorRequest: Sendable, Equatable {
    case selectDeliveryMethod(preferredMode: TwoFactorDeliveryMode, phoneNumbers: [TrustedPhoneNumber])
    case trustedDevice(error: String? = nil)
    case sms(phoneNumbers: [TrustedPhoneNumber], activeID: String, error: String? = nil)
    case voice(phoneNumbers: [TrustedPhoneNumber], activeID: String, error: String? = nil)

    public var mode: TwoFactorDeliveryMode? {
        switch self {
        case .selectDeliveryMethod(let preferredMode, _):
            return preferredMode
        case .trustedDevice:
            return .trustedDevice
        case .sms:
            return .sms
        case .voice:
            return .voice
        }
    }

    public var error: String? {
        switch self {
        case .selectDeliveryMethod:
            return nil
        case .trustedDevice(let error):
            return error
        case .sms(_, _, let error):
            return error
        case .voice(_, _, let error):
            return error
        }
    }
}'''
    new_request = '''// V3_TFA_TYPED_STATE_V1: provider retry outcomes are represented by a closed enum.
public enum TwoFactorVerificationFailure: String, Sendable, Equatable {
    case incorrectCode
    case serviceUnavailable
    case unknown

    public var userMessage: String {
        switch self {
        case .incorrectCode: return "The verification code was not accepted. Enter a new code and try again."
        case .serviceUnavailable: return "Apple could not verify the code just now. Try again, or change verification method."
        case .unknown: return "Apple could not verify that code. Enter it again or change verification method."
        }
    }
}

public enum TwoFactorRequest: Sendable, Equatable {
    case selectDeliveryMethod(preferredMode: TwoFactorDeliveryMode, phoneNumbers: [TrustedPhoneNumber])
    case trustedDevice(error: String? = nil)
    case sms(phoneNumbers: [TrustedPhoneNumber], activeID: String, error: String? = nil)
    case voice(phoneNumbers: [TrustedPhoneNumber], activeID: String, error: String? = nil)

    public var mode: TwoFactorDeliveryMode? {
        switch self {
        case .selectDeliveryMethod(let preferredMode, _): return preferredMode
        case .trustedDevice: return .trustedDevice
        case .sms: return .sms
        case .voice: return .voice
        }
    }

    private var rawError: String? {
        switch self {
        case .selectDeliveryMethod: return nil
        case .trustedDevice(let error): return error
        case .sms(_, _, let error): return error
        case .voice(_, _, let error): return error
        }
    }

    public var verificationFailure: TwoFactorVerificationFailure? {
        guard let rawError, rawError.hasPrefix("V3_TFA_FAILURE:"),
              let value = TwoFactorVerificationFailure(rawValue: String(rawError.dropFirst("V3_TFA_FAILURE:".count))) else { return nil }
        return value
    }

    public var error: String? {
        verificationFailure?.userMessage ?? rawError
    }
}'''
    api_text = replace_once(api_text, old_request, new_request, "typed verification request")

    old_result = '''    private enum TwoFactorAuthValidationResult {
        case success
        case retry(message: String)
    }'''
    new_result = '''    // V3_TFA_TYPED_STATE_V1: wrong-code state comes from SideSign's typed response parser.
    private enum TwoFactorAuthValidationResult {
        case success
        case retry(TwoFactorVerificationFailure)
    }'''
    auth_text = replace_once(auth_text, old_result, new_result, "typed validation result")

    old_log = '            debugLog("[SideSign] Prompting user with 2FA request (\\(currentRequest))...")'
    new_log = '            debugLog("[SideSign] Prompting user with 2FA request mode=\\(currentRequest.mode?.rawValue ?? "unknown")")'
    auth_text = replace_once(auth_text, old_log, new_log, "private 2FA request logging")

    auth_text = replace_once(auth_text, '                        case .retry(let message):\n                            debugLog("[SideSign] 2FA verification failed, retrying: \\(message)")\n                            switch channel {\n                                case .trustedDevice:\n                                    currentRequest = .trustedDevice(error: message)\n                                case .sms(let phoneID):\n                                    currentRequest = .sms(phoneNumbers: phoneNumbers, activeID: phoneID, error: message)\n                                case .voice(let phoneID):\n                                    currentRequest = .voice(phoneNumbers: phoneNumbers, activeID: phoneID, error: message)\n                            }',
        '                        case .retry(let failure):\n                            debugLog("[SideSign] 2FA verification rejected kind=\\(failure.rawValue)")\n                            let typedError = "V3_TFA_FAILURE:\\(failure.rawValue)"\n                            switch channel {\n                                case .trustedDevice:\n                                    currentRequest = .trustedDevice(error: typedError)\n                                case .sms(let phoneID):\n                                    currentRequest = .sms(phoneNumbers: phoneNumbers, activeID: phoneID, error: typedError)\n                                case .voice(let phoneID):\n                                    currentRequest = .voice(phoneNumbers: phoneNumbers, activeID: phoneID, error: typedError)\n                            }', "typed retry handoff")

    auth_text = replace_once(auth_text,
        '            let msg = errorMsg ?? "Incorrect verification code. Please try again."\n            debugLog("[SideSign] Incorrect 2FA verification code (\\(errorCode), HTTP \\(statusCode)): \\(msg)")\n            return .retry(message: msg)',
        '            debugLog("[SideSign] Incorrect 2FA verification code (\\(errorCode), HTTP \\(statusCode))")\n            return .retry(.incorrectCode)', "incorrect-code classification")

    auth_text = replace_once(auth_text,
        '            let rawStr = prettyJSONString(from: data)\n            let reason = errorMsg ?? HTTPStatusCodes.localizedDescription(for: statusCode)\n            debugLog("[SideSign] 2FA verification failed (HTTP \\(statusCode)): \\(reason) - body: \\(rawStr)")\n            return .retry(message: reason)',
        '            debugLog("[SideSign] 2FA verification failed with HTTP status=\\(statusCode)")\n            return .retry(statusCode >= 500 || statusCode == 0 ? .serviceUnavailable : .unknown)', "safe non-success response")

    auth_text = replace_once(auth_text,
        '                let rawStr = prettyJSONString(from: data)\n                let reason = errorMsg ?? "Incorrect verification code or missing session token"\n                debugLog("[SideSign] Secondary code verification failed (HTTP \\(HTTPStatusCodes.ok) missing PE token header): \\(reason) - Body: \\(rawStr)")\n                return .retry(message: reason)',
        '                debugLog("[SideSign] Secondary code verification could not confirm the session token")\n                return .retry(.unknown)', "safe verification-token response")

    with api.open("w", encoding="utf-8", newline="") as stream:
        stream.write(api_text)
    with auth.open("w", encoding="utf-8", newline="") as stream:
        stream.write(auth_text)
    if MARKER not in api.read_text(encoding="utf-8") or MARKER not in auth.read_text(encoding="utf-8"):
        raise SystemExit("patch_sidesign_2fa_state: marker verification failed")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_sidesign_2fa_state.py <sidesign-root>")
    patch(Path(sys.argv[1]).resolve())
    print("typed SideSign 2FA result patch applied and verified")


if __name__ == "__main__":
    main()
