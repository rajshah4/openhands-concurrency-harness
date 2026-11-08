# Methodology

The question is operational: how many requested native agent jobs can the selected installation complete while its application remains usable?

## Hold these constant

Use the same fixture image digest, pinned source commits, scripts, model/provider, output-token limit, grouping, workload proportions and timeouts. Match application-host CPU/RAM and document VM family, CPU architecture, storage, application/Agent Server versions and runtime policies. If an architecture uses an external database/object store, record those additional resources separately.

This is a rough experiment, not a hardware-normalized benchmark. Do not attribute a difference solely to Compose versus another deployment if versions, quotas or hardware differ.

## Choose the concurrency sequence before running

Start with one job to qualify authentication, fixture contents, native execution, verification and cleanup. Then use increasing levels suitable for the installation. The example config is a starting sequence, not an established capacity claim. For a quick comparison, stop at the first operational failure instead of retrying until it happens to pass. A later intermediate point should be a separately identified follow-up.

At each level, assign jobs round-robin to CLI tests, Rust builds/tests and Flask tests. Prepare independent sandboxes and idle native conversations, verify pinned repository commits, then submit the identical prepared command and release the native runs through a barrier. Preparation time is excluded from the wave duration. Every level in this portable implementation uses a fresh cohort, which is cleaned before the next one.

## Bound work and observations

- Python workloads repeat complete test cycles for at least 60 seconds. Record cycle counts as well as elapsed time; the duration floor hides throughput degradation.
- Each subprocess has a 900-second timeout. A Rust workload consists of separate clean, build and test commands, so 900 seconds is not a single total-workload limit.
- The native agent is instructed to use a 1,000-second terminal timeout. Confirm this timeout is supported by your runtime. The agent may fail to follow the instruction; independent verification remains authoritative.
- The example controller observation window is 1,200 seconds per job after barrier release. Configure it consistently across targets and record it. The example whole-run budget is four hours, including startup and inter-wave work. Cleanup proceeds after that budget to remove known resources; it is separately bounded by API timeouts and ledger size.
- Health probes run every two seconds. Three consecutive failures (non-200 or timeout) are enough to call the application unavailable and stop the cohort. The interval is a scheduling target; response time and controller contention can increase it.
- Any unsuccessful/unverified job stops escalation by default. A nonzero allowance must be chosen in advance and disclosed; it is not a perfect pass.
- Stop flags are checked during startup and execution. Collected job outcomes are written incrementally so partial evidence survives an interrupted wave. In-flight calls may take up to their bounded request/command timeouts to return.

This public version adds earlier cooperative health-failure stopping and fresh cohorts compared with its deployment-specific predecessor. It is not a byte-for-byte reproduction of an earlier private experiment and makes no claim to reproduce its measurements.

## What counts as success

A native final message alone is not proof. Read the prepared script's structured result through the runtime bash API, verify the script SHA-256 and unchanged tracked repository state, and require the expected workload/wave and a start time associated with this wave. A passing script has at least one successful complete cycle and no command exception.

Classify explicit script failure separately from an absent/unreadable result, observation failure, interrupted job, startup failure and admission rejection. The summary groups unsuccessful/unverified jobs, while per-job records retain the distinction. Unknown outcomes never become passes. An unavailable application is an operational failure even if some jobs eventually finish.

Report three different concurrency quantities:

1. Jobs requested for the cohort.
2. Peak native agent status observed as running, using fresh observations within 15 seconds. Poll skew means this is approximate.
3. Maximum overlap of independently verified successful script intervals. This excludes unsuccessful/unverified scripts and is a lower bound on total activity during a failed wave. Runtime clock differences can also distort intervals; keep clocks synchronized.

Also record wave duration, workload-specific script duration distributions, Python cycle counts, health failures and operator-observed host CPU/memory pressure. This portable controller does not claim its own memory is the target host's memory. Obtain host telemetry separately using read-only monitoring.

## Interpret and finish

The last all-successful tested level and the first unsuccessful level bracket the tested behavior. A configured count/pod ceiling is an admission limit, not a measured memory ceiling. A timed-out build is an operational failure under the chosen deadline, not proof of an OS crash or OOM kill. Cached dependencies, workload choice, model latency, rate limits and runtime resource policies all affect the result.

Delete only owned fixtures through the application API and independently verify disappearance. Preserve unresolved creation intents when a POST response is uncertain; never repeat an uncertain creation blindly. Restore manually changed settings and revoke the temporary key after verified cleanup. Keep all raw evidence private. This repository intentionally publishes no measured results.
