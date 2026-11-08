# Validation and review notes

This repository contains methodology and portable source code, with no measured results or deployment-specific configuration.

The prepared workload script, pinned public sources, native API client, ownership ledger and runtime execution/verification path were extracted from a deployment-specific harness. Cloud checks, instance identity data, credential files, automatic key changes, private logs, result reports and infrastructure restoration scripts were excluded.

The portable runner is adapted code. It adds environment-based credential input, explicit configuration validation, fresh cohorts, local duplicate-run locking, incremental job records and cooperative stopping on repeated health failures. Those changes are not represented as a deployed benchmark validation. Offline tests cover the stop/cleanup path, scope checks, ownership protection, configuration validation, independent result overlap and command failure recording. A live one-job canary is required on each chosen deployment before scaling.

Review these points when changing or redistributing the harness:

- Repository name and description: proposed `openhands-concurrency-harness`, “A small native-agent concurrency harness and reproducible load-testing methodology for OpenHands.”
- README, methodology and installed-API compatibility assumptions.
- The proposed MIT license and third-party attribution.
- Entire source/config/document tree and Git history for credentials, identifiers, private addresses and measured results.

There are no workflow files that automatically contact a deployment or publish anything. The fixture build and live workload execution are explicit operator actions.
