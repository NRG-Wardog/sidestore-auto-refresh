import Foundation

let secretReason = "private-token=CRASH_REASON_SECRET /Users/alice/Documents/account.json"
let emittedMessage = V3CrashLogPrivacy.safeCrashMarker(reason: secretReason)

precondition(emittedMessage.contains("UNCAUGHT_NSEXCEPTION_CRASH"))
precondition(emittedMessage.contains("details=omitted"))
precondition(!emittedMessage.contains("CRASH_REASON_SECRET"))
precondition(!emittedMessage.contains("/Users/alice"))
print("V3_CRASH_LOG_PRIVACY_PASS")
