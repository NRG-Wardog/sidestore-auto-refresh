@main
struct V3LogPrivacyHarness {
    static func main() {
        let input = """
        [DownloadAppOperation] start downloadURL=https://cdn.example/app.ipa?signature=SECRET_QUERY
        [AppBootManager] fetched UDID: PRIVATE_DEVICE_ID
        Error Domain=NSURLErrorDomain Code=-1005 UserInfo={NSErrorFailingURLStringKey=https://apple.example/auth?token=SECRET_TOKEN, NSLocalizedDescription=PRIVATE_PROVIDER_BODY}
        [SideBackup] log path=/private/var/mobile/Containers/Data/private-backup-record
        [AppDelegate] currentBundlePath=/var/mobile/Containers/Bundle/Application/PRIVATE_PATH
        [Authentication] password=PRIVATE_PASSWORD verificationCode=123456
        Authorization: Bearer PRIVATE_BEARER_TOKEN
        accessToken=PRIVATE_ACCESS_TOKEN refreshToken=PRIVATE_REFRESH_TOKEN
        bundleIdentifier=com.spotify.client App ID=PRIVATE_APP_ID teamIdentifier=PRIVATE_TEAM_ID
        session=PRIVATE_SESSION session_id=PRIVATE_SESSION_ID request_id=PRIVATE_REQUEST_ID
        correlationID=123e4567-e89b-12d3-a456-426614174000
        authToken=PRIVATE_AUTH_TOKEN xcodeToken=PRIVATE_XCODE_TOKEN secret=PRIVATE_SECRET credential=PRIVATE_CREDENTIAL
        {"session":"JSON_SESSION", "session_id":"JSON_SESSION_ID", "request_id":"JSON_REQUEST_ID",
         "correlationID":"JSON_CORRELATION", "authToken":"JSON_AUTH", "xcodeToken":"JSON_XCODE",
         "secret":"JSON_SECRET", "credential":"JSON_CREDENTIAL"}
        [PipelineRunner] starting operation for: com.example.privateguest
        [V3_INSTALL_UI] tap
        """
        let safe = formatLogMessage(input)
        for secret in ["SECRET_QUERY", "PRIVATE_DEVICE_ID", "SECRET_TOKEN", "PRIVATE_PROVIDER_BODY",
                       "private-backup-record", "PRIVATE_PATH", "PRIVATE_PASSWORD", "123456",
                       "PRIVATE_BEARER_TOKEN", "PRIVATE_ACCESS_TOKEN", "PRIVATE_REFRESH_TOKEN",
                       "com.spotify.client", "PRIVATE_APP_ID", "PRIVATE_TEAM_ID",
                       "PRIVATE_SESSION", "PRIVATE_SESSION_ID", "PRIVATE_REQUEST_ID",
                       "123e4567-e89b-12d3-a456-426614174000", "PRIVATE_AUTH_TOKEN", "PRIVATE_XCODE_TOKEN",
                       "PRIVATE_SECRET", "PRIVATE_CREDENTIAL", "JSON_SESSION", "JSON_SESSION_ID",
                       "JSON_REQUEST_ID", "JSON_CORRELATION", "JSON_AUTH", "JSON_XCODE", "JSON_SECRET",
                       "JSON_CREDENTIAL", "com.example.privateguest"] {
            precondition(!safe.contains(secret), "copyable log retained a sensitive value: \(secret)")
        }
        precondition(safe.contains("native_code=-1005"))
        precondition(safe.contains("[V3_INSTALL_UI] tap"))
        precondition(safe.contains("native_code=-1005"), "safe internal error diagnostics must remain")
        print("V3_LOG_PRIVACY_PASS")
    }
}
