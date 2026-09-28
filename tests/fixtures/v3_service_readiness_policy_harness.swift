@main
struct ServiceReadinessPolicyHarness {
    static func main() {
        precondition(V3ServiceReadinessProbeState.resolve(ready: true, invalid: false, expired: true) == .ready,
            "a verified readiness reply received at the deadline wins over timeout")
        precondition(V3ServiceReadinessProbeState.resolve(ready: false, invalid: true, expired: true) == .invalid,
            "a malformed readiness reply stays a typed terminal failure")
        precondition(V3ServiceReadinessProbeState.resolve(ready: false, invalid: false, expired: true) == .timedOut)

        var schedule = V3ServiceReadinessBackoff()
        let expected: [TimeInterval] = [0.2, 0.4, 0.8, 1.0, 1.0]
        for value in expected {
            guard let actual = schedule.nextDelay(remaining: 30) else {
                preconditionFailure("readiness retry ended before its deadline")
            }
            precondition(abs(actual - value) < 0.000_001, "startup retry backoff did not grow to its cap")
        }

        var bounded = V3ServiceReadinessBackoff()
        var remaining: TimeInterval = 30
        var attempts = 0
        while let delay = bounded.nextDelay(remaining: remaining) {
            attempts += 1
            remaining = delay >= remaining ? 0 : remaining - delay
        }
        precondition(attempts <= 32, "startup readiness must not issue up to 150 snapshots in 30 seconds")

        var deadline = V3ServiceReadinessBackoff()
        let shortDelay = deadline.nextDelay(remaining: 0.05)!
        precondition(abs(shortDelay - 0.05) < 0.000_001)
        precondition(deadline.nextDelay(remaining: 0) == nil)
        print("V3_SERVICE_READINESS_POLICY_PASS")
    }
}
