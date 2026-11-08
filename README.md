# OpenHands concurrency harness

Run a small mixed workload through native OpenHands agent conversations, increase concurrency, and stop when work no longer completes or the application becomes unavailable.

This repository contains the methodology and a portable harness. It contains no benchmark results, deployment addresses, cloud provisioning, credentials, or production sizing recommendations. It is an independent experiment, not an official OpenHands benchmark.

## What runs

An approximately equal mix of three prepared jobs:

| Job | Work |
| --- | --- |
| CLI tests | Click's full pytest suite, repeated for at least 60 seconds |
| Web application tests | Flask's `tests/test_basic.py`, repeated for at least 60 seconds |
| Build and test | A clean ripgrep release build and workspace tests, offline, with two Cargo workers |

Each agent receives one exact script command. It does not write an application or investigate a bug. The controller independently checks the result file, script hash, repository commit and tracked-file integrity. The Flask job is a stand-in for an automation workload; it does not exercise the product's automation scheduler.

Sources and dependencies are prepared before timing. Rust targets are cleaned before building. This measures a workload with cached inputs, not cold downloads or production traffic. See [methodology](docs/METHODOLOGY.md).

## Requirements

- Python 3.11+ on a Linux or macOS controller. The controller has no third-party Python dependencies.
- An idle, dedicated OpenHands installation with permission to create and delete test sandboxes/conversations in one existing organization.
- A short-lived, organization-bound application API key, and the expected organization/principal IDs from your own installation.
- An existing model profile, configured in advance with the same model and output limit across targets. One low-cost model is usually sufficient to execute these fixed commands.
- A fixture runtime built below and selected through the installation's supported runtime configuration.
- HTTPS access to the application and the runtime origins you explicitly allowlist. Private CAs must be trusted normally; TLS verification is not disabled.

**Compatibility:** this uses the V1 application sandbox/conversation APIs and Agent Server bash/event APIs. These APIs vary between releases. Check your installed OpenAPI schema and run a one-job canary before increasing concurrency. The portable packaging has offline regression coverage; it has not been qualified against every release. There is no compatibility promise for future APIs.

## Build the fixture runtime

Run the build on a suitable test build host. Build-time downloads and dependency installation happen here, outside measured waves. The supplied pins select Linux amd64 upstream images and Python 3.13; they are a reproducibility baseline, not a recommendation to run an older image in production.

```sh
RUNTIME_IMAGE=$(python3 -c 'import json; print(json.load(open("images.lock.json"))["runtime"]["reference"])')
RUST_IMAGE=$(python3 -c 'import json; print(json.load(open("images.lock.json"))["rust"]["reference"])')
docker build --platform linux/amd64 -f Dockerfile.fixture \
  --build-arg RUNTIME_IMAGE="$RUNTIME_IMAGE" \
  --build-arg RUST_IMAGE="$RUST_IMAGE" \
  -t concurrency-fixture:local .
```

Inspect the fixture image and record its digest. Make it available on the selected installation using its supported image registry/runtime configuration. For a Replicated installation, use the Admin Console and documented OpenHands configuration surfaces. This project does not access LiteLLM or edit Kubernetes, Docker Compose, database or node settings for you.

Changing a base image/toolchain may require refreshing dependency pins and requalifying each workload. Third-party source/license information is in [THIRD_PARTY.md](THIRD_PARTY.md).

## Configure a target

```sh
cp config.example.json config.local.json
```

Replace the reserved example domains, example IDs and profile name. Set the runtime origin allowlist to your actual runtime endpoints. A wildcard matches exactly one subdomain label. Keep `legacy_personal_sandbox_schema` false unless your installed API omits `credential_org_id` and you deliberately use an existing personal organization whose ID equals its user ID; this exception cannot be used for a shared organization.

Before running, manually verify the selected origin, principal, organization, runtime fixture, profile and resource limits. Configure independent sandboxes (`NO_GROUPING`) and a count cap that can admit the requested cohort. Record original and effective values. For Kubernetes-backed installations, also check node pod capacity and resource requests/limits. A count limit is distinct from physical capacity. See [configuration checklist](docs/CONFIGURATION.md).

Supply the API key through the environment, using your normal secret manager or a hidden shell prompt. Do not put it in the configuration or command line. The default environment variable name is `OPENHANDS_API_KEY`. The harness neither creates nor revokes API keys and does not change profiles, caps, grouping or runtime selection. Restore your changes and revoke your temporary key after cleanup.

Validate without connecting to the deployment:

```sh
python3 harness/run.py --config config.local.json
```

Start with `"levels": [1]`. After that canary completes and cleanup is verified, set a modest increasing sequence appropriate for your fixture and explicitly run it:

```sh
python3 harness/run.py --config config.local.json --execute
```

Each level creates a fresh cohort and removes it before the next level. Preparation is outside wave timing, uses at most four workers by default, and application control requests are paced at 0.5 requests/second. Prepared runtime conversation endpoints receive a lightweight read every 30 seconds to avoid idle expiry. The controller starts native agent runs at a common barrier. HTTP dispatch and model execution still introduce skew: requested jobs, native running-agent peak and verified script overlap are separate measurements.

## Stop, inspect and recover

Application health is probed every two seconds with a two-second request timeout, separately from the control-plane rate budget. By default, three consecutive non-200 responses/timeouts stop the run. Any unsuccessful or unverified workload stops escalation by default. There are also explicit job and whole-run deadlines. See [methodology](docs/METHODOLOGY.md) for exact behavior.

`Ctrl-C` or `SIGTERM` requests cooperative stop and cleanup. In-flight network calls/startup polls have bounded timeouts; cooperative stop is not an instantaneous remote process kill. Server-side command limits remain in force if connectivity is lost. No VM or application restart is attempted.

A run directory under ignored `runs/` contains `status.json`, health samples, final/partial wave records and the ownership ledger. Files and directories are created with restrictive permissions. These are private operational artifacts, not publication-ready output: API records can contain runtime credentials, addresses and resource IDs. Do not upload run directories or diagnostic archives.

Only resources with recorded creation provenance are eligible for deletion. Cleanup verifies absence through native APIs. If the application is unavailable or a creation response is ambiguous, cleanup can remain incomplete; do not delete arbitrary inventory to make it appear successful. Keep the ledger and reconcile the exact pending operation with your installation's supported tools.

To retry cleanup against the same recorded run, with the same credential environment variable available:

```sh
OH_LOAD_SESSION=runs/YOUR_RUN/session.json python3 harness/cleanup.py
```

Do not run cleanup concurrently with an active harness. A local file lock prevents duplicate execution for the same target/principal from the same output root; use only one controller/output root for an installation. It does not coordinate different machines.

## Validate the portable code

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q harness fixtures
```

These checks use temporary files and fake APIs; they create no real workloads. No results or deployment fixtures are bundled. [Review notes](docs/REVIEW.md) describe what was adapted and what still needs a live canary before external use.
