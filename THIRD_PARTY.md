# Third-party workloads

The fixture build fetches these public repositories at the exact commits in `workloads.json`:

- Click, Pallets, BSD-3-Clause: https://github.com/pallets/click
- Flask, Pallets, BSD-3-Clause: https://github.com/pallets/flask
- ripgrep, BurntSushi and contributors, MIT OR Unlicense: https://github.com/BurntSushi/ripgrep

Their source code is not vendored in this repository. Preserve their license files and dependency notices if redistributing a built fixture image. The harness's MIT license does not replace third-party licenses. Agent Server and Rust container images retain their respective upstream licensing and distribution terms. No container image is published by this repository.
