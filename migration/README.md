# Remaining source compatibility fixtures

The current application build uses the exact component graph in
`ci/issues-native/candidate-graph.json`; see `ci/current-build/`.

This directory is not a user-data migration system. Its remaining contracts,
source fixtures and diagnostic receipts are still consumed by legacy packaging
and regression helpers. Do not treat them as current build or device evidence.

Offline candidate finalizers, their private tests and unconsumed cutover reports
have been retired. The remaining parity report is still hash-referenced by two
compatibility records. Further removal requires separating those consumers,
not deleting application regression coverage or changing runtime source pins.
