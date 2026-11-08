# Configuration checklist

Use a dedicated idle installation and an existing authorized organization. Inspect the installed OpenAPI schema rather than assuming fields or defaults match another release.

Before a run, record locally:

- Application origin, expected user ID, expected organization ID, key binding and expiry.
- Existing model profile, provider/model identifier, output-token cap and inference route.
- Runtime image digest, selected runtime template and grouping strategy.
- Application concurrent sandbox cap and any organization-level limits.
- Node-wide pod cap, platform pod overhead, CPU/memory/storage requests and limits, and warm pools when applicable.
- Application/runtime versions, VM family, logical CPU count, RAM and storage.
- Separate database/object-storage resources, when used.

If the chosen count exceeds a policy limit, either record that admission limit as the outcome or deliberately change it through supported deployment settings and disclose the override. Do not silently tune CPU/memory policies between platforms. This harness does not make administrative changes. Keep the original values and a restoration plan.

Use the application/Replicated Admin Console and documented APIs for configuration. The harness uses only native application and runtime execution APIs. It does not access an inference gateway's internal administration, extract browser cookies or configure cloud infrastructure.

Run the same canary on each target before scaling. A failure to start one sandbox is a baseline problem, not a capacity result. Confirm the fixture sources, dependencies and toolchains are available before timing; no installs or downloads belong in the measured tasks.

Application cap increases do not establish a safe production setting. Match policy to host capacity, workload and multi-tenant isolation requirements.
