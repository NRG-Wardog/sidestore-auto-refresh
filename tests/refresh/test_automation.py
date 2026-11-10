"""The combined product owns scheduling in the host, not a second backend loop.

Standalone SideStore patch replay and its synthetic schedule/history model were
retired. Host schedule/history runtime suites remain in test_refresh_scheduler,
test_livecontainer and test_livecontainer_history. Backend startup/recovery has
its own maintained-owner suite; this checks the cross-process ownership boundary.
"""
import unittest
from tests.owner_source import owner_source


class AutomationOwnershipTests(unittest.TestCase):
    def test_host_registers_scheduler_after_shared_store_publication(self):
        host = owner_source("LiveContainerSwiftUI/App/AppDelegate.swift")
        entry = host[host.index("func application("):host.index("class SceneDelegate")]
        self.assertLess(entry.index("V3SharedAppGroup.publishRuntimeGroup"),
                        entry.index("LiveContainerAutoRefreshScheduler.register()"))
        for operation in ("register()", "recoverAfterLaunchOrResume()", "schedule()"):
            self.assertIn("LiveContainerAutoRefreshScheduler." + operation, entry)

    def test_backend_does_not_contain_retired_standalone_scheduler(self):
        backend = owner_source("AltStore/AppDelegate.swift", "SideStore")
        for retired in ("AutomaticRefreshSchedule", "AutomaticRefreshHistory", "startAutomaticRefresh"):
            self.assertNotIn(retired, backend)

    def test_backend_notification_uses_structured_failure_not_raw_error(self):
        operation = owner_source("SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift", "SideStore")
        body = operation[operation.index("private func scheduleFinishedRefreshingNotification("):operation.index("private func saveRefreshAttempt(")]
        self.assertIn("content.body = failure.message", body)
        self.assertIn("CombinedFailure", body)
        self.assertNotIn("content.body = error.localizedDescription", body)
        self.assertNotIn('content.body = "Failed to refresh apps in background.', body)


if __name__ == "__main__": unittest.main()
