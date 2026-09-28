@main
struct V3DeveloperDataAvailabilityHarness {
    static func main() {
        precondition(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
            authenticated: false, isLoading: false),
            "unauthenticated developer actions must be unavailable")
        precondition(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
            authenticated: false, isLoading: true),
            "unauthenticated and busy developer actions must be unavailable")
        precondition(V3DeveloperDataActionAvailabilityPolicy.isEnabled(
            authenticated: true, isLoading: false),
            "an authenticated idle developer action must be available")
        precondition(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
            authenticated: true, isLoading: true),
            "a developer action must remain unavailable while a request is active")
        print("V3_DEVELOPER_DATA_AVAILABILITY_PASS")
    }
}
