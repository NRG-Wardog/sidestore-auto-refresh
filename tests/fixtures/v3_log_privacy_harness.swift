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
        [PipelineRunner] starting operation for: com.example.privateguest
        [V3_INSTALL_UI] tap
        """
        let safe = formatLogMessage(input)
        for secret in ["SECRET_QUERY", "PRIVATE_DEVICE_ID", "SECRET_TOKEN", "PRIVATE_PROVIDER_BODY",
                       "private-backup-record", "PRIVATE_PATH", "PRIVATE_PASSWORD", "123456",
                       "PRIVATE_BEARER_TOKEN", "PRIVATE_ACCESS_TOKEN", "PRIVATE_REFRESH_TOKEN",
                       "com.spotify.client", "PRIVATE_APP_ID", "PRIVATE_TEAM_ID",
                       "com.example.privateguest"] {
            precondition(!safe.contains(secret), "copyable log retained a sensitive value: \(secret)")
        }
        precondition(safe.contains("native_code=-1005"))
        precondition(safe.contains("[V3_INSTALL_UI] tap"))
        print("V3_LOG_PRIVACY_PASS")
    }
}
